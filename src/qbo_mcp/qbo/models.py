from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class QBOCompany(BaseModel):
    company_id: str
    realm_id: str
    name: str
    currency: str
    fiscal_year_start: str


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    amount: float
    account_ref: str | None = None


class Invoice(BaseModel):
    id: str
    number: str
    customer_id: str
    customer_name: str
    status: Literal["draft", "sent", "paid", "overdue", "void"]
    issue_date: str
    due_date: str
    line_items: list[LineItem]
    subtotal: float
    tax: float
    total: float
    amount_due: float
    currency: str
    sync_token: str | None = None


class Customer(BaseModel):
    id: str
    name: str
    email: str | None = None
    phone: str | None = None
    balance: float
    currency: str


class Vendor(BaseModel):
    id: str
    name: str
    email: str | None = None
    balance: float
    currency: str


class Bill(BaseModel):
    id: str
    vendor_id: str
    vendor_name: str
    status: Literal["draft", "open", "paid", "overdue"]
    issue_date: str
    due_date: str
    line_items: list[LineItem]
    total: float
    amount_due: float
    currency: str
    sync_token: str | None = None


class Account(BaseModel):
    id: str
    code: str | None = None
    name: str
    account_type: str
    balance: float
    currency: str


class PLReport(BaseModel):
    period_start: str
    period_end: str
    revenue: float
    cost_of_goods: float
    gross_profit: float
    operating_expenses: float
    net_income: float
    currency: str
    sections: list[dict[str, object]]


class ARAgingRow(BaseModel):
    customer_name: str
    current: float
    overdue_1_30: float
    overdue_31_60: float
    overdue_61_90: float
    overdue_90_plus: float
    total: float


class DraftAction(BaseModel):
    draft_action_id: str
    tool_name: str
    description: str
    preview: dict[str, object]
    message: str
