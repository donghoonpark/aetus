from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from pathlib import Path
import time

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aetus_ingest.auth import extract_bearer_token, is_source_ip_allowed, verify_device_token
from aetus_ingest.config import Settings
from aetus_ingest.control_db import ControlDB, DeviceRecord
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
    app = FastAPI(title="AETUS Ingest Server", version="0.1.0")
    app.state.settings = settings or Settings.from_env()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app.state.settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Device-Id"],
    )
    app.state.control_db = ControlDB(app.state.settings.control_db_path)
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

    def raise_rate_limited(decision: RateLimitDecision, detail: str) -> None:
        retry_after = max(1, ceil(decision.retry_after_seconds))
        raise HTTPException(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )

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

    async def require_device_auth(
        *,
        request: Request,
        x_device_id: str,
        authorization: str | None,
        rate_limit_prefix: str,
    ) -> str:
        settings: Settings = app.state.settings
        control_db: ControlDB = app.state.control_db
        source_ip = request.client.host if request.client else "0.0.0.0"

        if not is_source_ip_allowed(source_ip, settings):
            raise HTTPException(status_code=403, detail="source ip not allowed")

        rate_limit_key = f"{rate_limit_prefix}:{x_device_id}:{source_ip}"
        rate_decision = app.state.rate_limiter.consume(rate_limit_key, ingest_rate_plan(x_device_id))
        if not rate_decision.allowed:
            raise_rate_limited(rate_decision, "rate limit exceeded")

        try:
            token = extract_bearer_token(authorization)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        if not await verify_device_token(x_device_id, token, control_db):
            raise HTTPException(status_code=401, detail="invalid device token")

        return source_ip

    async def render_admin_devices_page(
        request: Request,
        *,
        flash_message: str | None,
        issued_device: DeviceRecord | None,
        page: int,
        query: str = "",
    ) -> HTMLResponse:
        settings: Settings = app.state.settings
        control_db: ControlDB = app.state.control_db
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
                "control_db_path": settings.control_db_path,
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

    @app.get("/v1/control/status", response_model=ControlStatusResponse)
    async def control_status() -> ControlStatusResponse:
        settings: Settings = app.state.settings
        return await build_control_status(settings)

    @app.get("/v1/time", response_model=TimeSyncResponse)
    async def time_sync(
        request: Request,
        response: Response,
        x_device_id: str = Header(..., alias="X-Device-Id"),
        authorization: str | None = Header(None, alias="Authorization"),
    ) -> TimeSyncResponse:
        await require_device_auth(
            request=request,
            x_device_id=x_device_id,
            authorization=authorization,
            rate_limit_prefix="time",
        )

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

    @app.get("/v1/control/devices", response_model=DeviceListResponse)
    async def control_devices(
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=50),
        q: str = Query("", max_length=100),
    ) -> DeviceListResponse:
        control_db: ControlDB = app.state.control_db
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
    async def control_issue_device(payload: DeviceIssueRequest) -> DeviceSummary:
        control_db: ControlDB = app.state.control_db
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
        content_type: str | None = Header(None, alias="Content-Type"),
    ) -> dict[str, str | int]:
        settings: Settings = app.state.settings

        if not content_type or "application/x-protobuf" not in content_type:
            raise HTTPException(status_code=415, detail="content-type must be application/x-protobuf")

        source_ip = await require_device_auth(
            request=request,
            x_device_id=x_device_id,
            authorization=authorization,
            rate_limit_prefix="ingest",
        )

        body = await request.body()
        if len(body) > settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="request body too large")

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
        control_db: ControlDB = app.state.control_db
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
        page: int = Query(1, ge=1),
        q: str = Query("", max_length=100),
    ) -> HTMLResponse:
        return await render_admin_devices_page(request, flash_message=None, issued_device=None, page=page, query=q)

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
        control_db: ControlDB = app.state.control_db
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
