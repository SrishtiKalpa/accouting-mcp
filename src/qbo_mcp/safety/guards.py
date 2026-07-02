from __future__ import annotations

from qbo_mcp.db.connection import get_db


async def check_read_only(company_id: str) -> None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT read_only, name FROM companies WHERE id=?", (company_id,)
        )
        row = await cursor.fetchone()
    if row and row["read_only"]:
        raise ValueError(
            f'Company "{row["name"]}" is in read-only mode. '
            "Disable read-only in company settings before making changes."
        )


async def check_threshold(company_id: str, amount_usd: float) -> None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT write_threshold_usd, name FROM companies WHERE id=?", (company_id,)
        )
        row = await cursor.fetchone()
    if row and row["write_threshold_usd"] is not None:
        threshold = float(row["write_threshold_usd"])
        if amount_usd > threshold:
            raise ValueError(
                f"This action involves ${amount_usd:.2f} which exceeds the "
                f"${threshold:.2f} write threshold for \"{row['name']}\". "
                "Ask the user to confirm this amount before proceeding."
            )
