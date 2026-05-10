# Contributing

Thanks for helping improve AETUS. This project spans firmware, backend services, data storage, client SDKs, and frontend components, so keeping boundaries clear matters as much as individual code changes.

## Development Setup

Core tools:

- Python 3.11+
- `uv`
- Docker Compose
- Node.js 22+
- Rust stable
- ESP-IDF 6.0 for firmware work

Common checks:

```bash
cd services/ingest-api
uv run pytest tests/unit -q

cd ../query-api
uv run pytest -q

cd ../../clients/python-ingest
uv run pytest -q

cd ../rust-ingest
cargo fmt --check
cargo test -- --test-threads=1

cd ../../frontend/stream-viewer
npm ci
npm run build
npm run test:e2e
```

See [TESTING.md](TESTING.md) for the full test matrix, including Docker E2E, ESP-IDF builds, QEMU, and HIL expectations.

## Contribution Guidelines

- Keep ingest, query, firmware, and UI responsibilities separated.
- Prefer protobuf-compatible additive changes; avoid breaking existing message fields.
- Keep firmware APIs allocation-conscious and safe for FreeRTOS task boundaries.
- Do not add real credentials, generated build folders, local databases, or screenshots unless intentionally documented assets.
- Update docs with behavior changes. `docs/07-implementation-status.md` should describe what is actually implemented.
- Add or update tests at the same layer as the behavior being changed.

## Commit And PR Expectations

- Keep commits focused by milestone.
- Include test evidence in the PR description.
- Mention whether QEMU/HIL testing was skipped and why.
- For API/protobuf/storage changes, include migration or compatibility notes.
- For security-related changes, avoid public exploit detail until a fix is ready.

## Project Boundaries

Current stable-ish areas:

- Protobuf ingest contract.
- FastAPI ingest/provisioning/control service for restricted networks.
- PostgreSQL/TimescaleDB normalized telemetry storage.
- ESP-IDF upload component and example apps.
- Python and Rust ingest clients.

Active areas:

- Query API authorization and visualization ergonomics.
- Stream viewer dashboard composition.
- Longer-running fleet operations.
- Public-internet hardening.

