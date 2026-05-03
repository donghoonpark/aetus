from __future__ import annotations

import argparse
import math
import struct
from datetime import datetime, timezone

import psycopg


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed AETUS query-api with dense sampled signal data.")
    parser.add_argument("--dsn", default="postgresql://aetus:aetus@127.0.0.1:15432/aetus")
    parser.add_argument("--device-id", default="dense-device-1")
    parser.add_argument("--stream-key", default="dense.vibration")
    parser.add_argument("--points", type=int, default=1_002_000)
    parser.add_argument("--duration-seconds", type=int, default=3600)
    parser.add_argument("--frame-samples", type=int, default=1000)
    parser.add_argument("--channels", type=int, default=1)
    args = parser.parse_args()

    if args.points < 1_000_000:
        raise SystemExit("--points must be at least 1,000,000 for the dense benchmark seed")
    if args.channels < 1:
        raise SystemExit("--channels must be positive")

    seed_dense_query_data(
        dsn=args.dsn,
        device_id=args.device_id,
        stream_key=args.stream_key,
        points=args.points,
        duration_seconds=args.duration_seconds,
        frame_samples=args.frame_samples,
        channel_count=args.channels,
    )


def seed_dense_query_data(
    *,
    dsn: str,
    device_id: str,
    stream_key: str,
    points: int = 1_002_000,
    duration_seconds: int = 3600,
    frame_samples: int = 1000,
    channel_count: int = 1,
) -> None:
    sample_interval_ns = max(1, int(duration_seconds * 1_000_000_000 / points))
    frame_count = math.ceil(points / frame_samples)
    start = datetime(2026, 5, 3, 0, 0, 0, tzinfo=timezone.utc)
    channels_json = "[" + ",".join(
        f'{{"key":"ch{index}","unit":"g"}}' for index in range(channel_count)
    ) + "]"

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO devices(device_id) VALUES (%s) ON CONFLICT (device_id) DO UPDATE SET device_id = EXCLUDED.device_id RETURNING device_pk",
                (device_id,),
            )
            device_pk = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO device_boot_sessions(device_pk, boot_id)
                VALUES (%s, 'boot-dense-seed')
                ON CONFLICT (device_pk, boot_id) DO UPDATE SET first_seen_at = device_boot_sessions.first_seen_at
                RETURNING boot_pk
                """,
                (device_pk,),
            )
            boot_pk = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO signal_stream_definitions(stream_key, encoding, layout, channels_json)
                VALUES (%s, 'float32_le', 'interleaved', %s)
                ON CONFLICT (stream_key, encoding, layout, channels_json)
                DO UPDATE SET stream_key = EXCLUDED.stream_key
                RETURNING signal_pk
                """,
                (stream_key, channels_json),
            )
            signal_pk = cur.fetchone()[0]

            inserted_points = 0
            for frame_index in range(frame_count):
                samples_in_frame = min(frame_samples, points - inserted_points)
                if samples_in_frame <= 0:
                    break
                event_ns_offset = inserted_points * sample_interval_ns
                event_time = start.timestamp() + (event_ns_offset / 1_000_000_000)
                frame_time = datetime.fromtimestamp(event_time, timezone.utc)
                samples = _build_frame_samples(
                    frame_index=frame_index,
                    sample_count=samples_in_frame,
                    channel_count=channel_count,
                )
                cur.execute(
                    """
                    INSERT INTO device_signal_frames(
                        event_time,
                        event_time_ns,
                        received_at,
                        device_pk,
                        boot_pk,
                        signal_pk,
                        sequence,
                        schema_version,
                        sample_interval_ns,
                        sample_count,
                        samples,
                        samples_size,
                        request_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_time, request_id) DO NOTHING
                    """,
                    (
                        frame_time,
                        int(start.timestamp() * 1_000_000_000) + event_ns_offset,
                        frame_time,
                        device_pk,
                        boot_pk,
                        signal_pk,
                        frame_index,
                        sample_interval_ns,
                        samples_in_frame,
                        psycopg.Binary(samples),
                        len(samples),
                        f"dense-{frame_index}",
                    ),
                )
                inserted_points += samples_in_frame
        conn.commit()

    print(
        f"Seeded {inserted_points} points across {frame_count} frames "
        f"for {device_id}/{stream_key} at interval {sample_interval_ns}ns"
    )


def _build_frame_samples(*, frame_index: int, sample_count: int, channel_count: int) -> bytes:
    values = []
    base = frame_index * sample_count
    for sample_index in range(sample_count):
        phase = (base + sample_index) / 200.0
        for channel_index in range(channel_count):
            values.append(math.sin(phase + channel_index) + 0.05 * math.sin(phase * 11.0))
    return struct.pack("<" + ("f" * len(values)), *values)


if __name__ == "__main__":
    main()
