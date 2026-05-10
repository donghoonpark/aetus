CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS anomaly_jobs (
    job_id BIGSERIAL PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    device_selector JSONB NOT NULL,
    stream_selector JSONB NOT NULL,
    detector_type TEXT NOT NULL,
    detector_config JSONB NOT NULL,
    window_seconds INTEGER NOT NULL,
    step_seconds INTEGER NOT NULL,
    lookback_seconds INTEGER NOT NULL DEFAULT 0,
    severity TEXT NOT NULL DEFAULT 'warning',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS anomaly_job_state (
    job_id BIGINT PRIMARY KEY REFERENCES anomaly_jobs(job_id) ON DELETE CASCADE,
    last_window_end TIMESTAMPTZ NULL,
    lease_owner TEXT NULL,
    lease_until TIMESTAMPTZ NULL,
    heartbeat_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS anomaly_scores (
    score_id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES anomaly_jobs(job_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    stream_key TEXT NOT NULL,
    channel_key TEXT NULL,
    channel_key_norm TEXT NOT NULL DEFAULT '',
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    threshold DOUBLE PRECISION NULL,
    severity TEXT NOT NULL,
    detector_type TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS anomaly_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id BIGINT NOT NULL REFERENCES anomaly_jobs(job_id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    stream_key TEXT NOT NULL,
    channel_key TEXT NULL,
    channel_key_norm TEXT NOT NULL DEFAULT '',
    event_start TIMESTAMPTZ NOT NULL,
    event_end TIMESTAMPTZ NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    score DOUBLE PRECISION NOT NULL,
    threshold DOUBLE PRECISION NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_endpoints (
    endpoint_id BIGSERIAL PRIMARY KEY,
    endpoint_key TEXT NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    event_filter JSONB NOT NULL DEFAULT '{}'::jsonb,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    timeout_seconds DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_outbox (
    outbox_id BIGSERIAL PRIMARY KEY,
    endpoint_id BIGINT NOT NULL REFERENCES webhook_endpoints(endpoint_id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES anomaly_events(event_id) ON DELETE CASCADE,
    payload_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempt_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (endpoint_id, event_id)
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id BIGSERIAL PRIMARY KEY,
    outbox_id BIGINT NOT NULL REFERENCES webhook_outbox(outbox_id) ON DELETE CASCADE,
    endpoint_id BIGINT NOT NULL REFERENCES webhook_endpoints(endpoint_id) ON DELETE CASCADE,
    event_id UUID NOT NULL REFERENCES anomaly_events(event_id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    status_code INTEGER NULL,
    success BOOLEAN NOT NULL,
    error TEXT NULL,
    duration_ms INTEGER NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_anomaly_jobs_enabled ON anomaly_jobs(enabled);
CREATE UNIQUE INDEX IF NOT EXISTS uq_anomaly_scores_window
    ON anomaly_scores(job_id, device_id, stream_key, channel_key_norm, window_start, window_end);
CREATE UNIQUE INDEX IF NOT EXISTS uq_anomaly_events_start
    ON anomaly_events(job_id, device_id, stream_key, channel_key_norm, event_start);
CREATE INDEX IF NOT EXISTS idx_anomaly_scores_lookup ON anomaly_scores(device_id, stream_key, window_end DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_lookup ON anomaly_events(device_id, stream_key, event_end DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_events_status ON anomaly_events(status, event_end DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_outbox_pending ON webhook_outbox(status, next_attempt_at);
