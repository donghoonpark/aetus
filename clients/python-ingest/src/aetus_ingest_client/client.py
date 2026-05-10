from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import struct
from time import time_ns
from typing import Any, Literal
from uuid import uuid4
import warnings

import httpx

from aetus_ingest_client.generated import ingest_pb2


SignalEncoding = Literal["float32_le", "int16_le", "uint16_le", "int32_le"]
SignalEncodingInput = SignalEncoding | Literal["auto"]
SignalLayout = Literal["interleaved", "planar"]
AuthMode = Literal["bearer", "hmac"]
MetricInput = ingest_pb2.Metric | tuple[str, Any] | tuple[str, Any, str]
HMAC_SIGNATURE_SCHEME = "hmac-sha256-v1"
HMAC_SIGNATURE_PREFIX = "AETUS-HMAC-SHA256-V1"


ENCODING_TO_PROTO = {
    "float32_le": ingest_pb2.SIGNAL_SAMPLE_ENCODING_FLOAT32_LE,
    "int16_le": ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT16_LE,
    "uint16_le": ingest_pb2.SIGNAL_SAMPLE_ENCODING_UINT16_LE,
    "int32_le": ingest_pb2.SIGNAL_SAMPLE_ENCODING_INT32_LE,
}
ENCODING_STRUCT_FORMAT = {
    "float32_le": "<f",
    "int16_le": "<h",
    "uint16_le": "<H",
    "int32_le": "<i",
}
ENCODING_NUMPY_DTYPE = {
    "float32_le": "<f4",
    "int16_le": "<i2",
    "uint16_le": "<u2",
    "int32_le": "<i4",
}
LAYOUT_TO_PROTO = {
    "interleaved": ingest_pb2.SIGNAL_SAMPLE_LAYOUT_INTERLEAVED,
    "planar": ingest_pb2.SIGNAL_SAMPLE_LAYOUT_PLANAR,
}
DEVICE_STATUS_TO_PROTO = {
    "online": ingest_pb2.DEVICE_STATUS_ONLINE,
    "degraded": ingest_pb2.DEVICE_STATUS_DEGRADED,
    "offline": ingest_pb2.DEVICE_STATUS_OFFLINE,
}
SEVERITY_TO_PROTO = {
    "info": ingest_pb2.SEVERITY_INFO,
    "warn": ingest_pb2.SEVERITY_WARN,
    "error": ingest_pb2.SEVERITY_ERROR,
    "critical": ingest_pb2.SEVERITY_CRITICAL,
}


class IngestClientError(RuntimeError):
    """Raised when the ingest API rejects an upload."""

    def __init__(self, status_code: int, message: str, response_text: str) -> None:
        super().__init__(f"AETUS ingest failed with HTTP {status_code}: {message}")
        self.status_code = status_code
        self.response_text = response_text


@dataclass(frozen=True, slots=True)
class IngestResponse:
    status_code: int
    request_id: str | None
    device_id: str | None
    sequence: int | None
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SignalChannelSpec:
    key: str
    unit: str = ""
    scale: float | None = None
    offset: float | None = None


def metric(key: str, value: Any, unit: str = "") -> ingest_pb2.Metric:
    """Create a protobuf Metric from a Python scalar."""
    if not key:
        raise ValueError("metric key is required")

    item = ingest_pb2.Metric(key=key, unit=unit)
    if isinstance(value, bool):
        item.bool_value = value
    elif isinstance(value, int):
        item.int_value = value
    elif isinstance(value, float):
        item.double_value = value
    elif isinstance(value, str):
        item.string_value = value
    elif isinstance(value, bytes):
        item.bytes_value = value
    elif isinstance(value, bytearray):
        item.bytes_value = bytes(value)
    else:
        raise TypeError(f"unsupported metric value type: {type(value).__name__}")
    return item


def channel(key: str, unit: str = "", *, scale: float | None = None, offset: float | None = None) -> SignalChannelSpec:
    if not key:
        raise ValueError("signal channel key is required")
    return SignalChannelSpec(key=key, unit=unit, scale=scale, offset=offset)


