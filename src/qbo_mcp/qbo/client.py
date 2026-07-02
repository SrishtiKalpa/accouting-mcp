from __future__ import annotations

import asyncio
import base64
import time
from collections import deque
from typing import Any

import httpx
import structlog

from qbo_mcp.config import settings
from qbo_mcp.db.connection import get_db

log = structlog.get_logger(__name__)

_SANDBOX_BASE = "https://sandbox-quickbooks.api.intuit.com/v3/company"
_PRODUCTION_BASE = "https://quickbooks.api.intuit.com/v3/company"
_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
_REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"

# Intuit retired minor versions below 75 in 2025.
_MINOR_VERSION = "75"

# Simple sliding-window rate limiter: 500 req/min per company
_RATE_WINDOW = 60.0
_RATE_LIMIT = 500
_company_request_times: dict[str, deque[float]] = {}

# One refresh at a time per company — Intuit rotates refresh tokens, so
# concurrent refreshes can invalidate each other's new token.
_refresh_locks: dict[str, asyncio.Lock] = {}


def escape_query_value(value: str) -> str:
    """Escape a string for interpolation into a QBO query literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _check_rate_limit(company_id: str) -> None:
    now = time.monotonic()
    times = _company_request_times.setdefault(company_id, deque())
    while times and now - times[0] > _RATE_WINDOW:
        times.popleft()
    if len(times) >= _RATE_LIMIT:
        raise RuntimeError(
            f"Rate limit exceeded for company {company_id}: "
            f"{_RATE_LIMIT} requests per minute. Wait before retrying."
        )
    times.append(now)


def _basic_credentials() -> str:
    return base64.b64encode(
        f"{settings.qbo_client_id}:{settings.qbo_client_secret}".encode()
    ).decode()


class QBOClient:
    def __init__(
        self,
        company_id: str,
        realm_id: str,
        access_token: str,
        refresh_token: str,
        token_expires_at: int,
    ) -> None:
        self.company_id = company_id
        self.realm_id = realm_id
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_expires_at = token_expires_at

    @property
    def _base_url(self) -> str:
        if settings.qbo_environment == "production":
            return f"{_PRODUCTION_BASE}/{self.realm_id}"
        return f"{_SANDBOX_BASE}/{self.realm_id}"

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": content_type,
        }

    async def ensure_token_fresh(self) -> None:
        if time.time() <= self._token_expires_at - 300:
            return
        lock = _refresh_locks.setdefault(self.company_id, asyncio.Lock())
        async with lock:
            # Another task may have refreshed while we waited for the lock.
            async with get_db() as db:
                cursor = await db.execute(
                    "SELECT access_token, refresh_token, token_expires_at "
                    "FROM companies WHERE id=?",
                    (self.company_id,),
                )
                row = await cursor.fetchone()
            if row and row["token_expires_at"] > time.time() + 300:
                self._access_token = row["access_token"]
                self._refresh_token = row["refresh_token"]
                self._token_expires_at = row["token_expires_at"]
                return
            await self._refresh_tokens()

    async def _refresh_tokens(self) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _TOKEN_URL,
                headers={
                    "Authorization": f"Basic {_basic_credentials()}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Failed to refresh QuickBooks tokens for company {self.company_id} "
                    f"({resp.status_code}): {resp.text[:300]}. "
                    "The connection may have been revoked — reconnect with connect_company."
                )
            data = resp.json()

        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        self._token_expires_at = int(time.time()) + int(data.get("expires_in", 3600))

        async with get_db() as db:
            await db.execute(
                "UPDATE companies SET access_token=?, refresh_token=?, token_expires_at=?, "
                "updated_at=unixepoch() WHERE id=?",
                (self._access_token, self._refresh_token, self._token_expires_at, self.company_id),
            )
            await db.commit()
        log.info("qbo.token_refreshed", company_id=self.company_id)

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        _retry_on_auth: bool = True,
    ) -> dict[str, Any]:
        await self.ensure_token_fresh()
        _check_rate_limit(self.company_id)
        url = f"{self._base_url}/{endpoint}"
        all_params = {"minorversion": _MINOR_VERSION, **(params or {})}

        async with httpx.AsyncClient() as client:
            if method == "POST" and json_body is None:
                # Body-less POST (e.g. invoice send) — QBO requires octet-stream.
                resp = await client.post(
                    url,
                    headers=self._headers("application/octet-stream"),
                    params=all_params,
                    content=b"",
                )
            elif method == "POST":
                resp = await client.post(
                    url, headers=self._headers(), params=all_params, json=json_body
                )
            else:
                resp = await client.get(url, headers=self._headers(), params=all_params)

        if resp.status_code == 401 and _retry_on_auth:
            # Token may have been revoked or rotated externally; force a refresh and retry once.
            self._token_expires_at = 0
            return await self._request(
                method, endpoint, params=params, json_body=json_body, _retry_on_auth=False
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"QuickBooks API error {resp.status_code} on {method} {endpoint}: "
                f"{resp.text[:500]}"
            )
        return resp.json()  # type: ignore[no-any-return]

    async def get(self, endpoint: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return await self._request("GET", endpoint, params=params)

    async def post(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self._request("POST", endpoint, params=params, json_body=payload)

    async def query(self, sql: str) -> dict[str, Any]:
        return await self._request("GET", "query", params={"query": sql})

    async def report(self, report_name: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return await self._request("GET", f"reports/{report_name}", params=params)

    async def revoke_tokens(self) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(
                _REVOKE_URL,
                headers={
                    "Authorization": f"Basic {_basic_credentials()}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"token": self._refresh_token},
            )
        log.info("qbo.tokens_revoked", company_id=self.company_id)
