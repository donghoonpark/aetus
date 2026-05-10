from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import json
import ipaddress
from pathlib import Path
import sqlite3
import struct
import tempfile

from fastapi.testclient import TestClient
import pytest

from aetus_ingest.app import create_app
from aetus_ingest.config import Settings
from aetus_ingest.control_backup import backup_sqlite_database
from aetus_ingest.control_db import PostgresControlStore, SqliteControlStore, create_control_store
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
        "control_db_backend": "sqlite",
        "control_db_path": control_db_path,
        "control_db_backup_enabled": False,
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
    assert event["payload"]["kind"] == "metric_set"
    assert event["payload"]["metrics"][0]["key"] == "temperature"
    assert event["timestamp_ns"] is None
    assert len(publisher.metric_records) == 1
    metric_payload = publisher.metric_records[0]["payload"]
    assert metric_payload["metric_index"] == 0
    assert metric_payload["metric_key"] == "temperature"
    assert metric_payload["metric_unit"] == "celsius"
    assert metric_payload["value_type"] == "double"
    assert metric_payload["value_double"] == 22.25


def test_metrics_endpoints_track_http_and_ingest_counters() -> None:
    client, _ = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    upload_response = device.upload(client, device.build_telemetry())
    json_response = client.get("/v1/metrics")
    prometheus_response = client.get("/metrics")

    assert upload_response.status_code == 202
    assert json_response.status_code == 200
    body = json_response.json()
    counters = {
        (item["name"], tuple(sorted(item["labels"].items()))): item["value"]
        for item in body["counters"]
    }
    assert counters[
        (
            "aetus_ingest_events_accepted_total",
            (("event_type", "telemetry"), ("payload_kind", "metric_set")),
        )
    ] == 1
    assert counters[
        (
            "aetus_http_requests_total",
            (("method", "POST"), ("path", "/v1/ingest"), ("status_code", "202")),
        )
    ] == 1
    assert prometheus_response.status_code == 200
    assert "aetus_ingest_events_accepted_total" in prometheus_response.text
    assert 'path="/v1/ingest"' in prometheus_response.text


def test_virtual_device_can_upload_signal_frame() -> None:
    client, publisher = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    response = device.upload(client, device.build_signal_frame(timestamp_ns=1_712_345_678_901_234_567))

    assert response.status_code == 202
    event = publisher.events[0]
    assert event["device_id"] == "esp32c5-test-001"
    assert event["event_type"] == "telemetry"
    assert event["timestamp_ns"] == 1_712_345_678_901_234_567
    assert event["payload"]["kind"] == "signal_frame"

    signal_frame = event["payload"]["signal_frame"]
    assert signal_frame["stream_key"] == "imu.accel"
    assert signal_frame["sample_interval_ns"] == 5_000_000
    assert signal_frame["sample_count"] == 4
    assert signal_frame["encoding"] == "float32_le"
    assert signal_frame["layout"] == "interleaved"
    assert [channel["key"] for channel in signal_frame["channels"]] == ["accel_x", "accel_y", "accel_z"]
    assert len(base64.b64decode(signal_frame["samples_b64"])) == 48

    assert publisher.metric_records == []
    assert len(publisher.signal_frame_records) == 1
    signal_payload = publisher.signal_frame_records[0]["payload"]
    assert signal_payload["stream_key"] == "imu.accel"
    assert signal_payload["sample_count"] == 4
    assert json.loads(signal_payload["channels_json"])[0]["key"] == "accel_x"
    assert len(base64.b64decode(signal_payload["samples_b64"])) == 48


def test_virtual_device_can_upload_telemetry_with_hmac_signature() -> None:
    client, publisher = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")
    payload = device.build_telemetry()

    response = device.upload_hmac(client, payload)

    assert response.status_code == 202
    assert publisher.events[0]["device_id"] == "esp32c5-test-001"
    assert publisher.events[0]["sequence"] == 0


def test_ingest_rejects_invalid_hmac_signature() -> None:
    client, _ = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    response = device.upload_hmac(client, device.build_telemetry(), signature="hmac-sha256-v1=" + "0" * 64)

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid hmac signature"


def test_ingest_rejects_hmac_signature_for_modified_body() -> None:
    client, _ = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")
    signed_payload = device.build_telemetry()
    modified_payload = bytearray(signed_payload)
    modified_payload[-1] ^= 0x01

    response = device.upload_hmac(client, bytes(modified_payload), signature=device.hmac_signature(signed_payload))

    assert response.status_code == 401


