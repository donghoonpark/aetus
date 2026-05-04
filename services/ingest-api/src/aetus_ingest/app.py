from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
import time

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from aetus_ingest.auth import (
    extract_bearer_token,
    is_source_ip_allowed,
    verify_device_token,
    verify_hmac_signature,
)
from aetus_ingest.config import Settings
from aetus_ingest.control_backup import run_sqlite_backup_loop
from aetus_ingest.control_db import ControlStore, DeviceRecord, SqliteControlStore, create_control_store
from aetus_ingest.control_status import build_control_status
from aetus_ingest.generated import ingest_pb2
from aetus_ingest.normalize import normalize_event
from aetus_ingest.publisher import InMemoryEventPublisher, KafkaEventPublisher
from aetus_ingest.rate_limit import InMemoryRateLimiter, RateLimitDecision, RateLimitPlan
from aetus_ingest.schemas import (
    ControlStatusResponse,
    DeviceIssueRequest,
    DeviceListResponse,
    DeviceSummary,
    ProvisionRequest,
    ProvisionResponse,
    TimeSyncResponse,
)

ADMIN_PAGE_SIZE = 10
RTC_VALID_AFTER_UNIX_S = 1_577_836_800
ADMIN_SESSION_COOKIE = "aetus_admin_session"


class _AdminSessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._sessions: dict[str, float] = {}
        self._ttl = ttl_seconds

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = time.monotonic() + self._ttl
        return token

    def validate(self, token: str) -> bool:
        deadline = self._sessions.pop(token, 0)
        if deadline and deadline > time.monotonic():
            self._sessions[token] = deadline
            return True
        return False

    def revoke(self, token: str) -> None:
        self._sessions.pop(token, None)


def _is_admin_auth_enabled(settings: Settings) -> bool:
    return bool(settings.admin_password and settings.admin_password.strip())


def _parse_admin_session(request: Request) -> str | None:
    return request.cookies.get(ADMIN_SESSION_COOKIE)


