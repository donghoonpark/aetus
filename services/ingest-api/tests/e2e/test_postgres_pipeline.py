from __future__ import annotations

import os
import subprocess
import time
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


def _wait_for_row(timeout: float = 120.0) -> tuple[str, str, int, str]:
    deadline = time.time() + timeout
    dsn = "postgresql://aetus:aetus@127.0.0.1:15432/aetus"
    while time.time() < deadline:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT device_id, boot_id, sequence, event_type
                    FROM raw_device_events
                    WHERE device_id = 'esp32c5-test-001'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if row is not None:
                    return row
        time.sleep(2)
    raise RuntimeError("Timed out waiting for PostgreSQL row")


@pytest.mark.e2e
def test_ingest_to_postgres_pipeline() -> None:
    if os.getenv("AETUS_SKIP_E2E") == "1":
        pytest.skip("E2E disabled by environment")

    _docker_compose("up", "-d", "--build")
    try:
        _wait_for_http("http://127.0.0.1:18000/v1/healthz")
        _wait_for_http("http://127.0.0.1:18083/")

        device = NanopbMockDevice(
            device_id="esp32c5-test-001",
            token="devtok_test_001",
            boot_id="boot-e2e-0001",
        )
        payload = device.build_telemetry()

        response = httpx.post(
            "http://127.0.0.1:18000/v1/ingest",
            content=payload,
            headers={
                "Content-Type": "application/x-protobuf",
                "X-Device-Id": "esp32c5-test-001",
                "Authorization": "Bearer devtok_test_001",
            },
            timeout=10.0,
        )
        assert response.status_code == 202, response.text

        row = _wait_for_row()
        assert row == ("esp32c5-test-001", "boot-e2e-0001", 0, "telemetry")
    finally:
        _docker_compose("down", "-v")
