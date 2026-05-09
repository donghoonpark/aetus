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
                value_type="double",
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
        self.devices = ["dense-device-1", "device-1", "device-2"]

    def search_devices(self, query: str, limit: int) -> list[str]:
        needle = query.lower()
        return [device_id for device_id in self.devices if not needle or needle in device_id.lower()][:limit]

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
        Settings(
            redis_url=None,
            cache_ttl_seconds=99,
            feature_ttl_seconds=123,
            query_jwt_secret="unit-query-secret-with-at-least-32-bytes",
            query_admin_token="unit-admin-token",
            max_points_limit=10000,
        ),
        repository=repo,
        cache=MemoryCache(),
    )
    return TestClient(app), repo


def _auth_headers(
    client: TestClient,
    *,
    devices: list[str] | None = None,
    streams: list[str] | None = None,
    scopes: list[str] | None = None,
    max_range_seconds: int | None = None,
    max_points: int | None = None,
) -> dict[str, str]:
    payload: dict[str, Any] = {
        "subject": "unit-operator",
        "devices": devices or ["device-1"],
        "streams": streams or ["*"],
        "scopes": scopes or ["query:read", "streams:list", "frames:read"],
    }
    if max_range_seconds is not None:
        payload["max_range_seconds"] = max_range_seconds
    if max_points is not None:
        payload["max_points"] = max_points
    response = client.post("/v1/auth/token", json=payload, headers={"X-Aetus-Admin-Token": "unit-admin-token"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_token_endpoint_requires_admin_token(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.post("/v1/auth/token", json={"subject": "bad"})

    assert response.status_code == 401


def test_query_endpoints_require_jwt(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get("/v1/query/devices/device-1/streams")

    assert response.status_code == 401


def test_searches_devices_for_wildcard_token(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices",
        params={"search": "device-", "limit": 2},
        headers=_auth_headers(client, devices=["*"]),
    )

    assert response.status_code == 200
    assert response.json()["devices"] == [{"device_id": "dense-device-1"}, {"device_id": "device-1"}]


def test_search_devices_filters_restricted_token_without_repository_scan(
    client_and_repo: tuple[TestClient, FakeRepository],
) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices",
        params={"search": "device", "limit": 5},
        headers=_auth_headers(client, devices=["device-2", "hidden-device-9"]),
    )

    assert response.status_code == 200
    assert response.json()["devices"] == [{"device_id": "device-2"}, {"device_id": "hidden-device-9"}]


def test_lists_streams_with_unified_public_model(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get("/v1/query/devices/device-1/streams", headers=_auth_headers(client))

    assert response.status_code == 200
    body = response.json()
    assert [stream["key"] for stream in body["streams"]] == ["temperature", "imu.accel"]
    assert [stream["kind"] for stream in body["streams"]] == ["scalar", "sampled"]
    assert body["streams"][0]["value_type"] == "double"
    assert body["streams"][1]["nominal_rate_hz"] == 200.0


def test_list_streams_filters_by_stream_claim(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams",
        headers=_auth_headers(client, streams=["temperature"]),
    )

    assert response.status_code == 200
    assert [stream["key"] for stream in response.json()["streams"]] == ["temperature"]


def test_rejects_device_outside_token_claim(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams",
        headers=_auth_headers(client, devices=["device-2"]),
    )

    assert response.status_code == 403


def test_scalar_series_uses_scalar_repository_path(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, repo = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/temperature/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z", "max_points": 10},
        headers=_auth_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "scalar"
    assert response.json()["max_points"] == 10
    assert repo.series_calls == 1


def test_series_response_is_cached(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, repo = client_and_repo
    params = {"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"}

    headers = _auth_headers(client)
    first = client.get("/v1/query/devices/device-1/streams/imu.accel/series", params=params, headers=headers)
    second = client.get("/v1/query/devices/device-1/streams/imu.accel/series", params=params, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert repo.series_calls == 1
    assert second.json()["kind"] == "sampled"


def test_summary_uses_on_demand_feature_path(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, repo = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/imu.accel/summary",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        headers=_auth_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["feature_ttl_seconds"] == 123
    assert repo.summary_calls == 1


def test_frames_are_only_available_for_sampled_streams(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/temperature/frames",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:00:10Z"},
        headers=_auth_headers(client),
    )

    assert response.status_code == 404


def test_rejects_too_large_raw_drilldown_window(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/imu.accel/frames",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:02:00Z"},
        headers=_auth_headers(client),
    )

    assert response.status_code == 400


def test_rejects_invalid_time_range(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/imu.accel/series",
        params={"from": "2026-05-03T00:01:00Z", "to": "2026-05-03T00:00:00Z"},
        headers=_auth_headers(client),
    )

    assert response.status_code == 400


def test_rejects_stream_outside_token_claim(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/imu.accel/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z"},
        headers=_auth_headers(client, streams=["temperature"]),
    )

    assert response.status_code == 403


def test_rejects_query_over_token_limits(client_and_repo: tuple[TestClient, FakeRepository]) -> None:
    client, _ = client_and_repo

    response = client.get(
        "/v1/query/devices/device-1/streams/temperature/series",
        params={"from": "2026-05-03T00:00:00Z", "to": "2026-05-03T00:01:00Z", "max_points": 10},
        headers=_auth_headers(client, max_range_seconds=30, max_points=5),
    )

    assert response.status_code == 403
