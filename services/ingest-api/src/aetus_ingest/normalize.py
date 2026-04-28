from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from aetus_ingest.generated import ingest_pb2


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
        return {"metrics": metrics}

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
