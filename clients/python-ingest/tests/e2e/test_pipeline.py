from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

import httpx
import psycopg
import pytest

from aetus_ingest_client import AetusIngestClient, channel


pytestmark = pytest.mark.e2e

ROOT_DIR = Path(__file__).resolve().parents[4]
COMPOSE_FILE = ROOT_DIR / "compose" / "e2e-compose.yml"
INGEST_API_URL = "http://127.0.0.1:18000"
KAFKA_CONNECT_URL = "http://127.0.0.1:18083"
POSTGRES_DSN = "postgresql://aetus:aetus@127.0.0.1:15432/aetus"


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


def _wait_for_raw_rows(device_id: str, expected_count: int, timeout: float = 120.0) -> list[tuple]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with psycopg.connect(POSTGRES_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT device_id, boot_id, sequence, event_type, timestamp_ns, payload_json
                    FROM raw_device_events
                    WHERE device_id = %s
                    ORDER BY sequence ASC
                    """,
                    (device_id,),
                )
                rows = cur.fetchall()
                if len(rows) >= expected_count:
                    return rows
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for raw rows for {device_id}")


def _wait_for_metric_rows(device_id: str, expected_count: int, timeout: float = 120.0) -> list[tuple]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with psycopg.connect(POSTGRES_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        d.device_id,
                        b.boot_id,
                        p.sequence,
                        p.metric_index,
                        md.metric_key,
                        md.metric_unit,
                        md.value_type,
                        p.value_double,
                        p.value_int,
                        p.value_bool,
                        p.event_time_ns
                    FROM device_metric_points p
                    JOIN devices d ON d.device_pk = p.device_pk
                    JOIN device_boot_sessions b ON b.boot_pk = p.boot_pk
                    JOIN metric_definitions md ON md.metric_pk = p.metric_pk
                    WHERE d.device_id = %s
                    ORDER BY p.sequence ASC, p.metric_index ASC
                    """,
                    (device_id,),
                )
                rows = cur.fetchall()
                if len(rows) >= expected_count:
                    return rows
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for metric rows for {device_id}")