def test_ingest_rejects_unknown_hmac_scheme() -> None:
    client, _ = make_client()
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")

    response = device.upload_hmac(client, device.build_telemetry(), signature="hmac-sha256-v2=" + "0" * 64)

    assert response.status_code == 401


def test_ingest_can_disable_hmac_authentication() -> None:
    client, publisher = make_client(hmac_auth_enabled=False)
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")
    payload = device.build_telemetry()

    hmac_response = device.upload_hmac(client, payload)
    bearer_response = device.upload(client, payload)

    assert hmac_response.status_code == 401
    assert hmac_response.json()["detail"] == "hmac authentication disabled"
    assert bearer_response.status_code == 202
    assert len(publisher.events) == 1


def test_ingest_can_require_hmac_authentication() -> None:
    client, publisher = make_client(hmac_auth_required=True)
    device = NanopbMockDevice(device_id="esp32c5-test-001", token="devtok_test_001")
    payload = device.build_telemetry()

    bearer_response = device.upload(client, payload)
    hmac_response = device.upload_hmac(client, payload)

    assert bearer_response.status_code == 401
    assert bearer_response.json()["detail"] == "hmac authentication required"
    assert hmac_response.status_code == 202
    assert len(publisher.events) == 1


def test_hmac_required_rejects_disabled_hmac_configuration() -> None:
    try:
        make_client(hmac_auth_enabled=False, hmac_auth_required=True)
    except ValueError as exc:
        assert "hmac_auth_required requires hmac_auth_enabled" in str(exc)
    else:
        raise AssertionError("expected invalid hmac policy to fail")


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
    assert publisher.metric_records == []


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


def test_control_store_factory_uses_sqlite_backend() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(control_db_backend="sqlite", control_db_path=str(Path(tmpdir) / "control.db"))

        store = create_control_store(settings)

        assert isinstance(store, SqliteControlStore)


def test_control_store_factory_uses_postgres_backend_without_connecting() -> None:
    settings = Settings(
        control_db_backend="postgres",
        control_database_url="postgresql://control:secret@db.internal:5432/aetus",
        control_db_schema="control_plane",
    )

    store = create_control_store(settings)

    assert isinstance(store, PostgresControlStore)
    assert store.schema == "control_plane"
    assert store.dsn == "postgresql://control:secret@db.internal:5432/aetus"


def test_control_store_factory_rejects_invalid_postgres_schema() -> None:
    settings = Settings(
        control_db_backend="postgres",
        control_database_url="postgresql://control:secret@db.internal:5432/aetus",
        control_db_schema="control-plane",
    )

    try:
        create_control_store(settings)
    except ValueError as exc:
        assert "invalid SQL identifier" in str(exc)
    else:
        raise AssertionError("expected invalid control DB schema to fail")


def test_sqlite_control_db_backup_creates_consistent_dump() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "control.db"
        backup_dir = Path(tmpdir) / "backups"
        store = SqliteControlStore(str(db_path))
        store.initialize()
        store.seed_hardware_allowlist({"esp32c5-a1b2c3d4e5f6"})
        issued = asyncio.run(
            store.issue_device_token(
                "esp32c5-a1b2c3d4e5f6",
                model="esp32-c5",
                firmware_version=1002003,
                site_code="factory-a",
            )
        )

        backup_path = backup_sqlite_database(
            db_path,
            backup_dir,
            retention_count=2,
            timestamp=datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
        )

        assert backup_path.exists()
        with sqlite3.connect(backup_path) as conn:
            row = conn.execute(
                "SELECT device_id, token FROM devices WHERE hardware_id = ?",
                ("esp32c5-a1b2c3d4e5f6",),
            ).fetchone()
        assert row == (issued.device_id, issued.token)


def test_sqlite_control_db_backup_prunes_old_files() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "control.db"
        backup_dir = Path(tmpdir) / "backups"
        store = SqliteControlStore(str(db_path))
        store.initialize()

        for minute in range(3):
            backup_sqlite_database(
                db_path,
                backup_dir,
                retention_count=2,
                timestamp=datetime(2026, 5, 5, 0, minute, tzinfo=timezone.utc),
            )

        backups = sorted(path.name for path in backup_dir.glob("control-*.db"))
        assert backups == ["control-20260505T000100Z.db", "control-20260505T000200Z.db"]


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
    metric = event.telemetry.metric_set.metrics.add()
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


