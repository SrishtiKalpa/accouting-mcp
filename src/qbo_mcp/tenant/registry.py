from __future__ import annotations

import uuid
from typing import Any

import structlog

from qbo_mcp.db.connection import get_db
from qbo_mcp.qbo.client import QBOClient

log = structlog.get_logger(__name__)


class CompanyRegistry:
    async def get_client(self, company_id: str) -> QBOClient:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT id, realm_id, access_token, refresh_token, token_expires_at "
                "FROM companies WHERE id=?",
                (company_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            valid = await self.list_companies()
            ids = [c["id"] for c in valid]
            raise ValueError(
                f"Company '{company_id}' not found. "
                f"Valid company IDs: {ids}. "
                "Call list_companies() to see all connected companies."
            )

        return QBOClient(
            company_id=row["id"],
            realm_id=row["realm_id"],
            access_token=row["access_token"] or "",
            refresh_token=row["refresh_token"],
            token_expires_at=row["token_expires_at"],
        )

    async def list_companies(self) -> list[dict[str, Any]]:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT id, name, realm_id, read_only, write_threshold_usd FROM companies ORDER BY name"
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def add_company(
        self,
        name: str,
        realm_id: str,
        refresh_token: str,
        access_token: str,
        token_expires_at: int,
        read_only: bool = False,
        write_threshold_usd: float | None = None,
    ) -> str:
        company_id = str(uuid.uuid4())
        async with get_db() as db:
            await db.execute(
                "INSERT INTO companies "
                "(id, name, realm_id, access_token, refresh_token, token_expires_at, "
                "read_only, write_threshold_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    company_id,
                    name,
                    realm_id,
                    access_token,
                    refresh_token,
                    token_expires_at,
                    1 if read_only else 0,
                    write_threshold_usd,
                ),
            )
            await db.commit()
        log.info("registry.company_added", company_id=company_id, name=name, realm_id=realm_id)
        return company_id

    async def remove_company(self, company_id: str) -> None:
        async with get_db() as db:
            cursor = await db.execute("SELECT id FROM companies WHERE id=?", (company_id,))
            row = await cursor.fetchone()
            if row is None:
                raise ValueError(f"Company '{company_id}' not found.")
            await db.execute("DELETE FROM companies WHERE id=?", (company_id,))
            await db.commit()
        log.info("registry.company_removed", company_id=company_id)

    async def update_tokens(
        self,
        company_id: str,
        access_token: str,
        refresh_token: str,
        token_expires_at: int,
    ) -> None:
        async with get_db() as db:
            await db.execute(
                "UPDATE companies SET access_token=?, refresh_token=?, token_expires_at=?, "
                "updated_at=unixepoch() WHERE id=?",
                (access_token, refresh_token, token_expires_at, company_id),
            )
            await db.commit()


registry = CompanyRegistry()
