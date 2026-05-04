from __future__ import annotations

import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

import aiosqlite
import psycopg
from psycopg.rows import dict_row


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class DeviceRecord:
    device_id: str
    hardware_id: str
    token: str
    model: str | None
    firmware_version: int | None
    site_code: str | None
    created_at: str
    updated_at: str


class ControlStore(Protocol):
    def initialize(self) -> None: ...

    def seed_hardware_allowlist(self, hardware_ids: set[str]) -> None: ...

    def seed_devices(self, device_tokens: dict[str, str]) -> None: ...

    async def get_device_token_readonly(self, device_id: str) -> str | None: ...

    async def is_hardware_allowed_readonly(self, hardware_id: str) -> bool: ...

    async def count_devices_readonly(self, *, query: str | None = None) -> int: ...

    async def list_devices_readonly(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        query: str | None = None,
    ) -> list[DeviceRecord]: ...

    async def issue_device_token(
        self,
        hardware_id: str,
        model: str | None,
        firmware_version: int | None,
        site_code: str | None,
    ) -> DeviceRecord: ...


class SqliteControlStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect_rw() as conn:
            self._configure_connection(conn)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    hardware_id TEXT NOT NULL UNIQUE,
                    token TEXT NOT NULL,
                    model TEXT,
                    firmware_version INTEGER,
                    site_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hardware_allowlist (
                    hardware_id TEXT PRIMARY KEY,
                    description TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def seed_hardware_allowlist(self, hardware_ids: set[str]) -> None:
        if not hardware_ids:
            return
        now = utc_now_iso()
        with self._connect_rw() as conn:
            self._configure_connection(conn)
            conn.executemany(
                """
                INSERT INTO hardware_allowlist (hardware_id, description, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(hardware_id) DO NOTHING
                """,
                [(hardware_id, "seeded-from-env", now) for hardware_id in sorted(hardware_ids)],
            )
            conn.commit()

    def seed_devices(self, device_tokens: dict[str, str]) -> None:
        if not device_tokens:
            return
        now = utc_now_iso()
        with self._connect_rw() as conn:
            self._configure_connection(conn)
            for device_id, token in device_tokens.items():
                hardware_id = f"seed-{device_id}"
                conn.execute(
                    """
                    INSERT INTO devices (
                        device_id, hardware_id, token, model, firmware_version, site_code, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(device_id) DO UPDATE SET
                        token = excluded.token,
                        updated_at = excluded.updated_at
                    """,
                    (device_id, hardware_id, token, "seeded", None, None, now, now),
                )
            conn.commit()

    async def get_device_token_readonly(self, device_id: str) -> str | None:
        async with aiosqlite.connect(self._readonly_uri(), uri=True) as conn:
            conn.row_factory = sqlite3.Row
            await self._configure_connection_async(conn, readonly=True)
            cursor = await conn.execute(
                "SELECT token FROM devices WHERE device_id = ?",
                (device_id,),
            )
            row = await cursor.fetchone()
            return None if row is None else str(row["token"])

    async def is_hardware_allowed_readonly(self, hardware_id: str) -> bool:
        async with aiosqlite.connect(self._readonly_uri(), uri=True) as conn:
            conn.row_factory = sqlite3.Row
            await self._configure_connection_async(conn, readonly=True)
            cursor = await conn.execute(
                "SELECT 1 FROM hardware_allowlist WHERE hardware_id = ?",
                (hardware_id,),
            )
            row = await cursor.fetchone()
            return row is not None

    async def count_devices_readonly(self, *, query: str | None = None) -> int:
        async with aiosqlite.connect(self._readonly_uri(), uri=True) as conn:
            conn.row_factory = sqlite3.Row
            await self._configure_connection_async(conn, readonly=True)
            where_clause, params = self._search_clause(query)
            cursor = await conn.execute(
                f"SELECT COUNT(*) AS count FROM devices{where_clause}",
                params,
            )
            row = await cursor.fetchone()
        return 0 if row is None else int(row["count"])

    async def list_devices_readonly(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        query: str | None = None,
    ) -> list[DeviceRecord]:
        async with aiosqlite.connect(self._readonly_uri(), uri=True) as conn:
            conn.row_factory = sqlite3.Row
            await self._configure_connection_async(conn, readonly=True)
            where_clause, params = self._search_clause(query)
            cursor = await conn.execute(
                f"""
                SELECT device_id, hardware_id, token, model, firmware_version, site_code, created_at, updated_at
                FROM devices
                {where_clause}
                ORDER BY created_at DESC, device_id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            )
            rows = await cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    async def issue_device_token(
        self,
        hardware_id: str,
        model: str | None,
        firmware_version: int | None,
        site_code: str | None,
    ) -> DeviceRecord:
        now = utc_now_iso()
        token = f"devtok_{secrets.token_urlsafe(24)}"
        for attempt in range(3):
            try:
                async with aiosqlite.connect(self.path) as conn:
                    conn.row_factory = sqlite3.Row
                    await self._configure_connection_async(conn)
                    await conn.execute("BEGIN IMMEDIATE")
                    cursor = await conn.execute(
                        """
                        SELECT device_id FROM devices
                        WHERE hardware_id = ?
                        """,
                        (hardware_id,),
                    )
                    row = await cursor.fetchone()

                    if row is None:
                        device_id = await self._next_device_id(conn, hardware_id)
                        await conn.execute(
                            """
                            INSERT INTO devices (
                                device_id, hardware_id, token, model, firmware_version, site_code, created_at, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (device_id, hardware_id, token, model, firmware_version, site_code, now, now),
                        )
                    else:
                        device_id = str(row["device_id"])
                        await conn.execute(
                            """
                            UPDATE devices
                            SET token = ?, model = ?, firmware_version = ?, site_code = ?, updated_at = ?
                            WHERE hardware_id = ?
                            """,
                            (token, model, firmware_version, site_code, now, hardware_id),
                        )

                    await conn.execute(
                        """
                        INSERT INTO hardware_allowlist (hardware_id, description, created_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(hardware_id) DO NOTHING
                        """,
                        (hardware_id, "issued-via-admin-or-provision", now),
                    )
                    await conn.commit()

                    saved_cursor = await conn.execute(
                        """
                        SELECT device_id, hardware_id, token, model, firmware_version, site_code, created_at, updated_at
                        FROM devices
                        WHERE hardware_id = ?
                        """,
                        (hardware_id,),
                    )
                    saved = await saved_cursor.fetchone()
                    assert saved is not None
                    return self._row_to_record(saved)
            except sqlite3.IntegrityError:
                if attempt == 2:
                    raise

    async def _next_device_id(self, conn: aiosqlite.Connection, hardware_id: str) -> str:
        prefix = hardware_id.split("-", 1)[0]
        normalized_prefix = re.sub(r"[^a-z0-9]+", "", prefix.lower()) or "device"
        cursor = await conn.execute(
            "SELECT device_id FROM devices WHERE device_id LIKE ?",
            (f"{normalized_prefix}-%",),
        )
        rows = await cursor.fetchall()
        max_suffix = 0
        for row in rows:
            candidate = str(row["device_id"])
            try:
                suffix = int(candidate.rsplit("-", 1)[1])
            except (IndexError, ValueError):
                continue
            max_suffix = max(max_suffix, suffix)
        return f"{normalized_prefix}-{max_suffix + 1:03d}"

    def _connect_rw(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_ro(self) -> sqlite3.Connection:
        uri = f"file:{self.path}?mode=ro&cache=shared"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _readonly_uri(self) -> str:
        return f"file:{self.path}?mode=ro&cache=shared"

    @staticmethod
    def _search_clause(query: str | None) -> tuple[str, tuple[str, ...]]:
        if not query:
            return "", ()
        needle = f"%{query.strip().lower()}%"
        return (
            """
            WHERE
                lower(device_id) LIKE ?
                OR lower(hardware_id) LIKE ?
                OR lower(COALESCE(model, '')) LIKE ?
                OR lower(COALESCE(site_code, '')) LIKE ?
            """,
            (needle, needle, needle, needle),
        )

    @staticmethod
    def _configure_connection(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=3000;")

    @staticmethod
    async def _configure_connection_async(conn: aiosqlite.Connection, readonly: bool = False) -> None:
        if not readonly:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA busy_timeout=3000;")

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> DeviceRecord:
        return _row_to_record(row)


class PostgresControlStore:
    def __init__(self, dsn: str, *, schema: str = "control", connect_timeout_seconds: float = 5.0) -> None:
        self.dsn = dsn
        self.schema = _validate_identifier(schema)
        self.connect_timeout_seconds = max(int(connect_timeout_seconds), 1)
        self._schema_sql = _quote_identifier(self.schema)
        self._devices_table = f"{self._schema_sql}.devices"
        self._hardware_allowlist_table = f"{self._schema_sql}.hardware_allowlist"

    def initialize(self) -> None:
        with self._connect_sync() as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema_sql}")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._devices_table} (
                        device_id TEXT PRIMARY KEY,
                        hardware_id TEXT NOT NULL UNIQUE,
                        token TEXT NOT NULL,
                        model TEXT,
                        firmware_version INTEGER,
                        site_code TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._hardware_allowlist_table} (
                        hardware_id TEXT PRIMARY KEY,
                        description TEXT,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )

    def seed_hardware_allowlist(self, hardware_ids: set[str]) -> None:
        if not hardware_ids:
            return
        now = utc_now_iso()
        rows = [(hardware_id, "seeded-from-env", now) for hardware_id in sorted(hardware_ids)]
        with self._connect_sync() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f"""
                    INSERT INTO {self._hardware_allowlist_table} (hardware_id, description, created_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(hardware_id) DO NOTHING
                    """,
                    rows,
                )

    def seed_devices(self, device_tokens: dict[str, str]) -> None:
        if not device_tokens:
            return
        now = utc_now_iso()
        rows = [
            (device_id, f"seed-{device_id}", token, "seeded", None, None, now, now)
            for device_id, token in device_tokens.items()
        ]
        with self._connect_sync() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f"""
                    INSERT INTO {self._devices_table} (
                        device_id, hardware_id, token, model, firmware_version, site_code, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(device_id) DO UPDATE SET
                        token = excluded.token,
                        updated_at = excluded.updated_at
                    """,
                    rows,
                )

    async def get_device_token_readonly(self, device_id: str) -> str | None:
        async with await self._connect_async() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT token FROM {self._devices_table} WHERE device_id = %s",
                    (device_id,),
                )
                row = await cur.fetchone()
        return None if row is None else str(row["token"])

    async def is_hardware_allowed_readonly(self, hardware_id: str) -> bool:
        async with await self._connect_async() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT 1 FROM {self._hardware_allowlist_table} WHERE hardware_id = %s",
                    (hardware_id,),
                )
                row = await cur.fetchone()
        return row is not None

    async def count_devices_readonly(self, *, query: str | None = None) -> int:
        where_clause, params = _search_clause(query, placeholder="%s")
        async with await self._connect_async() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT COUNT(*) AS count FROM {self._devices_table}{where_clause}",
                    params,
                )
                row = await cur.fetchone()
        return 0 if row is None else int(row["count"])

    async def list_devices_readonly(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        query: str | None = None,
    ) -> list[DeviceRecord]:
        where_clause, params = _search_clause(query, placeholder="%s")
        async with await self._connect_async() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT device_id, hardware_id, token, model, firmware_version, site_code, created_at, updated_at
                    FROM {self._devices_table}
                    {where_clause}
                    ORDER BY created_at DESC, device_id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (*params, limit, offset),
                )
                rows = await cur.fetchall()
        return [_row_to_record(row) for row in rows]

    async def issue_device_token(
        self,
        hardware_id: str,
        model: str | None,
        firmware_version: int | None,
        site_code: str | None,
    ) -> DeviceRecord:
        now = utc_now_iso()
        token = f"devtok_{secrets.token_urlsafe(24)}"
        for attempt in range(3):
            try:
                async with await self._connect_async() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            f"""
                            SELECT device_id FROM {self._devices_table}
                            WHERE hardware_id = %s
                            FOR UPDATE
                            """,
                            (hardware_id,),
                        )
                        row = await cur.fetchone()

                        if row is None:
                            device_id = await self._next_device_id(cur, hardware_id)
                            await cur.execute(
                                f"""
                                INSERT INTO {self._devices_table} (
                                    device_id, hardware_id, token, model, firmware_version, site_code, created_at, updated_at
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (device_id, hardware_id, token, model, firmware_version, site_code, now, now),
                            )
                        else:
                            device_id = str(row["device_id"])
                            await cur.execute(
                                f"""
                                UPDATE {self._devices_table}
                                SET token = %s, model = %s, firmware_version = %s, site_code = %s, updated_at = %s
                                WHERE hardware_id = %s
                                """,
                                (token, model, firmware_version, site_code, now, hardware_id),
                            )

                        await cur.execute(
                            f"""
                            INSERT INTO {self._hardware_allowlist_table} (hardware_id, description, created_at)
                            VALUES (%s, %s, %s)
                            ON CONFLICT(hardware_id) DO NOTHING
                            """,
                            (hardware_id, "issued-via-admin-or-provision", now),
                        )
                        await cur.execute(
                            f"""
                            SELECT device_id, hardware_id, token, model, firmware_version, site_code, created_at, updated_at
                            FROM {self._devices_table}
                            WHERE hardware_id = %s
                            """,
                            (hardware_id,),
                        )
                        saved = await cur.fetchone()
                        assert saved is not None
                        return _row_to_record(saved)
            except psycopg.errors.UniqueViolation:
                if attempt == 2:
                    raise

    async def _next_device_id(self, cur: psycopg.AsyncCursor[Mapping[str, Any]], hardware_id: str) -> str:
        prefix = hardware_id.split("-", 1)[0]
        normalized_prefix = re.sub(r"[^a-z0-9]+", "", prefix.lower()) or "device"
        await cur.execute(
            f"SELECT device_id FROM {self._devices_table} WHERE device_id LIKE %s",
            (f"{normalized_prefix}-%",),
        )
        rows = await cur.fetchall()
        max_suffix = 0
        for row in rows:
            candidate = str(row["device_id"])
            try:
                suffix = int(candidate.rsplit("-", 1)[1])
            except (IndexError, ValueError):
                continue
            max_suffix = max(max_suffix, suffix)
        return f"{normalized_prefix}-{max_suffix + 1:03d}"

    def _connect_sync(self) -> psycopg.Connection[Mapping[str, Any]]:
        return psycopg.connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
            row_factory=dict_row,
        )

    async def _connect_async(self) -> psycopg.AsyncConnection[Mapping[str, Any]]:
        return await psycopg.AsyncConnection.connect(
            self.dsn,
            connect_timeout=self.connect_timeout_seconds,
            row_factory=dict_row,
        )


ControlDB = SqliteControlStore


def create_control_store(settings: Any) -> ControlStore:
    backend = str(settings.control_db_backend).strip().lower()
    if backend == "sqlite":
        return SqliteControlStore(settings.control_db_path)
    if backend in {"postgres", "postgresql"}:
        return PostgresControlStore(
            settings.resolved_control_database_url,
            schema=settings.control_db_schema,
            connect_timeout_seconds=settings.status_timeout_seconds,
        )
    raise ValueError(f"unsupported control DB backend: {settings.control_db_backend}")


def _search_clause(query: str | None, *, placeholder: str) -> tuple[str, tuple[str, ...]]:
    if not query:
        return "", ()
    needle = f"%{query.strip().lower()}%"
    return (
        f"""
        WHERE
            lower(device_id) LIKE {placeholder}
            OR lower(hardware_id) LIKE {placeholder}
            OR lower(COALESCE(model, '')) LIKE {placeholder}
            OR lower(COALESCE(site_code, '')) LIKE {placeholder}
        """,
        (needle, needle, needle, needle),
    )


def _row_to_record(row: Mapping[str, Any]) -> DeviceRecord:
    return DeviceRecord(
        device_id=str(row["device_id"]),
        hardware_id=str(row["hardware_id"]),
        token=str(row["token"]),
        model=None if row["model"] is None else str(row["model"]),
        firmware_version=None if row["firmware_version"] is None else int(row["firmware_version"]),
        site_code=None if row["site_code"] is None else str(row["site_code"]),
        created_at=_timestamp_to_string(row["created_at"]),
        updated_at=_timestamp_to_string(row["updated_at"]),
    )


def _timestamp_to_string(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _validate_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"invalid SQL identifier: {value}")
    return value


def _quote_identifier(value: str) -> str:
    return f'"{value}"'
