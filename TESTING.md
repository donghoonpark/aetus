# Testing Guide

This project has intentionally separated test tiers. The default CI loop covers server, client, frontend, and firmware build confidence without requiring physical hardware.

## Quick Local Checks

Run targeted checks while developing:

```bash
cd services/ingest-api
uv run pytest tests/unit -q

cd ../query-api
uv run pytest tests/unit -q

cd ../anomaly
cargo fmt --check
cargo test

cd ../../clients/python-ingest
uv run pytest tests/unit -q

cd ../rust-ingest
cargo fmt --check
cargo test --test unit_client
```

## Docker E2E

Docker E2E starts the local compose stack and verifies ingest through Kafka, Kafka Connect, PostgreSQL, Query API, and client SDK paths.

```bash
cd services/ingest-api
uv run pytest tests/e2e -q

cd ../query-api
uv run pytest tests/e2e -q

cd ../../clients/python-ingest
uv run pytest tests/e2e -q

cd ../rust-ingest
AETUS_RUST_E2E_BUILD=1 cargo test --test e2e_pipeline -- --test-threads=1
```

The Python ingest client E2E suite also covers anomaly paths: a real client uploads metric and signal-frame data, the compose stack persists it to PostgreSQL, detector jobs are created through `anomaly-api`, and `/v1/anomaly/events` is verified after manual detector runs. Covered anomaly variants include scalar threshold, signal-frame channel peak detection, and string telemetry as an event anchor.

Tear down manually if a run is interrupted:

```bash
docker compose -f compose/e2e-compose.yml down -v --remove-orphans
```

## Frontend

The stream viewer Playwright suite mocks the Query API contract and covers multi-stream selection, scalar and sampled streams, string markers, JWT forwarding, server-side device search, and navigation/fetch behavior.

```bash
cd frontend/stream-viewer
npm ci
npm run build
npm run test:e2e
```

The ingest control panel currently has build coverage:

```bash
cd frontend/ingest-control-panel
npm ci
npm run build
```

The anomaly panel mocks the anomaly API contract and covers job/event/webhook rendering plus threshold job creation:

```bash
cd frontend/anomaly-panel
npm ci
npm run build
npm run test:e2e
```

## Firmware Build Tests

Firmware examples and test apps are built in GitHub Actions with `espressif/idf:release-v6.0`.

Local example:

```bash
source /path/to/esp-idf/export.sh
idf.py -C firmware/examples/basic-telemetry set-target esp32c5 build
```

CI firmware jobs:

- `.github/workflows/firmware-examples.yml`
- `.github/workflows/firmware-test-apps.yml`

These jobs validate example apps, QEMU-oriented apps, HIL-oriented apps, ISR enqueue compile paths, and negative compile checks for C++ literal limits.

## QEMU E2E

QEMU E2E is heavy and manual-dispatch by design. It builds ESP-IDF firmware, runs it under ESP32 QEMU, captures the generated protobuf stream, sends it through ingest, and verifies PostgreSQL rows.

Workflow:

- `.github/workflows/qemu-e2e.yml`

Local runs require ESP-IDF 6.0, QEMU tools, Docker Compose, and the `AETUS_RUN_QEMU_E2E=1` environment variable.

## HIL

Hardware-in-the-loop tests require a connected ESP32-class device and local Wi-Fi/device credentials. They are not part of default CI.

Rules:

- Keep real credentials in untracked `.env.hil` or shell environment.
- Do not commit generated firmware config headers.
- Verify not only upload success but also PostgreSQL row content when memory ownership or binary packing changes.

Useful firmware apps:

- `firmware/test-apps/esp32c5-upload-smoke`
- `firmware/test-apps/esp32c5-isr-enqueue`

## Coverage Expectations

For ingest or protobuf changes:

- Add ingest API unit tests.
- Add Docker E2E when the DB shape changes.
- Add client SDK tests if the public client API changes.
- Add Query API and stream viewer tests if the change affects stream consumption.

For anomaly detection changes:

- Add Rust unit tests for detector math, webhook signing, retry, and worker planning.
- Add Docker E2E when the DB schema, repository, or detector execution path changes.
- Add client E2E when detector behavior depends on real ingest payload shapes such as signal frames or string telemetry anchors.
- Add anomaly-panel Playwright coverage when operator-facing API or state rendering changes.

For firmware changes:

- Add host-level compile or unit coverage where possible.
- Add ESP-IDF build coverage for examples/test apps.
- Use QEMU for runtime memory/ownership behavior when practical.
- Use HIL for Wi-Fi, provisioning, and physical device upload behavior.
