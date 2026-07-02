from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aiosqlite
import structlog

from qbo_mcp.config import settings
from qbo_mcp.db.schema import SCHEMA_SQL

log = structlog.get_logger(__name__)

_db_lock = asyncio.Lock()
_db_instance: aiosqlite.Connection | None = None


async def _get_connection() -> aiosqlite.Connection:
    global _db_instance
    async with _db_lock:
        if _db_instance is None:
            conn = await aiosqlite.connect(settings.db_path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            _db_instance = conn
            log.info("database.connected", path=settings.db_path)
        return _db_instance


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    db = await _get_connection()
    try:
        yield db
    except Exception:
        await db.rollback()
        raise


async def init_schema() -> None:
    db = await _get_connection()
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    log.info("database.schema_initialised")


async def close_db() -> None:
    global _db_instance
    async with _db_lock:
        if _db_instance is not None:
            await _db_instance.close()
            _db_instance = None
            log.info("database.closed")
