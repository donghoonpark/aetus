from __future__ import annotations

import math
import struct

import httpx
import pytest

from aetus_ingest_client import (
    AetusIngestClient,
    IngestClientError,
    build_alert_event,
    build_metric_event,
    build_signal_frame_event,
    build_status_event,
    channel,
    metric,
    pack_signal_samples,
)
from aetus_ingest_client.generated import ingest_pb2


DEVICE_ID = "python-test-device"
BOOT_ID = "boot-python-unit"


def test_metric_sets_expected_oneof_for_supported_python_values() -> None:
    cases = [
        (metric("i", -7, "count"), "int_value", -7),
        (metric("f", 22.25, "celsius"), "double_value", 22.25),
        (metric("b", True), "bool_value", True),
        (metric("s", "ok"), "string_value", "ok"),
        (metric("blob", b"\x01\x02"), "bytes_value", b"\x01\x02"),
        (metric("bytearray", bytearray(b"\x03")), "bytes_value", b"\x03"),
    ]

    for item, expected_oneof, expected_value in cases:
        assert item.WhichOneof("value") == expected_oneof
        assert getattr(item, expected_oneof) == expected_value


def test_metric_rejects_missing_key_and_unknown_value_type() -> None:
    with pytest.raises(ValueError, match="metric key"):
        metric("", 1)
    with pytest.raises(TypeError, match="unsupported"):
        metric("bad", object())


def test_metric_event_sets_telemetry_metric_set_and_metadata() -> None:
    event = build_metric_event(
        device_id=DEVICE_ID,
        sequence=3,
        boot_id=BOOT_ID,
        firmware_version=1002003,
        uptime_ms=1234,
        timestamp_ns=1_712_345_678_901_234_567,
        metrics=[("temperature", 22.25, "celsius"), metric("ok", True)],
    )

    assert event.schema_version == 1
    assert event.device_id == DEVICE_ID
    assert event.sequence == 3
    assert event.boot_id == BOOT_ID
    assert event.firmware_version == 1002003
    assert event.uptime_ms == 1234
    assert event.timestamp_ns == 1_712_345_678_901_234_567
    assert event.event_type == ingest_pb2.EVENT_TYPE_TELEMETRY
    assert event.WhichOneof("body") == "telemetry"
    assert event.telemetry.WhichOneof("payload") == "metric_set"
    assert [(m.key, m.unit, m.WhichOneof("value")) for m in event.telemetry.metric_set.metrics] == [
        ("temperature", "celsius", "double_value"),
        ("ok", "", "bool_value"),
    ]


def test_metric_event_rejects_empty_metric_set() -> None:
    with pytest.raises(ValueError, match="at least one metric"):
        build_metric_event(device_id=DEVICE_ID, sequence=0, boot_id=BOOT_ID, metrics=[])


def test_pack_signal_samples_interleaved_float32() -> None:
    packed = pack_signal_samples(
        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        encoding="float32_le",
        layout="interleaved",
    )

    assert packed == struct.pack("<ffffff", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)


def test_pack_signal_samples_planar_int16() -> None:
    packed = pack_signal_samples(
        [[1, 10], [2, 20], [3, 30]],
        encoding="int16_le",
        layout="planar",
    )

    assert packed == struct.pack("<hhhhhh", 1, 2, 3, 10, 20, 30)


def test_pack_signal_samples_rejects_ragged_rows() -> None:
    with pytest.raises(ValueError, match="same channel count"):
        pack_signal_samples([[1, 2], [3]], encoding="int16_le")


def test_signal_frame_event_builds_channels_and_packed_samples() -> None:
    event = build_signal_frame_event(
        device_id=DEVICE_ID,
        sequence=9,
        boot_id=BOOT_ID,
        stream_key="imu.accel",
        sample_interval_ns=5_000_000,
        channels=[
            channel("x", "g", scale=0.1),
            channel("y", "g", offset=-1.0),
            "z",
        ],
        samples=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        timestamp_ns=1_712_345_679_111_000_000,
    )
    frame = event.telemetry.signal_frame

    assert event.telemetry.WhichOneof("payload") == "signal_frame"
    assert frame.stream_key == "imu.accel"
    assert frame.sample_interval_ns == 5_000_000
    assert frame.sample_count == 2
    assert frame.encoding == ingest_pb2.SIGNAL_SAMPLE_ENCODING_FLOAT32_LE
    assert frame.layout == ingest_pb2.SIGNAL_SAMPLE_LAYOUT_INTERLEAVED
    assert [c.key for c in frame.channels] == ["x", "y", "z"]
    assert math.isclose(frame.channels[0].scale, 0.1, rel_tol=1e-6)
    assert frame.channels[1].offset == -1.0
    assert len(frame.samples) == 24


def test_signal_frame_event_accepts_prepacked_samples_when_count_is_given() -> None:
    packed = struct.pack("<hhhh", 1, 2, 3, 4)

    event = build_signal_frame_event(
        device_id=DEVICE_ID,
        sequence=0,
        boot_id=BOOT_ID,
        stream_key="adc.raw",
        sample_interval_ns=1_000_000,
        channels=["a", "b"],
        samples=packed,
        sample_count=2,
        encoding="int16_le",
    )

    assert event.telemetry.signal_frame.samples == packed
    assert event.telemetry.signal_frame.sample_count == 2
    assert event.telemetry.signal_frame.encoding == ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT16_LE


