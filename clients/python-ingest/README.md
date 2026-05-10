# AETUS Python Ingest Client

Python producer SDK for AETUS protobuf ingest.

It is intended for gateways, lab tools, server-side simulators, and non-ESP devices that want the same ingest contract as the embedded stack without hand-writing protobuf and HTTP boilerplate.

## Install

```bash
pip install aetus-ingest-client
```

Local development:

```bash
cd clients/python-ingest
uv sync --group dev
```

## Upload Metrics

```python
from aetus_ingest_client import AetusIngestClient

with AetusIngestClient(
    base_url="http://127.0.0.1:18000",
    device_id="python-device-001",
    token="devtok_...",
    boot_id="boot-python-0001",
    firmware_version=42,
) as client:
    client.send_metrics(
        [
            ("temperature", 22.75, "celsius"),
            ("battery_mv", 4012, "mV"),
            ("motion_detected", True),
        ],
        timestamp_ns=1_812_345_678_000_000_000,
    )
```

The client sends `Content-Type: application/x-protobuf`, `X-Device-Id`, and bearer auth headers, then advances its local sequence only after a successful `2xx` response.

## HMAC Authentication

Set `auth_mode="hmac"` when the ingest server is configured with `AETUS_HMAC_AUTH_REQUIRED=true`.

```python
with AetusIngestClient(
    base_url="http://127.0.0.1:18000",
    device_id="python-device-001",
    token="devtok_...",
    boot_id="boot-python-0001",
    auth_mode="hmac",
) as client:
    client.send_metrics([("temperature", 22.75, "celsius")])
```

HMAC mode sends `X-Aetus-Signature: hmac-sha256-v1=<hex>` instead of `Authorization: Bearer ...`. The `token` value is reused as the shared device secret.

## Upload Dense Signal Frames

```python
from aetus_ingest_client import AetusIngestClient, channel

with AetusIngestClient(
    base_url="http://127.0.0.1:18000",
    device_id="python-device-001",
    token="devtok_...",
    boot_id="boot-python-0001",
) as client:
    client.send_signal_frame(
        stream_key="imu.accel",
        sample_interval_ns=5_000_000,
        channels=[
            channel("accel_x", "g"),
            channel("accel_y", "g"),
            channel("accel_z", "g"),
        ],
        samples=[
            [0.1, 0.2, 0.3],
            [0.4, 0.5, 0.6],
            [0.7, 0.8, 0.9],
            [1.0, 1.1, 1.2],
        ],
    )
```

`samples` can be row-based Python values or pre-packed `bytes` when the caller already owns a binary signal buffer.

If NumPy is installed, `samples` can also be a `numpy.ndarray`. In that case the client infers the protobuf signal encoding from `dtype` when `encoding` is omitted:

```python
import numpy as np
from aetus_ingest_client import AetusIngestClient, channel

samples = np.array(
    [
        [120, -12],
        [121, -10],
        [119, -11],
    ],
    dtype=np.int16,
)

with AetusIngestClient(
    base_url="http://127.0.0.1:18000",
    device_id="python-device-001",
    token="devtok_...",
    boot_id="boot-python-0001",
) as client:
    client.send_signal_frame(
        stream_key="adc.raw",
        sample_interval_ns=1_000_000,
        channels=[channel("adc_a", "count"), channel("adc_b", "count")],
        samples=samples,
    )
```

Supported ndarray dtypes map to signal encodings as follows: `float32 -> float32_le`, `int16 -> int16_le`, `uint16 -> uint16_le`, and `int32 -> int32_le`. `float64` arrays are accepted, but the client emits a `RuntimeWarning` and downcasts them to `float32_le` before upload to keep the signal frame wire format compact.

## Tests

```bash
uv run pytest tests/unit -q
uv run pytest tests/e2e -q
```

The e2e suite starts the repository Docker Compose stack, provisions a device token, uploads metric/status/signal frame events, and verifies PostgreSQL normalized rows.
