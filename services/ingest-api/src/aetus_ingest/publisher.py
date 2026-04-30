from __future__ import annotations

import json
from typing import Any

from kafka import KafkaProducer

from aetus_ingest.config import Settings


RAW_EVENT_SCHEMA = {
    "type": "struct",
    "optional": False,
    "name": "aetus.device.raw.v1.RawDeviceEvent",
    "fields": [
        {"type": "string", "optional": False, "field": "request_id"},
        {"type": "string", "optional": False, "field": "received_at"},
        {"type": "string", "optional": False, "field": "source_ip"},
        {"type": "int32", "optional": False, "field": "schema_version"},
        {"type": "string", "optional": False, "field": "device_id"},
        {"type": "string", "optional": False, "field": "boot_id"},
        {"type": "int64", "optional": False, "field": "sequence"},
        {"type": "string", "optional": False, "field": "event_type"},
        {"type": "int32", "optional": True, "field": "firmware_version"},
        {"type": "int64", "optional": True, "field": "uptime_ms"},
        {"type": "int64", "optional": True, "field": "timestamp_ns"},
        {"type": "string", "optional": False, "field": "payload_json"},
    ],
}

METRIC_EVENT_SCHEMA = {
    "type": "struct",
    "optional": False,
    "name": "aetus.device.metric.v1.DeviceMetricEvent",
    "fields": [
        {"type": "string", "optional": False, "field": "request_id"},
        {"type": "int32", "optional": False, "field": "metric_index"},
        {"type": "string", "optional": False, "field": "received_at"},
        {"type": "string", "optional": False, "field": "source_ip"},
        {"type": "int32", "optional": False, "field": "schema_version"},
        {"type": "string", "optional": False, "field": "device_id"},
        {"type": "string", "optional": False, "field": "boot_id"},
        {"type": "int64", "optional": False, "field": "sequence"},
        {"type": "string", "optional": False, "field": "event_type"},
        {"type": "int32", "optional": True, "field": "firmware_version"},
        {"type": "int64", "optional": True, "field": "uptime_ms"},
        {"type": "int64", "optional": True, "field": "timestamp_ns"},
        {"type": "string", "optional": False, "field": "metric_key"},
        {"type": "string", "optional": True, "field": "metric_unit"},
        {"type": "string", "optional": False, "field": "value_type"},
        {"type": "double", "optional": True, "field": "value_double"},
        {"type": "int64", "optional": True, "field": "value_int"},
        {"type": "boolean", "optional": True, "field": "value_bool"},
        {"type": "string", "optional": True, "field": "value_string"},
        {"type": "string", "optional": True, "field": "value_bytes_hex"},
    ],
}


def build_sink_record(event: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "request_id": event["request_id"],
        "received_at": event["received_at"],
        "source_ip": event["source_ip"],
        "schema_version": event["schema_version"],
        "device_id": event["device_id"],
        "boot_id": event["boot_id"],
        "sequence": event["sequence"],
        "event_type": event["event_type"],
        "firmware_version": event["firmware_version"],
        "uptime_ms": event["uptime_ms"],
        "timestamp_ns": event["timestamp_ns"],
        "payload_json": event["payload_json"],
    }
    return {
        "schema": RAW_EVENT_SCHEMA,
        "payload": payload,
    }


def build_metric_records(event: dict[str, Any]) -> list[dict[str, Any]]:
    if event["event_type"] != "telemetry":
        return []

    metric_records = []
    for index, metric in enumerate(event["payload"].get("metrics", [])):
        value_type = metric["type"]
        value = metric["value"]
        payload = {
            "request_id": event["request_id"],
            "metric_index": index,
            "received_at": event["received_at"],
            "source_ip": event["source_ip"],
            "schema_version": event["schema_version"],
            "device_id": event["device_id"],
            "boot_id": event["boot_id"],
            "sequence": event["sequence"],
            "event_type": event["event_type"],
            "firmware_version": event["firmware_version"],
            "uptime_ms": event["uptime_ms"],
            "timestamp_ns": event["timestamp_ns"],
            "metric_key": metric["key"],
            "metric_unit": metric["unit"],
            "value_type": value_type,
            "value_double": value if value_type == "double" else None,
            "value_int": value if value_type == "int" else None,
            "value_bool": value if value_type == "bool" else None,
            "value_string": value if value_type == "string" else None,
            "value_bytes_hex": value if value_type == "bytes_hex" else None,
        }
        metric_records.append({"schema": METRIC_EVENT_SCHEMA, "payload": payload})
    return metric_records


class InMemoryEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.metric_records: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self.metric_records.extend(build_metric_records(event))


class KafkaEventPublisher:
    def __init__(self, settings: Settings) -> None:
        self.topic = settings.kafka_topic
        self.metric_topic = settings.kafka_metric_topic
        self.producer = KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda value: json.dumps(value, separators=(",", ":")).encode("utf-8"),
            key_serializer=lambda value: value.encode("utf-8"),
        )

    async def publish(self, event: dict[str, Any]) -> None:
        sink_record = build_sink_record(event)
        future = self.producer.send(self.topic, key=event["device_id"], value=sink_record)
        future.get(timeout=10)
        for metric_record in build_metric_records(event):
            metric_key = f"{event['device_id']}:{event['boot_id']}:{event['sequence']}:{metric_record['payload']['metric_index']}"
            metric_future = self.producer.send(self.metric_topic, key=metric_key, value=metric_record)
            metric_future.get(timeout=10)
        self.producer.flush()
