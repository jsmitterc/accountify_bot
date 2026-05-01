"""Async HTTP client for the Accountify Django API."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


import httpx


class AccountifyError(RuntimeError):
    pass


@dataclass
class AccountifyClient:
    base_url: str
    username: str
    password: str
    _access: str | None = None
    _refresh: str | None = None
    _client: httpx.AsyncClient | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def __aenter__(self) -> "AccountifyClient":
        self._client = httpx.AsyncClient(base_url=self.base_url.rstrip("/"), timeout=15.0)
        await self.login()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def login(self) -> None:
        assert self._client is not None
        r = await self._client.post(
            "/api/login/",
            json={"username": self.username, "password": self.password},
        )
        if r.status_code != 200:
            raise AccountifyError(f"login failed: {r.status_code} {r.text}")
        data = r.json()
        self._access = data["access"]
        self._refresh = data["refresh"]

    async def _refresh_access(self) -> None:
        assert self._client is not None
        if not self._refresh:
            await self.login()
            return
        r = await self._client.post("/api/token/refresh/", json={"refresh": self._refresh})
        if r.status_code != 200:
            await self.login()
            return
        self._access = r.json()["access"]

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        assert self._client is not None
        async with self._lock:
            access = self._access
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {access}"
        r = await self._client.request(method, path, headers=headers, **kwargs)
        if r.status_code == 401:
            async with self._lock:
                await self._refresh_access()
                headers["Authorization"] = f"Bearer {self._access}"
            r = await self._client.request(method, path, headers=headers, **kwargs)
        return r

    async def list_entities(self) -> list[dict[str, Any]]:
        r = await self._request("GET", "/api/entities/")
        if r.status_code != 200:
            raise AccountifyError(f"list_entities: {r.status_code} {r.text}")
        data = r.json()
        return data.get("results", data) if isinstance(data, dict) else data

    async def list_accounts(self, entity_id: str) -> list[dict[str, Any]]:
        r = await self._request("GET", "/api/accounts/", params={"entity": entity_id})
        if r.status_code != 200:
            raise AccountifyError(f"list_accounts: {r.status_code} {r.text}")
        data = r.json()
        return data.get("results", data) if isinstance(data, dict) else data

    async def create_transaction(
        self,
        *,
        entity_id: str,
        date: str,
        description: str,
        entries: list[dict[str, Any]],
        reference: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Post a balanced double-entry transaction.

        `entries`: list of {account, debit_amount, credit_amount, currency, ...}
        """
        payload: dict[str, Any] = {
            "entity": entity_id,
            "date": date,
            "description": description,
            "entries": entries,
        }
        if reference:
            payload["reference"] = reference
        if notes:
            payload["notes"] = notes

        r = await self._request("POST", "/api/transactions/", json=payload)
        if r.status_code not in (200, 201):
            raise AccountifyError(f"create_transaction: {r.status_code} {r.text}")
        return r.json()


def to_amount(value: float | int | str | Decimal) -> str:
    """Normalize amounts to a string with 2 decimals (Django DecimalField friendly)."""
    return f"{Decimal(str(value)):.2f}"
