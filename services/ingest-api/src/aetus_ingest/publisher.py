from __future__ import annotations

import json
from typing import Any

from kafka import KafkaProducer

from aetus_ingest.config import Settings


def build_sink_record(event: dict[str, Any]) -> dict[str, Any]:
    return {
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


class InMemoryEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class KafkaEventPublisher:
    def __init__(self, settings: Settings) -> None:
        self.topic = settings.kafka_topic
        self.producer = KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda value: json.dumps(value, separators=(",", ":")).encode("utf-8"),
            key_serializer=lambda value: value.encode("utf-8"),
        )

    async def publish(self, event: dict[str, Any]) -> None:
        sink_record = build_sink_record(event)
        future = self.producer.send(self.topic, key=event["device_id"], value=sink_record)
        future.get(timeout=10)
        self.producer.flush()
