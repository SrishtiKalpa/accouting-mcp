from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from qbo_mcp.qbo.adapter import qbo_invoice_to_model
from qbo_mcp.qbo.models import Invoice
from qbo_mcp.tenant.registry import registry

_SAMPLE_INVOICE: dict[str, Any] = {
    "Id": "123",
    "DocNumber": "INV-001",
    "CustomerRef": {"value": "cust-1", "name": "Acme Corp"},
    "TxnDate": "2025-01-15",
    "DueDate": "2025-02-15",
    "TotalAmt": "1500.00",
    "SubTotal": "1500.00",
    "Balance": "1500.00",
    "SyncToken": "0",
    "EmailStatus": "Pending",
    "Line": [
        {
            "DetailType": "SalesItemLineDetail",
            "Amount": "1500.00",
            "Description": "Consulting services",
            "SalesItemLineDetail": {
                "ItemRef": {"value": "item-1"},
                "Qty": "10",
                "UnitPrice": "150.00",
            },
        }
    ],
}


class TestAdapterMapping:
    def test_list_invoices_returns_normalised_models(self) -> None:
        invoice = qbo_invoice_to_model(_SAMPLE_INVOICE)
        assert isinstance(invoice, Invoice)
        assert invoice.id == "123"
        assert invoice.number == "INV-001"
        assert invoice.customer_id == "cust-1"
        assert invoice.customer_name == "Acme Corp"
        assert invoice.total == 1500.0
        assert invoice.amount_due == 1500.0
        assert invoice.status == "sent"  # EmailStatus=Pending → sent
        assert len(invoice.line_items) == 1
        assert invoice.line_items[0].quantity == 10.0
        assert invoice.line_items[0].unit_price == 150.0

    def test_paid_invoice_status(self) -> None:
        raw = {**_SAMPLE_INVOICE, "Balance": "0.00", "PaymentStatus": "PAID"}
        invoice = qbo_invoice_to_model(raw)
        assert invoice.status == "paid"

    def test_draft_invoice_status(self) -> None:
        raw = {**_SAMPLE_INVOICE, "EmailStatus": "Draft", "Balance": "1500.00"}
        invoice = qbo_invoice_to_model(raw)
        assert invoice.status == "draft"


async def _insert_company(company_id: str) -> None:
    from qbo_mcp.db.connection import get_db
    async with get_db() as db:
        await db.execute(
            "INSERT INTO companies (id, name, realm_id, refresh_token, token_expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, "Test Corp", f"realm-{company_id}", "tok", 9999999999),
        )
        await db.commit()


class TestCreateInvoiceDraft:
    async def test_create_invoice_returns_draft_not_qbo_call(self) -> None:
        """create_invoice must produce a draft row in DB without calling QBO."""
        from qbo_mcp.safety.draft import create_draft, get_draft

        company_id = "test-company-1"
        await _insert_company(company_id)

        payload = {
            "endpoint": "invoice",
            "method": "POST",
            "body": {"CustomerRef": {"value": "cust-1"}, "Line": []},
        }
        result = await create_draft(
            company_id, "create_invoice",
            "Create invoice for customer cust-1 total $200.00", payload,
        )
        result_data = json.loads(result.model_dump_json())

        assert "draft_action_id" in result_data
        draft_id = result_data["draft_action_id"]

        row = await get_draft(draft_id)
        assert row is not None
        assert row["status"] == "pending"
        assert row["company_id"] == company_id


class TestCommitAction:
    async def test_commit_action_makes_qbo_post(self) -> None:
        """commit_draft must call the QBO API via the executor."""
        from qbo_mcp.safety.draft import commit_draft, create_draft

        company_id = "company-commit-1"
        await _insert_company(company_id)

        draft = await create_draft(
            company_id,
            "create_invoice",
            "Test invoice",
            {"endpoint": "invoice", "method": "POST", "body": {"test": True}},
        )

        executed: list[dict[str, Any]] = []

        async def mock_executor(
            co_id: str, tool_name: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            executed.append({"company_id": co_id, "payload": payload})
            return {"Invoice": {"Id": "new-1"}}

        result = await commit_draft(draft.draft_action_id, mock_executor)

        assert result["status"] == "committed"
        assert len(executed) == 1
        assert executed[0]["company_id"] == company_id

    async def test_commit_already_committed_raises(self) -> None:
        from qbo_mcp.safety.draft import commit_draft, create_draft

        company_id = "company-commit-2"
        await _insert_company(company_id)
        draft = await create_draft(company_id, "create_invoice", "desc", {"x": 1})

        async def mock_executor(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

        await commit_draft(draft.draft_action_id, mock_executor)

        with pytest.raises(ValueError, match="committed"):
            await commit_draft(draft.draft_action_id, mock_executor)
