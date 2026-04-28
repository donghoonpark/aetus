from __future__ import annotations

from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from aetus_ingest.app import create_app
from aetus_ingest.config import Settings
from aetus_ingest.publisher import InMemoryEventPublisher
from ..helpers.nanopb_mock_device import NanopbMockDevice


def make_client() -> tuple[TestClient, InMemoryEventPublisher]:
    tmpdir = tempfile.TemporaryDirectory()
    control_db_path = str(Path(tmpdir.name) / "control.db")
    settings = Settings(
        device_tokens={"esp32c5-test-001": "devtok_test_001"},
        allowed_source_cidrs=Settings.from_env().allowed_source_cidrs,
        allowlist_device_ids=set(),
        allowed_hardware_ids={"esp32c5-a1b2c3d4e5f6"},
        bootstrap_token="bootstrap_shared_token",
        control_db_path=control_db_path,
    )
    publisher = InMemoryEventPublisher()
    app = create_app(settings=settings, publisher=publisher)
    client = TestClient(app, client=("127.0.0.1", 50000))
    client._tmpdir = tmpdir  # type: ignore[attr-defined]
    return client, publisher


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


def test_provisioning_issues_token_and_ingest_reads_from_sqlite() -> None:
    client, publisher = make_client()
    provision_response = client.post(
        "/v1/provision",
        json={
            "hardware_id": "esp32c5-a1b2c3d4e5f6",
            "model": "esp32-c5",
            "firmware_version": 1002003,
            "site_code": "factory-a",
        },
        headers={"Authorization": "Bearer bootstrap_shared_token"},
    )
    assert provision_response.status_code == 201
    provision_body = provision_response.json()
    issued_token = provision_body["access_token"]
    device_id = provision_body["device_id"]

    device = NanopbMockDevice(device_id=device_id, token=issued_token)
    upload_response = device.upload(client, device.build_telemetry())

    assert upload_response.status_code == 202
    assert publisher.events[-1]["device_id"] == device_id


def test_admin_page_renders_bootstrap_and_fontawesome() -> None:
    client, _ = make_client()

    response = client.get("/admin/devices")

    assert response.status_code == 200
    assert "aetus flight control" in response.text.lower()
    assert "font-awesome" in response.text.lower()
    assert "page 1 / 1" in response.text.lower()


def test_admin_page_supports_pagination() -> None:
    client, _ = make_client()

    for index in range(11):
        response = client.post(
            "/admin/devices/issue",
            data={
                "hardware_id": f"esp32c5-a1b2c3d4e{index:02d}",
                "model": "esp32-c5",
                "firmware_version": "1002003",
                "site_code": f"factory-{index}",
                "page": "1",
            },
        )
        assert response.status_code == 200

    page_one = client.get("/admin/devices?page=1")
    page_two = client.get("/admin/devices?page=2")

    assert page_one.status_code == 200
    assert page_two.status_code == 200
    assert "page 1 / 2" in page_one.text.lower()
    assert "page 2 / 2" in page_two.text.lower()
    assert "esp32c5-012" in page_one.text
    assert "esp32c5-002" not in page_one.text
    assert "esp32c5-002" in page_two.text
