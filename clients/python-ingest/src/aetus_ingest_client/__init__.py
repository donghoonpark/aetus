"""Python client SDK for AETUS protobuf ingest."""

from aetus_ingest_client.client import (
    AetusIngestClient,
    AuthMode,
    IngestClientError,
    IngestResponse,
    SignalChannelSpec,
    build_alert_event,
    build_metric_event,
    build_signal_frame_event,
    build_status_event,
    channel,
    metric,
    pack_signal_samples,
)

__all__ = [
    "AetusIngestClient",
    "AuthMode",
    "IngestClientError",
    "IngestResponse",
    "SignalChannelSpec",
    "build_alert_event",
    "build_metric_event",
    "build_signal_frame_event",
    "build_status_event",
    "channel",
    "metric",
    "pack_signal_samples",
]
