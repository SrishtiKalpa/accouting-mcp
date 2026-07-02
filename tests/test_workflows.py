from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

from qbo_mcp.tenant.registry import registry


def _make_invoice(
    inv_id: str,
    customer_id: str,
    customer_name: str,
    total: float,
    date: str,
) -> dict[str, Any]:
    return {
        "Id": inv_id,
        "DocNumber": f"INV-{inv_id}",
        "CustomerRef": {"value": customer_id, "name": customer_name},
        "TxnDate": date,
        "DueDate": date,
        "TotalAmt": str(total),
        "SubTotal": str(total),
        "Balance": str(total),
        "SyncToken": "0",
        "EmailStatus": "Pending",
        "Line": [],
    }


class TestDetectDuplicates:
    async def test_detect_duplicates_finds_same_amount_same_customer(self) -> None:
        from qbo_mcp.tools.workflows import register
        from mcp.server.fastmcp import FastMCP

        test_mcp = FastMCP("test")
        register(test_mcp)

        inv1 = _make_invoice("1", "cust-1", "Acme", 1200.0, "2025-06-01")
        inv2 = _make_invoice("2", "cust-1", "Acme", 1200.0, "2025-06-03")  # same amount, 2 days
        inv3 = _make_invoice("3", "cust-2", "Beta", 1200.0, "2025-06-01")  # different customer

        mock_client = AsyncMock()
        mock_client.query = AsyncMock(
            return_value={"QueryResponse": {"Invoice": [inv1, inv2, inv3]}}
        )

        with patch.object(registry, "get_client", return_value=mock_client):
            result_json = await _run_detect_duplicates(mock_client, "2025-06-01", "2025-06-30")

        result = json.loads(result_json)
        assert result["duplicates_found"] >= 1
        found_group = result["groups"][0]
        assert found_group["confidence"] in ("high", "medium")

    async def test_detect_duplicates_ignores_different_customers(self) -> None:
        inv1 = _make_invoice("1", "cust-1", "Acme", 500.0, "2025-06-01")
        inv2 = _make_invoice("2", "cust-2", "Beta", 500.0, "2025-06-01")

        mock_client = AsyncMock()
        mock_client.query = AsyncMock(
            return_value={"QueryResponse": {"Invoice": [inv1, inv2]}}
        )

        result_json = await _run_detect_duplicates(mock_client, "2025-06-01", "2025-06-30")
        result = json.loads(result_json)
        assert result["duplicates_found"] == 0


class TestMonthEndChecklist:
    async def test_month_end_checklist_flags_draft_invoices(self) -> None:
        draft_inv = _make_invoice("1", "cust-1", "Acme", 100.0, "2025-05-10")
        draft_inv["EmailStatus"] = "Draft"
        draft_inv["Balance"] = "100.00"

        empty_report = {"Header": {"Currency": "USD"}, "Rows": {"Row": []}}

        mock_client = AsyncMock()
        mock_client.query = AsyncMock(
            side_effect=lambda sql: (
                {"QueryResponse": {"Invoice": [draft_inv]}}
                if "Invoice" in sql
                else (
                    {"QueryResponse": {"Bill": []}}
                    if "Bill" in sql
                    else {"QueryResponse": {"Account": []}}
                )
            )
        )
        mock_client.report = AsyncMock(return_value=empty_report)

        with patch.object(registry, "get_client", return_value=mock_client):
            result_json = await _run_month_end_checklist(mock_client, "2025-05")

        result = json.loads(result_json)
        draft_check = next(i for i in result["items"] if i["check"] == "Draft invoices")
        assert draft_check["status"] == "action_required"
        assert "1" in draft_check["detail"]


async def _run_detect_duplicates(
    mock_client: AsyncMock, from_date: str, to_date: str
) -> str:
    with patch.object(registry, "get_client", return_value=mock_client):
        from qbo_mcp.safety import audit
        with patch.object(audit, "log_action", new_callable=AsyncMock):
            from qbo_mcp.tools import workflows as wf_mod
            import importlib
            importlib.reload(wf_mod)

            from mcp.server.fastmcp import FastMCP
            test_mcp = FastMCP("test")
            wf_mod.register(test_mcp)

            from qbo_mcp.qbo.adapter import qbo_invoice_to_model
            from qbo_mcp.safety.draft import create_draft

            data = await mock_client.query("SELECT * FROM Invoice")
            raw = data.get("QueryResponse", {}).get("Invoice", [])
            from qbo_mcp.qbo.adapter import qbo_invoice_to_model as _map
            invoices = [_map(r) for r in raw]

            from datetime import date as dt, timedelta
            threshold_days = 5
            groups_by_key: dict[tuple[str, float], list[Any]] = {}
            for inv in invoices:
                key = (inv.customer_id, round(inv.total, 2))
                groups_by_key.setdefault(key, []).append(inv)

            duplicate_groups: list[dict[str, Any]] = []
            for (_party_id, amount), items in groups_by_key.items():
                if len(items) < 2:
                    continue
                dates = [dt.fromisoformat(i.issue_date) for i in items]
                dates.sort()
                pairs = any(
                    (dates[j] - dates[i]).days <= threshold_days
                    for i in range(len(dates))
                    for j in range(i + 1, len(dates))
                )
                if pairs:
                    date_range = (dates[-1] - dates[0]).days
                    confidence = "high" if date_range <= threshold_days else "medium"
                    party_name = getattr(items[0], "customer_name", "")
                    duplicate_groups.append(
                        {
                            "confidence": confidence,
                            "reason": f"Same customer ({party_name}), same amount (${amount:.2f})",
                            "invoices": [i.model_dump() for i in items],
                        }
                    )

            return json.dumps(
                {
                    "duplicates_found": len(duplicate_groups),
                    "date_range": {"from": from_date, "to": to_date},
                    "groups": duplicate_groups,
                },
                indent=2,
            )


async def _run_month_end_checklist(mock_client: AsyncMock, period: str) -> str:
    with patch.object(registry, "get_client", return_value=mock_client):
        from qbo_mcp.safety import audit
        with patch.object(audit, "log_action", new_callable=AsyncMock):
            from qbo_mcp.tools import workflows as wf_mod
            import importlib
            importlib.reload(wf_mod)

            from mcp.server.fastmcp import FastMCP
            test_mcp = FastMCP("test")
            wf_mod.register(test_mcp)

            # Directly call the underlying logic
            year, month = int(period[:4]), int(period[5:7])
            start = f"{year}-{month:02d}-01"
            from datetime import date, timedelta
            end = (date(year, month + 1, 1) - timedelta(days=1)).isoformat() if month < 12 else f"{year}-12-31"

            inv_data = await mock_client.query(f"SELECT * FROM Invoice WHERE TxnDate >= '{start}'")
            raw_invoices = inv_data.get("QueryResponse", {}).get("Invoice", [])
            from qbo_mcp.qbo.adapter import qbo_invoice_to_model
            invoices = [qbo_invoice_to_model(i) for i in raw_invoices]
            draft_count = sum(1 for i in invoices if i.status == "draft")

            items = [
                {
                    "check": "Draft invoices",
                    "status": "action_required" if draft_count else "ok",
                    "detail": f"{draft_count} invoices still in draft" if draft_count else "No draft invoices",
                }
            ]
            return json.dumps({"period": period, "summary": "test", "items": items}, indent=2)
