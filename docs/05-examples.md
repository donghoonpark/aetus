# Examples

## FastAPI ingest 예제

```python
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from aetus.ingest.v1.ingest_pb2 import (
    EVENT_TYPE_ALERT,
    EVENT_TYPE_STATUS,
    EVENT_TYPE_TELEMETRY,
    IngestEvent,
)

app = FastAPI()


def normalize_payload(event: IngestEvent) -> dict[str, Any]:
    body = event.WhichOneof("body")
    if body == "telemetry":
        telemetry_kind = event.telemetry.WhichOneof("payload")
        if telemetry_kind == "metric_set":
            metrics = []
            for metric in event.telemetry.metric_set.metrics:
                value_kind = metric.WhichOneof("value")
                if value_kind == "int_value":
                    value = metric.int_value
                    value_type = "int"
                elif value_kind == "double_value":
                    value = metric.double_value
                    value_type = "double"
                elif value_kind == "bool_value":
                    value = metric.bool_value
                    value_type = "bool"
                elif value_kind == "string_value":
                    value = metric.string_value
                    value_type = "string"
                elif value_kind == "bytes_value":
                    value = metric.bytes_value.hex()
                    value_type = "bytes_hex"
                else:
                    raise HTTPException(status_code=400, detail="metric value missing")

                metrics.append(
                    {
                        "key": metric.key,
                        "type": value_type,
                        "value": value,
                        "unit": metric.unit or None,
                    }
                )
            return {"kind": "metric_set", "metrics": metrics}

        if telemetry_kind == "signal_frame":
            return {
                "kind": "signal_frame",
                "signal_frame": {
                    "stream_key": event.telemetry.signal_frame.stream_key,
                    "sample_count": event.telemetry.signal_frame.sample_count,
                },
            }

        raise HTTPException(status_code=400, detail="telemetry payload missing")

    if body == "status":
        return {
            "status": int(event.status.status),
            "rssi": event.status.rssi,
            "free_heap": event.status.free_heap,
            "reboot_reason": event.status.reboot_reason or None,
        }

    if body == "alert":
        return {
            "code": event.alert.code,
            "severity": int(event.alert.severity),
            "message": event.alert.message,
        }

    raise HTTPException(status_code=400, detail="body missing")


def event_type_name(event_type: int) -> str:
    if event_type == EVENT_TYPE_TELEMETRY:
        return "telemetry"
    if event_type == EVENT_TYPE_STATUS:
        return "status"
    if event_type == EVENT_TYPE_ALERT:
        return "alert"
    return "unknown"


@app.post("/v1/ingest")
async def ingest(
    request: Request,
    x_device_id: str = Header(..., alias="X-Device-Id"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization")

    raw = await request.body()
    event = IngestEvent()

    try:
        event.ParseFromString(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid protobuf") from exc

    if event.device_id != x_device_id:
        raise HTTPException(status_code=400, detail="device id mismatch")

    if not event.boot_id:
        raise HTTPException(status_code=400, detail="boot_id required")

    # TODO: apply in-memory rate limit here
    # TODO: allow `sequence = 0` because each boot session starts from 0
    # NOTE: in proto3, uint64 defaults to 0, so "missing sequence" and first event are not distinguishable
    # TODO: if device is allowlisted, use a relaxed limit instead of a full bypass

    normalized = {
        "schema_version": event.schema_version,
        "device_id": event.device_id,
        "boot_id": event.boot_id,
        "sequence": event.sequence,
        "event_type": event_type_name(event.event_type),
        "firmware_version": event.firmware_version or None,
        "uptime_ms": event.uptime_ms or None,
        "timestamp_ns": event.timestamp_ns or None,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": normalize_payload(event),
    }

    # TODO: publish `normalized` to Kafka topic `device.raw.v1`

    return {
        "status": "accepted",
        "device_id": event.device_id,
        "sequence": event.sequence,
    }
```

## provisioning API 예제

