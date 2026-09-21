"""LX 音源网关客户端与跨平台候选匹配。"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("monitor.lx")


class LxGatewayError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable

SOURCE_MAP = {
    "netease": "wy",
    "qq": "tx",
    "kugou": "kg",
    "kuwo": "kw",
    "migu": "mg",
    "wy": "wy",
    "tx": "tx",
    "kg": "kg",
    "kw": "kw",
    "mg": "mg",
}

QUALITY_MAP = {
    "standard": "128k",
    "high": "320k",
    "lossless": "flac",
    "hires": "hires",
}

_VARIANT_MARKERS = re.compile(
    r"(?:live|现场|演唱会|演唱會|跨年|acoustic|demo|试听|試聽|片段|snippet|remix|dj|混音|伴奏|instrumental|cover|翻唱|sped up|slowed)",
    re.IGNORECASE,
)
_ARTIST_SPLIT = re.compile(r"\s*(?:/|、|，|,|;|；|\||&|＆)\s*")


def _norm(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[\(\[（【].*?[\)\]）】]", "", text)
    text = re.sub(r"\b(feat|ft|remaster(?:ed)?|version|live|explicit|hq|hires)\b", "", text)
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def candidate_score(reference: dict[str, Any], candidate: dict[str, Any]) -> float:
    """歌名/歌手为主、时长为硬校验的候选评分。"""
    title = difflib.SequenceMatcher(None, _norm(reference.get("name", "")), _norm(candidate.get("name", ""))).ratio()
    ref_artist = _norm(reference.get("artist", ""))
    got_artist = _norm(candidate.get("artist", ""))
    if ref_artist and got_artist and (ref_artist in got_artist or got_artist in ref_artist):
        artist = 0.95
    else:
        artist = difflib.SequenceMatcher(None, ref_artist, got_artist).ratio() if ref_artist and got_artist else 0.5
    score = title * 0.68 + artist * 0.32

    expected = int(reference.get("duration") or 0)
    actual = int(candidate.get("duration") or 0)
    if expected and actual:
        delta = abs(expected - actual)
        if delta > max(12, round(expected * 0.18)):
            score -= 0.35
        elif delta <= 4:
            score += 0.03
    if _VARIANT_MARKERS.search(f"{candidate.get('name', '')} {candidate.get('album', '')}"):
        score -= 0.2
    return round(max(0.0, min(score, 1.0)), 4)


def _artist_is_member(song_artist: str, wanted: str) -> bool:
    target = _norm(wanted)
    if not target:
        return False
    parts = [_norm(part) for part in _ARTIST_SPLIT.split(song_artist or "") if _norm(part)]
    return target in parts or _norm(song_artist) == target


def _artist_fingerprint(song: dict[str, Any], wanted: str) -> str:
    """跨平台/跨 ID 去重：归一化歌名 + 目标歌手。"""
    return f"{_norm(song.get('name', ''))}|{_norm(wanted)}"


class LxGateway:
    def __init__(self, base_url: str | None = None) -> None:
        self.base = (base_url or settings.lx_gateway_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "application/json", "User-Agent": settings.user_agent}
            if settings.lx_gateway_token:
                headers["Authorization"] = f"Bearer {settings.lx_gateway_token}"
            self._client = httpx.AsyncClient(
                base_url=self.base,
                timeout=httpx.Timeout(settings.download_timeout, connect=10.0),
                headers=headers,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def healthz(self) -> dict[str, Any]:
        try:
            response = await (await self.client()).get("/healthz", timeout=5.0)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc), "sources": []}

    async def config(self) -> dict[str, Any]:
        return await self._management_request("GET", "/api/config")

    async def sources(self) -> list[dict[str, Any]]:
        return (await self._management_request("GET", "/api/sources")).get("items") or []

    async def reload_sources(self) -> dict[str, Any]:
        return await self._management_request("POST", "/api/sources/reload")

    async def import_source(self, *, url: str = "", script: str = "", name: str = "") -> dict[str, Any]:
        return await self._management_request(
            "POST", "/api/sources/import", json={"url": url, "script": script, "name": name}
        )

    async def delete_source(self, source_id: str) -> dict[str, Any]:
        return await self._management_request("DELETE", f"/api/sources/{source_id}")

    async def set_source_enabled(self, source_id: str, enabled: bool) -> dict[str, Any]:
        return await self._management_request("POST", f"/api/sources/{source_id}/enable", json={"enabled": enabled})

    async def downloads(self, limit: int = 100, *, user_id: str = "") -> list[dict[str, Any]]:
        data = await self._management_request("GET", "/api/downloads", params={"limit": limit, "user_id": user_id})
        return data.get("items") or []

    async def clear_completed_downloads(self, *, user_id: str = "") -> dict[str, Any]:
        return await self._management_request("DELETE", "/api/downloads/completed", params={"user_id": user_id})

    async def _management_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await (await self.client()).request(method, path, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise LxGatewayError(str(data.get("error") or data.get("message") or response.text[:300] or response.status_code))
        return data

    async def search(self, keyword: str, source: str, *, page: int = 1, limit: int = 30) -> list[dict[str, Any]]:
        data = await self.search_result(keyword, source, page=page, limit=limit)
        return data.get("items") or []

    async def search_result(self, keyword: str, source: str, *, page: int = 1, limit: int = 30) -> dict[str, Any]:
        response = await (await self.client()).post(
            "/api/search",
            json={"keyword": keyword, "source": SOURCE_MAP.get(source, source), "page": page, "limit": limit},
            timeout=min(settings.download_timeout, 45.0),
        )
        response.raise_for_status()
        return response.json()

    async def artist_songs(
        self,
        artist: str,
        sources: list[str] | None = None,
        *,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """歌手订阅发现：调用公开平台搜索并执行正式版过滤。"""
        requested = {SOURCE_MAP.get(item, item) for item in (sources or []) if item}
        platforms = [item for item in settings.lx_platform_priority if not requested or item in requested]
        if not platforms:
            platforms = list(settings.lx_platform_priority)
        async def collect(platform: str) -> list[dict[str, Any]]:
            page_size = min(30, max(5, limit))
            pages = max(1, (limit + page_size - 1) // page_size)
            collected: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for page in range(1, pages + 1):
                data = await self.search_result(artist, platform, page=page, limit=page_size)
                items = data.get("items") or []
                if not items:
                    break
                for item in items:
                    key = (str(item.get("source", platform)), str(item.get("id", "")))
                    if key not in seen:
                        seen.add(key)
                        collected.append(item)
                if len(collected) >= limit or len(items) < page_size:
                    break
            return collected[:limit]

        results = await asyncio.gather(
            *(collect(platform) for platform in platforms),
            return_exceptions=True,
        )
        stats = {"searched": 0, "artist_mismatch": 0, "variant": 0, "preview": 0, "duplicate": 0, "kept": 0, "capped": 0}
        out: list[dict[str, Any]] = []
        seen_fingerprints: set[str] = set()
        for result in results:
            if isinstance(result, BaseException):
                continue
            for song in result:
                stats["searched"] += 1
                if not _artist_is_member(song.get("artist", ""), artist):
                    stats["artist_mismatch"] += 1
                    continue
                text = f"{song.get('name', '')} {song.get('album', '')}"
                if _VARIANT_MARKERS.search(text):
                    stats["variant"] += 1
                    continue
                duration = int(song.get("duration") or 0)
                if 0 < duration <= 75:
                    stats["preview"] += 1
                    continue
                fingerprint = _artist_fingerprint(song, artist)
                if not fingerprint.split("|", 1)[0] or fingerprint in seen_fingerprints:
                    stats["duplicate"] += 1
                    continue
                seen_fingerprints.add(fingerprint)
                out.append(song)
        stats["kept"] = min(len(out), limit)
        stats["capped"] = max(0, len(out) - limit)
        return out[:limit], stats

    async def candidates(
        self,
        song: dict[str, Any],
        *,
        sources: list[str] | None = None,
        log_lines: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按配置的平台优先级搜索，并返回每个平台最可信的一个候选。"""
        priority = list(settings.lx_platform_priority)
        # 监控来源只作为元数据来源，不限制 LX 搜索范围；这样网易/哔哩等来源
        # 也会优先尝试 QQ，再退到其它 LX 平台。
        ordered = priority
        query = f"{song.get('name', '')} {song.get('artist', '')}".strip()
        out: list[dict[str, Any]] = []
        results = await asyncio.gather(
            *(self.search(query, source, limit=settings.lx_search_limit) for source in ordered),
            return_exceptions=True,
        )
        for source, result in zip(ordered, results, strict=True):
            if isinstance(result, BaseException):
                if log_lines is not None:
                    log_lines.append(f"  LX 搜索 {source} 失败：{result}")
                continue
            try:
                found = result
            except Exception:  # pragma: no cover - gather 已把异常转成结果
                found = []
            ranked = sorted(
                ((candidate_score(song, item), item) for item in found),
                key=lambda pair: pair[0],
                reverse=True,
            )
            if not ranked:
                if log_lines is not None:
                    log_lines.append(f"  LX 搜索 {source}：无结果")
                continue
            score, best = ranked[0]
            if log_lines is not None:
                log_lines.append(
                    f"  LX 搜索 {source}：最佳 {best.get('name', '')} - {best.get('artist', '')}，匹配度 {score:.2f}"
                )
            if score < settings.lx_match_threshold:
                continue
            out.append({**best, "similarity": score})
        return out

    async def download(
        self,
        song: dict[str, Any],
        *,
        candidates: list[dict[str, Any]],
        quality: str,
        user_id: str = "",
        download_subdir: str | None = None,
        filename_template: str | None = None,
        artist_dir: bool | None = None,
    ) -> dict[str, Any]:
        if not candidates:
            raise LxGatewayError("没有找到可交给 LX 音源的可信候选")
        payload = {
            "song": song,
            "candidates": candidates,
            "preferred_quality": QUALITY_MAP.get(quality, quality),
            "quality_floor": settings.lx_quality_floor,
            "cascade": True,
            "download_subdir": settings.lx_download_subdir if download_subdir is None else download_subdir,
            "filename_template": settings.lx_filename_template if filename_template is None else filename_template,
            "artist_dir": settings.lx_artist_dir if artist_dir is None else artist_dir,
            "user_id": user_id,
        }
        try:
            response = await (await self.client()).post("/api/download", json=payload)
        except httpx.ReadTimeout as exc:
            raise LxGatewayError(
                f"LX 下载超时（{settings.download_timeout:.0f}s），任务可能仍在落盘；本轮不自动重推",
                retryable=False,
            ) from exc
        if response.status_code >= 400:
            try:
                body = response.json()
                message = body.get("error") or str(body)
            except ValueError:
                message = response.text[:300]
            raise LxGatewayError(f"LX 下载失败：{message}")
        result = response.json()
        if not result.get("ok"):
            raise LxGatewayError(result.get("error") or "LX 网关未确认下载成功")
        return result
