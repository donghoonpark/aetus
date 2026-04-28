from __future__ import annotations

from pydantic import BaseModel


class ProvisionRequest(BaseModel):
    hardware_id: str
    model: str
    firmware_version: int | None = None
    site_code: str | None = None


class ProvisionResponse(BaseModel):
    device_id: str
    token_type: str
    access_token: str
    config: dict[str, str | int]
