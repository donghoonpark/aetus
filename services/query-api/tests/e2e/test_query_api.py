from __future__ import annotations

import os
import struct
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
import pytest


pytestmark = pytest.mark.e2e

ROOT_DIR = Path(__file__).resolve().parents[4]
COMPOSE_FILE = ROOT_DIR / "compose" / "e2e-compose.yml"
QUERY_API_URL = "http://127.0.0.1:18001"
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


@pytest.fixture(scope="module")
def query_stack() -> None:
    if os.getenv("AETUS_SKIP_E2E") == "1":
        pytest.skip("E2E disabled by environment")

    _docker_compose("down", "-v", "--remove-orphans")
    _docker_compose("up", "-d", "--build", "postgres", "redis", "query-api")
    try:
        _wait_for_http(f"{QUERY_API_URL}/v1/healthz")
        _seed_query_data()
        yield
    finally:
        _docker_compose("down", "-v")


def _seed_query_data() -> None:
    samples = struct.pack(
        "<ffffffffffff",
        0.10,
        0.20,
        0.30,
        0.11,
        0.21,
        0.31,
        0.12,
        0.22,
        0.32,
        0.13,
        0.23,
        0.33,
    )
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO devices(device_id) VALUES (%s) RETURNING device_pk", ("query-device-1",))
            device_pk = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO device_boot_sessions(device_pk, boot_id) VALUES (%s, %s) RETURNING boot_pk",
                (device_pk, "boot-query-1"),
            )
            boot_pk = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO metric_definitions(metric_key, metric_unit, value_type)
                VALUES ('temperature', 'celsius', 'double')
                RETURNING metric_pk
                """
            )
            metric_pk = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO signal_stream_definitions(stream_key, encoding, layout, channels_json)
                VALUES (
                    'imu.accel',
                    'float32_le',
                    'interleaved',
                    '[{"key":"accel_x","unit":"g"},{"key":"accel_y","unit":"g"},{"key":"accel_z","unit":"g"}]'
                )
                RETURNING signal_pk
                """
            )
            signal_pk = cur.fetchone()[0]
            event_time = datetime(2026, 5, 3, 0, 0, 0, tzinfo=timezone.utc)
            cur.execute(
                """
                INSERT INTO device_metric_points(
                    event_time,
                    received_at,
                    device_pk,
                    boot_pk,
                    metric_pk,
                    sequence,
                    metric_index,
                    schema_version,
                    value_double,
                    request_id
                )
                VALUES (%s, %s, %s, %s, %s, 0, 0, 1, 23.75, 'query-metric-1')
                """,
                (event_time, event_time, device_pk, boot_pk, metric_pk),
            )
            cur.execute(
                """
                INSERT INTO device_signal_frames(
                    event_time,
                    received_at,
                    device_pk,
                    boot_pk,
                    signal_pk,
                    sequence,
                    schema_version,
                    sample_interval_ns,
                    sample_count,
                    samples,
                    samples_size,
                    request_id
                )
                VALUES (%s, %s, %s, %s, %s, 1, 1, 5000000, 4, %s, %s, 'query-frame-1')
                """,
                (event_time, event_time, device_pk, boot_pk, signal_pk, psycopg.Binary(samples), len(samples)),
            )
        conn.commit()


def test_query_api_lists_scalar_and_sampled_streams(query_stack: None) -> None:
    del query_stack

    response = httpx.get(f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams", timeout=10.0)

    assert response.status_code == 200, response.text
    streams = {item["key"]: item for item in response.json()["streams"]}
    assert streams["temperature"]["kind"] == "scalar"
    assert streams["imu.accel"]["kind"] == "sampled"
    assert streams["imu.accel"]["nominal_rate_hz"] == 200.0


def test_query_api_returns_scalar_series(query_stack: None) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams/temperature/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "scalar"
    assert response.json()["points"] == [{"ts": "2026-05-03T00:00:00Z", "value": 23.75}]


def test_query_api_returns_sampled_series_from_raw_frame(query_stack: None) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams/imu.accel/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "sampled"
    assert body["mode"] == "envelope"
    channels = {channel["name"]: channel for channel in body["channels"]}
    assert channels["accel_x"]["points"][0]["min"] == pytest.approx(0.10)
    assert channels["accel_x"]["points"][0]["max"] == pytest.approx(0.13)


def test_query_api_materializes_summary_features(query_stack: None) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams/imu.accel/summary",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    features = response.json()["features"]
    assert features["accel_x"]["sample_count"] == 4
    assert features["accel_x"]["min"] == pytest.approx(0.10)
    assert features["accel_x"]["max"] == pytest.approx(0.13)

    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM signal_frame_features")
            assert cur.fetchone()[0] == 3


def test_query_api_returns_decoded_raw_frames(query_stack: None) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams/imu.accel/frames",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:00:10Z"},
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    frame = response.json()["frames"][0]
    assert frame["sample_count"] == 4
    channels = {channel["name"]: channel for channel in frame["channels"]}
    assert channels["accel_y"]["values"] == pytest.approx([0.20, 0.21, 0.22, 0.23])
