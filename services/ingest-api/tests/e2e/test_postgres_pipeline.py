from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from aetus_ingest.control_db import PostgresControlStore
from ..helpers.nanopb_mock_device import NanopbMockDevice


pytestmark = pytest.mark.e2e

ROOT_DIR = Path(__file__).resolve().parents[4]
COMPOSE_FILE = ROOT_DIR / "compose" / "e2e-compose.yml"
INGEST_API_URL = "http://127.0.0.1:18000"
KAFKA_CONNECT_URL = "http://127.0.0.1:18083"
POSTGRES_DSN = "postgresql://aetus:aetus@127.0.0.1:15432/aetus"


@dataclass(slots=True)
class ProvisioningResult:
    response: httpx.Response
    rate_limited_response: httpx.Response
    body: dict[str, Any]


@dataclass(slots=True)
class IngestResult:
    responses: list[httpx.Response]
    rows: list[tuple]
    metric_rows: list[tuple]
    provisioned_device: dict[str, Any]


@dataclass(slots=True)
class SignalFrameResult:
    response: httpx.Response
    raw_rows: list[tuple]
    signal_frame_rows: list[tuple]
    device_id: str


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
    while time.time() < deadline:
        with psycopg.connect(POSTGRES_DSN) as conn:
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
                        p.event_time_ns,
                        p.event_time,
                        p.received_at
                    FROM device_metric_points p
                    JOIN devices d ON d.device_pk = p.device_pk
                    JOIN device_boot_sessions b ON b.boot_pk = p.boot_pk
                    JOIN metric_definitions md ON md.metric_pk = p.metric_pk
                    WHERE d.device_id = %s
                    ORDER BY p.sequence DESC, p.metric_index ASC
                    """,
                    (device_id,),
                )
                rows = cur.fetchall()
                if len(rows) >= expected_count:
                    return rows
        time.sleep(2)
    raise RuntimeError("Timed out waiting for PostgreSQL metric rows")


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
                        f.event_time,
                        f.received_at,
                        sd.channels_json
                    FROM device_signal_frames f
                    JOIN devices d ON d.device_pk = f.device_pk
                    JOIN device_boot_sessions b ON b.boot_pk = f.boot_pk
                    JOIN signal_stream_definitions sd ON sd.signal_pk = f.signal_pk
                    WHERE d.device_id = %s
                    ORDER BY f.sequence DESC, f.created_at DESC
                    """,
                    (device_id,),
                )
                rows = cur.fetchall()
                if len(rows) >= expected_count:
                    return rows
        time.sleep(2)
    raise RuntimeError("Timed out waiting for PostgreSQL signal frame rows")


@pytest.fixture(scope="module")
def e2e_stack() -> None:
    if os.getenv("AETUS_SKIP_E2E") == "1":
        pytest.skip("E2E disabled by environment")

    # Keep E2E deterministic after interrupted local runs.
    _docker_compose("down", "-v", "--remove-orphans")
    _docker_compose("up", "-d", "--build")
    try:
        _wait_for_http(f"{INGEST_API_URL}/v1/healthz")
        _wait_for_http(f"{KAFKA_CONNECT_URL}/")
        yield
    finally:
        _docker_compose("down", "-v")


@pytest.fixture(scope="module")
def provisioning_result(e2e_stack: None) -> ProvisioningResult:
    del e2e_stack
    provision_response = httpx.post(
        f"{INGEST_API_URL}/v1/provision",
        json={
            "hardware_id": "esp32c5-a1b2c3d4e5f6",
            "model": "esp32-c5",
            "firmware_version": 1002003,
            "site_code": "factory-a",
        },
        headers={"Authorization": "Bearer bootstrap_shared_token"},
        timeout=10.0,
    )
    body = provision_response.json() if provision_response.status_code == 201 else {}

    rate_limited_response = httpx.post(
        f"{INGEST_API_URL}/v1/provision",
        json={
            "hardware_id": "esp32c5-a1b2c3d4e5f6",
            "model": "esp32-c5",
            "firmware_version": 1002003,
            "site_code": "factory-a",
        },
        headers={"Authorization": "Bearer bootstrap_shared_token"},
        timeout=10.0,
    )

    return ProvisioningResult(
        response=provision_response,
        rate_limited_response=rate_limited_response,
        body=body,
    )