def test_signal_frame_event_rejects_sample_shape_mismatch() -> None:
    with pytest.raises(ValueError, match="row width"):
        build_signal_frame_event(
            device_id=DEVICE_ID,
            sequence=0,
            boot_id=BOOT_ID,
            stream_key="bad",
            sample_interval_ns=1,
            channels=["a", "b"],
            samples=[[1, 2, 3]],
        )
    with pytest.raises(ValueError, match="sample_count is required"):
        build_signal_frame_event(
            device_id=DEVICE_ID,
            sequence=0,
            boot_id=BOOT_ID,
            stream_key="bad",
            sample_interval_ns=1,
            channels=["a"],
            samples=b"\x00\x00",
            encoding="int16_le",
        )


def test_status_and_alert_event_builders_set_expected_body() -> None:
    status = build_status_event(
        device_id=DEVICE_ID,
        sequence=1,
        boot_id=BOOT_ID,
        status="degraded",
        rssi=-61,
        free_heap=123456,
        reboot_reason="ota",
    )
    alert = build_alert_event(
        device_id=DEVICE_ID,
        sequence=2,
        boot_id=BOOT_ID,
        code="laser_fault",
        severity="critical",
        message="laser current too high",
    )

    assert status.event_type == ingest_pb2.EVENT_TYPE_STATUS
    assert status.status.status == ingest_pb2.DEVICE_STATUS_DEGRADED
    assert status.status.rssi == -61
    assert status.status.free_heap == 123456
    assert status.status.reboot_reason == "ota"
    assert alert.event_type == ingest_pb2.EVENT_TYPE_ALERT
    assert alert.alert.severity == ingest_pb2.SEVERITY_CRITICAL
    assert alert.alert.code == "laser_fault"


def test_client_posts_protobuf_headers_and_increments_sequence_on_success() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        event = ingest_pb2.IngestEvent()
        event.ParseFromString(request.content)
        return httpx.Response(
            202,
            headers={"X-Request-Id": "req-unit"},
            json={"request_id": "req-unit", "status": "accepted", "device_id": event.device_id, "sequence": event.sequence},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = AetusIngestClient(
            base_url="http://ingest.local/",
            device_id=DEVICE_ID,
            token="tok",
            boot_id=BOOT_ID,
            initial_sequence=7,
            http_client=http_client,
        )
        response = client.send_metrics([("temperature", 22.25, "celsius")])

    assert response.status_code == 202
    assert response.request_id == "req-unit"
    assert response.sequence == 7
    assert client.sequence == 8
    assert requests[0].url == "http://ingest.local/v1/ingest"
    assert requests[0].headers["content-type"] == "application/x-protobuf"
    assert requests[0].headers["x-device-id"] == DEVICE_ID
    assert requests[0].headers["authorization"] == "Bearer tok"


def test_client_can_sign_uploads_with_hmac_auth_mode() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        event = ingest_pb2.IngestEvent()
        event.ParseFromString(request.content)
        return httpx.Response(
            202,
            json={"request_id": "req-hmac", "status": "accepted", "device_id": event.device_id, "sequence": event.sequence},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = AetusIngestClient(
            base_url="http://ingest.local",
            device_id=DEVICE_ID,
            token="tok",
            boot_id=BOOT_ID,
            auth_mode="hmac",
            http_client=http_client,
        )
        client.send_metrics([("temperature", 22.25, "celsius")])

    assert "authorization" not in requests[0].headers
    assert requests[0].headers["x-aetus-signature"].startswith("hmac-sha256-v1=")
    assert len(requests[0].headers["x-aetus-signature"]) == len("hmac-sha256-v1=") + 64


def test_hmac_signature_matches_known_vector() -> None:
    client = AetusIngestClient(
        base_url="http://ingest.local",
        device_id=DEVICE_ID,
        token="secret",
        boot_id=BOOT_ID,
        auth_mode="hmac",
    )

    assert (
        client.hmac_signature(device_id="dev", body=b"\x01\x02test")
        == "hmac-sha256-v1=5f289c9b28519726a0e78e73646e9355d2068ea0c5d696909eb384a97e324e5c"
    )


def test_client_rejects_unknown_auth_mode() -> None:
    with pytest.raises(ValueError, match="auth_mode"):
        AetusIngestClient(
            base_url="http://ingest.local",
            device_id=DEVICE_ID,
            token="tok",
            boot_id=BOOT_ID,
            auth_mode="unknown",  # type: ignore[arg-type]
        )


def test_client_keeps_sequence_when_server_rejects_upload() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="bad token"))

    with httpx.Client(transport=transport) as http_client:
        client = AetusIngestClient(
            base_url="http://ingest.local",
            device_id=DEVICE_ID,
            token="tok",
            boot_id=BOOT_ID,
            http_client=http_client,
        )
        with pytest.raises(IngestClientError) as exc_info:
            client.send_metrics([("temperature", 22.25)])

    assert exc_info.value.status_code == 401
    assert exc_info.value.response_text == "bad token"
    assert client.sequence == 0


def test_client_does_not_advance_sequence_for_manual_event_with_different_sequence() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            202,
            json={"request_id": "req-manual", "status": "accepted", "device_id": DEVICE_ID, "sequence": 99},
        )
    )
    event = build_metric_event(device_id=DEVICE_ID, sequence=99, boot_id=BOOT_ID, metrics=[("x", 1)])

    with httpx.Client(transport=transport) as http_client:
        client = AetusIngestClient(
            base_url="http://ingest.local",
            device_id=DEVICE_ID,
            token="tok",
            boot_id=BOOT_ID,
            initial_sequence=0,
            http_client=http_client,
        )
        client.send_event(event)

    assert client.sequence == 0
