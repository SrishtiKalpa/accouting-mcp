from __future__ import annotations

import json
import time
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from qbo_mcp.config import settings
from qbo_mcp.db.connection import get_db
from qbo_mcp.safety import audit, draft as draft_mod
from qbo_mcp.tenant.registry import registry
from qbo_mcp.tools import crud, reports, workflows

log = structlog.get_logger(__name__)

mcp = FastMCP("QuickBooks MCP")

crud.register(mcp)
reports.register(mcp)
workflows.register(mcp)


# ── Company management ────────────────────────────────────────────────────────

@mcp.tool()
async def list_companies() -> str:
    """
    List all QuickBooks companies connected to this MCP server.

    Always call this first to get valid company_id values before calling other tools.
    Returns JSON array of company objects with id, name, realm_id, and read_only status.
    """
    companies = await registry.list_companies()
    await audit.log_action("list_companies", "list all companies", "success")
    return json.dumps(companies, indent=2)


@mcp.tool()
async def connect_company(
    name: str,
    realm_id: str,
    redirect_uri: str | None = None,
) -> str:
    """
    Begin the OAuth flow to connect a QuickBooks company.

    name: a human-readable label for this company (e.g. "Acme Corp")
    realm_id: the QuickBooks Company ID (found in your QBO URL: qbo.intuit.com/app/homepage?realmId=XXXXX)
    redirect_uri: optional override for the OAuth callback URL.

    Returns an Intuit OAuth URL. Direct the user to open this URL in a browser,
    sign in to QuickBooks, and authorise access. After authorisation, call
    complete_oauth_connection with the returned code and state parameters.
    """
    from urllib.parse import urlencode
    import secrets

    state = secrets.token_urlsafe(32)
    uri = redirect_uri or settings.qbo_redirect_uri

    async with get_db() as db:
        # Expire stale pending states so the table cannot grow unbounded.
        await db.execute(
            "DELETE FROM oauth_states WHERE created_at < unixepoch() - 600"
        )
        await db.execute(
            "INSERT INTO oauth_states (state, realm_id, name, redirect_uri) "
            "VALUES (?, ?, ?, ?)",
            (state, realm_id, name, uri),
        )
        await db.commit()

    params = {
        "client_id": settings.qbo_client_id,
        "scope": "com.intuit.quickbooks.accounting",
        "redirect_uri": uri,
        "response_type": "code",
        "state": state,
        "access_type": "offline",
    }
    auth_url = "https://appcenter.intuit.com/connect/oauth2?" + urlencode(params)

    await audit.log_action(
        "connect_company", f"name={name} realm_id={realm_id}", "success"
    )
    return json.dumps(
        {
            "auth_url": auth_url,
            "instructions": (
                "Open this URL in a browser and authorise QuickBooks access. "
                "After authorisation, you will receive a code and state parameter. "
                "Call complete_oauth_connection(code, state) to finish connecting."
            ),
        },
        indent=2,
    )


@mcp.tool()
async def complete_oauth_connection(code: str, state: str) -> str:
    """
    Complete the OAuth flow after the user authorises QuickBooks access.

    code: the authorisation code from the callback URL query parameter.
    state: the state parameter from the callback URL query parameter.

    Returns the new company_id. The company is now connected and ready to use.
    """
    import base64
    import httpx

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT realm_id, name, redirect_uri, created_at FROM oauth_states WHERE state=?",
            (state,),
        )
        row = await cursor.fetchone()
    if row is None or row["created_at"] < time.time() - 600:
        raise ValueError(
            "Unknown or expired state parameter. "
            "Re-run connect_company to get a fresh OAuth URL."
        )
    realm_id, name, redirect_uri = row["realm_id"], row["name"], row["redirect_uri"]

    credentials = base64.b64encode(
        f"{settings.qbo_client_id}:{settings.qbo_client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"OAuth token exchange failed ({resp.status_code}): {resp.text[:300]}"
            )
        data = resp.json()

    async with get_db() as db:
        await db.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        await db.commit()

    token_expires_at = int(time.time()) + int(data.get("expires_in", 3600))
    company_id = await registry.add_company(
        name=name,
        realm_id=realm_id,
        refresh_token=data["refresh_token"],
        access_token=data["access_token"],
        token_expires_at=token_expires_at,
    )

    await audit.log_action(
        "complete_oauth_connection", f"name={name} realm_id={realm_id}", "success",
        company_id=company_id,
    )
    return json.dumps(
        {
            "company_id": company_id,
            "name": name,
            "realm_id": realm_id,
            "message": f"Company '{name}' connected successfully. Use company_id='{company_id}' in other tools.",
        },
        indent=2,
    )


