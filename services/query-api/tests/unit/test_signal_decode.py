from __future__ import annotations

import struct

import pytest

from aetus_query.signal_decode import Channel, compute_channel_stats, decode_samples


pytestmark = pytest.mark.unit


def test_decode_interleaved_float32_samples() -> None:
    samples = struct.pack("<ffffff", 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    values = decode_samples(
        samples=samples,
        encoding="float32_le",
        layout="interleaved",
        channels=[Channel("x"), Channel("y"), Channel("z")],
        sample_count=2,
    )

    assert values["x"] == pytest.approx([0.1, 0.4])
    assert values["y"] == pytest.approx([0.2, 0.5])
    assert values["z"] == pytest.approx([0.3, 0.6])


def test_decode_planar_int16_samples_with_scale_and_offset() -> None:
    samples = struct.pack("<hhhh", 10, 20, 30, 40)
    values = decode_samples(
        samples=samples,
        encoding="int16_le",
        layout="planar",
        channels=[Channel("x", scale=0.1), Channel("y", offset=-1.0)],
        sample_count=2,
    )

    assert values["x"] == pytest.approx([1.0, 2.0])
    assert values["y"] == pytest.approx([29.0, 39.0])


def test_compute_channel_stats() -> None:
    stats = compute_channel_stats({"x": [1.0, 2.0, 3.0]}, [Channel("x", "g")])

    assert len(stats) == 1
    assert stats[0].key == "x"
    assert stats[0].unit == "g"
    assert stats[0].minimum == 1.0
    assert stats[0].maximum == 3.0
    assert stats[0].average == 2.0
    assert stats[0].sample_count == 3
