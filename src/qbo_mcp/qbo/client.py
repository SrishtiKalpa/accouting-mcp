from __future__ import annotations

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

# Simple sliding-window rate limiter: 500 req/min per company
_RATE_WINDOW = 60.0
_RATE_LIMIT = 500
_company_request_times: dict[str, deque[float]] = {}


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

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def ensure_token_fresh(self) -> None:
        if time.time() > self._token_expires_at - 300:
            await self._refresh_tokens()

    async def _refresh_tokens(self) -> None:
        import base64
        credentials = base64.b64encode(
            f"{settings.qbo_client_id}:{settings.qbo_client_secret}".encode()
        ).decode()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _TOKEN_URL,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        self._token_expires_at = int(time.time()) + int(data.get("expires_in", 3600))

        async with get_db() as db:
            await db.execute(
                "UPDATE companies SET access_token=?, refresh_token=?, token_expires_at=?, "
                "updated_at=unixepoch() WHERE id=?",
                (self._access_token, self._refresh_token, self._token_expires_at, self.company_id),
            )
            await db.commit()
        log.info("qbo.token_refreshed", company_id=self.company_id)

    async def get(self, endpoint: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        await self.ensure_token_fresh()
        _check_rate_limit(self.company_id)
        url = f"{self._base_url}/{endpoint}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_token_fresh()
        _check_rate_limit(self.company_id)
        url = f"{self._base_url}/{endpoint}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def query(self, sql: str) -> dict[str, Any]:
        await self.ensure_token_fresh()
        _check_rate_limit(self.company_id)
        url = f"{self._base_url}/query"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                params={"query": sql, "minorversion": "65"},
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def report(self, report_name: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        await self.ensure_token_fresh()
        _check_rate_limit(self.company_id)
        url = f"{self._base_url}/reports/{report_name}"
        all_params = {"minorversion": "65"}
        if params:
            all_params.update(params)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                params=all_params,
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    async def revoke_tokens(self) -> None:
        import base64
        credentials = base64.b64encode(
            f"{settings.qbo_client_id}:{settings.qbo_client_secret}".encode()
        ).decode()
        async with httpx.AsyncClient() as client:
            await client.post(
                _REVOKE_URL,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"token": self._refresh_token},
            )
        log.info("qbo.tokens_revoked", company_id=self.company_id)