@pytest.fixture(scope="module")
def ingest_result(provisioning_result: ProvisioningResult) -> IngestResult:
    provision_body = provisioning_result.body
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
        f"{INGEST_API_URL}/v1/ingest",
        content=device.build_telemetry(timestamp_ns=1_712_345_678_901_234_567),
        headers=headers,
        timeout=10.0,
    )

    device.sequence = 2
    response_second = httpx.post(
        f"{INGEST_API_URL}/v1/ingest",
        content=device.build_telemetry(timestamp_ns=1_712_345_678_901_234_890),
        headers=headers,
        timeout=10.0,
    )

    device.sequence = 1
    response_third = httpx.post(
        f"{INGEST_API_URL}/v1/ingest",
        content=device.build_telemetry(timestamp_ns=1_712_345_678_901_234_891),
        headers=headers,
        timeout=10.0,
    )

    rows = _wait_for_rows(provision_body["device_id"], expected_count=3)
    metric_rows = _wait_for_metric_rows(provision_body["device_id"], expected_count=3)
    return IngestResult(
        responses=[response_first, response_second, response_third],
        rows=rows,
        metric_rows=metric_rows,
        provisioned_device=provision_body,
    )


@pytest.fixture(scope="module")
def signal_frame_result(e2e_stack: None) -> SignalFrameResult:
    del e2e_stack
    device = NanopbMockDevice(
        device_id="esp32c5-test-001",
        token="devtok_test_001",
        boot_id="boot-e2e-signal-0001",
    )
    headers = {
        "Content-Type": "application/x-protobuf",
        "X-Device-Id": device.device_id,
        "Authorization": f"Bearer {device.token}",
    }

    response = httpx.post(
        f"{INGEST_API_URL}/v1/ingest",
        content=device.build_signal_frame(timestamp_ns=1_712_345_679_111_000_000),
        headers=headers,
        timeout=10.0,
    )

    raw_rows = _wait_for_rows(device.device_id, expected_count=1)
    signal_frame_rows = _wait_for_signal_frame_rows(device.device_id, expected_count=1)
    return SignalFrameResult(
        response=response,
        raw_rows=raw_rows,
        signal_frame_rows=signal_frame_rows,
        device_id=device.device_id,
    )


def test_stack_readiness_endpoints_are_available(e2e_stack: None) -> None:
    del e2e_stack

    health_response = httpx.get(f"{INGEST_API_URL}/v1/healthz", timeout=10.0)
    connect_response = httpx.get(f"{KAFKA_CONNECT_URL}/", timeout=10.0)

    assert health_response.status_code == 200
    assert connect_response.status_code == 200


def test_provisioning_issues_device_token(provisioning_result: ProvisioningResult) -> None:
    assert provisioning_result.response.status_code == 201, provisioning_result.response.text
    assert provisioning_result.body["device_id"].startswith("esp32c5-")
    assert provisioning_result.body["token_type"] == "Bearer"
    assert provisioning_result.body["access_token"].startswith("devtok_")


def test_bootstrap_rate_limit_returns_retry_after(provisioning_result: ProvisioningResult) -> None:
    assert provisioning_result.rate_limited_response.status_code == 429
    assert provisioning_result.rate_limited_response.headers["retry-after"] == "10"


def test_control_devices_lists_issued_device(provisioning_result: ProvisioningResult) -> None:
    control_devices_response = httpx.get(
        f"{INGEST_API_URL}/v1/control/devices?q=factory-a",
        timeout=10.0,
    )
    assert control_devices_response.status_code == 200, control_devices_response.text
    control_devices = control_devices_response.json()
    assert any(item["device_id"] == provisioning_result.body["device_id"] for item in control_devices["items"])


def test_control_status_reports_all_dependencies_healthy(e2e_stack: None) -> None:
    del e2e_stack

    control_status_response = httpx.get(
        f"{INGEST_API_URL}/v1/control/status",
        timeout=10.0,
    )
    assert control_status_response.status_code == 200, control_status_response.text
    control_status = {item["name"]: item for item in control_status_response.json()["components"]}
    assert control_status["api"]["state"] == "healthy"
    assert control_status["control_db"]["state"] == "healthy"
    assert control_status["kafka"]["state"] == "healthy"
    assert control_status["kafka_connect"]["state"] == "healthy"
    assert control_status["postgres"]["state"] == "healthy"


def test_postgres_control_store_supports_device_provisioning(e2e_stack: None) -> None:
    del e2e_stack
    store = PostgresControlStore(POSTGRES_DSN, schema="control_e2e", connect_timeout_seconds=2.0)
    store.initialize()
    store.seed_hardware_allowlist({"esp32c5-control-e2e"})
    store.seed_devices({"seeded-control-device": "devtok_seeded_control"})

    seeded_token = asyncio.run(store.get_device_token_readonly("seeded-control-device"))
    is_allowed = asyncio.run(store.is_hardware_allowed_readonly("esp32c5-control-e2e"))
    issued = asyncio.run(
        store.issue_device_token(
            "esp32c5-control-e2e",
            model="esp32-c5",
            firmware_version=1002003,
            site_code="control-e2e",
        )
    )
    listed = asyncio.run(store.list_devices_readonly(query="control-e2e"))

    assert seeded_token == "devtok_seeded_control"
    assert is_allowed is True
    assert issued.device_id == "esp32c5-001"
    assert issued.token.startswith("devtok_")
    assert any(record.hardware_id == "esp32c5-control-e2e" for record in listed)