def _wait_for_signal_frame_rows(device_id: str, expected_count: int, timeout: float = 120.0) -> list[tuple]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with psycopg.connect(POSTGRES_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        d.device_id,
                        b.boot_id,
                        f.sequence,
                        sd.stream_key,
                        sd.encoding,
                        sd.layout,
                        f.sample_interval_ns,
                        f.sample_count,
                        f.samples_size,
                        f.event_time_ns,
                        sd.channels_json
                    FROM device_signal_frames f
                    JOIN devices d ON d.device_pk = f.device_pk
                    JOIN device_boot_sessions b ON b.boot_pk = f.boot_pk
                    JOIN signal_stream_definitions sd ON sd.signal_pk = f.signal_pk
                    WHERE d.device_id = %s
                    ORDER BY f.sequence ASC
                    """,
                    (device_id,),
                )
                rows = cur.fetchall()
                if len(rows) >= expected_count:
                    return rows
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for signal frame rows for {device_id}")


@pytest.fixture(scope="module")
def e2e_stack() -> None:
    if os.getenv("AETUS_SKIP_E2E") == "1":
        pytest.skip("E2E disabled by environment")

    _docker_compose("down", "-v", "--remove-orphans")
    _docker_compose("up", "-d", "--build")
    try:
        _wait_for_http(f"{INGEST_API_URL}/v1/healthz")
        _wait_for_http(f"{KAFKA_CONNECT_URL}/")
        yield
    finally:
        _docker_compose("down", "-v", "--remove-orphans")


@pytest.fixture(scope="module")
def provisioned_device(e2e_stack: None) -> dict[str, Any]:
    del e2e_stack
    response = httpx.post(
        f"{INGEST_API_URL}/v1/provision",
        json={
            "hardware_id": "esp32c5-a1b2c3d4e5f6",
            "model": "python-client",
            "firmware_version": 42,
            "site_code": "sdk-e2e",
        },
        headers={"Authorization": "Bearer bootstrap_shared_token"},
        timeout=10.0,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="module")
def uploaded_client_data(provisioned_device: dict[str, Any]) -> dict[str, Any]:
    boot_id = "boot-python-client-e2e"
    with AetusIngestClient(
        base_url=INGEST_API_URL,
        device_id=provisioned_device["device_id"],
        token=provisioned_device["access_token"],
        boot_id=boot_id,
        firmware_version=42,
    ) as client:
        metric_response = client.send_metrics(
            [
                ("temperature", 22.75, "celsius"),
                ("battery_mv", 4012, "mV"),
                ("motion_detected", True),
            ],
            timestamp_ns=1_812_345_678_000_000_000,
        )
        status_response = client.send_status(
            status="online",
            rssi=-58,
            free_heap=123456,
            reboot_reason="power_on",
            timestamp_ns=1_812_345_679_000_000_000,
        )
        signal_response = client.send_signal_frame(
            stream_key="python.imu.accel",
            sample_interval_ns=5_000_000,
            channels=[
                channel("accel_x", "g"),
                channel("accel_y", "g"),
                channel("accel_z", "g"),
            ],
            samples=[
                [0.1, 0.2, 0.3],
                [0.4, 0.5, 0.6],
                [0.7, 0.8, 0.9],
                [1.0, 1.1, 1.2],
            ],
            timestamp_ns=1_812_345_680_000_000_000,
        )

    return {
        "device_id": provisioned_device["device_id"],
        "boot_id": boot_id,
        "responses": [metric_response, status_response, signal_response],
    }


def test_python_client_uploads_are_accepted(uploaded_client_data: dict[str, Any]) -> None:
    assert [response.status_code for response in uploaded_client_data["responses"]] == [202, 202, 202]
    assert [response.sequence for response in uploaded_client_data["responses"]] == [0, 1, 2]


def test_python_client_raw_events_are_persisted(uploaded_client_data: dict[str, Any]) -> None:
    rows = _wait_for_raw_rows(uploaded_client_data["device_id"], expected_count=3)

    assert [row[2] for row in rows[:3]] == [0, 1, 2]
    assert {row[1] for row in rows[:3]} == {uploaded_client_data["boot_id"]}
    assert rows[0][3] == "telemetry"
    assert rows[1][3] == "status"
    assert rows[2][3] == "telemetry"
    assert rows[0][4] == 1_812_345_678_000_000_000
    assert rows[1][4] == 1_812_345_679_000_000_000
    assert rows[2][4] == 1_812_345_680_000_000_000

    payloads = [json.loads(row[5]) for row in rows[:3]]
    assert payloads[0]["kind"] == "metric_set"
    assert payloads[1]["reboot_reason"] == "power_on"
    assert payloads[2]["kind"] == "signal_frame"


def test_python_client_metric_rows_are_normalized(uploaded_client_data: dict[str, Any]) -> None:
    rows = _wait_for_metric_rows(uploaded_client_data["device_id"], expected_count=3)

    by_key = {row[4]: row for row in rows}
    assert by_key["temperature"][0:8] == (
        uploaded_client_data["device_id"],
        uploaded_client_data["boot_id"],
        0,
        0,
        "temperature",
        "celsius",
        "double",
        22.75,
    )
    assert by_key["battery_mv"][6] == "int"
    assert by_key["battery_mv"][8] == 4012
    assert by_key["motion_detected"][6] == "bool"
    assert by_key["motion_detected"][9] is True
    assert {row[10] for row in rows} == {1_812_345_678_000_000_000}


def test_python_client_signal_frame_row_is_normalized(uploaded_client_data: dict[str, Any]) -> None:
    rows = _wait_for_signal_frame_rows(uploaded_client_data["device_id"], expected_count=1)
    row = rows[0]
    channels = json.loads(row[10])

    assert row[0:10] == (
        uploaded_client_data["device_id"],
        uploaded_client_data["boot_id"],
        2,
        "python.imu.accel",
        "float32_le",
        "interleaved",
        5_000_000,
        4,
        48,
        1_812_345_680_000_000_000,
    )
    assert [item["key"] for item in channels] == ["accel_x", "accel_y", "accel_z"]