def _coerce_metric(item: MetricInput) -> ingest_pb2.Metric:
    if isinstance(item, ingest_pb2.Metric):
        return item
    if len(item) == 2:
        key, value = item
        unit = ""
    elif len(item) == 3:
        key, value, unit = item
    else:
        raise ValueError("metric tuples must be (key, value) or (key, value, unit)")
    return metric(str(key), value, str(unit))


def _base_event(
    *,
    device_id: str,
    sequence: int,
    event_type: int,
    boot_id: str,
    firmware_version: int = 0,
    uptime_ms: int = 0,
    timestamp_ns: int | None = None,
    schema_version: int = 1,
) -> ingest_pb2.IngestEvent:
    if not device_id:
        raise ValueError("device_id is required")
    if not boot_id:
        raise ValueError("boot_id is required")
    if sequence < 0:
        raise ValueError("sequence must be non-negative")

    event = ingest_pb2.IngestEvent(
        schema_version=schema_version,
        device_id=device_id,
        sequence=sequence,
        event_type=event_type,
        boot_id=boot_id,
        firmware_version=firmware_version,
        uptime_ms=uptime_ms,
    )
    if timestamp_ns is not None:
        event.timestamp_ns = timestamp_ns
    return event


def build_metric_event(
    *,
    device_id: str,
    sequence: int,
    boot_id: str,
    metrics: Iterable[MetricInput],
    firmware_version: int = 0,
    uptime_ms: int = 0,
    timestamp_ns: int | None = None,
    schema_version: int = 1,
) -> ingest_pb2.IngestEvent:
    event = _base_event(
        device_id=device_id,
        sequence=sequence,
        event_type=ingest_pb2.EVENT_TYPE_TELEMETRY,
        boot_id=boot_id,
        firmware_version=firmware_version,
        uptime_ms=uptime_ms,
        timestamp_ns=timestamp_ns,
        schema_version=schema_version,
    )
    event.telemetry.metric_set.metrics.extend(_coerce_metric(item) for item in metrics)
    if not event.telemetry.metric_set.metrics:
        raise ValueError("at least one metric is required")
    return event


def _coerce_channel(item: SignalChannelSpec | str) -> ingest_pb2.SignalChannel:
    spec = SignalChannelSpec(item) if isinstance(item, str) else item
    proto = ingest_pb2.SignalChannel(key=spec.key, unit=spec.unit)
    if spec.scale is not None:
        proto.scale = spec.scale
    if spec.offset is not None:
        proto.offset = spec.offset
    return proto


def pack_signal_samples(
    samples: Any,
    *,
    encoding: SignalEncodingInput = "auto",
    layout: SignalLayout = "interleaved",
) -> bytes:
    """Pack rows of channel samples into the SignalFrame byte layout."""
    return _pack_signal_samples_with_metadata(samples, encoding=encoding, layout=layout)[0]


def _pack_signal_samples_with_metadata(
    samples: Any,
    *,
    encoding: SignalEncodingInput = "auto",
    layout: SignalLayout = "interleaved",
) -> tuple[bytes, int, SignalEncoding]:
    if encoding not in ENCODING_STRUCT_FORMAT:
        if encoding != "auto":
            raise ValueError(f"unsupported signal encoding: {encoding}")
    if layout not in LAYOUT_TO_PROTO:
        raise ValueError(f"unsupported signal layout: {layout}")
    if _looks_like_numpy_array(samples):
        return _pack_numpy_signal_samples(samples, encoding=encoding, layout=layout)
    if not samples:
        raise ValueError("at least one signal sample row is required")

    resolved_encoding: SignalEncoding = "float32_le" if encoding == "auto" else encoding
    channel_count = len(samples[0])
    if channel_count == 0:
        raise ValueError("signal samples must contain at least one channel")
    if any(len(row) != channel_count for row in samples):
        raise ValueError("all signal sample rows must have the same channel count")

    ordered_values: list[float | int] = []
    if layout == "interleaved":
        ordered_values = [value for row in samples for value in row]
    else:
        for channel_index in range(channel_count):
            ordered_values.extend(row[channel_index] for row in samples)

    pack = struct.Struct(ENCODING_STRUCT_FORMAT[resolved_encoding]).pack
    return b"".join(pack(value) for value in ordered_values), len(samples), resolved_encoding