def test_nanopb_ingest_accepts_provisioned_device_token(ingest_result: IngestResult) -> None:
    assert [response.status_code for response in ingest_result.responses] == [202, 202, 202]


def test_kafka_connect_persists_ingest_events_to_postgres(ingest_result: IngestResult) -> None:
    assert len(ingest_result.rows) >= 3
    assert {row[0] for row in ingest_result.rows[:3]} == {ingest_result.provisioned_device["device_id"]}


def test_out_of_order_sequences_are_persisted(ingest_result: IngestResult) -> None:
    rows = ingest_result.rows

    assert rows[0][0:4] == (ingest_result.provisioned_device["device_id"], "boot-e2e-0001", 2, "telemetry")
    assert rows[1][0:4] == (ingest_result.provisioned_device["device_id"], "boot-e2e-0001", 1, "telemetry")
    assert rows[2][0:4] == (ingest_result.provisioned_device["device_id"], "boot-e2e-0001", 0, "telemetry")


def test_timestamp_ns_is_persisted(ingest_result: IngestResult) -> None:
    rows = ingest_result.rows

    assert rows[0][4] == 1_712_345_678_901_234_890
    assert rows[1][4] == 1_712_345_678_901_234_891
    assert rows[2][4] == 1_712_345_678_901_234_567


def test_payload_and_metadata_are_persisted(ingest_result: IngestResult) -> None:
    latest_row = ingest_result.rows[0]
    oldest_row = ingest_result.rows[2]
    payload = json.loads(latest_row[7])

    ipaddress.ip_address(latest_row[6])
    assert payload["kind"] == "metric_set"
    assert payload["metrics"][0]["key"] == "temperature"
    parsed_received_at = datetime.fromisoformat(oldest_row[5])
    assert parsed_received_at.tzinfo is not None
    assert parsed_received_at.year >= 2026


def test_metric_points_are_persisted_in_normalized_tables(ingest_result: IngestResult) -> None:
    assert len(ingest_result.metric_rows) >= 3
    latest_metric = ingest_result.metric_rows[0]

    assert latest_metric[0] == ingest_result.provisioned_device["device_id"]
    assert latest_metric[1] == "boot-e2e-0001"
    assert latest_metric[2:8] == (2, 0, "temperature", "celsius", "double", 22.25)


def test_metric_points_use_device_timestamp_when_available(ingest_result: IngestResult) -> None:
    metric_by_sequence = {row[2]: row for row in ingest_result.metric_rows}
    sequence_zero = metric_by_sequence[0]

    assert sequence_zero[8] == 1_712_345_678_901_234_567
    assert abs(int(sequence_zero[9].timestamp() * 1_000_000_000) - sequence_zero[8]) < 1_000
    assert sequence_zero[9] < sequence_zero[10]


def test_nanopb_signal_frame_upload_is_accepted(signal_frame_result: SignalFrameResult) -> None:
    assert signal_frame_result.response.status_code == 202, signal_frame_result.response.text


def test_signal_frame_raw_payload_is_persisted(signal_frame_result: SignalFrameResult) -> None:
    raw_row = signal_frame_result.raw_rows[0]
    payload = json.loads(raw_row[7])

    assert raw_row[0:4] == (signal_frame_result.device_id, "boot-e2e-signal-0001", 0, "telemetry")
    assert payload["kind"] == "signal_frame"
    assert payload["signal_frame"]["stream_key"] == "imu.accel"
    assert payload["signal_frame"]["sample_count"] == 4
    assert "samples_b64" in payload["signal_frame"]


def test_signal_frame_is_persisted_in_normalized_table(signal_frame_result: SignalFrameResult) -> None:
    signal_frame = signal_frame_result.signal_frame_rows[0]
    channels = json.loads(signal_frame[12])

    assert signal_frame[0] == signal_frame_result.device_id
    assert signal_frame[1:9] == (
        "boot-e2e-signal-0001",
        0,
        "imu.accel",
        "float32_le",
        "interleaved",
        5_000_000,
        4,
        48,
    )
    assert [channel["key"] for channel in channels] == ["accel_x", "accel_y", "accel_z"]


