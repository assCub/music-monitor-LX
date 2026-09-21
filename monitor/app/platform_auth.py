"""独立 platform-auth 扫码服务客户端。"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx


class PlatformAuthClient:
    def __init__(self) -> None:
        self.base = os.getenv("PLATFORM_AUTH_URL", "http://platform-auth:8091").rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base, timeout=30)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def healthz(self) -> dict[str, Any]:
        try:
            response = await (await self.client()).get("/healthz", timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc), "sources": []}

    async def start(self, source: str) -> dict[str, Any]:
        response = await (await self.client()).post(f"/api/qr/{quote(source, safe='')}")
        return self._result(response)

    async def check(self, source: str, key: str) -> dict[str, Any]:
        response = await (await self.client()).get(
            f"/api/qr/{quote(source, safe='')}", params={"key": key}
        )
        return self._result(response)

    async def user_playlists(self, source: str, cookie: str) -> list[dict[str, Any]]:
        response = await (await self.client()).post(
            f"/api/catalog/{quote(source, safe='')}/user-playlists", json={"cookie": cookie}
        )
        data = self._result(response)
        return list(data.get("items") or [])

    async def playlist_songs(self, source: str, playlist_id: str, cookie: str) -> list[dict[str, Any]]:
        response = await (await self.client()).post(
            f"/api/catalog/{quote(source, safe='')}/playlist/{quote(playlist_id, safe='')}",
            json={"cookie": cookie},
        )
        data = self._result(response)
        return list(data.get("items") or [])

    @staticmethod
    def _result(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = {"error": response.text[:300]}
        if response.status_code >= 400:
            raise RuntimeError(str(data.get("error") or response.status_code))
        return data