def _looks_like_numpy_array(value: Any) -> bool:
    return (
        value.__class__.__module__.startswith("numpy")
        and hasattr(value, "dtype")
        and hasattr(value, "ndim")
        and hasattr(value, "shape")
    )


def _pack_numpy_signal_samples(
    samples: Any,
    *,
    encoding: SignalEncodingInput,
    layout: SignalLayout,
) -> tuple[bytes, int, SignalEncoding]:
    import numpy as np

    array = np.asarray(samples)
    if array.ndim == 1:
        sample_count = int(array.shape[0])
        channel_count = 1
        rows = array.reshape(sample_count, 1)
    elif array.ndim == 2:
        sample_count = int(array.shape[0])
        channel_count = int(array.shape[1])
        rows = array
    else:
        raise ValueError("signal ndarray samples must be 1D or 2D")
    if sample_count <= 0:
        raise ValueError("at least one signal sample row is required")
    if channel_count <= 0:
        raise ValueError("signal samples must contain at least one channel")

    resolved_encoding = _infer_numpy_signal_encoding(array.dtype) if encoding == "auto" else encoding
    target_dtype = np.dtype(ENCODING_NUMPY_DTYPE[resolved_encoding])
    ordered = rows if layout == "interleaved" else rows.T
    return np.ascontiguousarray(ordered, dtype=target_dtype).tobytes(), sample_count, resolved_encoding


def _infer_numpy_signal_encoding(dtype: Any) -> SignalEncoding:
    kind = dtype.kind
    itemsize = dtype.itemsize
    if kind == "f" and itemsize == 4:
        return "float32_le"
    if kind == "f" and itemsize == 8:
        warnings.warn(
            "float64 signal ndarray samples are downcast to float32_le; "
            "pass samples.astype('float32') to make this explicit",
            RuntimeWarning,
            stacklevel=3,
        )
        return "float32_le"
    if kind == "i" and itemsize == 2:
        return "int16_le"
    if kind == "u" and itemsize == 2:
        return "uint16_le"
    if kind == "i" and itemsize == 4:
        return "int32_le"
    raise ValueError(f"unsupported signal ndarray dtype: {dtype}")


def build_signal_frame_event(
    *,
    device_id: str,
    sequence: int,
    boot_id: str,
    stream_key: str,
    sample_interval_ns: int,
    channels: Sequence[SignalChannelSpec | str],
    samples: Sequence[Sequence[float | int]] | bytes | Any,
    sample_count: int | None = None,
    encoding: SignalEncodingInput = "auto",
    layout: SignalLayout = "interleaved",
    firmware_version: int = 0,
    uptime_ms: int = 0,
    timestamp_ns: int | None = None,
    schema_version: int = 1,
) -> ingest_pb2.IngestEvent:
    if not stream_key:
        raise ValueError("stream_key is required")
    if sample_interval_ns <= 0:
        raise ValueError("sample_interval_ns must be positive")
    if not channels:
        raise ValueError("at least one signal channel is required")
    if encoding != "auto" and encoding not in ENCODING_TO_PROTO:
        raise ValueError(f"unsupported signal encoding: {encoding}")
    if layout not in LAYOUT_TO_PROTO:
        raise ValueError(f"unsupported signal layout: {layout}")

    event = _base_event(
        device_id=device_id,
        sequence=sequence,
        event_type=ingest_pb2.EVENT_TYPE_TELEMETRY,
        boot_id=boot_id,
        firmware_version=firmware_version,
        uptime_ms=uptime_ms,
        timestamp_ns=timestamp_ns,
        schema_version=schema_version,
    )

    if isinstance(samples, bytes):
        if sample_count is None or sample_count <= 0:
            raise ValueError("sample_count is required when samples are already packed bytes")
        packed_samples = samples
        resolved_sample_count = sample_count
        resolved_encoding = "float32_le" if encoding == "auto" else encoding
    else:
        packed_samples, resolved_sample_count, resolved_encoding = _pack_signal_samples_with_metadata(
            samples,
            encoding=encoding,
            layout=layout,
        )
        if sample_count is not None and sample_count != resolved_sample_count:
            raise ValueError("sample_count must match the number of sample rows")
        if not _looks_like_numpy_array(samples) and samples and len(samples[0]) != len(channels):
            raise ValueError("sample row width must match channel count")
        if _looks_like_numpy_array(samples):
            inferred_channel_count = 1 if samples.ndim == 1 else int(samples.shape[1])
            if inferred_channel_count != len(channels):
                raise ValueError("sample row width must match channel count")

    frame = event.telemetry.signal_frame
    frame.stream_key = stream_key
    frame.sample_interval_ns = sample_interval_ns
    frame.sample_count = resolved_sample_count
    frame.encoding = ENCODING_TO_PROTO[resolved_encoding]
    frame.layout = LAYOUT_TO_PROTO[layout]
    frame.channels.extend(_coerce_channel(item) for item in channels)
    frame.samples = packed_samples
    return event


