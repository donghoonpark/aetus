from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from aetus_query.auth import (
    QueryPrincipal,
    TokenRequest,
    authenticate_query,
    enforce_device_access,
    enforce_query_limits,
    enforce_stream_access,
    issue_query_token,
    verify_admin_token,
)
from aetus_query.cache import Cache, NullCache, RedisJsonCache
from aetus_query.config import Settings
from aetus_query.repository import PostgresQueryRepository, QueryRepository, StreamRef
from aetus_query.time_utils import parse_datetime, to_iso8601


def create_app(
    settings: Settings | None = None,
    repository: QueryRepository | None = None,
    cache: Cache | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        repo = _app.state.repository
        if isinstance(repo, PostgresQueryRepository):
            repo._pool.close()

    app = FastAPI(title="AETUS Query API", version="0.1.0", lifespan=_lifespan)
    app.state.settings = resolved_settings
    app.state.repository = repository or PostgresQueryRepository(resolved_settings.postgres_dsn)
    app.state.cache = cache or (RedisJsonCache(resolved_settings.redis_url) if resolved_settings.redis_url else NullCache())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Aetus-Admin-Token"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=resolved_settings.compression_minimum_size)

    @app.get("/v1/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/v1/auth/token")
    def create_query_token(
        request: TokenRequest,
        x_aetus_admin_token: str | None = Header(default=None),
    ) -> dict:
        verify_admin_token(resolved_settings, x_aetus_admin_token)
        return issue_query_token(resolved_settings, request)

    @app.get("/v1/query/devices")
    def search_devices(
        search: str = Query(default="", max_length=128),
        limit: int = Query(default=20, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ) -> dict:
        principal = _authenticate(resolved_settings, authorization, "streams:list")
        query = search.strip()
        if "*" in principal.devices:
            device_ids = app.state.repository.search_devices(query, limit)
        else:
            needle = query.lower()
            device_ids = [
                device_id
                for device_id in sorted(principal.devices)
                if not needle or needle in device_id.lower()
            ][:limit]
        return {"devices": [{"device_id": device_id} for device_id in device_ids]}

    @app.get("/v1/query/devices/{device_id}/streams")
    def list_streams(device_id: str, authorization: str | None = Header(default=None)) -> dict:
        principal = _authenticate(resolved_settings, authorization, "streams:list")
        enforce_device_access(principal, device_id)
        streams = app.state.repository.list_streams(device_id)
        visible_streams = [stream for stream in streams if principal.can_read_stream(stream.key)]
        return {"device_id": device_id, "streams": [_stream_to_json(stream) for stream in visible_streams]}

    @app.get("/v1/query/devices/{device_id}/streams/{key}/series")
    def get_series(
        device_id: str,
        key: str,
        from_: str = Query(..., alias="from"),
        to: str = Query(...),
        max_points: int = Query(default=resolved_settings.max_points_default, ge=1),
        authorization: str | None = Header(default=None),
    ) -> dict:
        principal = _authenticate(resolved_settings, authorization, "query:read")
        enforce_device_access(principal, device_id)
        enforce_stream_access(principal, key)
        if max_points > resolved_settings.max_points_limit:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_points exceeds server limit")
        start, end = _parse_range(from_, to)
        enforce_query_limits(principal, range_seconds=(end - start).total_seconds(), max_points=max_points)
        cache_key = _cache_key("series", device_id, key, start, end, max_points)
        cached = app.state.cache.get_json(cache_key)
        if cached is not None:
            return cached

        stream = _find_stream(app.state.repository, device_id, key)
        if stream.kind == "scalar":
            response = app.state.repository.scalar_series(device_id, key, start, end, max_points)
        else:
            response = app.state.repository.sampled_series(device_id, key, start, end, max_points)
        app.state.cache.set_json(cache_key, response, resolved_settings.cache_ttl_seconds)
        return response

    @app.get("/v1/query/devices/{device_id}/streams/{key}/summary")
    def get_summary(
        device_id: str,
        key: str,
        from_: str = Query(..., alias="from"),
        to: str = Query(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        principal = _authenticate(resolved_settings, authorization, "query:read")
        enforce_device_access(principal, device_id)
        enforce_stream_access(principal, key)
        start, end = _parse_range(from_, to)
        enforce_query_limits(principal, range_seconds=(end - start).total_seconds())
        cache_key = _cache_key("summary", device_id, key, start, end, 0)
        cached = app.state.cache.get_json(cache_key)
        if cached is not None:
            return cached

        stream = _find_stream(app.state.repository, device_id, key)
        if stream.kind == "scalar":
            response = app.state.repository.scalar_series(device_id, key, start, end, resolved_settings.max_points_default)
            response["from"] = to_iso8601(start)
            response["to"] = to_iso8601(end)
        else:
            response = app.state.repository.summary(
                device_id,
                key,
                start,
                end,
                feature_ttl_seconds=resolved_settings.feature_ttl_seconds,
            )
        app.state.cache.set_json(cache_key, response, resolved_settings.cache_ttl_seconds)
        return response

    @app.get("/v1/query/devices/{device_id}/streams/{key}/frames")
    def get_frames(
        device_id: str,
        key: str,
        from_: str = Query(..., alias="from"),
        to: str = Query(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        principal = _authenticate(resolved_settings, authorization, "frames:read")
        enforce_device_access(principal, device_id)
        enforce_stream_access(principal, key)
        start, end = _parse_range(from_, to)
        enforce_query_limits(principal, range_seconds=(end - start).total_seconds())
        if (end - start).total_seconds() > resolved_settings.max_raw_drilldown_seconds:
            raise HTTPException(status_code=400, detail="raw drill-down window too large")
        stream = _find_stream(app.state.repository, device_id, key)
        if stream.kind != "sampled":
            raise HTTPException(status_code=404, detail="raw frames are only available for sampled streams")
        return app.state.repository.frames(device_id, key, start, end)

    return app


def _authenticate(settings: Settings, authorization: str | None, required_scope: str) -> QueryPrincipal:
    return authenticate_query(settings, authorization, required_scope=required_scope)


def _parse_range(from_: str, to: str) -> tuple[datetime, datetime]:
    start = parse_datetime(from_)
    end = parse_datetime(to)
    if end <= start:
        raise HTTPException(status_code=400, detail="to must be after from")
    return start, end


def _find_stream(repository: QueryRepository, device_id: str, key: str) -> StreamRef:
    for stream in repository.list_streams(device_id):
        if stream.key == key:
            return stream
    raise HTTPException(status_code=404, detail="stream not found")


def _stream_to_json(stream: StreamRef) -> dict:
    body = {
        "key": stream.key,
        "kind": stream.kind,
        "unit": stream.unit,
        "latest_event_time": to_iso8601(stream.latest_event_time),
    }
    if stream.kind == "scalar":
        body["value_type"] = stream.value_type
    if stream.kind == "sampled":
        body.update(
            {
                "encoding": stream.encoding,
                "layout": stream.layout,
                "channels": stream.channels,
                "nominal_rate_hz": stream.nominal_rate_hz,
            }
        )
    return body


def _cache_key(kind: str, device_id: str, key: str, start: datetime, end: datetime, max_points: int) -> str:
    return ":".join(
        [
            "aetus-query",
            kind,
            quote(device_id, safe=""),
            quote(key, safe=""),
            to_iso8601(start),
            to_iso8601(end),
            str(max_points),
        ]
    )
