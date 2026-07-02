from __future__ import annotations

from typing import Any

from qbo_mcp.qbo.models import (
    Account,
    ARAgingRow,
    Bill,
    Customer,
    Invoice,
    LineItem,
    PLReport,
    Vendor,
)

_INVOICE_STATUS_MAP = {
    "Draft": "draft",
    "Pending": "sent",
    "Paid": "paid",
    "Voided": "void",
}

_BILL_STATUS_MAP = {
    "Draft": "draft",
    "Open": "open",
    "Paid": "paid",
}


def _map_line_item(raw: dict[str, Any]) -> LineItem:
    detail = (
        raw.get("SalesItemLineDetail")
        or raw.get("ItemBasedExpenseLineDetail")
        or raw.get("AccountBasedExpenseLineDetail")
        or {}
    )
    ref = detail.get("ItemRef") or detail.get("AccountRef") or {}
    return LineItem(
        description=raw.get("Description", ""),
        quantity=float(detail.get("Qty", 1)),
        unit_price=float(detail.get("UnitPrice", 0)),
        amount=float(raw.get("Amount", 0)),
        account_ref=str(ref.get("value", "")) or None,
    )


def qbo_invoice_to_model(raw: dict[str, Any]) -> Invoice:
    payment_status = raw.get("PaymentStatus", "")
    email_status = raw.get("EmailStatus", "Draft")
    balance = float(raw.get("Balance", 0))

    if raw.get("PrivateNote", "").lower() == "voided" or raw.get("TxnStatus") == "Voided":
        status = "void"
    elif payment_status == "PAID" or balance == 0:
        status = "paid"
    else:
        status = _INVOICE_STATUS_MAP.get(email_status, "draft")

    return Invoice(
        id=raw["Id"],
        number=raw.get("DocNumber", ""),
        customer_id=raw["CustomerRef"]["value"],
        customer_name=raw["CustomerRef"].get("name", ""),
        status=status,  # type: ignore[arg-type]
        issue_date=raw["TxnDate"],
        due_date=raw.get("DueDate", raw["TxnDate"]),
        line_items=[
            _map_line_item(li)
            for li in raw.get("Line", [])
            if li.get("DetailType") in ("SalesItemLineDetail", "DescriptionOnly")
        ],
        subtotal=float(raw.get("SubTotal", 0)),
        tax=float(raw.get("TxnTaxDetail", {}).get("TotalTax", 0)),
        total=float(raw.get("TotalAmt", 0)),
        amount_due=balance,
        currency=raw.get("CurrencyRef", {}).get("value", "USD"),
        sync_token=raw.get("SyncToken"),
    )


def qbo_customer_to_model(raw: dict[str, Any]) -> Customer:
    return Customer(
        id=raw["Id"],
        name=raw.get("DisplayName", raw.get("FullyQualifiedName", "")),
        email=raw.get("PrimaryEmailAddr", {}).get("Address") if raw.get("PrimaryEmailAddr") else None,
        phone=raw.get("PrimaryPhone", {}).get("FreeFormNumber") if raw.get("PrimaryPhone") else None,
        balance=float(raw.get("Balance", 0)),
        currency=raw.get("CurrencyRef", {}).get("value", "USD"),
    )


def qbo_vendor_to_model(raw: dict[str, Any]) -> Vendor:
    return Vendor(
        id=raw["Id"],
        name=raw.get("DisplayName", raw.get("PrintOnCheckName", "")),
        email=raw.get("PrimaryEmailAddr", {}).get("Address") if raw.get("PrimaryEmailAddr") else None,
        balance=float(raw.get("Balance", 0)),
        currency=raw.get("CurrencyRef", {}).get("value", "USD"),
    )


def qbo_bill_to_model(raw: dict[str, Any]) -> Bill:
    balance = float(raw.get("Balance", 0))
    pay_status = raw.get("PaymentStatus", "")
    if pay_status == "PAID" or balance == 0:
        status = "paid"
    elif raw.get("DueDate", "") < _today():
        status = "overdue"
    else:
        status = "open"

    return Bill(
        id=raw["Id"],
        vendor_id=raw["VendorRef"]["value"],
        vendor_name=raw["VendorRef"].get("name", ""),
        status=status,  # type: ignore[arg-type]
        issue_date=raw["TxnDate"],
        due_date=raw.get("DueDate", raw["TxnDate"]),
        line_items=[
            _map_line_item(li)
            for li in raw.get("Line", [])
            if li.get("DetailType")
            in ("ItemBasedExpenseLineDetail", "AccountBasedExpenseLineDetail")
        ],
        total=float(raw.get("TotalAmt", 0)),
        amount_due=balance,
        currency=raw.get("CurrencyRef", {}).get("value", "USD"),
        sync_token=raw.get("SyncToken"),
    )


def qbo_account_to_model(raw: dict[str, Any]) -> Account:
    return Account(
        id=raw["Id"],
        code=raw.get("AcctNum") or None,
        name=raw.get("FullyQualifiedName", raw.get("Name", "")),
        account_type=raw.get("AccountType", ""),
        balance=float(raw.get("CurrentBalance", 0)),
        currency=raw.get("CurrencyRef", {}).get("value", "USD"),
    )


def qbo_pl_report_to_model(raw: dict[str, Any], start_date: str, end_date: str) -> PLReport:
    sections: list[dict[str, object]] = []
    revenue = 0.0
    cogs = 0.0
    gross_profit = 0.0
    op_expenses = 0.0
    net_income = 0.0
    currency = "USD"

    header = raw.get("Header", {})
    currency = header.get("Currency", "USD")

    rows = raw.get("Rows", {}).get("Row", [])
    for row in rows:
        group = row.get("group", "")
        summary = row.get("Summary", {})
        col_data = summary.get("ColData", [])
        amount = float(col_data[1]["value"]) if len(col_data) > 1 and col_data[1].get("value") else 0.0

        if group == "Income":
            revenue = amount
        elif group == "COGS":
            cogs = amount
        elif group == "GrossProfit":
            gross_profit = amount
        elif group == "Expenses":
            op_expenses = amount
        elif group == "NetIncome":
            net_income = amount

        sections.append({"group": group, "amount": amount, "rows": row.get("Rows", {})})

    return PLReport(
        period_start=start_date,
        period_end=end_date,
        revenue=revenue,
        cost_of_goods=cogs,
        gross_profit=gross_profit,
        operating_expenses=op_expenses,
        net_income=net_income,
        currency=currency,
        sections=sections,
    )


def qbo_ar_aging_to_model(raw: dict[str, Any]) -> list[ARAgingRow]:
    rows: list[ARAgingRow] = []
    data_rows = raw.get("Rows", {}).get("Row", [])

    for row in data_rows:
        if row.get("type") != "Data":
            continue
        col_data = row.get("ColData", [])
        if len(col_data) < 7:
            continue

        def _val(i: int) -> float:
            v = col_data[i].get("value", "0") if i < len(col_data) else "0"
            return float(v) if v else 0.0

        rows.append(ARAgingRow(
            customer_name=col_data[0].get("value", ""),
            current=_val(1),
            overdue_1_30=_val(2),
            overdue_31_60=_val(3),
            overdue_61_90=_val(4),
            overdue_90_plus=_val(5),
            total=_val(6),
        ))

    return rows


def _today() -> str:
    from datetime import date
    return date.today().isoformat()