def build_status_event(
    *,
    device_id: str,
    sequence: int,
    boot_id: str,
    status: Literal["online", "degraded", "offline"] = "online",
    rssi: int = 0,
    free_heap: int = 0,
    reboot_reason: str = "",
    firmware_version: int = 0,
    uptime_ms: int = 0,
    timestamp_ns: int | None = None,
    schema_version: int = 1,
) -> ingest_pb2.IngestEvent:
    if status not in DEVICE_STATUS_TO_PROTO:
        raise ValueError(f"unsupported device status: {status}")
    event = _base_event(
        device_id=device_id,
        sequence=sequence,
        event_type=ingest_pb2.EVENT_TYPE_STATUS,
        boot_id=boot_id,
        firmware_version=firmware_version,
        uptime_ms=uptime_ms,
        timestamp_ns=timestamp_ns,
        schema_version=schema_version,
    )
    event.status.status = DEVICE_STATUS_TO_PROTO[status]
    event.status.rssi = rssi
    event.status.free_heap = free_heap
    event.status.reboot_reason = reboot_reason
    return event


def build_alert_event(
    *,
    device_id: str,
    sequence: int,
    boot_id: str,
    code: str,
    severity: Literal["info", "warn", "error", "critical"],
    message: str,
    firmware_version: int = 0,
    uptime_ms: int = 0,
    timestamp_ns: int | None = None,
    schema_version: int = 1,
) -> ingest_pb2.IngestEvent:
    if severity not in SEVERITY_TO_PROTO:
        raise ValueError(f"unsupported severity: {severity}")
    event = _base_event(
        device_id=device_id,
        sequence=sequence,
        event_type=ingest_pb2.EVENT_TYPE_ALERT,
        boot_id=boot_id,
        firmware_version=firmware_version,
        uptime_ms=uptime_ms,
        timestamp_ns=timestamp_ns,
        schema_version=schema_version,
    )
    event.alert.code = code
    event.alert.severity = SEVERITY_TO_PROTO[severity]
    event.alert.message = message
    return event


