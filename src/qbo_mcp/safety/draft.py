from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from qbo_mcp.db.connection import get_db
from qbo_mcp.qbo.models import DraftAction

log = structlog.get_logger(__name__)


async def create_draft(
    company_id: str,
    tool_name: str,
    description: str,
    payload: dict[str, Any],
) -> DraftAction:
    draft_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO draft_actions (id, company_id, tool_name, description, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (draft_id, company_id, tool_name, description, json.dumps(payload)),
        )
        await db.commit()
    log.info("draft.created", draft_id=draft_id, tool_name=tool_name, company_id=company_id)
    return DraftAction(
        draft_action_id=draft_id,
        tool_name=tool_name,
        description=description,
        preview=payload,
        message=(
            f"Draft created (ID: {draft_id}). "
            f"This will: {description}. "
            f"Call commit_action('{draft_id}') to execute, "
            f"or discard_action('{draft_id}') to cancel."
        ),
    )


async def get_draft(draft_id: str) -> dict[str, Any] | None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, company_id, tool_name, description, payload, status "
            "FROM draft_actions WHERE id=?",
            (draft_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


async def commit_draft(
    draft_id: str,
    executor: Any,  # callable: (company_id, tool_name, payload) -> Any
) -> dict[str, Any]:
    row = await get_draft(draft_id)
    if row is None:
        raise ValueError(f"Draft action '{draft_id}' not found.")
    if row["status"] != "pending":
        raise ValueError(
            f"Draft action '{draft_id}' has status '{row['status']}' — "
            "only pending drafts can be committed."
        )
    payload = json.loads(row["payload"])
    result = await executor(row["company_id"], row["tool_name"], payload)

    async with get_db() as db:
        await db.execute(
            "UPDATE draft_actions SET status='committed', committed_at=unixepoch() WHERE id=?",
            (draft_id,),
        )
        await db.commit()

    log.info("draft.committed", draft_id=draft_id, tool_name=row["tool_name"])
    return {"status": "committed", "draft_action_id": draft_id, "result": result}


async def discard_draft(draft_id: str) -> None:
    row = await get_draft(draft_id)
    if row is None:
        raise ValueError(f"Draft action '{draft_id}' not found.")
    if row["status"] != "pending":
        raise ValueError(
            f"Draft action '{draft_id}' has status '{row['status']}' — "
            "only pending drafts can be discarded."
        )
    async with get_db() as db:
        await db.execute(
            "UPDATE draft_actions SET status='discarded' WHERE id=?",
            (draft_id,),
        )
        await db.commit()
    log.info("draft.discarded", draft_id=draft_id)
