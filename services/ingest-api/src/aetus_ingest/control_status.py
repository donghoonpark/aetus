from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import psycopg
from kafka import KafkaAdminClient

from aetus_ingest.config import Settings
from aetus_ingest.schemas import ComponentStatus, ControlStatusResponse


async def build_control_status(settings: Settings) -> ControlStatusResponse:
    components = await asyncio.gather(
        _check_api(),
        _check_control_db(settings),
        _check_kafka(settings),
        _check_kafka_connect(settings),
        _check_postgres(settings),
    )
    return ControlStatusResponse(
        checked_at=datetime.now(timezone.utc).isoformat(),
        components=components,
    )


async def _check_api() -> ComponentStatus:
    return ComponentStatus(name="api", state="healthy", detail="FastAPI process is running")


async def _check_control_db(settings: Settings) -> ComponentStatus:
    try:
        await asyncio.to_thread(_check_sqlite_file, settings.control_db_path)
        return ComponentStatus(name="control_db", state="healthy", detail=settings.control_db_path)
    except Exception as exc:
        return ComponentStatus(name="control_db", state="down", detail=str(exc))


def _check_sqlite_file(path: str) -> None:
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute("SELECT 1")


async def _check_kafka(settings: Settings) -> ComponentStatus:
    try:
        detail = await asyncio.to_thread(_kafka_topics_detail, settings)
        return ComponentStatus(name="kafka", state="healthy", detail=detail)
    except Exception as exc:
        return ComponentStatus(name="kafka", state="down", detail=str(exc))


def _kafka_topics_detail(settings: Settings) -> str:
    client = KafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        request_timeout_ms=int(settings.status_timeout_seconds * 1000),
    )
    try:
        topics = sorted(client.list_topics())
        return f"{settings.kafka_bootstrap_servers} ({len(topics)} topics visible)"
    finally:
        client.close()


async def _check_kafka_connect(settings: Settings) -> ComponentStatus:
    try:
        async with httpx.AsyncClient(timeout=settings.status_timeout_seconds) as client:
            response = await client.get(f"{settings.kafka_connect_url.rstrip('/')}/connectors")
            response.raise_for_status()
            connectors = response.json()
        return ComponentStatus(
            name="kafka_connect",
            state="healthy",
            detail=f"{settings.kafka_connect_url} ({len(connectors)} connectors)",
        )
    except Exception as exc:
        return ComponentStatus(name="kafka_connect", state="down", detail=str(exc))


async def _check_postgres(settings: Settings) -> ComponentStatus:
    try:
        detail = await asyncio.to_thread(_postgres_detail, settings)
        return ComponentStatus(name="postgres", state="healthy", detail=detail)
    except Exception as exc:
        return ComponentStatus(name="postgres", state="down", detail=str(exc))


def _postgres_detail(settings: Settings) -> str:
    with psycopg.connect(settings.postgres_dsn, connect_timeout=max(int(settings.status_timeout_seconds), 1)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            db_name = cur.fetchone()[0]
    return f"{db_name} via {settings.postgres_dsn.rsplit('@', 1)[-1]}"