class AetusIngestClient:
    """Small synchronous client for uploading AETUS protobuf events."""

    def __init__(
        self,
        *,
        base_url: str,
        device_id: str,
        token: str,
        boot_id: str | None = None,
        firmware_version: int = 0,
        initial_sequence: int = 0,
        auth_mode: AuthMode = "bearer",
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not token:
            raise ValueError("token is required")
        if auth_mode not in {"bearer", "hmac"}:
            raise ValueError("auth_mode must be 'bearer' or 'hmac'")
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self.token = token
        self.boot_id = boot_id or f"py-{time_ns()}-{uuid4().hex[:8]}"
        self.firmware_version = firmware_version
        self.sequence = initial_sequence
        self.auth_mode = auth_mode
        self._client = http_client or httpx.Client(timeout=timeout)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> AetusIngestClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def send_event(self, event: ingest_pb2.IngestEvent) -> IngestResponse:
        body = event.SerializeToString()
        headers = self._headers(event.device_id, body)
        response = self._client.post(
            f"{self.base_url}/v1/ingest",
            content=body,
            headers=headers,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise IngestClientError(response.status_code, response.reason_phrase, response.text)

        if event.device_id == self.device_id and event.sequence == self.sequence:
            self.sequence += 1

        parsed = response.json()
        return IngestResponse(
            status_code=response.status_code,
            request_id=response.headers.get("X-Request-Id") or parsed.get("request_id"),
            device_id=parsed.get("device_id"),
            sequence=parsed.get("sequence"),
            body=parsed,
        )

    def _headers(self, device_id: str, body: bytes) -> dict[str, str]:
        headers = {
            "Content-Type": "application/x-protobuf",
            "X-Device-Id": device_id,
        }
        if self.auth_mode == "hmac":
            headers["X-Aetus-Signature"] = self.hmac_signature(device_id=device_id, body=body)
        else:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def hmac_signature(self, *, device_id: str, body: bytes) -> str:
        body_sha256_hex = hashlib.sha256(body).hexdigest()
        signing_input = f"{HMAC_SIGNATURE_PREFIX}\nPOST\n/v1/ingest\n{device_id}\n{body_sha256_hex}".encode(
            "utf-8"
        )
        signature = hmac.new(self.token.encode("utf-8"), signing_input, hashlib.sha256).hexdigest()
        return f"{HMAC_SIGNATURE_SCHEME}={signature}"

    def send_metrics(
        self,
        metrics: Iterable[MetricInput],
        *,
        uptime_ms: int = 0,
        timestamp_ns: int | None = None,
    ) -> IngestResponse:
        event = build_metric_event(
            device_id=self.device_id,
            sequence=self.sequence,
            boot_id=self.boot_id,
            firmware_version=self.firmware_version,
            uptime_ms=uptime_ms,
            timestamp_ns=timestamp_ns,
            metrics=metrics,
        )
        return self.send_event(event)

    def send_signal_frame(
        self,
        *,
        stream_key: str,
        sample_interval_ns: int,
        channels: Sequence[SignalChannelSpec | str],
        samples: Sequence[Sequence[float | int]] | bytes | Any,
        sample_count: int | None = None,
        encoding: SignalEncodingInput = "auto",
        layout: SignalLayout = "interleaved",
        uptime_ms: int = 0,
        timestamp_ns: int | None = None,
    ) -> IngestResponse:
        event = build_signal_frame_event(
            device_id=self.device_id,
            sequence=self.sequence,
            boot_id=self.boot_id,
            stream_key=stream_key,
            sample_interval_ns=sample_interval_ns,
            channels=channels,
            samples=samples,
            sample_count=sample_count,
            encoding=encoding,
            layout=layout,
            firmware_version=self.firmware_version,
            uptime_ms=uptime_ms,
            timestamp_ns=timestamp_ns,
        )
        return self.send_event(event)

    def send_status(
        self,
        *,
        status: Literal["online", "degraded", "offline"] = "online",
        rssi: int = 0,
        free_heap: int = 0,
        reboot_reason: str = "",
        uptime_ms: int = 0,
        timestamp_ns: int | None = None,
    ) -> IngestResponse:
        event = build_status_event(
            device_id=self.device_id,
            sequence=self.sequence,
            boot_id=self.boot_id,
            status=status,
            rssi=rssi,
            free_heap=free_heap,
            reboot_reason=reboot_reason,
            firmware_version=self.firmware_version,
            uptime_ms=uptime_ms,
            timestamp_ns=timestamp_ns,
        )
        return self.send_event(event)

    def send_alert(
        self,
        *,
        code: str,
        severity: Literal["info", "warn", "error", "critical"],
        message: str,
        uptime_ms: int = 0,
        timestamp_ns: int | None = None,
    ) -> IngestResponse:
        event = build_alert_event(
            device_id=self.device_id,
            sequence=self.sequence,
            boot_id=self.boot_id,
            code=code,
            severity=severity,
            message=message,
            firmware_version=self.firmware_version,
            uptime_ms=uptime_ms,
            timestamp_ns=timestamp_ns,
        )
        return self.send_event(event)
