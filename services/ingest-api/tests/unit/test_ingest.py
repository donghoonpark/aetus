from __future__ import annotations

import ipaddress
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from aetus_ingest.app import create_app
from aetus_ingest.config import Settings
from aetus_ingest.generated import ingest_pb2
from aetus_ingest.publisher import InMemoryEventPublisher
from aetus_ingest.rate_limit import InMemoryRateLimiter
from ..helpers.nanopb_mock_device import NanopbMockDevice


class FakeClock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_client(
    *,
    client_host: str = "127.0.0.1",
    rate_limiter: InMemoryRateLimiter | None = None,
    **settings_overrides,
) -> tuple[TestClient, InMemoryEventPublisher]:
    tmpdir = tempfile.TemporaryDirectory()
    control_db_path = str(Path(tmpdir.name) / "control.db")
    defaults = {
        "device_tokens": {"esp32c5-test-001": "devtok_test_001"},
        "allowed_source_cidrs": Settings.from_env().allowed_source_cidrs,
        "allowlist_device_ids": set(),
        "allowed_hardware_ids": {"esp32c5-a1b2c3d4e5f6"},
        "bootstrap_token": "bootstrap_shared_token",
        "kafka_bootstrap_servers": "127.0.0.1:65530",
        "kafka_connect_url": "http://127.0.0.1:65531",
        "postgres_dsn": "postgresql://aetus:aetus@127.0.0.1:65532/aetus",
        "status_timeout_seconds": 0.2,
        "control_db_path": control_db_path,
    }
    defaults.update(settings_overrides)
    settings = Settings(**defaults)
    publisher = InMemoryEventPublisher()
    app = create_app(settings=settings, publisher=publisher, rate_limiter=rate_limiter)
    client = TestClient(app, client=(client_host, 50000))
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
    assert event["timestamp_ns"] is None


def test_virtual_device_preserves_timestamp_ns() -> None:
    client, publisher = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    response = device.upload(client, device.build_telemetry(timestamp_ns=1_712_345_678_901_234_567))

    assert response.status_code == 202
    assert publisher.events[0]["timestamp_ns"] == 1_712_345_678_901_234_567


