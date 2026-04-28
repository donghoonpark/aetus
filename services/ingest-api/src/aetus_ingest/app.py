from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aetus_ingest.auth import extract_bearer_token, is_source_ip_allowed, verify_device_token
from aetus_ingest.config import Settings
from aetus_ingest.control_db import ControlDB, DeviceRecord
from aetus_ingest.generated import ingest_pb2
from aetus_ingest.normalize import normalize_event
from aetus_ingest.publisher import InMemoryEventPublisher, KafkaEventPublisher
from aetus_ingest.rate_limit import InMemoryRateLimiter, RateLimitPlan
from aetus_ingest.schemas import ProvisionRequest, ProvisionResponse


def create_app(
    settings: Settings | None = None,
    publisher: InMemoryEventPublisher | None = None,
    rate_limiter: InMemoryRateLimiter | None = None,
) -> FastAPI:
    app = FastAPI(title="AETUS Ingest Server", version="0.1.0")
    app.state.settings = settings or Settings.from_env()
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

    @app.get("/v1/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/v1/ingest", status_code=status.HTTP_202_ACCEPTED)
    async def ingest(
        request: Request,
        response: Response,
        x_device_id: str = Header(..., alias="X-Device-Id"),
        authorization: str | None = Header(None, alias="Authorization"),
        content_type: str | None = Header(None, alias="Content-Type"),
    ) -> dict[str, str | int]:
        settings: Settings = app.state.settings
        control_db: ControlDB = app.state.control_db
        source_ip = request.client.host if request.client else "0.0.0.0"

        if not content_type or "application/x-protobuf" not in content_type:
            raise HTTPException(status_code=415, detail="content-type must be application/x-protobuf")

        if not is_source_ip_allowed(source_ip, settings):
            raise HTTPException(status_code=403, detail="source ip not allowed")

        try:
            token = extract_bearer_token(authorization)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        if not await verify_device_token(x_device_id, token, control_db):
            raise HTTPException(status_code=401, detail="invalid device token")

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

        is_allowlisted = event.device_id in settings.allowlist_device_ids
        plan = RateLimitPlan(
            rate_per_second=(
                settings.allowlist_requests_per_second
                if is_allowlisted
                else settings.ingest_requests_per_second
            ),
            burst=settings.allowlist_burst if is_allowlisted else settings.ingest_burst,
        )
        rate_limit_key = f"{event.device_id}:{source_ip}"
        if not app.state.rate_limiter.allow(rate_limit_key, plan):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

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
        if not app.state.rate_limiter.allow(f"bootstrap:{source_ip}:{payload.hardware_id}", bootstrap_plan):
            raise HTTPException(status_code=429, detail="bootstrap rate limit exceeded")

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
    async def admin_devices(request: Request) -> HTMLResponse:
        settings: Settings = app.state.settings
        control_db: ControlDB = app.state.control_db
        return templates.TemplateResponse(
            request,
            "admin_devices.html",
            {
                "devices": await control_db.list_devices_readonly(),
                "flash_message": None,
                "issued_device": None,
                "control_db_path": settings.control_db_path,
                "allowed_source_cidrs": ", ".join(str(network) for network in settings.allowed_source_cidrs),
            },
        )

    @app.post("/admin/devices/issue", response_class=HTMLResponse)
    async def admin_issue_device(
        request: Request,
        hardware_id: str = Form(...),
        model: str = Form("esp32-c5"),
        firmware_version: int | None = Form(None),
        site_code: str | None = Form(None),
    ) -> HTMLResponse:
        settings: Settings = app.state.settings
        control_db: ControlDB = app.state.control_db
        issued = await control_db.issue_device_token(
            hardware_id=hardware_id,
            model=model,
            firmware_version=firmware_version,
            site_code=site_code,
        )
        return templates.TemplateResponse(
            request,
            "admin_devices.html",
            {
                "devices": await control_db.list_devices_readonly(),
                "flash_message": "Device token has been issued successfully.",
                "issued_device": issued,
                "control_db_path": settings.control_db_path,
                "allowed_source_cidrs": ", ".join(str(network) for network in settings.allowed_source_cidrs),
            },
        )

    return app
