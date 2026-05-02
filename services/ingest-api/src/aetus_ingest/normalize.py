from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from aetus_ingest.generated import ingest_pb2


SIGNAL_ENCODING_NAMES = {
    ingest_pb2.SIGNAL_SAMPLE_ENCODING_FLOAT32_LE: "float32_le",
    ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT16_LE: "int16_le",
    ingest_pb2.SIGNAL_SAMPLE_ENCODING_UINT16_LE: "uint16_le",
    ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT32_LE: "int32_le",
}

SIGNAL_ENCODING_SAMPLE_BYTES = {
    ingest_pb2.SIGNAL_SAMPLE_ENCODING_FLOAT32_LE: 4,
    ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT16_LE: 2,
    ingest_pb2.SIGNAL_SAMPLE_ENCODING_UINT16_LE: 2,
    ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT32_LE: 4,
}

SIGNAL_LAYOUT_NAMES = {
    ingest_pb2.SIGNAL_SAMPLE_LAYOUT_INTERLEAVED: "interleaved",
    ingest_pb2.SIGNAL_SAMPLE_LAYOUT_PLANAR: "planar",
}


def _metric_value(metric: ingest_pb2.Metric) -> tuple[str, Any]:
    value_kind = metric.WhichOneof("value")
    if value_kind == "int_value":
        return "int", metric.int_value
    if value_kind == "double_value":
        return "double", metric.double_value
    if value_kind == "bool_value":
        return "bool", metric.bool_value
    if value_kind == "string_value":
        return "string", metric.string_value
    if value_kind == "bytes_value":
        return "bytes_hex", metric.bytes_value.hex()
    raise HTTPException(status_code=400, detail="metric value missing")


def _normalize_signal_frame(frame: ingest_pb2.SignalFrame) -> dict[str, Any]:
    if not frame.stream_key:
        raise HTTPException(status_code=400, detail="signal frame stream_key required")
    if frame.sample_interval_ns == 0:
        raise HTTPException(status_code=400, detail="signal frame sample_interval_ns required")
    if frame.sample_count == 0:
        raise HTTPException(status_code=400, detail="signal frame sample_count required")
    if frame.encoding not in SIGNAL_ENCODING_NAMES:
        raise HTTPException(status_code=400, detail="signal frame encoding unsupported")
    if frame.layout not in SIGNAL_LAYOUT_NAMES:
        raise HTTPException(status_code=400, detail="signal frame layout unsupported")
    if not frame.channels:
        raise HTTPException(status_code=400, detail="signal frame channels required")

    channels = []
    for channel in frame.channels:
        if not channel.key:
            raise HTTPException(status_code=400, detail="signal frame channel key required")
        normalized_channel: dict[str, Any] = {
            "key": channel.key,
            "unit": channel.unit or None,
        }
        if channel.HasField("scale"):
            normalized_channel["scale"] = channel.scale
        if channel.HasField("offset"):
            normalized_channel["offset"] = channel.offset
        channels.append(normalized_channel)

    expected_sample_bytes = frame.sample_count * len(channels) * SIGNAL_ENCODING_SAMPLE_BYTES[frame.encoding]
    if len(frame.samples) != expected_sample_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"signal frame samples length mismatch: expected {expected_sample_bytes}, got {len(frame.samples)}",
        )

    return {
        "stream_key": frame.stream_key,
        "sample_interval_ns": frame.sample_interval_ns,
        "sample_count": frame.sample_count,
        "encoding": SIGNAL_ENCODING_NAMES[frame.encoding],
        "layout": SIGNAL_LAYOUT_NAMES[frame.layout],
        "channels": channels,
        "samples_b64": base64.b64encode(frame.samples).decode("ascii"),
    }


def event_type_name(value: int) -> str:
    if value == ingest_pb2.EVENT_TYPE_TELEMETRY:
        return "telemetry"
    if value == ingest_pb2.EVENT_TYPE_STATUS:
        return "status"
    if value == ingest_pb2.EVENT_TYPE_ALERT:
        return "alert"
    return "unknown"


def normalize_payload(event: ingest_pb2.IngestEvent) -> dict[str, Any]:
    body = event.WhichOneof("body")
    if body == "telemetry":
        metrics = []
        for metric in event.telemetry.metrics:
            value_type, value = _metric_value(metric)
            metrics.append(
                {
                    "key": metric.key,
                    "type": value_type,
                    "value": value,
                    "unit": metric.unit or None,
                }
            )
        payload: dict[str, Any] = {"metrics": metrics}
        if event.telemetry.HasField("signal_frame"):
            payload["signal_frame"] = _normalize_signal_frame(event.telemetry.signal_frame)
        return payload

    if body == "status":
        return {
            "status": int(event.status.status),
            "rssi": event.status.rssi,
            "free_heap": event.status.free_heap,
            "reboot_reason": event.status.reboot_reason or None,
        }

    if body == "alert":
        return {
            "code": event.alert.code,
            "severity": int(event.alert.severity),
            "message": event.alert.message,
        }

    raise HTTPException(status_code=400, detail="body missing")


def normalize_event(event: ingest_pb2.IngestEvent, source_ip: str) -> dict[str, Any]:
    payload = normalize_payload(event)
    return {
        "request_id": f"req-{uuid4().hex[:12]}",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "source_ip": source_ip,
        "schema_version": event.schema_version,
        "device_id": event.device_id,
        "boot_id": event.boot_id,
        "sequence": event.sequence,
        "event_type": event_type_name(event.event_type),
        "firmware_version": event.firmware_version or None,
        "uptime_ms": event.uptime_ms or None,
        "timestamp_ns": event.timestamp_ns or None,
        "payload": payload,
        "payload_json": json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
    }
