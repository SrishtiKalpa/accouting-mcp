from __future__ import annotations

import json
from typing import Any, Literal

import structlog
from mcp.server.fastmcp import FastMCP

from qbo_mcp.qbo.adapter import (
    qbo_bill_to_model,
    qbo_customer_to_model,
    qbo_invoice_to_model,
    qbo_vendor_to_model,
)
from qbo_mcp.safety import audit, draft, guards
from qbo_mcp.tenant.registry import registry

log = structlog.get_logger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: ANN001
    # ── Invoices ──────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_invoices(
        company_id: str,
        status: Literal["draft", "sent", "paid", "overdue", "void"] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        customer_id: str | None = None,
        limit: int = 50,
    ) -> str:
        """
        List invoices for a connected QuickBooks company.

        Use this to find invoices, check payment status, or get an overview of receivables.
        Call list_companies first to get valid company_id values.
        from_date and to_date accept ISO format: YYYY-MM-DD.
        Returns JSON array of invoice objects.
        """
        client = await registry.get_client(company_id)
        conditions = ["SELECT * FROM Invoice"]
        clauses: list[str] = []
        if from_date:
            clauses.append(f"TxnDate >= '{from_date}'")
        if to_date:
            clauses.append(f"TxnDate <= '{to_date}'")
        if customer_id:
            clauses.append(f"CustomerRef = '{customer_id}'")
        if clauses:
            conditions.append("WHERE " + " AND ".join(clauses))
        conditions.append(f"ORDERBY TxnDate DESC MAXRESULTS {limit}")
        sql = " ".join(conditions)

        data = await client.query(sql)
        raw_invoices = data.get("QueryResponse", {}).get("Invoice", [])
        invoices = [qbo_invoice_to_model(i) for i in raw_invoices]

        if status:
            invoices = [i for i in invoices if i.status == status]

        await audit.log_action(
            "list_invoices",
            f"company={company_id} status={status} from={from_date} to={to_date}",
            "success",
            company_id=company_id,
        )
        return json.dumps([i.model_dump() for i in invoices], indent=2)

    @mcp.tool()
    async def get_invoice(company_id: str, invoice_id: str) -> str:
        """
        Get a single invoice with full line items.

        Use this when you need complete details about a specific invoice.
        Returns a single invoice object with all line items.
        """
        client = await registry.get_client(company_id)
        data = await client.get(f"invoice/{invoice_id}")
        invoice = qbo_invoice_to_model(data["Invoice"])
        await audit.log_action(
            "get_invoice", f"company={company_id} invoice={invoice_id}", "success",
            company_id=company_id,
        )
        return json.dumps(invoice.model_dump(), indent=2)

    @mcp.tool()
    async def create_invoice(
        company_id: str,
        customer_id: str,
        line_items: str,
        due_date: str | None = None,
        memo: str | None = None,
    ) -> str:
        """
        Create a new invoice for a customer. This is a WRITE operation — a draft is created first.

        line_items must be a JSON array: [{"description": "...", "quantity": 1, "unit_price": 100.0, "item_id": "..."}]
        item_id is the QBO Item/Service ID (get from list_accounts or a previous invoice).
        Returns a draft_action_id. Call commit_action to write to QuickBooks.
        """
        await guards.check_read_only(company_id)
        items = json.loads(line_items)
        total = sum(float(i.get("quantity", 1)) * float(i.get("unit_price", 0)) for i in items)
        await guards.check_threshold(company_id, total)

        payload: dict[str, Any] = {
            "CustomerRef": {"value": customer_id},
            "Line": [
                {
                    "Amount": float(item["quantity"]) * float(item["unit_price"]),
                    "DetailType": "SalesItemLineDetail",
                    "Description": item.get("description", ""),
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": item["item_id"]},
                        "Qty": float(item["quantity"]),
                        "UnitPrice": float(item["unit_price"]),
                    },
                }
                for item in items
            ],
        }
        if due_date:
            payload["DueDate"] = due_date
        if memo:
            payload["PrivateNote"] = memo

        result = await draft.create_draft(
            company_id, "create_invoice",
            f"Create invoice for customer {customer_id} total ${total:.2f}",
            {"endpoint": "invoice", "method": "POST", "body": payload},
        )
        await audit.log_action(
            "create_invoice", f"company={company_id} customer={customer_id} total={total}",
            "draft_created", company_id=company_id, draft_action_id=result.draft_action_id,
        )
        return result.model_dump_json(indent=2)

    @mcp.tool()
    async def update_invoice(
        company_id: str, invoice_id: str, changes_as_json: str
    ) -> str:
        """
        Update an existing invoice. This is a WRITE operation — a draft is created first.

        changes_as_json must be a JSON object of fields to update (e.g. {"DueDate": "2025-12-31"}).
        SyncToken is fetched automatically. Returns a draft_action_id.
        """
        await guards.check_read_only(company_id)
        client = await registry.get_client(company_id)
        data = await client.get(f"invoice/{invoice_id}")
        existing = data["Invoice"]
        changes = json.loads(changes_as_json)
        payload = {**existing, **changes}

        result = await draft.create_draft(
            company_id, "update_invoice",
            f"Update invoice {invoice_id}",
            {"endpoint": "invoice", "method": "POST", "body": payload},
        )
        await audit.log_action(
            "update_invoice", f"company={company_id} invoice={invoice_id}",
            "draft_created", company_id=company_id, draft_action_id=result.draft_action_id,
        )
        return result.model_dump_json(indent=2)

    @mcp.tool()
    async def void_invoice(company_id: str, invoice_id: str) -> str:
        """
        Void an invoice. This is a WRITE operation — a draft is created first.

        Voiding is irreversible in QuickBooks. A draft is created for confirmation.
        Returns a draft_action_id. Call commit_action to proceed.
        """
        await guards.check_read_only(company_id)
        client = await registry.get_client(company_id)
        data = await client.get(f"invoice/{invoice_id}")
        existing = data["Invoice"]

        result = await draft.create_draft(
            company_id, "void_invoice",
            f"Void invoice {invoice_id} (irreversible)",
            {
                "endpoint": "invoice",
                "method": "POST",
                "params": {"operation": "void"},
                "body": {"Id": invoice_id, "SyncToken": existing["SyncToken"]},
            },
        )
        await audit.log_action(
            "void_invoice", f"company={company_id} invoice={invoice_id}",
            "draft_created", company_id=company_id, draft_action_id=result.draft_action_id,
        )
        return result.model_dump_json(indent=2)

    @mcp.tool()
    async def send_invoice(
        company_id: str, invoice_id: str, email: str | None = None
    ) -> str:
        """
        Send an invoice to the customer by email. This is a WRITE operation — a draft is created first.

        If email is not provided, the customer's primary email is used.
        Returns a draft_action_id. Call commit_action to send.
        """
        await guards.check_read_only(company_id)
        payload: dict[str, Any] = {
            "endpoint": f"invoice/{invoice_id}/send",
            "method": "POST",
            "params": {"sendTo": email} if email else {},
            "body": {},
        }
        result = await draft.create_draft(
            company_id, "send_invoice",
            f"Send invoice {invoice_id} by email{' to ' + email if email else ''}",
            payload,
        )
        await audit.log_action(
            "send_invoice", f"company={company_id} invoice={invoice_id}",
            "draft_created", company_id=company_id, draft_action_id=result.draft_action_id,
        )
        return result.model_dump_json(indent=2)

    # ── Customers ─────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_customers(
        company_id: str, search: str | None = None, limit: int = 50
    ) -> str:
        """
        List customers for a connected QuickBooks company.

        Use search to filter by name or email. Returns JSON array of customer objects.
        """
        client = await registry.get_client(company_id)
        if search:
            sql = f"SELECT * FROM Customer WHERE DisplayName LIKE '%{search}%' MAXRESULTS {limit}"
        else:
            sql = f"SELECT * FROM Customer ORDERBY DisplayName MAXRESULTS {limit}"
        data = await client.query(sql)
        raw = data.get("QueryResponse", {}).get("Customer", [])
        customers = [qbo_customer_to_model(c) for c in raw]
        await audit.log_action(
            "list_customers", f"company={company_id} search={search}", "success",
            company_id=company_id,
        )
        return json.dumps([c.model_dump() for c in customers], indent=2)

    @mcp.tool()
    async def get_customer(company_id: str, customer_id: str) -> str:
        """
        Get a single customer by ID.

        Returns full customer details including balance and contact info.
        """
        client = await registry.get_client(company_id)
        data = await client.get(f"customer/{customer_id}")
        customer = qbo_customer_to_model(data["Customer"])
        await audit.log_action(
            "get_customer", f"company={company_id} customer={customer_id}", "success",
            company_id=company_id,
        )
        return json.dumps(customer.model_dump(), indent=2)

    @mcp.tool()
    async def create_customer(
        company_id: str,
        name: str,
        email: str | None = None,
        phone: str | None = None,
    ) -> str:
        """
        Create a new customer. This is a WRITE operation — a draft is created first.

        Returns a draft_action_id. Call commit_action to write to QuickBooks.
        """
        await guards.check_read_only(company_id)
        payload: dict[str, Any] = {"DisplayName": name}
        if email:
            payload["PrimaryEmailAddr"] = {"Address": email}
        if phone:
            payload["PrimaryPhone"] = {"FreeFormNumber": phone}

        result = await draft.create_draft(
            company_id, "create_customer",
            f"Create customer '{name}'",
            {"endpoint": "customer", "method": "POST", "body": payload},
        )
        await audit.log_action(
            "create_customer", f"company={company_id} name={name}",
            "draft_created", company_id=company_id, draft_action_id=result.draft_action_id,
        )
        return result.model_dump_json(indent=2)

    @mcp.tool()
    async def update_customer(
        company_id: str, customer_id: str, changes_as_json: str
    ) -> str:
        """
        Update an existing customer. This is a WRITE operation — a draft is created first.

        changes_as_json: JSON object with fields to update. SyncToken fetched automatically.
        Returns a draft_action_id.
        """
        await guards.check_read_only(company_id)
        client = await registry.get_client(company_id)
        data = await client.get(f"customer/{customer_id}")
        existing = data["Customer"]
        changes = json.loads(changes_as_json)
        payload = {**existing, **changes}

        result = await draft.create_draft(
            company_id, "update_customer",
            f"Update customer {customer_id}",
            {"endpoint": "customer", "method": "POST", "body": payload},
        )
        await audit.log_action(
            "update_customer", f"company={company_id} customer={customer_id}",
            "draft_created", company_id=company_id, draft_action_id=result.draft_action_id,
        )
        return result.model_dump_json(indent=2)

    # ── Vendors ───────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_vendors(
        company_id: str, search: str | None = None, limit: int = 50
    ) -> str:
        """
        List vendors for a connected QuickBooks company.

        Use search to filter by name. Returns JSON array of vendor objects.
        """
        client = await registry.get_client(company_id)
        if search:
            sql = f"SELECT * FROM Vendor WHERE DisplayName LIKE '%{search}%' MAXRESULTS {limit}"
        else:
            sql = f"SELECT * FROM Vendor ORDERBY DisplayName MAXRESULTS {limit}"
        data = await client.query(sql)
        raw = data.get("QueryResponse", {}).get("Vendor", [])
        vendors = [qbo_vendor_to_model(v) for v in raw]
        await audit.log_action(
            "list_vendors", f"company={company_id} search={search}", "success",
            company_id=company_id,
        )
        return json.dumps([v.model_dump() for v in vendors], indent=2)

    @mcp.tool()
    async def get_vendor(company_id: str, vendor_id: str) -> str:
        """
        Get a single vendor by ID.

        Returns full vendor details including balance.
        """
        client = await registry.get_client(company_id)
        data = await client.get(f"vendor/{vendor_id}")
        vendor = qbo_vendor_to_model(data["Vendor"])
        await audit.log_action(
            "get_vendor", f"company={company_id} vendor={vendor_id}", "success",
            company_id=company_id,
        )
        return json.dumps(vendor.model_dump(), indent=2)

    @mcp.tool()
    async def create_vendor(
        company_id: str, name: str, email: str | None = None
    ) -> str:
        """
        Create a new vendor. This is a WRITE operation — a draft is created first.

        Returns a draft_action_id. Call commit_action to write to QuickBooks.
        """
        await guards.check_read_only(company_id)
        payload: dict[str, Any] = {"DisplayName": name}
        if email:
            payload["PrimaryEmailAddr"] = {"Address": email}

        result = await draft.create_draft(
            company_id, "create_vendor",
            f"Create vendor '{name}'",
            {"endpoint": "vendor", "method": "POST", "body": payload},
        )
        await audit.log_action(
            "create_vendor", f"company={company_id} name={name}",
            "draft_created", company_id=company_id, draft_action_id=result.draft_action_id,
        )
        return result.model_dump_json(indent=2)

    # ── Bills ─────────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_bills(
        company_id: str,
        status: Literal["draft", "open", "paid", "overdue"] | None = None,
        vendor_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 50,
    ) -> str:
        """
        List bills (vendor invoices) for a connected QuickBooks company.

        Filter by status, vendor, or date range. Returns JSON array of bill objects.
        """
        client = await registry.get_client(company_id)
        clauses: list[str] = []
        if vendor_id:
            clauses.append(f"VendorRef = '{vendor_id}'")
        if from_date:
            clauses.append(f"TxnDate >= '{from_date}'")
        if to_date:
            clauses.append(f"TxnDate <= '{to_date}'")

        sql = "SELECT * FROM Bill"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDERBY TxnDate DESC MAXRESULTS {limit}"

        data = await client.query(sql)
        raw = data.get("QueryResponse", {}).get("Bill", [])
        bills = [qbo_bill_to_model(b) for b in raw]
        if status:
            bills = [b for b in bills if b.status == status]

        await audit.log_action(
            "list_bills", f"company={company_id} status={status}", "success",
            company_id=company_id,
        )
        return json.dumps([b.model_dump() for b in bills], indent=2)

    @mcp.tool()
    async def get_bill(company_id: str, bill_id: str) -> str:
        """
        Get a single bill by ID with full line items.

        Returns complete bill details.
        """
        client = await registry.get_client(company_id)
        data = await client.get(f"bill/{bill_id}")
        bill = qbo_bill_to_model(data["Bill"])
        await audit.log_action(
            "get_bill", f"company={company_id} bill={bill_id}", "success",
            company_id=company_id,
        )
        return json.dumps(bill.model_dump(), indent=2)

    @mcp.tool()
    async def create_bill(
        company_id: str,
        vendor_id: str,
        line_items: str,
        due_date: str | None = None,
    ) -> str:
        """
        Create a new bill from a vendor. This is a WRITE operation — a draft is created first.

        line_items: JSON array [{"description": "...", "quantity": 1, "unit_price": 50.0, "account_id": "..."}]
        account_id is the expense account ID from list_accounts.
        Returns a draft_action_id. Call commit_action to write to QuickBooks.
        """
        await guards.check_read_only(company_id)
        items = json.loads(line_items)
        total = sum(float(i.get("quantity", 1)) * float(i.get("unit_price", 0)) for i in items)
        await guards.check_threshold(company_id, total)

        payload: dict[str, Any] = {
            "VendorRef": {"value": vendor_id},
            "Line": [
                {
                    "Amount": float(item["quantity"]) * float(item["unit_price"]),
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Description": item.get("description", ""),
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": item["account_id"]},
                    },
                }
                for item in items
            ],
        }
        if due_date:
            payload["DueDate"] = due_date

        result = await draft.create_draft(
            company_id, "create_bill",
            f"Create bill from vendor {vendor_id} total ${total:.2f}",
            {"endpoint": "bill", "method": "POST", "body": payload},
        )
        await audit.log_action(
            "create_bill", f"company={company_id} vendor={vendor_id} total={total}",
            "draft_created", company_id=company_id, draft_action_id=result.draft_action_id,
        )
        return result.model_dump_json(indent=2)

    # ── Accounts / Ledger ─────────────────────────────────────────────────

    @mcp.tool()
    async def list_accounts(company_id: str) -> str:
        """
        List the full chart of accounts for a connected QuickBooks company.

        Returns JSON array of account objects with type, balance, and currency.
        Use account IDs from this list when creating bills or other transactions.
        """
        from qbo_mcp.qbo.adapter import qbo_account_to_model
        client = await registry.get_client(company_id)
        sql = "SELECT * FROM Account ORDERBY AccountType MAXRESULTS 1000"
        data = await client.query(sql)
        raw = data.get("QueryResponse", {}).get("Account", [])
        accounts = [qbo_account_to_model(a) for a in raw]
        await audit.log_action(
            "list_accounts", f"company={company_id}", "success", company_id=company_id,
        )
        return json.dumps([a.model_dump() for a in accounts], indent=2)

    @mcp.tool()
    async def query_ledger(company_id: str, query: str) -> str:
        """
        Run a QBO SQL-like query directly against the QuickBooks ledger.

        Accepts QBO query syntax: SELECT * FROM Invoice WHERE TxnDate > '2025-01-01'
        Supported entities: Invoice, Bill, Customer, Vendor, Account, Payment, Deposit, JournalEntry.
        Returns raw JSON from the QBO API. Use this for custom lookups not covered by other tools.
        """
        client = await registry.get_client(company_id)
        data = await client.query(query)
        await audit.log_action(
            "query_ledger", f"company={company_id} query={query[:100]}",
            "success", company_id=company_id,
        )
        return json.dumps(data, indent=2)
