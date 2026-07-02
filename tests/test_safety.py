from __future__ import annotations

import pytest

from qbo_mcp.db.connection import get_db
from qbo_mcp.safety import audit
from qbo_mcp.safety.draft import create_draft
from qbo_mcp.safety.guards import check_read_only, check_threshold


async def _add_company(
    company_id: str,
    name: str,
    read_only: bool = False,
    threshold: float | None = None,
) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO companies "
            "(id, name, realm_id, refresh_token, token_expires_at, read_only, write_threshold_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (company_id, name, f"realm-{company_id}", "token", 9999999999, 1 if read_only else 0, threshold),
        )
        await db.commit()


class TestReadOnlyGuard:
    async def test_read_only_blocks_write(self) -> None:
        await _add_company("ro-company", "Read Only Corp", read_only=True)
        with pytest.raises(ValueError, match="read-only"):
            await check_read_only("ro-company")

    async def test_writable_company_passes(self) -> None:
        await _add_company("rw-company", "Writable Corp", read_only=False)
        await check_read_only("rw-company")  # should not raise


class TestThresholdGuard:
    async def test_threshold_blocks_above_limit(self) -> None:
        await _add_company("thresh-company", "Threshold Corp", threshold=500.0)
        with pytest.raises(ValueError, match="exceeds the"):
            await check_threshold("thresh-company", 1000.0)

    async def test_threshold_allows_below_limit(self) -> None:
        await _add_company("thresh-company2", "Threshold Corp 2", threshold=500.0)
        await check_threshold("thresh-company2", 499.99)  # should not raise

    async def test_no_threshold_allows_any_amount(self) -> None:
        await _add_company("no-thresh", "No Threshold Corp", threshold=None)
        await check_threshold("no-thresh", 1_000_000.0)  # should not raise


class TestAuditLog:
    async def test_audit_log_written_on_tool_call(self) -> None:
        await _add_company("audit-company", "Audit Corp")

        await audit.log_action(
            tool_name="test_tool",
            input_summary="test input",
            outcome="success",
            company_id="audit-company",
        )

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM audit_log WHERE company_id=? AND tool_name=?",
                ("audit-company", "test_tool"),
            )
            rows = await cursor.fetchall()

        assert len(rows) == 1
        assert rows[0]["outcome"] == "success"
        assert rows[0]["tool_name"] == "test_tool"

    async def test_audit_log_with_draft_action_id(self) -> None:
        await _add_company("audit-company2", "Audit Corp 2")
        draft = await create_draft(
            "audit-company2", "create_invoice", "Test draft",
            {"endpoint": "invoice", "method": "POST", "body": {}},
        )
        await audit.log_action(
            tool_name="create_invoice",
            input_summary="company=audit-company2",
            outcome="draft_created",
            company_id="audit-company2",
            draft_action_id=draft.draft_action_id,
        )

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM audit_log WHERE draft_action_id=?",
                (draft.draft_action_id,),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["outcome"] == "draft_created"

    async def test_audit_never_raises(self) -> None:
        # Even if the DB is in a weird state, audit should not raise
        await audit.log_action(
            tool_name="any_tool",
            input_summary="test",
            outcome="error",
            company_id=None,
            error_message="something went wrong",
        )  # should not raise


class TestDraftLifecycle:
    async def test_discard_draft(self) -> None:
        from qbo_mcp.safety.draft import discard_draft, get_draft

        await _add_company("discard-co", "Discard Corp")
        draft = await create_draft("discard-co", "create_customer", "Create test customer", {})
        await discard_draft(draft.draft_action_id)
        row = await get_draft(draft.draft_action_id)
        assert row is not None
        assert row["status"] == "discarded"

    async def test_discard_already_discarded_raises(self) -> None:
        from qbo_mcp.safety.draft import discard_draft

        await _add_company("discard-co2", "Discard Corp 2")
        draft = await create_draft("discard-co2", "create_customer", "desc", {})
        await discard_draft(draft.draft_action_id)
        with pytest.raises(ValueError, match="discarded"):
            await discard_draft(draft.draft_action_id)
