from __future__ import annotations

import os
import ipaddress
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx
import psycopg
import pytest

from ..helpers.nanopb_mock_device import NanopbMockDevice


ROOT_DIR = Path(__file__).resolve().parents[4]
COMPOSE_FILE = ROOT_DIR / "compose" / "e2e-compose.yml"


def _docker_compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _wait_for_http(url: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code == 200:
                return
        except Exception as exc:  # pragma: no cover - retry loop
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def _wait_for_rows(device_id: str, expected_count: int, timeout: float = 120.0) -> list[tuple]:
    deadline = time.time() + timeout
    dsn = "postgresql://aetus:aetus@127.0.0.1:15432/aetus"
    while time.time() < deadline:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        device_id,
                        boot_id,
                        sequence,
                        event_type,
                        timestamp_ns,
                        received_at,
                        source_ip,
                        payload_json
                    FROM raw_device_events
                    WHERE device_id = %s
                    ORDER BY sequence DESC, created_at DESC
                    """,
                    (device_id,),
                )
                rows = cur.fetchall()
                if len(rows) >= expected_count:
                    return rows
        time.sleep(2)
    raise RuntimeError("Timed out waiting for PostgreSQL rows")


@pytest.mark.e2e
def test_ingest_to_postgres_pipeline() -> None:
    if os.getenv("AETUS_SKIP_E2E") == "1":
        pytest.skip("E2E disabled by environment")

    _docker_compose("up", "-d", "--build")
    try:
        _wait_for_http("http://127.0.0.1:18000/v1/healthz")
        _wait_for_http("http://127.0.0.1:18083/")

        provision_response = httpx.post(
            "http://127.0.0.1:18000/v1/provision",
            json={
                "hardware_id": "esp32c5-a1b2c3d4e5f6",
                "model": "esp32-c5",
                "firmware_version": 1002003,
                "site_code": "factory-a",
            },
            headers={"Authorization": "Bearer bootstrap_shared_token"},
            timeout=10.0,
        )
        assert provision_response.status_code == 201, provision_response.text
        provision_body = provision_response.json()

        provision_rate_limited_response = httpx.post(
            "http://127.0.0.1:18000/v1/provision",
            json={
                "hardware_id": "esp32c5-a1b2c3d4e5f6",
                "model": "esp32-c5",
                "firmware_version": 1002003,
                "site_code": "factory-a",
            },
            headers={"Authorization": "Bearer bootstrap_shared_token"},
            timeout=10.0,
        )
        assert provision_rate_limited_response.status_code == 429, provision_rate_limited_response.text
        assert provision_rate_limited_response.headers["retry-after"] == "10"

        control_devices_response = httpx.get(
            "http://127.0.0.1:18000/v1/control/devices?q=factory-a",
            timeout=10.0,
        )
        assert control_devices_response.status_code == 200, control_devices_response.text
        control_devices = control_devices_response.json()
        assert any(item["device_id"] == provision_body["device_id"] for item in control_devices["items"])

        control_status_response = httpx.get(
            "http://127.0.0.1:18000/v1/control/status",
            timeout=10.0,
        )
        assert control_status_response.status_code == 200, control_status_response.text
        control_status = {item["name"]: item for item in control_status_response.json()["components"]}
        assert control_status["api"]["state"] == "healthy"
        assert control_status["control_db"]["state"] == "healthy"
        assert control_status["kafka"]["state"] == "healthy"
        assert control_status["kafka_connect"]["state"] == "healthy"
        assert control_status["postgres"]["state"] == "healthy"

        device = NanopbMockDevice(
            device_id=provision_body["device_id"],
            token=provision_body["access_token"],
            boot_id="boot-e2e-0001",
        )
        headers = {
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": provision_body["device_id"],
            "Authorization": f"Bearer {provision_body['access_token']}",
        }

        response_first = httpx.post(
            "http://127.0.0.1:18000/v1/ingest",
            content=device.build_telemetry(timestamp_ns=1_712_345_678_901_234_567),
            headers=headers,
            timeout=10.0,
        )
        assert response_first.status_code == 202, response_first.text

        device.sequence = 2
        response_second = httpx.post(
            "http://127.0.0.1:18000/v1/ingest",
            content=device.build_telemetry(timestamp_ns=1_712_345_678_901_234_890),
            headers=headers,
            timeout=10.0,
        )
        assert response_second.status_code == 202, response_second.text

        device.sequence = 1
        response_third = httpx.post(
            "http://127.0.0.1:18000/v1/ingest",
            content=device.build_telemetry(timestamp_ns=1_712_345_678_901_234_891),
            headers=headers,
            timeout=10.0,
        )
        assert response_third.status_code == 202, response_third.text

        rows = _wait_for_rows(provision_body["device_id"], expected_count=3)
        assert rows[0][0:4] == (provision_body["device_id"], "boot-e2e-0001", 2, "telemetry")
        assert rows[1][0:4] == (provision_body["device_id"], "boot-e2e-0001", 1, "telemetry")
        assert rows[2][0:4] == (provision_body["device_id"], "boot-e2e-0001", 0, "telemetry")
        assert rows[0][4] == 1_712_345_678_901_234_890
        assert rows[1][4] == 1_712_345_678_901_234_891
        assert rows[2][4] == 1_712_345_678_901_234_567
        ipaddress.ip_address(rows[0][6])
        assert '"metrics"' in rows[0][7]
        parsed_received_at = datetime.fromisoformat(rows[2][5])
        assert parsed_received_at.tzinfo is not None
        assert parsed_received_at.year >= 2026
    finally:
        _docker_compose("down", "-v")
