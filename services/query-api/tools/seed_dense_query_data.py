from __future__ import annotations

import argparse
import math
import struct
from datetime import datetime, timedelta, timezone

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
    parser.add_argument(
        "--start-iso",
        default="2026-05-03T00:00:00Z",
        help="UTC start time for generated samples. Use an ISO-8601 value such as 2026-05-03T00:00:00Z.",
    )
    parser.add_argument(
        "--skip-scalar-types",
        action="store_true",
        help="Only seed the dense sampled stream; by default the dataset also includes double/int/bool/string scalar streams.",
    )
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
        include_scalar_types=not args.skip_scalar_types,
        start=parse_start_iso(args.start_iso),
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
    include_scalar_types: bool = True,
    start: datetime | None = None,
) -> None:
    sample_interval_ns = max(1, int(duration_seconds * 1_000_000_000 / points))
    frame_count = math.ceil(points / frame_samples)
    start = start or datetime(2026, 5, 3, 0, 0, 0, tzinfo=timezone.utc)
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
            if include_scalar_types:
                _seed_scalar_type_examples(cur, device_pk, boot_pk, start, duration_seconds)

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
    if include_scalar_types:
        print("Seeded scalar type examples: env.temperature, env.humidity, motor.rpm, pump.enabled, machine.state")


def _build_frame_samples(*, frame_index: int, sample_count: int, channel_count: int) -> bytes:
    values = []
    base = frame_index * sample_count
    for sample_index in range(sample_count):
        phase = (base + sample_index) / 200.0
        for channel_index in range(channel_count):
            values.append(math.sin(phase + channel_index) + 0.05 * math.sin(phase * 11.0))
    return struct.pack("<" + ("f" * len(values)), *values)


def _seed_scalar_type_examples(
    cur: psycopg.Cursor,
    device_pk: int,
    boot_pk: int,
    start: datetime,
    duration_seconds: int,
) -> None:
    metrics = [
        ("env.temperature", "celsius", "double", "value_double"),
        ("env.humidity", "percent", "float", "value_double"),
        ("motor.rpm", "rpm", "int", "value_int"),
        ("pump.enabled", "unitless", "bool", "value_bool"),
        ("machine.state", "unitless", "string", "value_string"),
    ]
    step_seconds = 10
    sample_count = max(1, math.floor(duration_seconds / step_seconds) + 1)
    for metric_index, (metric_key, metric_unit, value_type, value_column) in enumerate(metrics):
        cur.execute(
            """
            INSERT INTO metric_definitions(metric_key, metric_unit, value_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (metric_key, metric_unit, value_type)
            DO UPDATE SET metric_key = EXCLUDED.metric_key
            RETURNING metric_pk
            """,
            (metric_key, metric_unit, value_type),
        )
        metric_pk = cur.fetchone()[0]
        for sample_index in range(sample_count):
            offset_seconds = sample_index * step_seconds
            event_time = start + timedelta(seconds=offset_seconds)
            value = _scalar_example_value(metric_key, sample_index, offset_seconds, duration_seconds)
            cur.execute(
                f"""
                INSERT INTO device_metric_points(
                    event_time,
                    received_at,
                    device_pk,
                    boot_pk,
                    metric_pk,
                    sequence,
                    metric_index,
                    schema_version,
                    {value_column},
                    request_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                ON CONFLICT (event_time, request_id, metric_index) DO NOTHING
                """,
                (
                    event_time,
                    event_time,
                    device_pk,
                    boot_pk,
                    metric_pk,
                    sample_index,
                    metric_index,
                    value,
                    f"dense-scalar-type-{device_pk}-{metric_key}-{sample_index}",
                ),
            )


def _scalar_example_value(metric_key: str, sample_index: int, offset_seconds: int, duration_seconds: int) -> float | int | bool | str:
    if metric_key == "env.temperature":
        return 23.5 + 1.5 * math.sin(offset_seconds / 300.0)
    if metric_key == "env.humidity":
        return 48.0 + 5.0 * math.sin(offset_seconds / 420.0)
    if metric_key == "motor.rpm":
        return 1725 + int(120 * math.sin(offset_seconds / 180.0))
    if metric_key == "pump.enabled":
        return (sample_index // 12) % 2 == 0
    if metric_key == "machine.state":
        if offset_seconds < duration_seconds * 0.1:
            return "warming"
        if offset_seconds > duration_seconds * 0.9:
            return "cooldown"
        return "running"
    raise ValueError(f"Unsupported scalar metric key: {metric_key}")


def parse_start_iso(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    main()