def test_ingest_rejects_missing_telemetry_payload_kind() -> None:
    client, _ = make_client()
    event = ingest_pb2.IngestEvent(
        schema_version=1,
        device_id="esp32c5-test-001",
        boot_id="boot-unit-0001",
        sequence=0,
        event_type=ingest_pb2.EVENT_TYPE_TELEMETRY,
    )
    event.telemetry.SetInParent()

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
    assert response.json()["detail"] == "telemetry payload missing"


def test_ingest_rejects_signal_frame_sample_length_mismatch() -> None:
    client, _ = make_client()
    event = ingest_pb2.IngestEvent(
        schema_version=1,
        device_id="esp32c5-test-001",
        boot_id="boot-unit-0001",
        sequence=0,
        event_type=ingest_pb2.EVENT_TYPE_TELEMETRY,
        timestamp_ns=1_712_345_678_901_234_567,
    )
    frame = event.telemetry.signal_frame
    frame.stream_key = "imu.accel"
    frame.sample_interval_ns = 5_000_000
    frame.sample_count = 4
    frame.encoding = ingest_pb2.SIGNAL_SAMPLE_ENCODING_FLOAT32_LE
    frame.layout = ingest_pb2.SIGNAL_SAMPLE_LAYOUT_INTERLEAVED
    for key in ("accel_x", "accel_y", "accel_z"):
        channel = frame.channels.add()
        channel.key = key
        channel.unit = "g"
    frame.samples = b"\x00" * 47

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
    assert response.json()["detail"] == "signal frame samples length mismatch: expected 48, got 47"


@pytest.mark.parametrize(
    ("encoding_name", "encoding", "layout", "samples", "expected_size"),
    [
        (
            "int16_le",
            ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT16_LE,
            ingest_pb2.SIGNAL_SAMPLE_LAYOUT_INTERLEAVED,
            struct.pack("<hhhh", -10, 100, -20, 200),
            8,
        ),
        (
            "uint16_le",
            ingest_pb2.SIGNAL_SAMPLE_ENCODING_UINT16_LE,
            ingest_pb2.SIGNAL_SAMPLE_LAYOUT_PLANAR,
            struct.pack("<HHHH", 1000, 2000, 3000, 4000),
            8,
        ),
        (
            "int32_le",
            ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT32_LE,
            ingest_pb2.SIGNAL_SAMPLE_LAYOUT_INTERLEAVED,
            struct.pack("<iiii", -100_000, 100_000, -100_100, 100_100),
            16,
        ),
    ],
)
def test_ingest_accepts_supported_integer_signal_frame_encodings(
    encoding_name: str,
    encoding: int,
    layout: int,
    samples: bytes,
    expected_size: int,
) -> None:
    client, publisher = make_client()
    event = ingest_pb2.IngestEvent(
        schema_version=1,
        device_id="esp32c5-test-001",
        boot_id="boot-unit-0001",
        sequence=0,
        event_type=ingest_pb2.EVENT_TYPE_TELEMETRY,
        timestamp_ns=1_712_345_678_901_234_567,
    )
    frame = event.telemetry.signal_frame
    frame.stream_key = f"test.{encoding_name}"
    frame.sample_interval_ns = 5_000_000
    frame.sample_count = 2
    frame.encoding = encoding
    frame.layout = layout
    for key in ("ch0", "ch1"):
        channel = frame.channels.add()
        channel.key = key
        channel.unit = "count"
    frame.samples = samples

    response = client.post(
        "/v1/ingest",
        content=event.SerializeToString(),
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": "esp32c5-test-001",
            "Authorization": "Bearer devtok_test_001",
        },
    )

    assert response.status_code == 202
    assert len(publisher.signal_frame_records) == 1
    payload = publisher.signal_frame_records[0]["payload"]
    assert payload["stream_key"] == f"test.{encoding_name}"
    assert payload["encoding"] == encoding_name
    assert payload["layout"] == ("planar" if layout == ingest_pb2.SIGNAL_SAMPLE_LAYOUT_PLANAR else "interleaved")
    assert payload["sample_count"] == 2
    assert base64.b64decode(payload["samples_b64"]) == samples
    assert len(samples) == expected_size


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