def test_signal_frame_uses_device_timestamp_when_available(signal_frame_result: SignalFrameResult) -> None:
    signal_frame = signal_frame_result.signal_frame_rows[0]

    assert signal_frame[9] == 1_712_345_679_111_000_000
    assert abs(int(signal_frame[10].timestamp() * 1_000_000_000) - signal_frame[9]) < 1_000
    assert signal_frame[10] < signal_frame[11]


def test_dimension_tables_deduplicate_device_boot_and_metric_keys(ingest_result: IngestResult) -> None:
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM devices WHERE device_id = %s", (ingest_result.provisioned_device["device_id"],))
            device_count = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*)
                FROM device_boot_sessions b
                JOIN devices d ON d.device_pk = b.device_pk
                WHERE d.device_id = %s AND b.boot_id = %s
                """,
                (ingest_result.provisioned_device["device_id"], "boot-e2e-0001"),
            )
            boot_count = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*)
                FROM metric_definitions
                WHERE metric_key = 'temperature'
                  AND metric_unit = 'celsius'
                  AND value_type = 'double'
                """
            )
            metric_count = cur.fetchone()[0]

    assert device_count == 1
    assert boot_count == 1
    assert metric_count == 1


def test_signal_frame_dimension_table_deduplicates_stream_definition(
    signal_frame_result: SignalFrameResult,
) -> None:
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM signal_stream_definitions
                WHERE stream_key = 'imu.accel'
                  AND encoding = 'float32_le'
                  AND layout = 'interleaved'
                """
            )
            stream_count = cur.fetchone()[0]

    assert signal_frame_result.signal_frame_rows
    assert stream_count == 1


def test_metric_points_table_is_timescaledb_hypertable(ingest_result: IngestResult) -> None:
    del ingest_result
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')")
            extension_exists = cur.fetchone()[0]
            cur.execute(
                """
                SELECT compression_enabled
                FROM timescaledb_information.hypertables
                WHERE hypertable_schema = 'public'
                  AND hypertable_name = 'device_metric_points'
                """
            )
            hypertable_row = cur.fetchone()
            cur.execute(
                """
                SELECT array_agg(a.attname ORDER BY array_position(i.indkey, a.attnum))
                FROM pg_class t
                JOIN pg_index i ON i.indrelid = t.oid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(i.indkey)
                WHERE t.relname = 'device_metric_points'
                  AND i.indisunique
                GROUP BY i.indexrelid
                """
            )
            unique_indexes = [tuple(row[0]) for row in cur.fetchall()]

    assert extension_exists is True
    assert hypertable_row == (True,)
    assert ("event_time", "request_id", "metric_index") in unique_indexes


def test_signal_frames_table_is_timescaledb_hypertable(signal_frame_result: SignalFrameResult) -> None:
    del signal_frame_result
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')")
            extension_exists = cur.fetchone()[0]
            cur.execute(
                """
                SELECT compression_enabled
                FROM timescaledb_information.hypertables
                WHERE hypertable_schema = 'public'
                  AND hypertable_name = 'device_signal_frames'
                """
            )
            hypertable_row = cur.fetchone()
            cur.execute(
                """
                SELECT array_agg(a.attname ORDER BY array_position(i.indkey, a.attnum))
                FROM pg_class t
                JOIN pg_index i ON i.indrelid = t.oid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(i.indkey)
                WHERE t.relname = 'device_signal_frames'
                  AND i.indisunique
                GROUP BY i.indexrelid
                """
            )
            unique_indexes = [tuple(row[0]) for row in cur.fetchall()]

    assert extension_exists is True
    assert hypertable_row == (True,)
    assert ("event_time", "request_id") in unique_indexes


def test_timescaledb_metric_retention_and_compression_jobs_exist(ingest_result: IngestResult) -> None:
    del ingest_result
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT proc_name
                FROM timescaledb_information.jobs
                WHERE hypertable_schema = 'public'
                  AND hypertable_name = 'device_metric_points'
                  AND proc_name IN ('policy_compression', 'policy_retention')
                ORDER BY proc_name
                """
            )
            job_names = {row[0] for row in cur.fetchall()}

    assert job_names == {"policy_compression", "policy_retention"}


def test_timescaledb_signal_frame_retention_and_compression_jobs_exist(
    signal_frame_result: SignalFrameResult,
) -> None:
    del signal_frame_result
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT proc_name
                FROM timescaledb_information.jobs
                WHERE hypertable_schema = 'public'
                  AND hypertable_name = 'device_signal_frames'
                  AND proc_name IN ('policy_compression', 'policy_retention')
                ORDER BY proc_name
                """
            )
            job_names = {row[0] for row in cur.fetchall()}

    assert job_names == {"policy_compression", "policy_retention"}
