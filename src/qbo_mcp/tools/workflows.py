from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from qbo_mcp.qbo.adapter import qbo_invoice_to_model, qbo_pl_report_to_model
from qbo_mcp.safety import audit
from qbo_mcp.tenant.registry import registry

log = structlog.get_logger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: ANN001
    @mcp.tool()
    async def detect_duplicate_transactions(
        company_id: str,
        entity_type: str = "invoices",
        from_date: str = "",
        to_date: str = "",
        threshold_days: int = 5,
    ) -> str:
        """
        Detect potential duplicate invoices or bills in a date range.

        entity_type: "invoices" or "bills"
        from_date, to_date: ISO format YYYY-MM-DD
        threshold_days: flag records with same customer/vendor and same amount within N days.
        Returns groups of potential duplicates with confidence ratings.
        """
        client = await registry.get_client(company_id)
        if not from_date:
            from_date = (date.today() - timedelta(days=90)).isoformat()
        if not to_date:
            to_date = date.today().isoformat()

        entity = "Invoice" if entity_type == "invoices" else "Bill"
        sql = (
            f"SELECT * FROM {entity} WHERE TxnDate >= '{from_date}' "
            f"AND TxnDate <= '{to_date}' ORDERBY TxnDate MAXRESULTS 1000"
        )
        data = await client.query(sql)
        raw_list = data.get("QueryResponse", {}).get(entity, [])

        if entity_type == "invoices":
            records = [qbo_invoice_to_model(r) for r in raw_list]
            groups_by_key: dict[tuple[str, float], list[Any]] = {}
            for inv in records:
                key = (inv.customer_id, round(inv.total, 2))
                groups_by_key.setdefault(key, []).append(inv)
        else:
            from qbo_mcp.qbo.adapter import qbo_bill_to_model
            bills = [qbo_bill_to_model(r) for r in raw_list]
            groups_by_key = {}
            for bill in bills:
                key = (bill.vendor_id, round(bill.total, 2))
                groups_by_key.setdefault(key, []).append(bill)

        duplicate_groups: list[dict[str, Any]] = []
        for (_party_id, amount), items in groups_by_key.items():
            if len(items) < 2:
                continue
            # Check if any pair is within threshold_days
            dates = [date.fromisoformat(getattr(i, "issue_date")) for i in items]
            dates.sort()
            pairs_within_threshold = any(
                (dates[j] - dates[i]).days <= threshold_days
                for i in range(len(dates))
                for j in range(i + 1, len(dates))
            )
            if pairs_within_threshold:
                date_range = (dates[-1] - dates[0]).days
                confidence = "high" if date_range <= threshold_days else "medium"
                party_name = getattr(items[0], "customer_name", None) or getattr(
                    items[0], "vendor_name", ""
                )
                duplicate_groups.append(
                    {
                        "confidence": confidence,
                        "reason": (
                            f"Same {'customer' if entity_type == 'invoices' else 'vendor'} "
                            f"({party_name}), same amount (${amount:.2f}), "
                            f"{date_range} days apart"
                        ),
                        entity_type: [i.model_dump() for i in items],
                    }
                )

        result = {
            "duplicates_found": len(duplicate_groups),
            "date_range": {"from": from_date, "to": to_date},
            "groups": duplicate_groups,
        }
        await audit.log_action(
            "detect_duplicate_transactions",
            f"company={company_id} entity={entity_type} found={len(duplicate_groups)}",
            "success",
            company_id=company_id,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def month_end_checklist(company_id: str, period: str) -> str:
        """
        Run a month-end close checklist for a given period.

        period: YYYY-MM format (e.g. "2025-05")
        Checks: draft invoices, unpaid bills, overdue AR, uncategorised accounts, income trend.
        Returns a structured checklist with status for each check.
        """
        client = await registry.get_client(company_id)
        year, month = int(period[:4]), int(period[5:7])
        start = f"{year}-{month:02d}-01"
        # Last day of month
        if month == 12:
            end = f"{year}-12-31"
        else:
            end = (date(year, month + 1, 1) - timedelta(days=1)).isoformat()

        items: list[dict[str, str]] = []

        # Check 1: Draft invoices
        sql_draft = (
            f"SELECT * FROM Invoice WHERE TxnDate >= '{start}' "
            f"AND TxnDate <= '{end}' MAXRESULTS 200"
        )
        inv_data = await client.query(sql_draft)
        raw_invoices = inv_data.get("QueryResponse", {}).get("Invoice", [])
        invoices = [qbo_invoice_to_model(i) for i in raw_invoices]
        draft_count = sum(1 for i in invoices if i.status == "draft")
        items.append(
            {
                "check": "Draft invoices",
                "status": "action_required" if draft_count else "ok",
                "detail": (
                    f"{draft_count} invoices still in draft" if draft_count else "No draft invoices"
                ),
            }
        )

        # Check 2: Unpaid bills due this period
        sql_bills = (
            f"SELECT * FROM Bill WHERE DueDate >= '{start}' "
            f"AND DueDate <= '{end}' MAXRESULTS 200"
        )
        bill_data = await client.query(sql_bills)
        raw_bills = bill_data.get("QueryResponse", {}).get("Bill", [])
        unpaid_bills = [b for b in raw_bills if float(b.get("Balance", 0)) > 0]
        items.append(
            {
                "check": "Unpaid bills due",
                "status": "warning" if unpaid_bills else "ok",
                "detail": (
                    f"{len(unpaid_bills)} unpaid bills due this period"
                    if unpaid_bills
                    else "All bills paid"
                ),
            }
        )

        # Check 3: AR 90+ days overdue
        ar_data = await client.report("AgedReceivableDetail")
        ar_rows = ar_data.get("Rows", {}).get("Row", [])
        overdue_90 = sum(
            float(r.get("ColData", [{}] * 6)[5].get("value", 0) or 0)
            for r in ar_rows
            if r.get("type") == "Data"
        )
        items.append(
            {
                "check": "AR 90+ days overdue",
                "status": "warning" if overdue_90 > 0 else "ok",
                "detail": (
                    f"${overdue_90:.2f} in receivables 90+ days overdue"
                    if overdue_90 > 0
                    else "No receivables 90+ days overdue"
                ),
            }
        )

        # Check 4: Uncategorised accounts with non-zero balance
        sql_accounts = "SELECT * FROM Account MAXRESULTS 1000"
        acc_data = await client.query(sql_accounts)
        raw_accounts = acc_data.get("QueryResponse", {}).get("Account", [])
        uncategorised = [
            a
            for a in raw_accounts
            if any(
                kw in a.get("Name", "").lower()
                for kw in ("uncategorised", "uncategorized", "ask my accountant")
            )
            and float(a.get("CurrentBalance", 0)) != 0
        ]
        items.append(
            {
                "check": "Uncategorised accounts",
                "status": "action_required" if uncategorised else "ok",
                "detail": (
                    f"{len(uncategorised)} uncategorised accounts with non-zero balance"
                    if uncategorised
                    else "No uncategorised accounts with balance"
                ),
            }
        )

        # Check 5: Income vs prior period
        prior_year, prior_month = (year, month - 1) if month > 1 else (year - 1, 12)
        prior_start = f"{prior_year}-{prior_month:02d}-01"
        if prior_month == 12:
            prior_end = f"{prior_year}-12-31"
        else:
            prior_end = (date(prior_year, prior_month + 1, 1) - timedelta(days=1)).isoformat()

        try:
            pl_this = await client.report(
                "ProfitAndLoss", {"start_date": start, "end_date": end}
            )
            pl_prior = await client.report(
                "ProfitAndLoss", {"start_date": prior_start, "end_date": prior_end}
            )
            this_model = qbo_pl_report_to_model(pl_this, start, end)
            prior_model = qbo_pl_report_to_model(pl_prior, prior_start, prior_end)
            if prior_model.revenue != 0:
                change_pct = (
                    (this_model.revenue - prior_model.revenue) / prior_model.revenue * 100
                )
                direction = "up" if change_pct >= 0 else "down"
                items.append(
                    {
                        "check": "Income vs prior period",
                        "status": "info",
                        "detail": (
                            f"Revenue ${this_model.revenue:.2f} this period, "
                            f"${prior_model.revenue:.2f} prior period "
                            f"({direction} {abs(change_pct):.1f}%)"
                        ),
                    }
                )
        except Exception:
            items.append(
                {"check": "Income vs prior period", "status": "info", "detail": "Could not fetch P&L"}
            )

        action_required = sum(1 for i in items if i["status"] == "action_required")
        warnings = sum(1 for i in items if i["status"] == "warning")
        summary = (
            f"{action_required} items need attention"
            if action_required
            else (f"{warnings} warnings" if warnings else "All checks passed")
        )

        result = {"period": period, "summary": summary, "items": items}
        await audit.log_action(
            "month_end_checklist", f"company={company_id} period={period}",
            "success", company_id=company_id,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def flag_unusual_transactions(
        company_id: str,
        lookback_months: int = 12,
        std_dev_threshold: float = 2.0,
    ) -> str:
        """
        Flag expense accounts with unusually high spending compared to their historical average.

        lookback_months: how many months of history to analyse (default 12).
        std_dev_threshold: flag if spend > mean + (threshold × std_dev) (default 2.0).
        Returns flagged accounts with current spend, mean, and deviation.
        """
        client = await registry.get_client(company_id)
        today = date.today()
        months: list[tuple[str, str]] = []
        for i in range(lookback_months):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            start = f"{y}-{m:02d}-01"
            if m == 12:
                end = f"{y}-12-31"
            else:
                end = (date(y, m + 1, 1) - timedelta(days=1)).isoformat()
            months.append((start, end))

        months.reverse()

        monthly_pl: list[dict[str, float]] = []
        for start, end in months:
            try:
                data = await client.report(
                    "ProfitAndLoss",
                    {"start_date": start, "end_date": end, "summarize_column_by": "Month"},
                )
                model = qbo_pl_report_to_model(data, start, end)
                expenses_by_account: dict[str, float] = {}
                for section in model.sections:
                    if section.get("group") == "Expenses":
                        rows_data = section.get("rows", {})
                        if isinstance(rows_data, dict):
                            for row in rows_data.get("Row", []):
                                col_data = row.get("ColData", [])
                                if len(col_data) >= 2 and row.get("type") == "Data":
                                    acct_name = col_data[0].get("value", "")
                                    amount = float(col_data[1].get("value", 0) or 0)
                                    expenses_by_account[acct_name] = amount
                monthly_pl.append(expenses_by_account)
            except Exception:
                monthly_pl.append({})

        all_accounts: set[str] = set()
        for month_data in monthly_pl:
            all_accounts.update(month_data.keys())

        flagged: list[dict[str, object]] = []
        current_period = monthly_pl[-1] if monthly_pl else {}
        history = monthly_pl[:-1]

        for account in sorted(all_accounts):
            historical_amounts = [h.get(account, 0.0) for h in history if account in h]
            if len(historical_amounts) < 3:
                continue
            mean = statistics.mean(historical_amounts)
            std_dev = statistics.stdev(historical_amounts) if len(historical_amounts) > 1 else 0.0
            current = current_period.get(account, 0.0)
            threshold = mean + std_dev_threshold * std_dev
            if current > threshold and current > 0:
                flagged.append(
                    {
                        "account": account,
                        "current_month": current,
                        "historical_mean": round(mean, 2),
                        "std_dev": round(std_dev, 2),
                        "threshold": round(threshold, 2),
                        "deviation_from_mean": round(current - mean, 2),
                        "deviation_std_devs": round(
                            (current - mean) / std_dev if std_dev > 0 else 0, 2
                        ),
                    }
                )

        result = {
            "lookback_months": lookback_months,
            "std_dev_threshold": std_dev_threshold,
            "analysis_period": {
                "from": months[0][0] if months else "",
                "to": months[-1][1] if months else "",
            },
            "flagged_count": len(flagged),
            "flagged_accounts": flagged,
        }
        await audit.log_action(
            "flag_unusual_transactions",
            f"company={company_id} flagged={len(flagged)}",
            "success",
            company_id=company_id,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def summarize_financial_health(company_id: str) -> str:
        """
        Summarise the overall financial health of a connected QuickBooks company.

        Analyses P&L trend (3 months + YoY), AR/AP aging, and balance sheet ratios.
        Returns a structured health summary with revenue trend, cash position, quick ratio,
        and overdue percentages.
        """
        client = await registry.get_client(company_id)
        today = date.today()

        # Last 3 months P&L
        three_months_ago = (today - timedelta(days=90)).isoformat()
        pl_recent = await client.report(
            "ProfitAndLoss",
            {"start_date": three_months_ago, "end_date": today.isoformat()},
        )
        pl_model = qbo_pl_report_to_model(pl_recent, three_months_ago, today.isoformat())

        # Same period last year
        year_ago_start = date(today.year - 1, today.month, 1).isoformat()
        year_ago_end = (today - timedelta(days=365)).isoformat()
        try:
            pl_yoy = await client.report(
                "ProfitAndLoss",
                {"start_date": year_ago_start, "end_date": year_ago_end},
            )
            pl_yoy_model = qbo_pl_report_to_model(pl_yoy, year_ago_start, year_ago_end)
            yoy_change = (
                (pl_model.revenue - pl_yoy_model.revenue) / pl_yoy_model.revenue * 100
                if pl_yoy_model.revenue != 0
                else 0.0
            )
        except Exception:
            yoy_change = 0.0

        if yoy_change > 5:
            revenue_trend = "increasing"
        elif yoy_change < -5:
            revenue_trend = "decreasing"
        else:
            revenue_trend = "stable"

        # AR aging
        ar_data = await client.report("AgedReceivableDetail")
        ar_rows = ar_data.get("Rows", {}).get("Row", [])
        ar_total = 0.0
        ar_overdue = 0.0
        for row in ar_rows:
            if row.get("type") != "Data":
                continue
            cols = row.get("ColData", [])
            if len(cols) >= 7:
                total_val = float(cols[6].get("value", 0) or 0)
                current_val = float(cols[1].get("value", 0) or 0)
                ar_total += total_val
                ar_overdue += total_val - current_val

        ar_overdue_pct = (ar_overdue / ar_total * 100) if ar_total > 0 else 0.0

        # AP aging
        ap_data = await client.report("AgedPayableDetail")
        ap_rows = ap_data.get("Rows", {}).get("Row", [])
        ap_total = 0.0
        ap_overdue = 0.0
        for row in ap_rows:
            if row.get("type") != "Data":
                continue
            cols = row.get("ColData", [])
            if len(cols) >= 7:
                total_val = float(cols[6].get("value", 0) or 0)
                current_val = float(cols[1].get("value", 0) or 0)
                ap_total += total_val
                ap_overdue += total_val - current_val
        ap_overdue_pct = (ap_overdue / ap_total * 100) if ap_total > 0 else 0.0

        # Balance sheet
        bs_data = await client.report("BalanceSheet", {"date": today.isoformat()})
        cash_position = 0.0
        current_assets = 0.0
        current_liabilities = 0.0

        bs_rows = bs_data.get("Rows", {}).get("Row", [])
        for row in bs_rows:
            group = row.get("group", "")
            summary = row.get("Summary", {})
            col_data = summary.get("ColData", [])
            amount = float(col_data[1].get("value", 0) or 0) if len(col_data) > 1 else 0.0
            if group in ("Cash", "BankAccounts"):
                cash_position += amount
            elif group in ("CurrentAssets", "OtherCurrentAssets"):
                current_assets += amount
            elif group in ("CurrentLiabilities", "OtherCurrentLiabilities"):
                current_liabilities += amount

        quick_ratio = (
            (cash_position + ar_total) / current_liabilities
            if current_liabilities > 0
            else 0.0
        )

        # Build flags
        flags: list[str] = []
        if ar_overdue_pct > 15:
            flags.append(
                f"{ar_overdue_pct:.1f}% of receivables are overdue — consider chasing collections"
            )
        if ap_overdue_pct > 10:
            flags.append(f"{ap_overdue_pct:.1f}% of payables are overdue")
        if quick_ratio < 1.0 and quick_ratio > 0:
            flags.append(f"Quick ratio is {quick_ratio:.2f} — below 1.0 may indicate liquidity risk")
        if revenue_trend == "increasing":
            flags.append(f"Revenue growing {abs(yoy_change):.1f}% year-over-year")
        elif revenue_trend == "decreasing":
            flags.append(f"Revenue declining {abs(yoy_change):.1f}% year-over-year — investigate")

        result: dict[str, object] = {
            "as_of": today.isoformat(),
            "revenue_trend": revenue_trend,
            "revenue_yoy_change_pct": round(yoy_change, 1),
            "net_income": round(pl_model.net_income, 2),
            "cash_position": round(cash_position, 2),
            "quick_ratio": round(quick_ratio, 2),
            "ar_overdue_pct": round(ar_overdue_pct, 1),
            "ap_overdue_pct": round(ap_overdue_pct, 1),
            "flags": flags,
        }
        await audit.log_action(
            "summarize_financial_health", f"company={company_id}",
            "success", company_id=company_id,
        )
        return json.dumps(result, indent=2)
