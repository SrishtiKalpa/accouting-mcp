from __future__ import annotations

from typing import Any

from qbo_mcp.qbo.adapter import qbo_ar_aging_to_model, qbo_pl_report_to_model
from qbo_mcp.qbo.models import ARAgingRow, PLReport


def _make_pl_response(revenue: float, cogs: float, expenses: float) -> dict[str, Any]:
    def col_row(group: str, amount: float) -> dict[str, Any]:
        return {
            "group": group,
            "Summary": {"ColData": [{"value": group}, {"value": str(amount)}]},
            "Rows": {},
        }

    return {
        "Header": {"Currency": "USD"},
        "Rows": {
            "Row": [
                col_row("Income", revenue),
                col_row("COGS", cogs),
                col_row("GrossProfit", revenue - cogs),
                col_row("Expenses", expenses),
                col_row("NetIncome", revenue - cogs - expenses),
            ]
        },
    }


def _make_ar_aging_response(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "Rows": {
            "Row": [
                {
                    "type": "Data",
                    "ColData": [
                        {"value": r["name"]},
                        {"value": str(r["current"])},
                        {"value": str(r["d30"])},
                        {"value": str(r["d60"])},
                        {"value": str(r["d90"])},
                        {"value": str(r["d90plus"])},
                        {"value": str(r["total"])},
                    ],
                }
                for r in rows
            ]
        }
    }


class TestPLReport:
    def test_get_pl_report_maps_sections(self) -> None:
        raw = _make_pl_response(revenue=100_000, cogs=40_000, expenses=30_000)
        report = qbo_pl_report_to_model(raw, "2025-01-01", "2025-03-31")

        assert isinstance(report, PLReport)
        assert report.revenue == 100_000
        assert report.cost_of_goods == 40_000
        assert report.gross_profit == 60_000
        assert report.operating_expenses == 30_000
        assert report.net_income == 30_000
        assert report.currency == "USD"
        assert report.period_start == "2025-01-01"
        assert report.period_end == "2025-03-31"
        assert len(report.sections) == 5

    def test_zero_values_handled(self) -> None:
        raw = _make_pl_response(revenue=0, cogs=0, expenses=0)
        report = qbo_pl_report_to_model(raw, "2025-01-01", "2025-01-31")
        assert report.net_income == 0
        assert report.revenue == 0


class TestARAgingBuckets:
    def test_get_ar_aging_buckets(self) -> None:
        raw = _make_ar_aging_response(
            [
                {
                    "name": "Acme Corp",
                    "current": 500,
                    "d30": 200,
                    "d60": 100,
                    "d90": 50,
                    "d90plus": 0,
                    "total": 850,
                },
                {
                    "name": "Beta LLC",
                    "current": 0,
                    "d30": 0,
                    "d60": 0,
                    "d90": 0,
                    "d90plus": 1200,
                    "total": 1200,
                },
            ]
        )
        rows = qbo_ar_aging_to_model(raw)

        assert len(rows) == 2
        assert isinstance(rows[0], ARAgingRow)
        assert rows[0].customer_name == "Acme Corp"
        assert rows[0].current == 500
        assert rows[0].overdue_1_30 == 200
        assert rows[0].overdue_61_90 == 50
        assert rows[0].total == 850

        assert rows[1].customer_name == "Beta LLC"
        assert rows[1].overdue_90_plus == 1200

    def test_empty_ar_aging(self) -> None:
        raw: dict[str, Any] = {"Rows": {"Row": []}}
        rows = qbo_ar_aging_to_model(raw)
        assert rows == []

    def test_non_data_rows_skipped(self) -> None:
        raw = {
            "Rows": {
                "Row": [
                    {"type": "Section", "ColData": []},
                    {
                        "type": "Data",
                        "ColData": [
                            {"value": "Customer A"},
                            {"value": "100"},
                            {"value": "0"},
                            {"value": "0"},
                            {"value": "0"},
                            {"value": "0"},
                            {"value": "100"},
                        ],
                    },
                ]
            }
        }
        rows = qbo_ar_aging_to_model(raw)
        assert len(rows) == 1
        assert rows[0].customer_name == "Customer A"
