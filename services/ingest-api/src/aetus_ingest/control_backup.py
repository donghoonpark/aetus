from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def backup_sqlite_database(
    db_path: str | Path,
    backup_dir: str | Path,
    *,
    retention_count: int | None = None,
    timestamp: datetime | None = None,
) -> Path:
    source = Path(db_path)
    if not source.exists():
        raise FileNotFoundError(f"SQLite control DB does not exist: {source}")

    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    now = timestamp or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{source.stem}-{stamp}.db"
    tmp_target = target.with_suffix(".db.tmp")

    source_uri = f"file:{source}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as src, sqlite3.connect(tmp_target) as dst:
        src.backup(dst)

    tmp_target.replace(target)
    if retention_count is not None and retention_count > 0:
        prune_sqlite_backups(target_dir, source.stem, keep=retention_count)
    return target


def prune_sqlite_backups(backup_dir: str | Path, db_stem: str, *, keep: int) -> list[Path]:
    target_dir = Path(backup_dir)
    backups = sorted(
        target_dir.glob(f"{db_stem}-*.db"),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    removed: list[Path] = []
    for backup in backups[keep:]:
        backup.unlink(missing_ok=True)
        removed.append(backup)
    return removed


async def run_sqlite_backup_loop(
    db_path: str | Path,
    backup_dir: str | Path,
    *,
    interval_seconds: float,
    retention_count: int,
    backup_on_startup: bool,
) -> None:
    if backup_on_startup:
        await _backup_safely(db_path, backup_dir, retention_count)

    while True:
        await asyncio.sleep(interval_seconds)
        await _backup_safely(db_path, backup_dir, retention_count)


async def _backup_safely(db_path: str | Path, backup_dir: str | Path, retention_count: int) -> None:
    try:
        created = await asyncio.to_thread(
            backup_sqlite_database,
            db_path,
            backup_dir,
            retention_count=retention_count,
        )
        logger.info("SQLite control DB backup created: %s", created)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("SQLite control DB backup failed")
