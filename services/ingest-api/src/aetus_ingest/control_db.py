from __future__ import annotations

import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


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


class ControlDB:
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
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            await self._configure_connection_async(conn)
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
        return DeviceRecord(
            device_id=str(row["device_id"]),
            hardware_id=str(row["hardware_id"]),
            token=str(row["token"]),
            model=None if row["model"] is None else str(row["model"]),
            firmware_version=None if row["firmware_version"] is None else int(row["firmware_version"]),
            site_code=None if row["site_code"] is None else str(row["site_code"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
