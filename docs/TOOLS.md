# Tool Reference

All tools accept `company_id` (a UUID returned by `list_companies`). Write operations return a `draft_action_id` — call `commit_action` to execute or `discard_action` to cancel.

## Company Management

| Tool | Description |
|---|---|
| `list_companies()` | List all connected QBO companies |
| `connect_company(name, realm_id)` | Start OAuth flow — returns auth URL |
| `complete_oauth_connection(code, state)` | Finish OAuth — saves tokens |
| `disconnect_company(company_id)` | Remove company and revoke tokens |

## Invoices

| Tool | Write? | Description |
|---|---|---|
| `list_invoices(company_id, status?, from_date?, to_date?, customer_id?, limit?)` | No | List invoices with filters |
| `get_invoice(company_id, invoice_id)` | No | Full invoice with line items |
| `create_invoice(company_id, customer_id, line_items, due_date?, memo?)` | **Draft** | Create new invoice |
| `update_invoice(company_id, invoice_id, changes_as_json)` | **Draft** | Update existing invoice |
| `void_invoice(company_id, invoice_id)` | **Draft** | Void an invoice (irreversible) |
| `send_invoice(company_id, invoice_id, email?)` | **Draft** | Email invoice to customer |

## Customers

| Tool | Write? | Description |
|---|---|---|
| `list_customers(company_id, search?, limit?)` | No | List/search customers |
| `get_customer(company_id, customer_id)` | No | Full customer details |
| `create_customer(company_id, name, email?, phone?)` | **Draft** | Create new customer |
| `update_customer(company_id, customer_id, changes_as_json)` | **Draft** | Update customer |

## Vendors

| Tool | Write? | Description |
|---|---|---|
| `list_vendors(company_id, search?, limit?)` | No | List/search vendors |
| `get_vendor(company_id, vendor_id)` | No | Full vendor details |
| `create_vendor(company_id, name, email?)` | **Draft** | Create new vendor |

## Bills

| Tool | Write? | Description |
|---|---|---|
| `list_bills(company_id, status?, vendor_id?, from_date?, to_date?)` | No | List bills |
| `get_bill(company_id, bill_id)` | No | Full bill with line items |
| `create_bill(company_id, vendor_id, line_items, due_date?)` | **Draft** | Create new bill |

## Accounts & Ledger

| Tool | Description |
|---|---|
| `list_accounts(company_id)` | Full chart of accounts |
| `query_ledger(company_id, query)` | Run raw QBO SQL-like query |

## Financial Reports (read-only)

| Tool | Description |
|---|---|
| `get_pl_report(company_id, start_date, end_date, period?)` | Profit & Loss |
| `get_balance_sheet(company_id, as_of_date)` | Balance sheet |
| `get_cash_flow(company_id, start_date, end_date)` | Cash flow statement |
| `get_ar_aging(company_id)` | AR aging buckets |
| `get_ap_aging(company_id)` | AP aging buckets |
| `get_trial_balance(company_id, as_of_date)` | Trial balance |

## Workflow Intelligence

| Tool | Description |
|---|---|
| `detect_duplicate_transactions(company_id, entity_type, from_date, to_date, threshold_days?)` | Find potential duplicate invoices/bills |
| `month_end_checklist(company_id, period)` | Month-end close checklist (YYYY-MM) |
| `flag_unusual_transactions(company_id, lookback_months?, std_dev_threshold?)` | Statistical outlier detection |
| `summarize_financial_health(company_id)` | Overall health summary with ratios |

## Draft Management

| Tool | Description |
|---|---|
| `preview_action(draft_action_id)` | Show full draft details before committing |
| `commit_action(draft_action_id)` | Execute the draft — writes to QBO |
| `discard_action(draft_action_id)` | Cancel without writing |

## Audit

| Tool | Description |
|---|---|
| `get_audit_log(company_id, from_date?, to_date?, limit?)` | View action history |
