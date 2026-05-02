from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field


def _parse_mapping(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"Invalid mapping entry: {item}")
        mapping[key.strip()] = value.strip()
    return mapping


def _parse_list(raw: str) -> set[str]:
    return {item.strip() for item in raw.split(",") if item.strip()}


def _parse_origin_list(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _parse_cidrs(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        networks.append(ipaddress.ip_network(item, strict=False))
    return tuple(networks)


@dataclass(slots=True)
class Settings:
    publisher_backend: str = "memory"
    device_tokens: dict[str, str] = field(default_factory=dict)
    bootstrap_token: str = "bootstrap_shared_token"
    allowed_source_cidrs: tuple[ipaddress._BaseNetwork, ...] = field(default_factory=tuple)
    allowlist_device_ids: set[str] = field(default_factory=set)
    allowed_hardware_ids: set[str] = field(default_factory=set)
    max_body_bytes: int = 1024 * 1024
    ingest_requests_per_second: float = 2.0
    ingest_burst: int = 10
    allowlist_requests_per_second: float = 20.0
    allowlist_burst: int = 20
    bootstrap_requests_per_window: int = 1
    bootstrap_window_seconds: float = 10.0
    kafka_bootstrap_servers: str = "127.0.0.1:19092"
    kafka_topic: str = "device.raw.v1"
    kafka_metric_topic: str = "device.metric.v1"
    kafka_signal_frame_topic: str = "device.signal_frame.v1"
    kafka_connect_url: str = "http://127.0.0.1:18083"
    postgres_dsn: str = "postgresql://aetus:aetus@127.0.0.1:15432/aetus"
    status_timeout_seconds: float = 2.0
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:4173", "http://localhost:4173")
    control_db_path: str = "data/control.db"
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        raw_tokens = os.getenv("AETUS_DEVICE_TOKENS", "esp32c5-test-001=devtok_test_001")
        raw_cidrs = os.getenv("AETUS_ALLOWED_SOURCE_CIDRS", "0.0.0.0/0")
        raw_allowlist = os.getenv("AETUS_ALLOWLIST_DEVICE_IDS", "esp32c5-lab-001")
        raw_hardware_allowlist = os.getenv("AETUS_ALLOWED_HARDWARE_IDS", "esp32c5-a1b2c3d4e5f6")
        return cls(
            publisher_backend=os.getenv("AETUS_PUBLISHER_BACKEND", "memory"),
            device_tokens=_parse_mapping(raw_tokens),
            bootstrap_token=os.getenv("AETUS_BOOTSTRAP_TOKEN", "bootstrap_shared_token"),
            allowed_source_cidrs=_parse_cidrs(raw_cidrs),
            allowlist_device_ids=_parse_list(raw_allowlist),
            allowed_hardware_ids=_parse_list(raw_hardware_allowlist),
            max_body_bytes=int(os.getenv("AETUS_MAX_BODY_BYTES", str(1024 * 1024))),
            ingest_requests_per_second=float(os.getenv("AETUS_INGEST_RPS", "2")),
            ingest_burst=int(os.getenv("AETUS_INGEST_BURST", "10")),
            allowlist_requests_per_second=float(os.getenv("AETUS_ALLOWLIST_INGEST_RPS", "20")),
            allowlist_burst=int(os.getenv("AETUS_ALLOWLIST_INGEST_BURST", "20")),
            bootstrap_requests_per_window=int(os.getenv("AETUS_BOOTSTRAP_REQUESTS_PER_WINDOW", "1")),
            bootstrap_window_seconds=float(os.getenv("AETUS_BOOTSTRAP_WINDOW_SECONDS", "10")),
            kafka_bootstrap_servers=os.getenv("AETUS_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:19092"),
            kafka_topic=os.getenv("AETUS_KAFKA_TOPIC", "device.raw.v1"),
            kafka_metric_topic=os.getenv("AETUS_KAFKA_METRIC_TOPIC", "device.metric.v1"),
            kafka_signal_frame_topic=os.getenv("AETUS_KAFKA_SIGNAL_FRAME_TOPIC", "device.signal_frame.v1"),
            kafka_connect_url=os.getenv("AETUS_KAFKA_CONNECT_URL", "http://127.0.0.1:18083"),
            postgres_dsn=os.getenv("AETUS_POSTGRES_DSN", "postgresql://aetus:aetus@127.0.0.1:15432/aetus"),
            status_timeout_seconds=float(os.getenv("AETUS_STATUS_TIMEOUT_SECONDS", "2")),
            cors_origins=_parse_origin_list(
                os.getenv("AETUS_CORS_ORIGINS", "http://127.0.0.1:4173,http://localhost:4173")
            ),
            control_db_path=os.getenv("AETUS_CONTROL_DB_PATH", "data/control.db"),
            host=os.getenv("AETUS_HOST", "0.0.0.0"),
            port=int(os.getenv("AETUS_PORT", "8000")),
        )
