from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from qbo_mcp.db.connection import close_db, init_schema


@pytest_asyncio.fixture(autouse=True)
async def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None, None]:
    """Each test gets a fresh SQLite DB at a temp path."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_path)

    import qbo_mcp.db.connection as conn_mod
    conn_mod._db_instance = None  # noqa: SLF001

    import importlib
    import qbo_mcp.config as config_mod
    importlib.reload(config_mod)
    conn_mod.settings = config_mod.settings  # type: ignore[attr-defined]

    await init_schema()
    yield
    await close_db()
