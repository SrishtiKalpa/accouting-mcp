from __future__ import annotations

import pytest

from qbo_mcp.tenant.registry import CompanyRegistry


async def _make_registry() -> CompanyRegistry:
    return CompanyRegistry()


class TestCompanyRegistry:
    async def test_list_companies_empty_on_fresh_db(self) -> None:
        reg = await _make_registry()
        companies = await reg.list_companies()
        assert companies == []

    async def test_add_and_list_company(self) -> None:
        reg = await _make_registry()
        company_id = await reg.add_company(
            name="Acme Corp",
            realm_id="realm-123",
            refresh_token="refresh-tok",
            access_token="access-tok",
            token_expires_at=9999999999,
        )
        assert company_id  # UUID string
        companies = await reg.list_companies()
        assert len(companies) == 1
        assert companies[0]["name"] == "Acme Corp"
        assert companies[0]["realm_id"] == "realm-123"
        assert companies[0]["read_only"] == 0

    async def test_add_read_only_company(self) -> None:
        reg = await _make_registry()
        company_id = await reg.add_company(
            name="Read Only Corp",
            realm_id="realm-ro",
            refresh_token="tok",
            access_token="tok",
            token_expires_at=9999999999,
            read_only=True,
        )
        companies = await reg.list_companies()
        target = next(c for c in companies if c["id"] == company_id)
        assert target["read_only"] == 1

    async def test_add_company_with_threshold(self) -> None:
        reg = await _make_registry()
        company_id = await reg.add_company(
            name="Thresh Corp",
            realm_id="realm-thresh",
            refresh_token="tok",
            access_token="tok",
            token_expires_at=9999999999,
            write_threshold_usd=500.0,
        )
        companies = await reg.list_companies()
        target = next(c for c in companies if c["id"] == company_id)
        assert target["write_threshold_usd"] == 500.0

    async def test_get_client_returns_qbo_client(self) -> None:
        from qbo_mcp.qbo.client import QBOClient

        reg = await _make_registry()
        company_id = await reg.add_company(
            name="Client Corp",
            realm_id="realm-client",
            refresh_token="tok",
            access_token="access",
            token_expires_at=9999999999,
        )
        client = await reg.get_client(company_id)
        assert isinstance(client, QBOClient)
        assert client.company_id == company_id
        assert client.realm_id == "realm-client"

    async def test_get_client_raises_for_unknown_id(self) -> None:
        reg = await _make_registry()
        with pytest.raises(ValueError, match="not found"):
            await reg.get_client("nonexistent-id")

    async def test_remove_company(self) -> None:
        reg = await _make_registry()
        company_id = await reg.add_company(
            name="Remove Me",
            realm_id="realm-remove",
            refresh_token="tok",
            access_token="tok",
            token_expires_at=9999999999,
        )
        await reg.remove_company(company_id)
        companies = await reg.list_companies()
        assert not any(c["id"] == company_id for c in companies)

    async def test_remove_nonexistent_raises(self) -> None:
        reg = await _make_registry()
        with pytest.raises(ValueError, match="not found"):
            await reg.remove_company("nonexistent-id")

    async def test_multiple_companies(self) -> None:
        reg = await _make_registry()
        id1 = await reg.add_company("A Corp", "realm-a", "tok", "tok", 9999999999)
        id2 = await reg.add_company("B Corp", "realm-b", "tok", "tok", 9999999999)
        id3 = await reg.add_company("C Corp", "realm-c", "tok", "tok", 9999999999)

        companies = await reg.list_companies()
        assert len(companies) == 3
        ids = {c["id"] for c in companies}
        assert {id1, id2, id3} == ids

    async def test_duplicate_realm_id_raises(self) -> None:
        reg = await _make_registry()
        await reg.add_company("First", "realm-dup", "tok", "tok", 9999999999)
        with pytest.raises(Exception):
            await reg.add_company("Second", "realm-dup", "tok", "tok", 9999999999)
