# AETUS Rust Ingest Client

Rust producer SDK for AETUS protobuf ingest.

It targets gateways, native services, simulators, and high-throughput edge agents that want the same ingest contract as the embedded and Python clients without hand-writing protobuf or HTTP plumbing.

## Usage

```rust
use aetus_ingest_client::{
    metric, AetusIngestClient,
};

let mut client = AetusIngestClient::new(
    "http://127.0.0.1:18000",
    "rust-device-001",
    "devtok_...",
    "boot-rust-0001",
    42,
)?;

client.send_metrics(
    vec![
        metric("temperature", 22.75_f64, "celsius")?,
        metric("battery_mv", 4012_i64, "mV")?,
        metric("motion_detected", true, "")?,
    ],
    Some(1_812_345_678_000_000_000),
)?;
# Ok::<(), Box<dyn std::error::Error>>(())
```

## HMAC Authentication

Use `AuthMode::HmacSha256` when the ingest server is configured with `AETUS_HMAC_AUTH_REQUIRED=true`.

```rust
use aetus_ingest_client::{
    metric, AetusIngestClient, AuthMode,
};

let mut client = AetusIngestClient::with_sequence_and_auth_mode(
    "http://127.0.0.1:18000",
    "rust-device-001",
    "devtok_...",
    "boot-rust-0001",
    42,
    0,
    AuthMode::HmacSha256,
)?;

client.send_metrics(vec![metric("temperature", 22.75_f64, "celsius")?], None)?;
# Ok::<(), Box<dyn std::error::Error>>(())
```

HMAC mode sends `X-Aetus-Signature: hmac-sha256-v1=<hex>` instead of `Authorization: Bearer ...`. The token value is reused as the shared device secret.

## Dense Signal Frames

```rust
use aetus_ingest_client::{
    pack_signal_samples_f32, SignalChannelSpec, SignalLayout,
};

let samples = pack_signal_samples_f32(
    &[
        vec![0.1, 0.2, 0.3],
        vec![0.4, 0.5, 0.6],
        vec![0.7, 0.8, 0.9],
    ],
    SignalLayout::Interleaved,
)?;

client.send_signal_frame(
    "imu.accel",
    5_000_000,
    vec![
        SignalChannelSpec::new("accel_x", "g")?,
        SignalChannelSpec::new("accel_y", "g")?,
        SignalChannelSpec::new("accel_z", "g")?,
    ],
    samples,
    None,
)?;
# Ok::<(), Box<dyn std::error::Error>>(())
```

The client supports `float32_le`, `int16_le`, `uint16_le`, and `int32_le` sample packing with interleaved or planar layout.

## Tests

```bash
cd clients/rust-ingest
cargo test --test unit_client
cargo test --test e2e_pipeline -- --test-threads=1
```

The e2e test starts the repository Docker Compose stack, provisions a token, uploads metric/status/signal frame events, and verifies PostgreSQL raw and normalized rows.

By default, local e2e uses existing compose images to avoid unnecessary base image metadata lookups. Set `AETUS_RUST_E2E_BUILD=1` to force `docker compose up --build`, which is what CI uses.
