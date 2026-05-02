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

CREATE TABLE IF NOT EXISTS devices (
    device_pk BIGSERIAL PRIMARY KEY,
    device_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS device_boot_sessions (
    boot_pk BIGSERIAL PRIMARY KEY,
    device_pk BIGINT NOT NULL REFERENCES devices(device_pk),
    boot_id TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (device_pk, boot_id)
);

CREATE TABLE IF NOT EXISTS metric_definitions (
    metric_pk BIGSERIAL PRIMARY KEY,
    metric_key TEXT NOT NULL,
    metric_unit TEXT NOT NULL DEFAULT '',
    value_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (metric_key, metric_unit, value_type)
);

CREATE TABLE IF NOT EXISTS metric_ingest_staging (
    request_id TEXT NOT NULL,
    metric_index INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    source_ip TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    firmware_version INTEGER NULL,
    uptime_ms BIGINT NULL,
    timestamp_ns BIGINT NULL,
    metric_key TEXT NOT NULL,
    metric_unit TEXT NULL,
    value_type TEXT NOT NULL,
    value_double DOUBLE PRECISION NULL,
    value_int BIGINT NULL,
    value_bool BOOLEAN NULL,
    value_string TEXT NULL,
    value_bytes_hex TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (request_id, metric_index)
);

CREATE TABLE IF NOT EXISTS device_metric_points (
    point_id BIGSERIAL NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    event_time_ns BIGINT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    device_pk BIGINT NOT NULL REFERENCES devices(device_pk),
    boot_pk BIGINT NOT NULL REFERENCES device_boot_sessions(boot_pk),
    metric_pk BIGINT NOT NULL REFERENCES metric_definitions(metric_pk),
    sequence BIGINT NOT NULL,
    metric_index INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    firmware_version INTEGER NULL,
    uptime_ms BIGINT NULL,
    source_ip INET NULL,
    value_double DOUBLE PRECISION NULL,
    value_int BIGINT NULL,
    value_bool BOOLEAN NULL,
    value_string TEXT NULL,
    value_bytes_hex TEXT NULL,
    request_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_time, request_id, metric_index)
);

CREATE TABLE IF NOT EXISTS signal_stream_definitions (
    signal_pk BIGSERIAL PRIMARY KEY,
    stream_key TEXT NOT NULL,
    encoding TEXT NOT NULL,
    layout TEXT NOT NULL,
    channels_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (stream_key, encoding, layout, channels_json)
);

