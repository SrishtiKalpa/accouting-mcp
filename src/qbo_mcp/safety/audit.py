from __future__ import annotations

import uuid

import structlog

from qbo_mcp.db.connection import get_db

log = structlog.get_logger(__name__)


async def log_action(
    tool_name: str,
    input_summary: str,
    outcome: str,
    company_id: str | None = None,
    draft_action_id: str | None = None,
    error_message: str | None = None,
) -> None:
    try:
        audit_id = str(uuid.uuid4())
        async with get_db() as db:
            await db.execute(
                "INSERT INTO audit_log "
                "(id, company_id, tool_name, input_summary, outcome, draft_action_id, error_message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (audit_id, company_id, tool_name, input_summary, outcome, draft_action_id, error_message),
            )
            await db.commit()
    except Exception as exc:
        log.warning("audit.write_failed", error=str(exc), tool_name=tool_name)
