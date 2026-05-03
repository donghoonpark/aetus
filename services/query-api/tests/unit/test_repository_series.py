from __future__ import annotations

import struct
from datetime import datetime, timezone

import pytest

from aetus_query.repository import _raw_frames_to_series


pytestmark = pytest.mark.unit


def test_raw_frames_to_series_returns_samples_when_under_point_budget() -> None:
    response = _raw_frames_to_series(
        "device-1",
        "dense.vibration",
        [
            _frame(
                samples=struct.pack("<ffff", 0.1, 0.2, 0.3, 0.4),
                sample_count=4,
                sample_interval_ns=1_000_000,
            )
        ],
        max_points=10,
    )

    channel = response["channels"][0]
    assert response["mode"] == "samples"
    assert response["source_sample_count"] == 4
    assert [point["value"] for point in channel["points"]] == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert [point["ts"] for point in channel["points"]] == [
        "2026-05-03T00:00:00Z",
        "2026-05-03T00:00:00.001000Z",
        "2026-05-03T00:00:00.002000Z",
        "2026-05-03T00:00:00.003000Z",
    ]


def test_raw_frames_to_series_buckets_samples_to_max_points() -> None:
    response = _raw_frames_to_series(
        "device-1",
        "dense.vibration",
        [
            _frame(
                samples=struct.pack("<" + "f" * 12, *[float(value) for value in range(12)]),
                sample_count=12,
                sample_interval_ns=1_000_000,
            )
        ],
        max_points=3,
    )

    channel = response["channels"][0]
    assert response["mode"] == "envelope"
    assert response["resolution"] == "raw-sample-bucket"
    assert response["source_sample_count"] == 12
    assert len(channel["points"]) == 3
    assert [(point["min"], point["max"]) for point in channel["points"]] == [(0.0, 3.0), (4.0, 7.0), (8.0, 11.0)]


def _frame(*, samples: bytes, sample_count: int, sample_interval_ns: int) -> dict[str, object]:
    return {
        "event_time": datetime(2026, 5, 3, 0, 0, 0, tzinfo=timezone.utc),
        "sample_interval_ns": sample_interval_ns,
        "sample_count": sample_count,
        "samples": samples,
        "encoding": "float32_le",
        "layout": "interleaved",
        "channels_json": '[{"key":"ch0","unit":"g"}]',
    }