@mcp.tool()
async def disconnect_company(company_id: str) -> str:
    """
    Disconnect a QuickBooks company and revoke its tokens.

    This removes the company from the registry and revokes the OAuth tokens.
    The company will need to re-authorise to reconnect.
    """
    try:
        client = await registry.get_client(company_id)
        await client.revoke_tokens()
    except Exception as exc:
        log.warning("disconnect.revoke_failed", error=str(exc), company_id=company_id)

    await registry.remove_company(company_id)
    await audit.log_action(
        "disconnect_company", f"company={company_id}", "success", company_id=company_id
    )
    return json.dumps({"status": "disconnected", "company_id": company_id}, indent=2)


# ── Draft management ──────────────────────────────────────────────────────────

@mcp.tool()
async def preview_action(draft_action_id: str) -> str:
    """
    Show full details of a pending draft action before committing it.

    Use this to inspect exactly what will be sent to QuickBooks before confirming.
    Returns the full payload, description, and current status.
    """
    row = await draft_mod.get_draft(draft_action_id)
    if row is None:
        raise ValueError(f"Draft action '{draft_action_id}' not found.")
    return json.dumps(dict(row), indent=2)


@mcp.tool()
async def commit_action(draft_action_id: str) -> str:
    """
    Execute a pending draft action and write it to QuickBooks.

    This is irreversible for most operations (void, send). Always preview first.
    Returns the QBO API response.
    """
    async def _executor(company_id: str, tool_name: str, payload: dict[str, Any]) -> Any:
        client = await registry.get_client(company_id)
        endpoint = str(payload.get("endpoint", ""))
        method = payload.get("method", "POST")
        body = payload.get("body") or None
        params = {str(k): str(v) for k, v in payload.get("params", {}).items()}

        if method == "POST":
            return await client.post(endpoint, body, params=params)
        return await client.get(endpoint, params)

    row = await draft_mod.get_draft(draft_action_id)
    company_id = row["company_id"] if row else None
    try:
        result = await draft_mod.commit_draft(draft_action_id, _executor)
    except Exception as exc:
        await audit.log_action(
            "commit_action", f"draft={draft_action_id}", "error",
            company_id=company_id, draft_action_id=draft_action_id,
            error_message=str(exc)[:500],
        )
        raise
    await audit.log_action(
        "commit_action", f"draft={draft_action_id}", "success",
        company_id=company_id, draft_action_id=draft_action_id,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
async def discard_action(draft_action_id: str) -> str:
    """
    Cancel a pending draft action without writing to QuickBooks.

    Use this when the user decides not to proceed with a proposed change.
    """
    row = await draft_mod.get_draft(draft_action_id)
    company_id = row["company_id"] if row else None
    await draft_mod.discard_draft(draft_action_id)
    await audit.log_action(
        "discard_action", f"draft={draft_action_id}", "success",
        company_id=company_id, draft_action_id=draft_action_id,
    )
    return json.dumps({"status": "discarded", "draft_action_id": draft_action_id}, indent=2)


# ── Audit log ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_audit_log(
    company_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
) -> str:
    """
    View the audit log of all tool calls for a QuickBooks company.

    Shows who called what, when, and the outcome. Useful for compliance and debugging.
    from_date and to_date are ISO format: YYYY-MM-DD.
    """
    limit = max(1, min(limit, 500))
    conditions = ["company_id=?"]
    params: list[Any] = [company_id]

    if from_date:
        conditions.append("created_at >= CAST(strftime('%s', ?) AS INTEGER)")
        params.append(from_date)
    if to_date:
        conditions.append("created_at < CAST(strftime('%s', ?) AS INTEGER) + 86400")
        params.append(to_date)

    sql = (
        "SELECT id, tool_name, input_summary, outcome, draft_action_id, "
        "error_message, created_at FROM audit_log WHERE "
        + " AND ".join(conditions)
        + " ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)

    async with get_db() as db:
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()

    return json.dumps([dict(r) for r in rows], indent=2)


# ── Health endpoint (HTTP mode only) ─────────────────────────────────────────

@mcp.resource("health://status")
async def health_check() -> str:
    """Server health status."""
    return json.dumps({"status": "ok", "version": "0.1.0"})
