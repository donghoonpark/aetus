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

- Rule-based detectors over scalar metric streams and decoded signal-frame channels.
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

Implemented detector types:

- `threshold`
- `range`
- `mean_threshold`
- `rms_threshold`
- `peak_abs_threshold`
- `stddev_threshold`
- `delta_threshold`
- `rate_of_change`
- `zscore_threshold`
- `ewma_deviation`
- `missing_data`
- `flatline`
- `stuck_at`
- `duty_cycle`
- `event_sequence`
- `fft_threshold`

Signal frames are decoded from `device_signal_frames.samples` into channel-specific numeric windows. Use `stream_selector.channels` to limit channels and `detector_config.max_points` to cap samples loaded per channel.

Event-anchored windows are supported through `detector_config.anchor`. The anchor stream can be numeric, bool, or string telemetry. String anchors are useful for state-machine events such as `machine_state == "RUN"`:

```json
{
  "detector_type": "rms_threshold",
  "detector_config": {
    "threshold": 0.5,
    "max_points": 10000,
    "anchor": {
      "stream": "machine_state",
      "value_string": "RUN",
      "pre_seconds": 0,
      "post_seconds": 10,
      "max_events": 1
    }
  }
}
```

Worker leases, event acknowledgement/resolution APIs, Prometheus metrics, and cached signal-frame feature reuse remain follow-up work.

Detector selection guide:

- Use `threshold` or `range` for simple scalar operating bounds.
- Use `mean_threshold` for sustained high/low load.
- Use `rms_threshold`, `peak_abs_threshold`, `stddev_threshold`, or `fft_threshold` for vibration/current waveform inspection.
- Use `rate_of_change`, `delta_threshold`, or `ewma_deviation` for fast transitions or drift.
- Use `zscore_threshold` when each device has its own baseline and sigma.
- Use `missing_data`, `flatline`, or `stuck_at` for sensor health and stuck-value checks.
- Use `duty_cycle` for ON/OFF streams and `event_sequence` for event-count expectations around an anchor window.