def _to_device_summary(record: DeviceRecord) -> DeviceSummary:
    return DeviceSummary(
        device_id=record.device_id,
        hardware_id=record.hardware_id,
        token=record.token,
        model=record.model,
        firmware_version=record.firmware_version,
        site_code=record.site_code,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def create_app(
    settings: Settings | None = None,
    publisher: InMemoryEventPublisher | None = None,
    rate_limiter: InMemoryRateLimiter | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        backup_task: asyncio.Task[None] | None = None
        control_db = app.state.control_db
        app.state.control_db_backup_task = None
        if (
            isinstance(control_db, SqliteControlStore)
            and app.state.settings.control_db_backup_enabled
            and app.state.settings.control_db_backup_interval_seconds > 0
        ):
            backup_task = asyncio.create_task(
                run_sqlite_backup_loop(
                    control_db.path,
                    app.state.settings.control_db_backup_dir,
                    interval_seconds=app.state.settings.control_db_backup_interval_seconds,
                    retention_count=app.state.settings.control_db_backup_retention_count,
                    backup_on_startup=app.state.settings.control_db_backup_on_startup,
                ),
                name="aetus-sqlite-control-backup",
            )
            app.state.control_db_backup_task = backup_task
        try:
            yield
        finally:
            if backup_task is not None:
                backup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await backup_task

    app = FastAPI(title="AETUS Ingest Server", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app.state.settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Device-Id", "X-Aetus-Signature"],
    )
    app.state.control_db = create_control_store(app.state.settings)
    app.state.control_db.initialize()
    app.state.control_db.seed_hardware_allowlist(app.state.settings.allowed_hardware_ids)
    app.state.control_db.seed_devices(app.state.settings.device_tokens)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    if publisher is not None:
        app.state.publisher = publisher
    elif app.state.settings.publisher_backend == "kafka":
        app.state.publisher = KafkaEventPublisher(app.state.settings)
    else:
        app.state.publisher = InMemoryEventPublisher()
    app.state.rate_limiter = rate_limiter or InMemoryRateLimiter()
    app.state.admin_sessions = _AdminSessionStore(resolved_settings.admin_session_ttl_seconds)

    def raise_rate_limited(decision: RateLimitDecision, detail: str) -> None:
        retry_after = max(1, ceil(decision.retry_after_seconds))
        raise HTTPException(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )

    def _require_admin_session(request: Request) -> None:
        settings: Settings = app.state.settings
        if not _is_admin_auth_enabled(settings):
            return
        token = _parse_admin_session(request)
        if not token or not app.state.admin_sessions.validate(token):
            raise HTTPException(status_code=401, detail="authentication required")

    def ingest_rate_plan(device_id: str) -> RateLimitPlan:
        is_allowlisted = device_id in app.state.settings.allowlist_device_ids
        return RateLimitPlan(
            rate_per_second=(
                app.state.settings.allowlist_requests_per_second
                if is_allowlisted
                else app.state.settings.ingest_requests_per_second
            ),
            burst=app.state.settings.allowlist_burst if is_allowlisted else app.state.settings.ingest_burst,
        )

    async def prepare_device_ingest(
        *,
        request: Request,
        x_device_id: str,
        rate_limit_prefix: str,
    ) -> str:
        settings: Settings = app.state.settings
        source_ip = request.client.host if request.client else "0.0.0.0"

        if not is_source_ip_allowed(source_ip, settings):
            raise HTTPException(status_code=403, detail="source ip not allowed")

        rate_limit_key = f"{rate_limit_prefix}:{x_device_id}:{source_ip}"
        rate_decision = app.state.rate_limiter.consume(rate_limit_key, ingest_rate_plan(x_device_id))
        if not rate_decision.allowed:
            raise_rate_limited(rate_decision, "rate limit exceeded")

        return source_ip

    async def require_bearer_device_auth(
        *,
        x_device_id: str,
        authorization: str | None,
    ) -> None:
        control_db: ControlStore = app.state.control_db
        try:
            token = extract_bearer_token(authorization)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        if not await verify_device_token(x_device_id, token, control_db):
            raise HTTPException(status_code=401, detail="invalid device token")

    async def require_ingest_auth(
        *,
        request: Request,
        x_device_id: str,
        authorization: str | None,
        x_aetus_signature: str | None,
        body: bytes,
    ) -> None:
        if x_aetus_signature:
            ok = await verify_hmac_signature(
                device_id=x_device_id,
                method=request.method,
                path=request.url.path,
                body=body,
                signature_header=x_aetus_signature,
                control_db=app.state.control_db,
            )
            if not ok:
                raise HTTPException(status_code=401, detail="invalid hmac signature")
            return

        await require_bearer_device_auth(x_device_id=x_device_id, authorization=authorization)

    async def render_admin_devices_page(
        request: Request,
        *,
        flash_message: str | None,
        issued_device: DeviceRecord | None,
        page: int,
        query: str = "",
    ) -> HTMLResponse:
        settings: Settings = app.state.settings
        control_db: ControlStore = app.state.control_db
        current_page = max(page, 1)
        total_devices = await control_db.count_devices_readonly(query=query)
        total_pages = max(ceil(total_devices / ADMIN_PAGE_SIZE), 1)
        if current_page > total_pages:
            current_page = total_pages
        offset = (current_page - 1) * ADMIN_PAGE_SIZE
        devices = await control_db.list_devices_readonly(limit=ADMIN_PAGE_SIZE, offset=offset, query=query)
        return templates.TemplateResponse(
            request,
            "admin_devices.html",
            {
                "devices": devices,
                "flash_message": flash_message,
                "issued_device": issued_device,
                "control_db_detail": _control_db_detail(settings),
                "allowed_source_cidrs": ", ".join(str(network) for network in settings.allowed_source_cidrs),
                "current_page": current_page,
                "page_size": ADMIN_PAGE_SIZE,
                "total_devices": total_devices,
                "total_pages": total_pages,
                "has_previous": current_page > 1,
                "has_next": current_page < total_pages,
                "previous_page": current_page - 1,
                "next_page": current_page + 1,
                "search_query": query,
            },
        )

    @app.get("/v1/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/v1/time", response_model=TimeSyncResponse)
    async def time_sync(
        request: Request,
        response: Response,
        x_device_id: str = Header(..., alias="X-Device-Id"),
        authorization: str | None = Header(None, alias="Authorization"),
    ) -> TimeSyncResponse:
        await prepare_device_ingest(
            request=request,
            x_device_id=x_device_id,
            rate_limit_prefix="time",
        )
        await require_bearer_device_auth(x_device_id=x_device_id, authorization=authorization)

        unix_time_ns = time.time_ns()
        unix_time_s = unix_time_ns // 1_000_000_000
        response.headers["Cache-Control"] = "no-store"
        return TimeSyncResponse(
            unix_time_s=unix_time_s,
            unix_time_ms=unix_time_ns // 1_000_000,
            unix_time_ns=str(unix_time_ns),
            iso8601=datetime.fromtimestamp(unix_time_s, timezone.utc).isoformat().replace("+00:00", "Z"),
            source="ingest-api",
            valid_after_unix_s=RTC_VALID_AFTER_UNIX_S,
        )

    @app.post("/v1/control/login")
    async def control_login(request: Request, response: Response) -> JSONResponse:
        settings: Settings = app.state.settings
        if not _is_admin_auth_enabled(settings):
            return JSONResponse({"message": "admin auth not configured"})
        body = await request.json()
        password = body.get("password", "")
        if password != settings.admin_password:
            raise HTTPException(status_code=401, detail="invalid password")
        token = app.state.admin_sessions.create()
        response.set_cookie(
            ADMIN_SESSION_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            max_age=settings.admin_session_ttl_seconds,
        )
        return JSONResponse({"message": "authenticated"})

    @app.post("/v1/control/logout")
    async def control_logout(request: Request, response: Response) -> JSONResponse:
        token = _parse_admin_session(request)
        if token:
            app.state.admin_sessions.revoke(token)
        response.delete_cookie(ADMIN_SESSION_COOKIE)
        return JSONResponse({"message": "logged out"})

    @app.get("/v1/control/status", response_model=ControlStatusResponse)
    async def control_status(request: Request) -> ControlStatusResponse:
        _require_admin_session(request)
        settings: Settings = app.state.settings
        return await build_control_status(settings)

    @app.get("/v1/control/devices", response_model=DeviceListResponse)
    async def control_devices(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=50),
        q: str = Query("", max_length=100),
    ) -> DeviceListResponse:
        _require_admin_session(request)
        control_db: ControlStore = app.state.control_db
        current_page = max(page, 1)
        total_items = await control_db.count_devices_readonly(query=q)
        total_pages = max(ceil(total_items / page_size), 1)
        if current_page > total_pages:
            current_page = total_pages
        offset = (current_page - 1) * page_size
        items = await control_db.list_devices_readonly(limit=page_size, offset=offset, query=q)
        return DeviceListResponse(
            items=[_to_device_summary(item) for item in items],
            page=current_page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
            query=q,
        )

    @app.post("/v1/control/devices/issue", response_model=DeviceSummary, status_code=status.HTTP_201_CREATED)
    async def control_issue_device(request: Request, payload: DeviceIssueRequest) -> DeviceSummary:
        _require_admin_session(request)
        control_db: ControlStore = app.state.control_db
        issued = await control_db.issue_device_token(
            hardware_id=payload.hardware_id,
            model=payload.model,
            firmware_version=payload.firmware_version,
            site_code=payload.site_code,
        )
        return _to_device_summary(issued)

    @app.post("/v1/ingest", status_code=status.HTTP_202_ACCEPTED)
    async def ingest(
        request: Request,
        response: Response,
        x_device_id: str = Header(..., alias="X-Device-Id"),
        authorization: str | None = Header(None, alias="Authorization"),
        x_aetus_signature: str | None = Header(None, alias="X-Aetus-Signature"),
        content_type: str | None = Header(None, alias="Content-Type"),
    ) -> dict[str, str | int]:
        settings: Settings = app.state.settings

        if not content_type or "application/x-protobuf" not in content_type:
            raise HTTPException(status_code=415, detail="content-type must be application/x-protobuf")

        source_ip = await prepare_device_ingest(
            request=request,
            x_device_id=x_device_id,
            rate_limit_prefix="ingest",
        )

        body = await request.body()
        if len(body) > settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="request body too large")

        await require_ingest_auth(
            request=request,
            x_device_id=x_device_id,
            authorization=authorization,
            x_aetus_signature=x_aetus_signature,
            body=body,
        )

        event = ingest_pb2.IngestEvent()
        try:
            event.ParseFromString(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid protobuf") from exc

        if event.device_id != x_device_id:
            raise HTTPException(status_code=400, detail="device id mismatch")
        if not event.boot_id:
            raise HTTPException(status_code=400, detail="boot_id required")
        if event.WhichOneof("body") is None:
            raise HTTPException(status_code=400, detail="body missing")

        normalized = normalize_event(event, source_ip=source_ip)
        await app.state.publisher.publish(normalized)

        response.headers["X-Request-Id"] = normalized["request_id"]
        return {
            "request_id": normalized["request_id"],
            "status": "accepted",
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "device_id": event.device_id,
            "sequence": event.sequence,
        }

    @app.post("/v1/provision", response_model=ProvisionResponse, status_code=status.HTTP_201_CREATED)
    async def provision(
        request: Request,
        payload: ProvisionRequest,
        authorization: str | None = Header(None, alias="Authorization"),
    ) -> ProvisionResponse:
        settings: Settings = app.state.settings
        control_db: ControlStore = app.state.control_db
        source_ip = request.client.host if request.client else "0.0.0.0"

        if not is_source_ip_allowed(source_ip, settings):
            raise HTTPException(status_code=403, detail="source ip not allowed")

        try:
            token = extract_bearer_token(authorization)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        if token != settings.bootstrap_token:
            raise HTTPException(status_code=401, detail="invalid bootstrap token")

        bootstrap_plan = RateLimitPlan(
            rate_per_second=settings.bootstrap_requests_per_window / settings.bootstrap_window_seconds,
            burst=settings.bootstrap_requests_per_window,
        )
        bootstrap_decision = app.state.rate_limiter.consume(f"bootstrap:{source_ip}", bootstrap_plan)
        if not bootstrap_decision.allowed:
            raise_rate_limited(bootstrap_decision, "bootstrap rate limit exceeded")

        if not await control_db.is_hardware_allowed_readonly(payload.hardware_id):
            raise HTTPException(status_code=403, detail="hardware id not allowlisted")

        issued = await control_db.issue_device_token(
            hardware_id=payload.hardware_id,
            model=payload.model,
            firmware_version=payload.firmware_version,
            site_code=payload.site_code,
        )
        return ProvisionResponse(
            device_id=issued.device_id,
            token_type="Bearer",
            access_token=issued.token,
            config={
                "ingest_url": "/v1/ingest",
                "max_batch_size": 1,
                "retry_backoff_ms": 3000,
            },
        )

    @app.get("/admin/devices", response_class=HTMLResponse)
    async def admin_devices(
        request: Request,
        response: Response,
        page: int = Query(1, ge=1),
        q: str = Query("", max_length=100),
    ) -> HTMLResponse:
        settings: Settings = app.state.settings
        if _is_admin_auth_enabled(settings):
            token = _parse_admin_session(request)
            if not token or not app.state.admin_sessions.validate(token):
                return _render_login_page(request, error=None)
        return await render_admin_devices_page(request, flash_message=None, issued_device=None, page=page, query=q)

    @app.post("/admin/login", response_class=HTMLResponse)
    async def admin_login(
        request: Request,
        response: Response,
        password: str = Form(""),
        redirect: str = Form("/admin/devices"),
    ) -> HTMLResponse:
        settings: Settings = app.state.settings
        if not _is_admin_auth_enabled(settings):
            response.headers["Location"] = redirect
            response.status_code = status.HTTP_302_FOUND
            return HTMLResponse("")
        if password != settings.admin_password:
            return _render_login_page(request, error="Invalid password")
        token = app.state.admin_sessions.create()
        response = _login_redirect(redirect, token, settings.admin_session_ttl_seconds)
        return response

    @app.post("/admin/logout", response_class=HTMLResponse)
    async def admin_logout(request: Request) -> HTMLResponse:
        token = _parse_admin_session(request)
        if token:
            app.state.admin_sessions.revoke(token)
        response = HTMLResponse(
            '<html><body><script>document.cookie="aetus_admin_session=;path=/;max-age=0";location.href="/admin/devices";</script></body></html>'
        )
        response.delete_cookie(ADMIN_SESSION_COOKIE)
        return response

    @app.post("/admin/devices/issue", response_class=HTMLResponse)
    async def admin_issue_device(
        request: Request,
        hardware_id: str = Form(...),
        model: str = Form("esp32-c5"),
        firmware_version: int | None = Form(None),
        site_code: str | None = Form(None),
        page: int = Form(1),
        search_query: str = Form(""),
    ) -> HTMLResponse:
        settings: Settings = app.state.settings
        if _is_admin_auth_enabled(settings):
            token = _parse_admin_session(request)
            if not token or not app.state.admin_sessions.validate(token):
                return _render_login_page(request, error="Session expired. Please log in again.")
        control_db: ControlStore = app.state.control_db
        issued = await control_db.issue_device_token(
            hardware_id=hardware_id,
            model=model,
            firmware_version=firmware_version,
            site_code=site_code,
        )
        return await render_admin_devices_page(
            request,
            flash_message="Device token has been issued successfully.",
            issued_device=issued,
            page=page,
            query=search_query,
        )

    return app


def _render_login_page(request: Request, *, error: str | None = None) -> HTMLResponse:
    error_html = f'<div class="alert alert-danger">{error}</div>' if error else ""
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AETUS Admin Login</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
</head>
<body class="bg-light">
<div class="container" style="max-width:400px;margin-top:80px">
<div class="card shadow">
<div class="card-body text-center">
<h5 class="card-title mb-3">AETUS Admin</h5>
{error_html}
<form method="post" action="/admin/login">
<input type="hidden" name="redirect" value="/admin/devices">
<div class="mb-3">
<label for="password" class="form-label">Password</label>
<input type="password" class="form-control" id="password" name="password" autofocus>
</div>
<button type="submit" class="btn btn-primary w-100">Sign In</button>
</form>
</div></div></div>
</body></html>"""
    )


def _login_redirect(redirect: str, token: str, max_age: int) -> HTMLResponse:
    response = HTMLResponse(
        f'<html><body>Redirecting...<script>location.href="{redirect}";</script></body></html>'
    )
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        max_age=max_age,
    )
    return response


def _control_db_detail(settings: Settings) -> str:
    backend = settings.control_db_backend.strip().lower()
    if backend == "sqlite":
        return f"sqlite:{settings.control_db_path}"
    dsn_tail = settings.resolved_control_database_url.rsplit("@", 1)[-1]
    return f"postgres:{settings.control_db_schema}@{dsn_tail}"