CREATE TABLE IF NOT EXISTS signal_frame_ingest_staging (
    request_id TEXT NOT NULL PRIMARY KEY,
    received_at TEXT NOT NULL,
    source_ip TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    firmware_version INTEGER NULL,
    uptime_ms BIGINT NULL,
    timestamp_ns BIGINT NULL,
    stream_key TEXT NOT NULL,
    sample_interval_ns BIGINT NOT NULL,
    sample_count INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    layout TEXT NOT NULL,
    channels_json TEXT NOT NULL,
    samples_b64 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS device_signal_frames (
    frame_id BIGSERIAL NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    event_time_ns BIGINT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    device_pk BIGINT NOT NULL REFERENCES devices(device_pk),
    boot_pk BIGINT NOT NULL REFERENCES device_boot_sessions(boot_pk),
    signal_pk BIGINT NOT NULL REFERENCES signal_stream_definitions(signal_pk),
    sequence BIGINT NOT NULL,
    schema_version INTEGER NOT NULL,
    firmware_version INTEGER NULL,
    uptime_ms BIGINT NULL,
    source_ip INET NULL,
    sample_interval_ns BIGINT NOT NULL,
    sample_count INTEGER NOT NULL,
    samples BYTEA NOT NULL,
    samples_size INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (event_time, request_id)
);

CREATE INDEX IF NOT EXISTS idx_metric_points_time ON device_metric_points(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_metric_points_device_time ON device_metric_points(device_pk, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_metric_points_metric_time ON device_metric_points(metric_pk, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_signal_frames_time ON device_signal_frames(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_signal_frames_device_time ON device_signal_frames(device_pk, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_signal_frames_stream_time ON device_signal_frames(signal_pk, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_signal_stream_key ON signal_stream_definitions(stream_key);
CREATE INDEX IF NOT EXISTS idx_boot_sessions_device ON device_boot_sessions(device_pk);

CREATE OR REPLACE FUNCTION ingest_metric_staging_row()
RETURNS TRIGGER AS $$
DECLARE
    resolved_device_pk BIGINT;
    resolved_boot_pk BIGINT;
    resolved_metric_pk BIGINT;
    resolved_received_at TIMESTAMPTZ;
    resolved_event_time TIMESTAMPTZ;
    resolved_source_ip INET;
BEGIN
    resolved_received_at := NEW.received_at::TIMESTAMPTZ;
    IF NEW.timestamp_ns IS NOT NULL THEN
        resolved_event_time := TO_TIMESTAMP(NEW.timestamp_ns / 1000000000.0);
    ELSE
        resolved_event_time := resolved_received_at;
    END IF;

    BEGIN
        resolved_source_ip := NEW.source_ip::INET;
    EXCEPTION WHEN OTHERS THEN
        resolved_source_ip := NULL;
    END;

    INSERT INTO devices(device_id)
    VALUES (NEW.device_id)
    ON CONFLICT (device_id) DO NOTHING;

    SELECT device_pk INTO resolved_device_pk
    FROM devices
    WHERE device_id = NEW.device_id;

    INSERT INTO device_boot_sessions(device_pk, boot_id, first_seen_at)
    VALUES (resolved_device_pk, NEW.boot_id, resolved_received_at)
    ON CONFLICT (device_pk, boot_id) DO NOTHING;

    SELECT boot_pk INTO resolved_boot_pk
    FROM device_boot_sessions
    WHERE device_pk = resolved_device_pk
      AND boot_id = NEW.boot_id;

    INSERT INTO metric_definitions(metric_key, metric_unit, value_type)
    VALUES (NEW.metric_key, COALESCE(NEW.metric_unit, ''), NEW.value_type)
    ON CONFLICT (metric_key, metric_unit, value_type) DO NOTHING;

    SELECT metric_pk INTO resolved_metric_pk
    FROM metric_definitions
    WHERE metric_key = NEW.metric_key
      AND metric_unit = COALESCE(NEW.metric_unit, '')
      AND value_type = NEW.value_type;

    INSERT INTO device_metric_points(
        event_time,
        event_time_ns,
        received_at,
        device_pk,
        boot_pk,
        metric_pk,
        sequence,
        metric_index,
        schema_version,
        firmware_version,
        uptime_ms,
        source_ip,
        value_double,
        value_int,
        value_bool,
        value_string,
        value_bytes_hex,
        request_id
    )
    VALUES (
        resolved_event_time,
        NEW.timestamp_ns,
        resolved_received_at,
        resolved_device_pk,
        resolved_boot_pk,
        resolved_metric_pk,
        NEW.sequence,
        NEW.metric_index,
        NEW.schema_version,
        NEW.firmware_version,
        NEW.uptime_ms,
        resolved_source_ip,
        NEW.value_double,
        NEW.value_int,
        NEW.value_bool,
        NEW.value_string,
        NEW.value_bytes_hex,
        NEW.request_id
    )
    ON CONFLICT (event_time, request_id, metric_index) DO UPDATE SET
        event_time = EXCLUDED.event_time,
        event_time_ns = EXCLUDED.event_time_ns,
        received_at = EXCLUDED.received_at,
        device_pk = EXCLUDED.device_pk,
        boot_pk = EXCLUDED.boot_pk,
        metric_pk = EXCLUDED.metric_pk,
        sequence = EXCLUDED.sequence,
        schema_version = EXCLUDED.schema_version,
        firmware_version = EXCLUDED.firmware_version,
        uptime_ms = EXCLUDED.uptime_ms,
        source_ip = EXCLUDED.source_ip,
        value_double = EXCLUDED.value_double,
        value_int = EXCLUDED.value_int,
        value_bool = EXCLUDED.value_bool,
        value_string = EXCLUDED.value_string,
        value_bytes_hex = EXCLUDED.value_bytes_hex;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_metric_ingest_staging_row ON metric_ingest_staging;
CREATE TRIGGER trg_metric_ingest_staging_row
AFTER INSERT OR UPDATE ON metric_ingest_staging
FOR EACH ROW
EXECUTE FUNCTION ingest_metric_staging_row();

CREATE OR REPLACE FUNCTION ingest_signal_frame_staging_row()
RETURNS TRIGGER AS $$
DECLARE
    resolved_device_pk BIGINT;
    resolved_boot_pk BIGINT;
    resolved_signal_pk BIGINT;
    resolved_received_at TIMESTAMPTZ;
    resolved_event_time TIMESTAMPTZ;
    resolved_source_ip INET;
    decoded_samples BYTEA;
BEGIN
    resolved_received_at := NEW.received_at::TIMESTAMPTZ;
    IF NEW.timestamp_ns IS NOT NULL THEN
        resolved_event_time := TO_TIMESTAMP(NEW.timestamp_ns / 1000000000.0);
    ELSE
        resolved_event_time := resolved_received_at;
    END IF;

    BEGIN
        resolved_source_ip := NEW.source_ip::INET;
    EXCEPTION WHEN OTHERS THEN
        resolved_source_ip := NULL;
    END;

    decoded_samples := DECODE(NEW.samples_b64, 'base64');

    INSERT INTO devices(device_id)
    VALUES (NEW.device_id)
    ON CONFLICT (device_id) DO NOTHING;

    SELECT device_pk INTO resolved_device_pk
    FROM devices
    WHERE device_id = NEW.device_id;

    INSERT INTO device_boot_sessions(device_pk, boot_id, first_seen_at)
    VALUES (resolved_device_pk, NEW.boot_id, resolved_received_at)
    ON CONFLICT (device_pk, boot_id) DO NOTHING;

    SELECT boot_pk INTO resolved_boot_pk
    FROM device_boot_sessions
    WHERE device_pk = resolved_device_pk
      AND boot_id = NEW.boot_id;

    INSERT INTO signal_stream_definitions(stream_key, encoding, layout, channels_json)
    VALUES (NEW.stream_key, NEW.encoding, NEW.layout, NEW.channels_json)
    ON CONFLICT (stream_key, encoding, layout, channels_json) DO NOTHING;

    SELECT signal_pk INTO resolved_signal_pk
    FROM signal_stream_definitions
    WHERE stream_key = NEW.stream_key
      AND encoding = NEW.encoding
      AND layout = NEW.layout
      AND channels_json = NEW.channels_json;

    INSERT INTO device_signal_frames(
        event_time,
        event_time_ns,
        received_at,
        device_pk,
        boot_pk,
        signal_pk,
        sequence,
        schema_version,
        firmware_version,
        uptime_ms,
        source_ip,
        sample_interval_ns,
        sample_count,
        samples,
        samples_size,
        request_id
    )
    VALUES (
        resolved_event_time,
        NEW.timestamp_ns,
        resolved_received_at,
        resolved_device_pk,
        resolved_boot_pk,
        resolved_signal_pk,
        NEW.sequence,
        NEW.schema_version,
        NEW.firmware_version,
        NEW.uptime_ms,
        resolved_source_ip,
        NEW.sample_interval_ns,
        NEW.sample_count,
        decoded_samples,
        OCTET_LENGTH(decoded_samples),
        NEW.request_id
    )
    ON CONFLICT (event_time, request_id) DO UPDATE SET
        event_time = EXCLUDED.event_time,
        event_time_ns = EXCLUDED.event_time_ns,
        received_at = EXCLUDED.received_at,
        device_pk = EXCLUDED.device_pk,
        boot_pk = EXCLUDED.boot_pk,
        signal_pk = EXCLUDED.signal_pk,
        sequence = EXCLUDED.sequence,
        schema_version = EXCLUDED.schema_version,
        firmware_version = EXCLUDED.firmware_version,
        uptime_ms = EXCLUDED.uptime_ms,
        source_ip = EXCLUDED.source_ip,
        sample_interval_ns = EXCLUDED.sample_interval_ns,
        sample_count = EXCLUDED.sample_count,
        samples = EXCLUDED.samples,
        samples_size = EXCLUDED.samples_size;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_signal_frame_ingest_staging_row ON signal_frame_ingest_staging;
CREATE TRIGGER trg_signal_frame_ingest_staging_row
AFTER INSERT OR UPDATE ON signal_frame_ingest_staging
FOR EACH ROW
EXECUTE FUNCTION ingest_signal_frame_staging_row();
