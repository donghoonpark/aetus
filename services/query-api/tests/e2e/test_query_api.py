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
QUERY_ADMIN_TOKEN = "e2e-query-admin-token"


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
            metric_pks: dict[str, int] = {}
            for metric_key, metric_unit, value_type in [
                ("temperature", "celsius", "double"),
                ("env.humidity", "percent", "float"),
                ("motor.rpm", "rpm", "int"),
                ("pump.enabled", "unitless", "bool"),
                ("machine.state", "unitless", "string"),
            ]:
                cur.execute(
                    """
                    INSERT INTO metric_definitions(metric_key, metric_unit, value_type)
                    VALUES (%s, %s, %s)
                    RETURNING metric_pk
                    """,
                    (metric_key, metric_unit, value_type),
                )
                metric_pks[metric_key] = cur.fetchone()[0]
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
                (event_time, event_time, device_pk, boot_pk, metric_pks["temperature"]),
            )
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
                VALUES (%s, %s, %s, %s, %s, 0, 4, 1, 48.5, 'query-metric-float-1')
                """,
                (event_time, event_time, device_pk, boot_pk, metric_pks["env.humidity"]),
            )
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
                    value_int,
                    request_id
                )
                VALUES (%s, %s, %s, %s, %s, 0, 1, 1, 1725, 'query-metric-int-1')
                """,
                (event_time, event_time, device_pk, boot_pk, metric_pks["motor.rpm"]),
            )
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
                    value_bool,
                    request_id
                )
                VALUES (%s, %s, %s, %s, %s, 0, 2, 1, true, 'query-metric-bool-1')
                """,
                (event_time, event_time, device_pk, boot_pk, metric_pks["pump.enabled"]),
            )
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
                    value_string,
                    request_id
                )
                VALUES (%s, %s, %s, %s, %s, 0, 3, 1, 'warming', 'query-metric-string-1')
                """,
                (event_time, event_time, device_pk, boot_pk, metric_pks["machine.state"]),
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


def _query_auth_headers(
    *,
    devices: list[str] | None = None,
    streams: list[str] | None = None,
    scopes: list[str] | None = None,
) -> dict[str, str]:
    response = httpx.post(
        f"{QUERY_API_URL}/v1/auth/token",
        headers={"X-Aetus-Admin-Token": QUERY_ADMIN_TOKEN},
        json={
            "subject": "query-e2e",
            "devices": devices or ["query-device-1"],
            "streams": streams or ["*"],
            "scopes": scopes or ["query:read", "streams:list", "frames:read"],
        },
        timeout=10.0,
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_query_api_lists_scalar_and_sampled_streams(query_stack: None) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams",
        headers=_query_auth_headers(),
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    streams = {item["key"]: item for item in response.json()["streams"]}
    assert streams["temperature"]["kind"] == "scalar"
    assert streams["temperature"]["value_type"] == "double"
    assert streams["env.humidity"]["value_type"] == "float"
    assert streams["motor.rpm"]["value_type"] == "int"
    assert streams["pump.enabled"]["value_type"] == "bool"
    assert streams["machine.state"]["value_type"] == "string"
    assert streams["imu.accel"]["kind"] == "sampled"
    assert streams["imu.accel"]["nominal_rate_hz"] == 200.0


def test_query_api_returns_scalar_series(query_stack: None) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams/temperature/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        headers=_query_auth_headers(),
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "scalar"
    assert response.json()["value_type"] == "double"
    assert response.json()["points"] == [{"ts": "2026-05-03T00:00:00Z", "value": 23.75}]


@pytest.mark.parametrize(
    ("stream_key", "expected_value_type", "expected_points"),
    [
        ("motor.rpm", "int", [{"ts": "2026-05-03T00:00:00Z", "value": 1725}]),
        ("env.humidity", "float", [{"ts": "2026-05-03T00:00:00Z", "value": 48.5}]),
        ("pump.enabled", "bool", [{"ts": "2026-05-03T00:00:00Z", "value": 1.0, "text": "true"}]),
        ("machine.state", "string", [{"ts": "2026-05-03T00:00:00Z", "text": "warming"}]),
    ],
)
def test_query_api_returns_scalar_series_by_value_type(
    query_stack: None,
    stream_key: str,
    expected_value_type: str,
    expected_points: list[dict[str, object]],
) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams/{stream_key}/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        headers=_query_auth_headers(),
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "scalar"
    assert response.json()["value_type"] == expected_value_type
    assert response.json()["points"] == expected_points


def test_query_api_returns_sampled_series_from_raw_frame(query_stack: None) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams/imu.accel/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        headers=_query_auth_headers(),
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "sampled"
    assert body["mode"] == "samples"
    channels = {channel["name"]: channel for channel in body["channels"]}
    assert [point["value"] for point in channels["accel_x"]["points"]] == pytest.approx([0.10, 0.11, 0.12, 0.13])


def test_query_api_materializes_summary_features(query_stack: None) -> None:
    del query_stack

    response = httpx.get(
        f"{QUERY_API_URL}/v1/query/devices/query-device-1/streams/imu.accel/summary",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        headers=_query_auth_headers(),
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
        headers=_query_auth_headers(),
        timeout=10.0,
    )

    assert response.status_code == 200, response.text
    frame = response.json()["frames"][0]
    assert frame["sample_count"] == 4
    channels = {channel["name"]: channel for channel in frame["channels"]}
    assert channels["accel_y"]["values"] == pytest.approx([0.20, 0.21, 0.22, 0.23])
