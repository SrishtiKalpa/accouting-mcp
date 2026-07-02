from __future__ import annotations

import json

import structlog
from mcp.server.fastmcp import FastMCP

from qbo_mcp.qbo.adapter import qbo_ar_aging_to_model, qbo_pl_report_to_model
from qbo_mcp.safety import audit
from qbo_mcp.tenant.registry import registry

log = structlog.get_logger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: ANN001
    @mcp.tool()
    async def get_pl_report(
        company_id: str,
        start_date: str,
        end_date: str,
        period: str = "Monthly",
    ) -> str:
        """
        Get the Profit & Loss report for a connected QuickBooks company.

        start_date and end_date are ISO format: YYYY-MM-DD.
        period can be Monthly or Quarterly for column grouping.
        Returns structured P&L with revenue, COGS, gross profit, expenses, and net income.
        """
        client = await registry.get_client(company_id)
        data = await client.report(
            "ProfitAndLoss",
            {
                "start_date": start_date,
                "end_date": end_date,
                "summarize_column_by": period,
            },
        )
        report = qbo_pl_report_to_model(data, start_date, end_date)
        await audit.log_action(
            "get_pl_report", f"company={company_id} {start_date} to {end_date}",
            "success", company_id=company_id,
        )
        return json.dumps(report.model_dump(), indent=2)

    @mcp.tool()
    async def get_balance_sheet(company_id: str, as_of_date: str) -> str:
        """
        Get the Balance Sheet for a connected QuickBooks company as of a given date.

        as_of_date is ISO format: YYYY-MM-DD.
        Returns assets, liabilities, and equity sections.
        """
        client = await registry.get_client(company_id)
        data = await client.report("BalanceSheet", {"date": as_of_date})
        await audit.log_action(
            "get_balance_sheet", f"company={company_id} as_of={as_of_date}",
            "success", company_id=company_id,
        )
        return json.dumps(data, indent=2)

    @mcp.tool()
    async def get_cash_flow(
        company_id: str, start_date: str, end_date: str
    ) -> str:
        """
        Get the Cash Flow Statement for a connected QuickBooks company.

        start_date and end_date are ISO format: YYYY-MM-DD.
        Returns operating, investing, and financing cash flow sections.
        """
        client = await registry.get_client(company_id)
        data = await client.report(
            "CashFlow",
            {"start_date": start_date, "end_date": end_date},
        )
        await audit.log_action(
            "get_cash_flow", f"company={company_id} {start_date} to {end_date}",
            "success", company_id=company_id,
        )
        return json.dumps(data, indent=2)

    @mcp.tool()
    async def get_ar_aging(company_id: str) -> str:
        """
        Get the Accounts Receivable aging report.

        Returns customer balances bucketed into: current, 1-30, 31-60, 61-90, and 90+ days overdue.
        Use this to identify slow-paying customers and prioritise collections.
        """
        client = await registry.get_client(company_id)
        data = await client.report("AgedReceivableDetail")
        rows = qbo_ar_aging_to_model(data)
        await audit.log_action(
            "get_ar_aging", f"company={company_id}", "success", company_id=company_id,
        )
        return json.dumps([r.model_dump() for r in rows], indent=2)

    @mcp.tool()
    async def get_ap_aging(company_id: str) -> str:
        """
        Get the Accounts Payable aging report.

        Returns vendor balances bucketed into: current, 1-30, 31-60, 61-90, and 90+ days overdue.
        Use this to understand upcoming payment obligations.
        """
        client = await registry.get_client(company_id)
        data = await client.report("AgedPayableDetail")
        await audit.log_action(
            "get_ap_aging", f"company={company_id}", "success", company_id=company_id,
        )
        return json.dumps(data, indent=2)

    @mcp.tool()
    async def get_trial_balance(company_id: str, as_of_date: str) -> str:
        """
        Get the Trial Balance for a connected QuickBooks company.

        as_of_date is ISO format: YYYY-MM-DD.
        Returns all account balances in debit/credit format.
        """
        client = await registry.get_client(company_id)
        data = await client.report("TrialBalance", {"date": as_of_date})
        await audit.log_action(
            "get_trial_balance", f"company={company_id} as_of={as_of_date}",
            "success", company_id=company_id,
        )
        return json.dumps(data, indent=2)