```python
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI()


class ProvisionRequest(BaseModel):
    hardware_id: str
    model: str
    firmware_version: int | None = None
    site_code: str | None = None


@app.post("/v1/provision", status_code=201)
async def provision(
    payload: ProvisionRequest,
    authorization: str | None = Header(None, alias="Authorization"),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bootstrap authorization")

    # TODO: verify single public bootstrap token
    # TODO: apply in-memory limiter: 1 request / 10 seconds per source IP + hardware_id
    # TODO: verify source IP device-network allowlist + hardware_id from SQLite
    # TODO: create or lookup device registry record in SQLite
    device_id = "esp32c5-001"
    access_token = "devtok_xxxxx"

    return {
        "device_id": device_id,
        "token_type": "Bearer",
        "access_token": access_token,
        "config": {
            "ingest_url": "http://ingest.internal/v1/ingest",
            "kafka_mode": "self-managed",
            "postgres_mode": "self-managed-vm",
            "control_db": "sqlite by default, postgresql for multi-pod",
            "control_db_backup": "sqlite online backup every 1 hour",
            "control_db_scale_up_target": "postgresql control schema for multi-pod or higher write concurrency",
            "bootstrap_rate_limit": "1 request / 10 seconds",
            "ingest_rate_limit": "2 requests / second",
            "allowlist_ingest_rate_limit": "20 requests / second",
            "max_batch_size": 1,
            "retry_backoff_ms": 3000,
        },
    }
```

## protobuf 스키마 초안

```proto
syntax = "proto3";

package aetus.ingest.v1;

enum EventType {
  EVENT_TYPE_UNSPECIFIED = 0;
  EVENT_TYPE_TELEMETRY = 1;
  EVENT_TYPE_STATUS = 2;
  EVENT_TYPE_ALERT = 3;
}

message IngestEvent {
  uint32 schema_version = 1;
  string device_id = 2;
  uint64 sequence = 3;
  EventType event_type = 4;

  string boot_id = 5;
  uint32 firmware_version = 6;
  uint64 uptime_ms = 7;
  uint64 timestamp_ns = 8;

  oneof body {
    TelemetryPayload telemetry = 10;
    StatusPayload status = 11;
    AlertPayload alert = 12;
  }
}

message TelemetryPayload {
  oneof payload {
    MetricSet metric_set = 1;
    SignalFrame signal_frame = 2;
  }
}

message MetricSet {
  repeated Metric metrics = 1;
}

message Metric {
  string key = 1;

  oneof value {
    sint64 int_value = 2;
    double double_value = 3;
    bool bool_value = 4;
    string string_value = 5;
    bytes bytes_value = 6;
  }

  string unit = 7;
}

enum SignalSampleEncoding {
  SIGNAL_SAMPLE_ENCODING_UNSPECIFIED = 0;
  SIGNAL_SAMPLE_ENCODING_FLOAT32_LE = 1;
  SIGNAL_SAMPLE_ENCODING_INT16_LE = 2;
  SIGNAL_SAMPLE_ENCODING_UINT16_LE = 3;
  SIGNAL_SAMPLE_ENCODING_INT32_LE = 4;
}

enum SignalSampleLayout {
  SIGNAL_SAMPLE_LAYOUT_UNSPECIFIED = 0;
  SIGNAL_SAMPLE_LAYOUT_INTERLEAVED = 1;
  SIGNAL_SAMPLE_LAYOUT_PLANAR = 2;
}

message SignalFrame {
  string stream_key = 1;
  uint64 sample_interval_ns = 2;
  uint32 sample_count = 3;
  SignalSampleEncoding encoding = 4;
  SignalSampleLayout layout = 5;
  repeated SignalChannel channels = 6;
  bytes samples = 7;
}

message SignalChannel {
  string key = 1;
  string unit = 2;
  optional float scale = 3;
  optional float offset = 4;
}
```

## ESP32 nanopb 예제

packed version helper:

