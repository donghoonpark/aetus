from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from aetus_query.signal_decode import Channel, compute_channel_stats, decode_samples
from aetus_query.time_utils import to_iso8601


@dataclass(frozen=True, slots=True)
class StreamRef:
    key: str
    kind: str
    unit: str | None
    latest_event_time: datetime
    encoding: str | None = None
    layout: str | None = None
    channels: list[dict[str, Any]] | None = None
    nominal_rate_hz: float | None = None


class QueryRepository:
    def list_streams(self, device_id: str) -> list[StreamRef]: ...

    def scalar_series(self, device_id: str, key: str, start: datetime, end: datetime, max_points: int) -> dict[str, Any]: ...

    def sampled_series(self, device_id: str, key: str, start: datetime, end: datetime, max_points: int) -> dict[str, Any]: ...

    def summary(
        self,
        device_id: str,
        key: str,
        start: datetime,
        end: datetime,
        *,
        feature_ttl_seconds: int,
    ) -> dict[str, Any]: ...

    def frames(self, device_id: str, key: str, start: datetime, end: datetime) -> dict[str, Any]: ...


class PostgresQueryRepository(QueryRepository):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def list_streams(self, device_id: str) -> list[StreamRef]:
        with self._connect() as conn:
            scalar_rows = conn.execute(
                """
                SELECT
                    md.metric_key AS key,
                    md.metric_unit AS unit,
                    md.value_type AS value_type,
                    MAX(p.event_time) AS latest_event_time
                FROM device_metric_points p
                JOIN devices d ON d.device_pk = p.device_pk
                JOIN metric_definitions md ON md.metric_pk = p.metric_pk
                WHERE d.device_id = %s
                GROUP BY md.metric_key, md.metric_unit, md.value_type
                ORDER BY md.metric_key
                """,
                (device_id,),
            ).fetchall()
            sampled_rows = conn.execute(
                """
                SELECT
                    sd.stream_key AS key,
                    sd.encoding,
                    sd.layout,
                    sd.channels_json,
                    MAX(f.event_time) AS latest_event_time,
                    MIN(f.sample_interval_ns) AS sample_interval_ns
                FROM device_signal_frames f
                JOIN devices d ON d.device_pk = f.device_pk
                JOIN signal_stream_definitions sd ON sd.signal_pk = f.signal_pk
                WHERE d.device_id = %s
                GROUP BY sd.stream_key, sd.encoding, sd.layout, sd.channels_json
                ORDER BY sd.stream_key
                """,
                (device_id,),
            ).fetchall()

        streams = [
            StreamRef(
                key=row["key"],
                kind="scalar",
                unit=row["unit"] or None,
                latest_event_time=row["latest_event_time"],
            )
            for row in scalar_rows
        ]
        streams.extend(
            StreamRef(
                key=row["key"],
                kind="sampled",
                unit=_common_unit(json.loads(row["channels_json"])),
                latest_event_time=row["latest_event_time"],
                encoding=row["encoding"],
                layout=row["layout"],
                channels=json.loads(row["channels_json"]),
                nominal_rate_hz=(1_000_000_000 / row["sample_interval_ns"]) if row["sample_interval_ns"] else None,
            )
            for row in sampled_rows
        )
        return streams

    def scalar_series(self, device_id: str, key: str, start: datetime, end: datetime, max_points: int) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.event_time,
                    md.metric_unit,
                    COALESCE(p.value_double, p.value_int::DOUBLE PRECISION, CASE WHEN p.value_bool THEN 1.0 ELSE 0.0 END) AS value
                FROM device_metric_points p
                JOIN devices d ON d.device_pk = p.device_pk
                JOIN metric_definitions md ON md.metric_pk = p.metric_pk
                WHERE d.device_id = %s
                  AND md.metric_key = %s
                  AND p.event_time >= %s
                  AND p.event_time <= %s
                  AND (
                    p.value_double IS NOT NULL
                    OR p.value_int IS NOT NULL
                    OR p.value_bool IS NOT NULL
                  )
                ORDER BY p.event_time ASC
                """,
                (device_id, key, start, end),
            ).fetchall()
        rows = _limit_points(rows, max_points)
        return {
            "device_id": device_id,
            "key": key,
            "kind": "scalar",
            "resolution": "raw",
            "points": [{"ts": to_iso8601(row["event_time"]), "value": row["value"]} for row in rows],
        }

    def sampled_series(self, device_id: str, key: str, start: datetime, end: datetime, max_points: int) -> dict[str, Any]:
        with self._connect() as conn:
            rollups = conn.execute(
                """
                SELECT r.bucket_start, r.channel_key, r.channel_unit, r.min_value, r.max_value, r.avg_value, r.bucket_ns
                FROM signal_rollup_points r
                JOIN devices d ON d.device_pk = r.device_pk
                JOIN signal_stream_definitions sd ON sd.signal_pk = r.signal_pk
                WHERE d.device_id = %s
                  AND sd.stream_key = %s
                  AND r.bucket_start >= %s
                  AND r.bucket_start <= %s
                ORDER BY r.bucket_ns ASC, r.bucket_start ASC
                """,
                (device_id, key, start, end),
            ).fetchall()
            if rollups:
                return _rollup_series_response(device_id, key, rollups, max_points)
            frames = self._read_signal_frames(conn, device_id, key, start, end)
        return _raw_frames_to_series(device_id, key, frames, max_points)

    def summary(
        self,
        device_id: str,
        key: str,
        start: datetime,
        end: datetime,
        *,
        feature_ttl_seconds: int,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=feature_ttl_seconds)
        with self._connect() as conn:
            self._delete_expired_features(conn, now)
            existing = self._read_feature_rows(conn, device_id, key, start, end)
            if not existing:
                frames = self._read_signal_frames(conn, device_id, key, start, end)
                self._materialize_feature_rows(conn, device_id, key, start, end, frames, expires_at)
                existing = self._read_feature_rows(conn, device_id, key, start, end)
                conn.commit()
        return _feature_summary_response(device_id, key, start, end, existing)

    def frames(self, device_id: str, key: str, start: datetime, end: datetime) -> dict[str, Any]:
        with self._connect() as conn:
            frames = self._read_signal_frames(conn, device_id, key, start, end)
        response_frames = []
        for frame in frames:
            channels = _channels_from_json(frame["channels_json"])
            values_by_channel = decode_samples(
                samples=frame["samples"],
                encoding=frame["encoding"],
                layout=frame["layout"],
                channels=channels,
                sample_count=frame["sample_count"],
            )
            response_frames.append(
                {
                    "ts": to_iso8601(frame["event_time"]),
                    "sample_interval_ns": frame["sample_interval_ns"],
                    "sample_count": frame["sample_count"],
                    "channels": [
                        {"name": channel.key, "unit": channel.unit, "values": values_by_channel[channel.key]}
                        for channel in channels
                    ],
                }
            )
        return {"device_id": device_id, "key": key, "kind": "sampled", "frames": response_frames}

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, row_factory=dict_row)

    def _read_signal_frames(
        self,
        conn: psycopg.Connection,
        device_id: str,
        key: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        return conn.execute(
            """
            SELECT
                f.frame_id,
                f.event_time,
                f.sample_interval_ns,
                f.sample_count,
                f.samples,
                sd.encoding,
                sd.layout,
                sd.channels_json,
                d.device_pk,
                sd.signal_pk
            FROM device_signal_frames f
            JOIN devices d ON d.device_pk = f.device_pk
            JOIN signal_stream_definitions sd ON sd.signal_pk = f.signal_pk
            WHERE d.device_id = %s
              AND sd.stream_key = %s
              AND f.event_time >= %s
              AND f.event_time <= %s
            ORDER BY f.event_time ASC
            """,
            (device_id, key, start, end),
        ).fetchall()

    def _read_feature_rows(
        self,
        conn: psycopg.Connection,
        device_id: str,
        key: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        return conn.execute(
            """
            SELECT
                sf.channel_key,
                sf.channel_unit,
                sf.sample_count,
                sf.min_value,
                sf.max_value,
                sf.avg_value,
                sf.rms_value,
                sf.stddev_value,
                sf.peak_abs_value
            FROM signal_frame_features sf
            JOIN devices d ON d.device_pk = sf.device_pk
            JOIN signal_stream_definitions sd ON sd.signal_pk = sf.signal_pk
            WHERE d.device_id = %s
              AND sd.stream_key = %s
              AND sf.window_start = %s
              AND sf.window_end = %s
              AND sf.expires_at > NOW()
            ORDER BY sf.channel_key
            """,
            (device_id, key, start, end),
        ).fetchall()

    def _materialize_feature_rows(
        self,
        conn: psycopg.Connection,
        device_id: str,
        key: str,
        start: datetime,
        end: datetime,
        frames: list[dict[str, Any]],
        expires_at: datetime,
    ) -> None:
        merged_values: dict[str, list[float]] = {}
        channels_by_key: dict[str, Channel] = {}
        device_pk = None
        signal_pk = None
        for frame in frames:
            device_pk = frame["device_pk"]
            signal_pk = frame["signal_pk"]
            channels = _channels_from_json(frame["channels_json"])
            channels_by_key.update({channel.key: channel for channel in channels})
            decoded = decode_samples(
                samples=frame["samples"],
                encoding=frame["encoding"],
                layout=frame["layout"],
                channels=channels,
                sample_count=frame["sample_count"],
            )
            for channel_key, values in decoded.items():
                merged_values.setdefault(channel_key, []).extend(values)

        if device_pk is None or signal_pk is None:
            return

        stats = compute_channel_stats(merged_values, channels_by_key.values())
        for row in stats:
            conn.execute(
                """
                INSERT INTO signal_frame_features(
                    device_pk,
                    signal_pk,
                    window_start,
                    window_end,
                    channel_key,
                    channel_unit,
                    sample_count,
                    min_value,
                    max_value,
                    avg_value,
                    rms_value,
                    stddev_value,
                    peak_abs_value,
                    expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_pk, signal_pk, window_start, window_end, channel_key) DO UPDATE SET
                    channel_unit = EXCLUDED.channel_unit,
                    sample_count = EXCLUDED.sample_count,
                    min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value,
                    avg_value = EXCLUDED.avg_value,
                    rms_value = EXCLUDED.rms_value,
                    stddev_value = EXCLUDED.stddev_value,
                    peak_abs_value = EXCLUDED.peak_abs_value,
                    expires_at = EXCLUDED.expires_at
                """,
                (
                    device_pk,
                    signal_pk,
                    start,
                    end,
                    row.key,
                    row.unit,
                    row.sample_count,
                    row.minimum,
                    row.maximum,
                    row.average,
                    row.rms,
                    row.stddev,
                    row.peak_abs,
                    expires_at,
                ),
            )

    def _delete_expired_features(self, conn: psycopg.Connection, now: datetime) -> None:
        conn.execute("DELETE FROM signal_frame_features WHERE expires_at <= %s", (now,))


def _channels_from_json(raw: str) -> list[Channel]:
    channels = []
    for item in json.loads(raw):
        channels.append(
            Channel(
                key=item["key"],
                unit=item.get("unit"),
                scale=item.get("scale"),
                offset=item.get("offset"),
            )
        )
    return channels


def _common_unit(channels: list[dict[str, Any]]) -> str | None:
    units = {channel.get("unit") for channel in channels if channel.get("unit")}
    if len(units) == 1:
        return next(iter(units))
    return None


def _limit_points(rows: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if max_points <= 0 or len(rows) <= max_points:
        return rows
    stride = max(1, len(rows) // max_points)
    return rows[::stride][:max_points]


def _raw_frames_to_series(device_id: str, key: str, frames: list[dict[str, Any]], max_points: int) -> dict[str, Any]:
    channel_samples: dict[str, dict[str, Any]] = {}
    timestamps: list[datetime] = []
    for frame in frames:
        channels = _channels_from_json(frame["channels_json"])
        values_by_channel = decode_samples(
            samples=frame["samples"],
            encoding=frame["encoding"],
            layout=frame["layout"],
            channels=channels,
            sample_count=frame["sample_count"],
        )
        frame_timestamps = _sample_timestamps(
            frame["event_time"],
            frame["sample_interval_ns"],
            frame["sample_count"],
        )
        timestamps.extend(frame_timestamps)
        for channel in channels:
            channel_samples.setdefault(channel.key, {"name": channel.key, "unit": channel.unit, "values": []})
            channel_samples[channel.key]["values"].extend(values_by_channel[channel.key])

    if not channel_samples:
        channels = []
        mode = "samples"
    elif len(timestamps) <= max_points:
        channels = _sample_series_channels(channel_samples, timestamps)
        mode = "samples"
    else:
        channels = _bucketed_envelope_channels(channel_samples, timestamps, max_points)
        mode = "envelope"

    return {
        "device_id": device_id,
        "key": key,
        "kind": "sampled",
        "resolution": "raw-sample" if mode == "samples" else "raw-sample-bucket",
        "mode": mode,
        "source_sample_count": len(timestamps),
        "channels": channels,
    }


def _sample_timestamps(event_time: datetime, sample_interval_ns: int, sample_count: int) -> list[datetime]:
    return [
        event_time + timedelta(microseconds=(sample_index * sample_interval_ns) / 1000)
        for sample_index in range(sample_count)
    ]


def _sample_series_channels(
    channel_samples: dict[str, dict[str, Any]],
    timestamps: list[datetime],
) -> list[dict[str, Any]]:
    return [
        {
            "name": channel["name"],
            "unit": channel["unit"],
            "points": [
                {"ts": to_iso8601(timestamp), "value": value}
                for timestamp, value in zip(timestamps, channel["values"], strict=False)
            ],
        }
        for channel in channel_samples.values()
    ]


def _bucketed_envelope_channels(
    channel_samples: dict[str, dict[str, Any]],
    timestamps: list[datetime],
    max_points: int,
) -> list[dict[str, Any]]:
    total = len(timestamps)
    bucket_count = min(total, max_points)
    channels = []
    for channel in channel_samples.values():
        points = []
        values = channel["values"]
        for bucket_index in range(bucket_count):
            start_index = bucket_index * total // bucket_count
            end_index = (bucket_index + 1) * total // bucket_count
            bucket_values = values[start_index:end_index]
            if not bucket_values:
                continue
            points.append(
                {
                    "ts": to_iso8601(timestamps[start_index]),
                    "min": min(bucket_values),
                    "max": max(bucket_values),
                    "avg": sum(bucket_values) / len(bucket_values),
                }
            )
        channels.append({"name": channel["name"], "unit": channel["unit"], "points": points})
    return channels


def _rollup_series_response(device_id: str, key: str, rows: list[dict[str, Any]], max_points: int) -> dict[str, Any]:
    bucket_ns = rows[0]["bucket_ns"]
    channel_points: dict[str, dict[str, Any]] = {}
    for row in rows:
        channel_points.setdefault(row["channel_key"], {"name": row["channel_key"], "unit": row["channel_unit"], "points": []})
        channel_points[row["channel_key"]]["points"].append(
            {
                "ts": to_iso8601(row["bucket_start"]),
                "min": row["min_value"],
                "max": row["max_value"],
                "avg": row["avg_value"],
            }
        )
    for channel in channel_points.values():
        channel["points"] = _limit_points(channel["points"], max_points)
    return {
        "device_id": device_id,
        "key": key,
        "kind": "sampled",
        "resolution": f"{bucket_ns}ns",
        "mode": "envelope",
        "channels": list(channel_points.values()),
    }


def _feature_summary_response(
    device_id: str,
    key: str,
    start: datetime,
    end: datetime,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "key": key,
        "kind": "sampled",
        "from": to_iso8601(start),
        "to": to_iso8601(end),
        "features": {
            row["channel_key"]: {
                "unit": row["channel_unit"],
                "sample_count": row["sample_count"],
                "min": row["min_value"],
                "max": row["max_value"],
                "avg": row["avg_value"],
                "rms": row["rms_value"],
                "stddev": row["stddev_value"],
                "peak_abs": row["peak_abs_value"],
            }
            for row in rows
        },
    }
