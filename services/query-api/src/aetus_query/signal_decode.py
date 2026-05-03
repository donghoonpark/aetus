from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Channel:
    key: str
    unit: str | None = None
    scale: float | None = None
    offset: float | None = None


@dataclass(frozen=True, slots=True)
class ChannelStats:
    key: str
    unit: str | None
    minimum: float
    maximum: float
    average: float
    rms: float
    stddev: float
    peak_abs: float
    sample_count: int


_ENCODING_FORMATS = {
    "float32_le": ("<f", 4),
    "int16_le": ("<h", 2),
    "uint16_le": ("<H", 2),
    "int32_le": ("<i", 4),
}


def sample_width_bytes(encoding: str) -> int:
    try:
        return _ENCODING_FORMATS[encoding][1]
    except KeyError as exc:
        raise ValueError(f"unsupported signal encoding: {encoding}") from exc


def decode_samples(
    *,
    samples: bytes,
    encoding: str,
    layout: str,
    channels: list[Channel],
    sample_count: int,
) -> dict[str, list[float]]:
    if not channels:
        raise ValueError("channels are required")
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    try:
        fmt, width = _ENCODING_FORMATS[encoding]
    except KeyError as exc:
        raise ValueError(f"unsupported signal encoding: {encoding}") from exc
    expected_size = sample_count * len(channels) * width
    if len(samples) != expected_size:
        raise ValueError(f"sample size mismatch: expected {expected_size}, got {len(samples)}")
    if layout not in {"interleaved", "planar"}:
        raise ValueError(f"unsupported signal layout: {layout}")

    values = {channel.key: [] for channel in channels}
    offset = 0
    if layout == "interleaved":
        for _ in range(sample_count):
            for channel in channels:
                raw_value = struct.unpack_from(fmt, samples, offset)[0]
                values[channel.key].append(_apply_affine(float(raw_value), channel))
                offset += width
    else:
        for channel in channels:
            for _ in range(sample_count):
                raw_value = struct.unpack_from(fmt, samples, offset)[0]
                values[channel.key].append(_apply_affine(float(raw_value), channel))
                offset += width
    return values


def compute_channel_stats(values_by_channel: dict[str, list[float]], channels: Iterable[Channel]) -> list[ChannelStats]:
    stats = []
    channel_by_key = {channel.key: channel for channel in channels}
    for key, values in values_by_channel.items():
        if not values:
            continue
        total = sum(values)
        sample_count = len(values)
        average = total / sample_count
        square_average = sum(value * value for value in values) / sample_count
        variance = sum((value - average) ** 2 for value in values) / sample_count
        channel = channel_by_key[key]
        stats.append(
            ChannelStats(
                key=key,
                unit=channel.unit,
                minimum=min(values),
                maximum=max(values),
                average=average,
                rms=math.sqrt(square_average),
                stddev=math.sqrt(variance),
                peak_abs=max(abs(value) for value in values),
                sample_count=sample_count,
            )
        )
    return stats


def _apply_affine(value: float, channel: Channel) -> float:
    if channel.scale is not None:
        value *= channel.scale
    if channel.offset is not None:
        value += channel.offset
    return value
