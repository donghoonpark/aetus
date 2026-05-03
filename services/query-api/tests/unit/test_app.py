from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aetus_query.app import create_app
from aetus_query.config import Settings
from aetus_query.repository import StreamRef


pytestmark = pytest.mark.unit


class FakeRepository:
    def __init__(self) -> None:
        self.series_calls = 0
        self.summary_calls = 0
        self.frames_calls = 0
        self.streams = [
            StreamRef(
                key="temperature",
                kind="scalar",
                unit="celsius",
                latest_event_time=datetime(2026, 5, 3, tzinfo=timezone.utc),
            ),
            StreamRef(
                key="imu.accel",
                kind="sampled",
                unit="g",
                latest_event_time=datetime(2026, 5, 3, tzinfo=timezone.utc),
                encoding="float32_le",
                layout="interleaved",
                channels=[{"key": "x", "unit": "g"}],
                nominal_rate_hz=200.0,
            ),
        ]

    def list_streams(self, device_id: str) -> list[StreamRef]:
        assert device_id == "device-1"
        return self.streams

    def scalar_series(self, device_id: str, key: str, start: datetime, end: datetime, max_points: int) -> dict[str, Any]:
        del start, end
        self.series_calls += 1
        return {
            "device_id": device_id,
            "key": key,
            "kind": "scalar",
            "resolution": "raw",
            "max_points": max_points,
            "points": [{"ts": "2026-05-03T00:00:00Z", "value": 23.0}],
        }

    def sampled_series(self, device_id: str, key: str, start: datetime, end: datetime, max_points: int) -> dict[str, Any]:
        del start, end
        self.series_calls += 1
        return {
            "device_id": device_id,
            "key": key,
            "kind": "sampled",
            "resolution": "raw-frame",
            "max_points": max_points,
            "channels": [{"name": "x", "points": [{"ts": "2026-05-03T00:00:00Z", "min": 0.1, "max": 0.2}]}],
        }

    def summary(
        self,
        device_id: str,
        key: str,
        start: datetime,
        end: datetime,
        *,
        feature_ttl_seconds: int,
    ) -> dict[str, Any]:
        del start, end
        self.summary_calls += 1
        return {
            "device_id": device_id,
            "key": key,
            "kind": "sampled",
            "feature_ttl_seconds": feature_ttl_seconds,
            "features": {"x": {"min": 0.1, "max": 0.2}},
        }

    def frames(self, device_id: str, key: str, start: datetime, end: datetime) -> dict[str, Any]:
        del start, end
        self.frames_calls += 1
        return {"device_id": device_id, "key": key, "kind": "sampled", "frames": []}


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    def get_json(self, key: str) -> dict[str, Any] | None:
        return self.values.get(key)

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        assert ttl_seconds == 99
        self.values[key] = value


@pytest.fixture()
def client_and_repo() -> tuple[TestClient, FakeRepository]:
    repo = FakeRepository()
    app = create_app(
        Settings(redis_url=None, cache_ttl_seconds=99, feature_ttl_seconds=123),
        repository=repo,
        cache=MemoryCache(),
    )
    return TestClient(app), repo


def test_lists_streams_with_unified_public_model(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get("/v1/query/devices/device-1/streams")

    assert response.status_code == 200
    body = response.json()
    assert [stream["key"] for stream in body["streams"]] == ["temperature", "imu.accel"]
    assert [stream["kind"] for stream in body["streams"]] == ["scalar", "sampled"]
    assert body["streams"][1]["nominal_rate_hz"] == 200.0


def test_scalar_series_uses_scalar_repository_path(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, repo = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/temperature/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z", "max_points": 10},
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "scalar"
    assert response.json()["max_points"] == 10
    assert repo.series_calls == 1


def test_series_response_is_cached(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, repo = client_and_repo
    params = {"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"}

    first = client.get("/v1/query/devices/device-1/streams/imu.accel/series", params=params)
    second = client.get("/v1/query/devices/device-1/streams/imu.accel/series", params=params)

    assert first.status_code == 200
    assert second.status_code == 200
    assert repo.series_calls == 1
    assert second.json()["kind"] == "sampled"


def test_summary_uses_on_demand_feature_path(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, repo = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/imu.accel/summary",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
    )

    assert response.status_code == 200
    assert response.json()["feature_ttl_seconds"] == 123
    assert repo.summary_calls == 1


def test_frames_are_only_available_for_sampled_streams(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/temperature/frames",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:00:10Z"},
    )

    assert response.status_code == 404


def test_rejects_too_large_raw_drilldown_window(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/imu.accel/frames",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:02:00Z"},
    )

    assert response.status_code == 400


def test_rejects_invalid_time_range(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/imu.accel/series",
        params={"from": "2026-05-03T00:01:00Z", "to": "2026-05-03T00:00:00Z"},
    )

    assert response.status_code == 400
