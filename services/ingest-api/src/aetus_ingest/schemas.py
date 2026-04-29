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


class TimeSyncResponse(BaseModel):
    unix_time_s: int
    unix_time_ms: int
    unix_time_ns: str
    iso8601: str
    source: str
    valid_after_unix_s: int


class DeviceIssueRequest(BaseModel):
    hardware_id: str
    model: str = "esp32-c5"
    firmware_version: int | None = None
    site_code: str | None = None


class DeviceSummary(BaseModel):
    device_id: str
    hardware_id: str
    token: str
    model: str | None = None
    firmware_version: int | None = None
    site_code: str | None = None
    created_at: str
    updated_at: str


class DeviceListResponse(BaseModel):
    items: list[DeviceSummary]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    query: str = ""


class ComponentStatus(BaseModel):
    name: str
    state: str
    detail: str


class ControlStatusResponse(BaseModel):
    checked_at: str
    components: list[ComponentStatus]