```c
#include <stdint.h>

static uint32_t pack_version_u32(uint8_t major, uint8_t minor, uint16_t patch) {
    return ((uint32_t)major << 24) | ((uint32_t)minor << 16) | (uint32_t)patch;
}

static void make_boot_id(char *out, size_t out_size, uint32_t boot_counter) {
    snprintf(out, out_size, "boot-%08" PRIx32, boot_counter);
}
```

provisioning용 `hardware_id` 예시:

```c
static void make_hardware_id(char *out, size_t out_size, const uint8_t mac[6]) {
    snprintf(
        out,
        out_size,
        "esp32c5-%02x%02x%02x%02x%02x%02x",
        mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]
    );
}
```

메시지 채우기 + 직렬화:

```c
#include <pb_encode.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include "ingest.pb.h"

bool build_telemetry_event(uint8_t *out_buf, size_t out_buf_size, size_t *encoded_size) {
    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;

    event.schema_version = 1;
    strncpy(event.device_id, "esp32c5-001", sizeof(event.device_id) - 1);
    make_boot_id(event.boot_id, sizeof(event.boot_id), 1);
    event.sequence = 0;
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_TELEMETRY;
    event.firmware_version = pack_version_u32(1, 2, 3);
    event.uptime_ms = 5320123;
    event.timestamp_ns = 1777242001000000000ULL;

    event.which_body = aetus_ingest_v1_IngestEvent_telemetry_tag;
    event.body.telemetry.which_payload = aetus_ingest_v1_TelemetryPayload_metric_set_tag;
    event.body.telemetry.payload.metric_set.metrics_count = 3;

    aetus_ingest_v1_Metric *m0 = &event.body.telemetry.payload.metric_set.metrics[0];
    strncpy(m0->key, "temperature", sizeof(m0->key) - 1);
    m0->which_value = aetus_ingest_v1_Metric_double_value_tag;
    m0->value.double_value = 21.4;
    strncpy(m0->unit, "celsius", sizeof(m0->unit) - 1);

    aetus_ingest_v1_Metric *m1 = &event.body.telemetry.payload.metric_set.metrics[1];
    strncpy(m1->key, "humidity", sizeof(m1->key) - 1);
    m1->which_value = aetus_ingest_v1_Metric_double_value_tag;
    m1->value.double_value = 44.8;
    strncpy(m1->unit, "percent", sizeof(m1->unit) - 1);

    aetus_ingest_v1_Metric *m2 = &event.body.telemetry.payload.metric_set.metrics[2];
    strncpy(m2->key, "battery", sizeof(m2->key) - 1);
    m2->which_value = aetus_ingest_v1_Metric_double_value_tag;
    m2->value.double_value = 3.82;
    strncpy(m2->unit, "volt", sizeof(m2->unit) - 1);

    pb_ostream_t stream = pb_ostream_from_buffer(out_buf, out_buf_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}
```

HTTP 전송 흐름:

```c
uint8_t buf[256];
size_t encoded_size = 0;

if (build_telemetry_event(buf, sizeof(buf), &encoded_size)) {
    // POST /v1/ingest
    // Content-Type: application/x-protobuf
    // X-Device-Id: esp32c5-001
    // Authorization: Bearer <device-token>
    // default transport: http
    // if https is used, certificate verification is disabled in device client
    // sequence starts from 0 for each boot session
    // body = buf[0:encoded_size]
}
```

## nanopb `.options` 예시

```text
IngestEvent.device_id max_size:32
IngestEvent.boot_id max_size:32
Metric.key max_size:20
Metric.unit max_size:8
MetricSet.metrics max_count:32
SignalFrame.stream_key max_size:32
SignalFrame.channels type:FT_CALLBACK
SignalFrame.samples type:FT_CALLBACK
SignalChannel.key max_size:20
SignalChannel.unit max_size:8
StatusPayload.reboot_reason max_size:24
AlertPayload.code max_size:24
AlertPayload.message max_size:80
```
