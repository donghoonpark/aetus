from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Header, HTTPException, status
from pydantic import BaseModel, Field

from aetus_query.config import Settings


class TokenRequest(BaseModel):
    subject: str = Field(default="operator", min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=lambda: ["query:read", "streams:list", "frames:read"])
    devices: list[str] = Field(default_factory=lambda: ["*"])
    device_groups: list[str] = Field(default_factory=list)
    streams: list[str] = Field(default_factory=lambda: ["*"])
    expires_in_seconds: int | None = Field(default=None, ge=60)
    max_range_seconds: int | None = Field(default=None, ge=1)
    max_points: int | None = Field(default=None, ge=1)


@dataclass(frozen=True, slots=True)
class QueryPrincipal:
    subject: str
    scopes: frozenset[str]
    devices: frozenset[str]
    device_groups: frozenset[str]
    streams: frozenset[str]
    max_range_seconds: int | None
    max_points: int | None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes

    def can_read_device(self, device_id: str) -> bool:
        return "*" in self.devices or device_id in self.devices

    def can_read_stream(self, stream_key: str) -> bool:
        return "*" in self.streams or stream_key in self.streams


def issue_query_token(settings: Settings, request: TokenRequest) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires_in = request.expires_in_seconds or settings.query_jwt_ttl_seconds
    expires_in = min(expires_in, settings.query_jwt_max_ttl_seconds)
    expires_at = now + timedelta(seconds=expires_in)
    max_points = request.max_points if request.max_points is not None else settings.max_points_limit
    payload = {
        "iss": settings.query_jwt_issuer,
        "aud": settings.query_jwt_audience,
        "sub": request.subject,
        "scope": sorted(set(request.scopes)),
        "devices": sorted(set(request.devices)),
        "device_groups": sorted(set(request.device_groups)),
        "streams": sorted(set(request.streams)),
        "max_range_seconds": request.max_range_seconds,
        "max_points": min(max_points, settings.max_points_limit),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.query_jwt_secret, algorithm="HS256")
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "scope": payload["scope"],
    }


def verify_admin_token(settings: Settings, x_aetus_admin_token: str | None = Header(default=None)) -> None:
    if not settings.query_auth_enabled:
        return
    if not x_aetus_admin_token or x_aetus_admin_token != settings.query_admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")


def authenticate_query(
    settings: Settings,
    authorization: str | None,
    *,
    required_scope: str,
) -> QueryPrincipal:
    if not settings.query_auth_enabled:
        return QueryPrincipal(
            subject="auth-disabled",
            scopes=frozenset({"*"}),
            devices=frozenset({"*"}),
            device_groups=frozenset({"*"}),
            streams=frozenset({"*"}),
            max_range_seconds=None,
            max_points=settings.max_points_limit,
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(
            token,
            settings.query_jwt_secret,
            algorithms=["HS256"],
            audience=settings.query_jwt_audience,
            issuer=settings.query_jwt_issuer,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token") from exc

    principal = _principal_from_claims(claims)
    if not principal.has_scope(required_scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")
    return principal


def enforce_device_access(principal: QueryPrincipal, device_id: str) -> None:
    if not principal.can_read_device(device_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="device not allowed")


def enforce_stream_access(principal: QueryPrincipal, stream_key: str) -> None:
    if not principal.can_read_stream(stream_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="stream not allowed")


def enforce_query_limits(
    principal: QueryPrincipal,
    *,
    range_seconds: float,
    max_points: int | None = None,
) -> None:
    if principal.max_range_seconds is not None and range_seconds > principal.max_range_seconds:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="time range exceeds token limit")
    if max_points is not None and principal.max_points is not None and max_points > principal.max_points:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="max_points exceeds token limit")


def _principal_from_claims(claims: dict[str, Any]) -> QueryPrincipal:
    scopes = _claim_list(claims, "scope")
    devices = _claim_list(claims, "devices")
    streams = _claim_list(claims, "streams")
    return QueryPrincipal(
        subject=str(claims.get("sub") or ""),
        scopes=frozenset(scopes),
        devices=frozenset(devices),
        device_groups=frozenset(_claim_list(claims, "device_groups")),
        streams=frozenset(streams),
        max_range_seconds=_optional_int(claims.get("max_range_seconds")),
        max_points=_optional_int(claims.get("max_points")),
    )


def _claim_list(claims: dict[str, Any], key: str) -> list[str]:
    value = claims.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
