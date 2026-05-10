# AETUS Anomaly Service

Rust PoC service for DB-backed anomaly detection.

The service builds one binary with multiple runtime modes:

```bash
aetus-anomaly api
aetus-anomaly worker
aetus-anomaly dispatcher
aetus-anomaly run-once
```

The PoC implements:

- Threshold detector over scalar metric streams in `device_metric_points`.
- Job/event/score tables in PostgreSQL.
- Webhook endpoint, outbox, delivery log, HMAC signing, retry/dead-letter dispatcher.
- Axum API for jobs, events, webhook endpoints, and manual job run.

## Local

```bash
cd services/anomaly
cargo test
cargo run -- api
```

Environment:

- `AETUS_POSTGRES_DSN`
- `AETUS_ANOMALY_ADMIN_TOKEN`
- `AETUS_ANOMALY_HOST`
- `AETUS_ANOMALY_PORT`
- `AETUS_ANOMALY_POLL_INTERVAL_SECONDS`

## API

All anomaly management endpoints except health/ready require:

```text
x-aetus-admin-token: <AETUS_ANOMALY_ADMIN_TOKEN>
```

Implemented endpoints:

- `GET /v1/healthz`
- `GET /v1/readyz`
- `GET /v1/anomaly/jobs`
- `POST /v1/anomaly/jobs`
- `POST /v1/anomaly/jobs/{job_id}/run`
- `GET /v1/anomaly/events?limit=100`
- `GET /v1/anomaly/webhooks/endpoints`
- `POST /v1/anomaly/webhooks/endpoints`

Example threshold job:

```bash
curl -X POST http://127.0.0.1:18002/v1/anomaly/jobs \
  -H 'content-type: application/json' \
  -H 'x-aetus-admin-token: e2e-anomaly-admin-token' \
  --data '{
    "job_key": "temperature-high",
    "device_selector": {"devices": ["python-client-e2e"]},
    "stream_selector": {"streams": ["temperature"]},
    "detector_type": "threshold",
    "detector_config": {"operator": "gt", "threshold": 20.0},
    "window_seconds": 60,
    "step_seconds": 60,
    "severity": "warning"
  }'
```

## Boundaries

The current detector reads scalar values from `device_metric_points`. Signal-frame feature detectors, worker leases, event acknowledgement/resolution APIs, and Prometheus metrics are intentionally left as follow-up work.
