CREATE TABLE IF NOT EXISTS raw_device_events (
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    firmware_version INTEGER NULL,
    uptime_ms BIGINT NULL,
    timestamp_ns BIGINT NULL,
    request_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    source_ip TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (device_id, boot_id, sequence)
);
