from __future__ import annotations

import time

import httpx
import psycopg
import pytest

from ..helpers.nanopb_mock_device import NanopbMockDevice
from .test_postgres_pipeline import (
    INGEST_API_URL,
    KAFKA_CONNECT_URL,
    POSTGRES_DSN,
    _docker_compose,
    _wait_for_http,
    _wait_for_rows,
)


pytestmark = pytest.mark.e2e
HMAC_DEVICE_ID = "esp32c5-test-001"
HMAC_DEVICE_TOKEN = "devtok_test_001"


def _wait_for_connector_configs(timeout: float = 120.0) -> None:
    expected = {
        "metric-ingest-staging-sink",
        "raw-device-events-sink",
        "signal-frame-ingest-staging-sink",
    }
    deadline = time.time() + timeout
    last_seen: set[str] = set()
    while time.time() < deadline:
        try:
            response = httpx.get(f"{KAFKA_CONNECT_URL}/connectors", timeout=5.0)
            if response.status_code == 200:
                last_seen = set(response.json())
                if expected.issubset(last_seen):
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for Kafka connector configs; last_seen={sorted(last_seen)}")


def _count_raw_rows(device_id: str, boot_id: str) -> int:
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM raw_device_events
                WHERE device_id = %s
                  AND boot_id = %s
                """,
                (device_id, boot_id),
            )
            return int(cur.fetchone()[0])


def _start_e2e_stack() -> None:
    _docker_compose("down", "-v", "--remove-orphans")
    _docker_compose("up", "-d", "--build")
    _wait_for_http(f"{INGEST_API_URL}/v1/healthz")
    _wait_for_http(f"{KAFKA_CONNECT_URL}/")
    _wait_for_connector_configs()


@pytest.fixture()
def isolated_e2e_stack() -> None:
    _start_e2e_stack()
    try:
        yield
    finally:
        _docker_compose("down", "-v", "--remove-orphans")


@pytest.fixture()
def hmac_required_e2e_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETUS_HMAC_AUTH_REQUIRED", "true")
    _start_e2e_stack()
    try:
        yield
    finally:
        _docker_compose("down", "-v", "--remove-orphans")


def test_kafka_connect_outage_buffers_ingest_and_recovers_to_postgres(isolated_e2e_stack: None) -> None:
    del isolated_e2e_stack
    device = NanopbMockDevice(
        device_id="esp32c5-test-001",
        token="devtok_test_001",
        boot_id="boot-fault-connect-0001",
    )

    _docker_compose("stop", "kafka-connect")
    try:
        status_response = httpx.get(f"{INGEST_API_URL}/v1/control/status", timeout=10.0)
        components = {item["name"]: item for item in status_response.json()["components"]}
        assert components["kafka"]["state"] == "healthy"
        assert components["kafka_connect"]["state"] == "down"

        ingest_response = httpx.post(
            f"{INGEST_API_URL}/v1/ingest",
            content=device.build_telemetry(timestamp_ns=1_712_345_680_000_000_000),
            headers={
                "Content-Type": "application/x-protobuf",
                "X-Device-Id": device.device_id,
                "Authorization": f"Bearer {device.token}",
            },
            timeout=10.0,
        )
        assert ingest_response.status_code == 202, ingest_response.text

        time.sleep(5)
        assert _count_raw_rows(device.device_id, device.boot_id) == 0
    finally:
        _docker_compose("start", "kafka-connect")

    _wait_for_http(f"{KAFKA_CONNECT_URL}/")
    rows = _wait_for_rows(device.device_id, expected_count=1)
    assert any(row[1] == device.boot_id and row[2] == 0 for row in rows)


def test_hmac_required_rejects_bearer_upload_and_persists_signed_upload(
    hmac_required_e2e_stack: None,
) -> None:
    del hmac_required_e2e_stack
    device = NanopbMockDevice(
        device_id=HMAC_DEVICE_ID,
        token=HMAC_DEVICE_TOKEN,
        boot_id="boot-fault-hmac-required-0001",
    )
    payload = device.build_telemetry(timestamp_ns=1_712_345_681_000_000_000)

    bearer_response = httpx.post(
        f"{INGEST_API_URL}/v1/ingest",
        content=payload,
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": device.device_id,
            "Authorization": f"Bearer {device.token}",
        },
        timeout=10.0,
    )
    assert bearer_response.status_code == 401
    assert bearer_response.json()["detail"] == "hmac authentication required"
    assert _count_raw_rows(device.device_id, device.boot_id) == 0

    hmac_response = httpx.post(
        f"{INGEST_API_URL}/v1/ingest",
        content=payload,
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": device.device_id,
            "X-Aetus-Signature": device.hmac_signature(payload),
        },
        timeout=10.0,
    )
    assert hmac_response.status_code == 202, hmac_response.text

    rows = _wait_for_rows(device.device_id, expected_count=1)
    assert any(row[1] == device.boot_id and row[2] == 0 for row in rows)