def test_time_sync_returns_authenticated_server_time() -> None:
    client, _ = make_client()

    response = client.get(
        "/v1/time",
        headers={
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer devtok_test_001",
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["source"] == "ingest-api"
    assert body["valid_after_unix_s"] == 1_577_836_800
    assert body["unix_time_s"] >= body["valid_after_unix_s"]
    assert body["unix_time_ms"] // 1000 == body["unix_time_s"]
    assert body["unix_time_ns"].isdigit()
    assert int(body["unix_time_ns"]) // 1_000_000_000 == body["unix_time_s"]


def test_time_sync_rejects_invalid_device_token() -> None:
    client, _ = make_client()

    response = client.get(
        "/v1/time",
        headers={
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer wrong-token",
        },
    )

    assert response.status_code == 401


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


def test_ingest_rate_limit_blocks_repeated_uploads_with_retry_after() -> None:
    clock = FakeClock()
    client, publisher = make_client(
        rate_limiter=InMemoryRateLimiter(clock=clock.monotonic),
        ingest_requests_per_second=1.0,
        ingest_burst=1,
    )
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    first_response = device.upload(client, device.build_telemetry())
    second_response = device.upload(client, device.build_telemetry())
    clock.advance(1.0)
    third_response = device.upload(client, device.build_telemetry())

    assert first_response.status_code == 202
    assert second_response.status_code == 429
    assert second_response.headers["retry-after"] == "1"
    assert third_response.status_code == 202
    assert len(publisher.events) == 2


def test_ingest_allowlist_uses_relaxed_rate_limit() -> None:
    clock = FakeClock()
    client, publisher = make_client(
        rate_limiter=InMemoryRateLimiter(clock=clock.monotonic),
        allowlist_device_ids={"esp32c5-test-001"},
        ingest_requests_per_second=1.0,
        ingest_burst=1,
        allowlist_requests_per_second=1.0,
        allowlist_burst=2,
    )
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    first_response = device.upload(client, device.build_telemetry())
    second_response = device.upload(client, device.build_telemetry())
    third_response = device.upload(client, device.build_telemetry())

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    assert third_response.status_code == 429
    assert len(publisher.events) == 2


def test_ingest_rate_limit_runs_before_token_verification() -> None:
    clock = FakeClock()
    client, _ = make_client(
        rate_limiter=InMemoryRateLimiter(clock=clock.monotonic),
        ingest_requests_per_second=1.0,
        ingest_burst=1,
    )
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="wrong-token")

    first_response = device.upload(client, device.build_telemetry())
    second_response = device.upload(client, device.build_telemetry())

    assert first_response.status_code == 401
    assert second_response.status_code == 429


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


def test_provisioning_rate_limit_blocks_hardware_id_rotation() -> None:
    clock = FakeClock()
    client, _ = make_client(
        rate_limiter=InMemoryRateLimiter(clock=clock.monotonic),
        allowed_hardware_ids={"esp32c5-a1b2c3d4e5f6", "esp32c5-b1b2c3d4e5f6"},
        bootstrap_requests_per_window=1,
        bootstrap_window_seconds=10.0,
    )
    headers = {"Authorization": "Bearer bootstrap_shared_token"}

    first_response = client.post(
        "/v1/provision",
        json={
            "hardware_id": "esp32c5-a1b2c3d4e5f6",
            "model": "esp32-c5",
            "firmware_version": 1002003,
        },
        headers=headers,
    )
    second_response = client.post(
        "/v1/provision",
        json={
            "hardware_id": "esp32c5-b1b2c3d4e5f6",
            "model": "esp32-c5",
            "firmware_version": 1002003,
        },
        headers=headers,
    )
    clock.advance(10.0)
    third_response = client.post(
        "/v1/provision",
        json={
            "hardware_id": "esp32c5-b1b2c3d4e5f6",
            "model": "esp32-c5",
            "firmware_version": 1002003,
        },
        headers=headers,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 429
    assert second_response.headers["retry-after"] == "10"
    assert third_response.status_code == 201


def test_provisioning_rejects_invalid_bootstrap_token() -> None:
    client, _ = make_client()

    response = client.post(
        "/v1/provision",
        json={
            "hardware_id": "esp32c5-a1b2c3d4e5f6",
            "model": "esp32-c5",
            "firmware_version": 1002003,
        },
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401


def test_provisioning_rejects_non_allowlisted_hardware_id() -> None:
    client, _ = make_client(allowed_hardware_ids={"esp32c5-a1b2c3d4e5f6"})

    response = client.post(
        "/v1/provision",
        json={
            "hardware_id": "esp32c5-not-allowed",
            "model": "esp32-c5",
            "firmware_version": 1002003,
        },
        headers={"Authorization": "Bearer bootstrap_shared_token"},
    )

    assert response.status_code == 403


def test_source_ip_cidr_blocks_ingest_and_provisioning() -> None:
    client, _ = make_client(
        client_host="10.0.0.5",
        allowed_source_cidrs=(ipaddress.ip_network("192.168.0.0/16"),),
    )
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    ingest_response = device.upload(client, device.build_telemetry())
    provision_response = client.post(
        "/v1/provision",
        json={
            "hardware_id": "esp32c5-a1b2c3d4e5f6",
            "model": "esp32-c5",
            "firmware_version": 1002003,
        },
        headers={"Authorization": "Bearer bootstrap_shared_token"},
    )

    assert ingest_response.status_code == 403
    assert provision_response.status_code == 403


def test_ingest_rejects_non_protobuf_content_type() -> None:
    client, _ = make_client()

    response = client.post(
        "/v1/ingest",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer devtok_test_001",
        },
    )

    assert response.status_code == 415


def test_ingest_rejects_oversized_body() -> None:
    client, _ = make_client(max_body_bytes=1)

    response = client.post(
        "/v1/ingest",
        content=b"\x00\x00",
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer devtok_test_001",
        },
    )

    assert response.status_code == 413


def test_ingest_rejects_invalid_protobuf() -> None:
    client, _ = make_client()

    response = client.post(
        "/v1/ingest",
        content=b"\xff",
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer devtok_test_001",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid protobuf"


def test_ingest_rejects_device_id_mismatch() -> None:
    client, _ = make_client()
    other_device = NanopbMockDevice(device_id="esp32c5-other-001", token="unused")

    response = client.post(
        "/v1/ingest",
        content=other_device.build_telemetry(),
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer devtok_test_001",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "device id mismatch"


def test_ingest_rejects_missing_boot_id() -> None:
    client, _ = make_client()
    event = ingest_pb2.IngestEvent(
        schema_version=1,
        device_id="esp32c5-test-001",
        sequence=0,
        event_type=ingest_pb2.EVENT_TYPE_TELEMETRY,
    )
    metric = event.telemetry.metrics.add()
    metric.key = "temperature"
    metric.double_value = 24.5

    response = client.post(
        "/v1/ingest",
        content=event.SerializeToString(),
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer devtok_test_001",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "boot_id required"


def test_ingest_rejects_missing_body() -> None:
    client, _ = make_client()
    event = ingest_pb2.IngestEvent(
        schema_version=1,
        device_id="esp32c5-test-001",
        boot_id="boot-unit-0001",
        sequence=0,
        event_type=ingest_pb2.EVENT_TYPE_TELEMETRY,
    )

    response = client.post(
        "/v1/ingest",
        content=event.SerializeToString(),
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer devtok_test_001",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "body missing"


def test_control_devices_json_endpoints_work() -> None:
    client, _ = make_client()

    issue_response = client.post(
        "/v1/control/devices/issue",
        json={
            "hardware_id": "esp32c5-aaaaaaaaaaaa",
            "model": "esp32-c5",
            "firmware_version": 1002003,
            "site_code": "control-lab",
        },
    )
    assert issue_response.status_code == 201
    issued = issue_response.json()
    assert issued["device_id"].startswith("esp32c5-")

    list_response = client.get("/v1/control/devices?q=control")
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["total_items"] >= 1
    assert any(item["site_code"] == "control-lab" for item in body["items"])


def test_control_status_endpoint_returns_component_states() -> None:
    client, _ = make_client()

    response = client.get("/v1/control/status")

    assert response.status_code == 200
    body = response.json()
    components = {item["name"]: item for item in body["components"]}
    assert components["api"]["state"] == "healthy"
    assert components["control_db"]["state"] == "healthy"
    assert components["kafka"]["state"] == "down"
    assert components["kafka_connect"]["state"] == "down"
    assert components["postgres"]["state"] == "down"


def test_control_api_allows_vue_dev_origin() -> None:
    client, _ = make_client()

    response = client.options(
        "/v1/control/status",
        headers={
            "Origin": "http://127.0.0.1:4173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:4173"


def test_ingest_accepts_out_of_order_sequence_values() -> None:
    client, publisher = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")
    device.sequence = 2
    response_one = device.upload(client, device.build_telemetry())
    device.sequence = 0
    response_two = device.upload(client, device.build_telemetry())

    assert response_one.status_code == 202
    assert response_two.status_code == 202
    assert [event["sequence"] for event in publisher.events] == [2, 0]


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


def test_admin_page_supports_search_and_copy_controls() -> None:
    client, _ = make_client()
    client.post(
        "/admin/devices/issue",
        data={
            "hardware_id": "esp32c5-bbbbbbbbbbbb",
            "model": "esp32-c5",
            "firmware_version": "1002003",
            "site_code": "alpha-lab",
            "page": "1",
        },
    )
    client.post(
        "/admin/devices/issue",
        data={
            "hardware_id": "esp32c5-cccccccccccc",
            "model": "esp32-c5",
            "firmware_version": "1002003",
            "site_code": "beta-lab",
            "page": "1",
        },
    )

    response = client.get("/admin/devices?q=alpha")

    assert response.status_code == 200
    assert "alpha-lab" in response.text
    assert "beta-lab" not in response.text
    assert "data-copy-token" in response.text
