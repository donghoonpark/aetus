from __future__ import annotations

from fastapi.testclient import TestClient

from aetus_ingest.app import create_app
from aetus_ingest.config import Settings
from aetus_ingest.publisher import InMemoryEventPublisher
from ..helpers.nanopb_mock_device import NanopbMockDevice


def make_client() -> tuple[TestClient, InMemoryEventPublisher]:
    settings = Settings(
        device_tokens={"esp32c5-test-001": "devtok_test_001"},
        allowed_source_cidrs=Settings.from_env().allowed_source_cidrs,
        allowlist_device_ids=set(),
    )
    publisher = InMemoryEventPublisher()
    app = create_app(settings=settings, publisher=publisher)
    return TestClient(app, client=("127.0.0.1", 50000)), publisher


def test_virtual_device_can_upload_telemetry() -> None:
    client, publisher = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    response = device.upload(client, device.build_telemetry())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["sequence"] == 0
    assert len(publisher.events) == 1
    event = publisher.events[0]
    assert event["device_id"] == "esp32c5-test-001"
    assert event["sequence"] == 0
    assert event["event_type"] == "telemetry"
    assert event["payload"]["metrics"][0]["key"] == "temperature"


def test_virtual_device_can_upload_reboot_status() -> None:
    client, publisher = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    response = device.upload(client, device.build_status(reboot_reason="power_on"))

    assert response.status_code == 202
    assert publisher.events[0]["event_type"] == "status"
    assert publisher.events[0]["payload"]["reboot_reason"] == "power_on"


def test_ingest_rejects_invalid_token() -> None:
    client, _ = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="wrong-token")

    response = device.upload(client, device.build_telemetry())

    assert response.status_code == 401
