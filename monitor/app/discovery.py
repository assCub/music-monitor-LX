"""用户作用域的平台发现层：搜索、公开歌单和个人收藏夹。"""
from __future__ import annotations

import contextvars
import json
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx

from .config import settings

PLATFORMS = [
    {"key": "qq", "label": "QQ 音乐", "playlist": True, "album": True, "user_playlist": True},
    {"key": "netease", "label": "网易云音乐", "playlist": True, "album": True, "user_playlist": True},
    {"key": "kugou", "label": "酷狗音乐", "playlist": True, "album": False, "user_playlist": True},
    {"key": "kuwo", "label": "酷我音乐", "playlist": False, "album": False, "user_playlist": False},
    {"key": "migu", "label": "咪咕音乐", "playlist": False, "album": False, "user_playlist": False},
    {"key": "soda", "label": "汽水音乐", "playlist": True, "album": False, "user_playlist": True},
]

_credentials: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("discovery_credentials", default={})


class DirectDiscovery:
    def __init__(self, platform_auth: Any | None = None) -> None:
        self._client: httpx.AsyncClient | None = None
        self._platform_auth = platform_auth

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.http_timeout, connect=10),
                follow_redirects=True,
                headers={"User-Agent": settings.user_agent, "Accept": "application/json,text/plain,*/*"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    @asynccontextmanager
    async def credentials(self, mapping: dict[str, str]) -> AsyncIterator[None]:
        token = _credentials.set(dict(mapping or {}))
        try:
            yield
        finally:
            _credentials.reset(token)

    async def sources(self) -> list[dict[str, Any]]:
        return PLATFORMS

    async def account_info(self, source: str, cookie: str) -> dict[str, Any]:
        key = normalize_source(source)
        if key == "netease":
            return await self._netease_account(cookie)
        if key == "qq":
            return await self._qq_account(cookie)
        if key == "kugou":
            return await self._kugou_account(cookie)
        if key == "bilibili":
            return await self._bilibili_account(cookie)
        if key in {"soda", "kuwo", "migu"}:
            return self._stored_account(key, cookie)
        raise ValueError(f"暂不支持平台账号信息：{source}")

    async def _netease_account(self, cookie: str) -> dict[str, Any]:
        c = await self.client()
        response = await c.get(
            "https://music.163.com/api/nuser/account/get",
            headers=_cookie_headers(cookie, "https://music.163.com/"),
        )
        response.raise_for_status()
        data = response.json()
        profile = data.get("profile") or {}
        account = data.get("account") or {}
        user_id = str(profile.get("userId") or account.get("id") or "")
        return {
            "valid": bool(user_id),
            "verified": bool(user_id),
            "user_id": user_id,
            "nickname": profile.get("nickname") or (f"网易云用户 {user_id}" if user_id else ""),
            "avatar": profile.get("avatarUrl") or "",
            "vip_type": account.get("vipType") or profile.get("vipType") or 0,
        }

    async def _qq_account(self, cookie: str) -> dict[str, Any]:
        match = re.search(r"(?:^|;\s*)(?:uin|p_uin|wxuin)=o?(\d+)", cookie)
        uin = match.group(1) if match else ""
        if not uin:
            return {"valid": False, "verified": False, "user_id": "", "nickname": "", "avatar": "", "vip_type": 0}
        nickname = f"QQ 用户 {uin}"
        avatar = f"https://q1.qlogo.cn/g?b=qq&nk={uin}&s=100"
        try:
            c = await self.client()
            response = await c.get(
                "https://c.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg",
                params={"cid": 205360838, "userid": uin, "reqfrom": 1, "format": "json", "loginUin": uin, "hostUin": uin},
                headers=_cookie_headers(cookie, "https://y.qq.com/"),
            )
            response.raise_for_status()
            data = _json_or_jsonp(response.text).get("data") or {}
            creator = data.get("creator") or data.get("user_info") or {}
            nickname = creator.get("nick") or creator.get("nickname") or data.get("nick") or nickname
            avatar = creator.get("headpic") or creator.get("avatar") or data.get("headpic") or avatar
        except Exception:
            pass
        return {"valid": True, "verified": True, "user_id": uin, "nickname": nickname, "avatar": avatar, "vip_type": 0}

    async def _kugou_account(self, cookie: str) -> dict[str, Any]:
        values = _parse_cookie(cookie)
        user_id = values.get("userid") or values.get("kugooid") or ""
        token = values.get("token") or ""
        valid = bool(user_id and user_id != "0" and token)
        verified = False
        vip_type = 0
        if valid:
            try:
                response = await (await self.client()).get(
                    "https://vip.kugou.com/recharge/roleinfo",
                    headers=_cookie_headers(cookie, "https://www.kugou.com/"),
                )
                response.raise_for_status()
                data = response.json()
                verified = int(data.get("errno") or 0) == 0 and int(data.get("error_code") or 0) == 0
                vip_type = int(data.get("role") or 0)
            except Exception:
                # 扫码凭据包含 userid/token 时仍可保存；网络验证失败会在界面标为“已配置”。
                valid = bool(user_id and token)
        return {
            "valid": valid, "verified": verified, "user_id": user_id,
            "nickname": f"酷狗用户 {user_id}" if user_id else "", "avatar": "", "vip_type": vip_type,
        }

    async def _bilibili_account(self, cookie: str) -> dict[str, Any]:
        response = await (await self.client()).get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=_cookie_headers(cookie, "https://www.bilibili.com/"),
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        valid = payload.get("code") == 0 and bool(data.get("isLogin"))
        return {
            "valid": valid, "verified": valid, "user_id": str(data.get("mid") or "") if valid else "",
            "nickname": data.get("uname") or "", "avatar": data.get("face") or "",
            "vip_type": (data.get("vip") or {}).get("type") or data.get("vipType") or 0,
        }

    @staticmethod
    def _stored_account(source: str, cookie: str) -> dict[str, Any]:
        labels = {"soda": "汽水音乐", "kuwo": "酷我音乐", "migu": "咪咕音乐"}
        values = _parse_cookie(cookie)
        candidate_keys = {
            "soda": ("sessionid", "sessionid_ss", "uid_tt", "sid_guard"),
            "kuwo": ("kw_token", "userid", "uid"),
            "migu": ("token", "userid", "account", "jsessionid"),
        }[source]
        user_id = next((values[key] for key in candidate_keys if values.get(key) and key in {"userid", "uid", "account"}), "")
        valid = bool(cookie.strip()) and (source in {"kuwo", "migu"} or any(values.get(key) for key in candidate_keys))
        return {
            "valid": valid, "verified": False, "user_id": user_id,
            "nickname": f"{labels[source]}账号" if valid else "", "avatar": "", "vip_type": 0,
        }

    async def search_playlists(self, keyword: str, sources: list[str] | None = None, *, page_size: int = 60) -> list[dict[str, Any]]:
        del page_size
        parsed = parse_playlist_input(keyword, sources or [])
        if not parsed:
            return []
        source, playlist_id = parsed
        songs, info = await self._playlist(source, playlist_id)
        return [{
            "id": playlist_id,
            "source": source,
            "name": info.get("name") or f"{source}:{playlist_id}",
            "creator": info.get("creator") or "",
            "track_count": info.get("track_count") or len(songs),
            "cover": info.get("cover") or "",
            "link": keyword if keyword.startswith("http") else "",
        }]

    async def playlist_songs(self, playlist_id: str, source: str, *, link: str = "") -> list[dict[str, Any]]:
        del link
        songs, _ = await self._playlist(source, playlist_id)
        return songs

    async def user_playlists(self, sources: list[str] | None = None) -> list[dict[str, Any]]:
        wanted = sources or ["qq", "netease", "kugou", "soda"]
        creds = _credentials.get()
        out: list[dict[str, Any]] = []
        if "netease" in wanted and creds.get("netease"):
            out.extend(await self._netease_user_playlists(creds["netease"]))
        if "qq" in wanted and creds.get("qq"):
            out.extend(await self._qq_user_playlists(creds["qq"]))
        if self._platform_auth:
            for source in ("kugou", "soda"):
                if source in wanted and creds.get(source):
                    out.extend(await self._platform_auth.user_playlists(source, creds[source]))
        return out

    async def _playlist(self, source: str, playlist_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        key = normalize_source(source)
        creds = _credentials.get()
        if key == "netease":
            return await self._netease_playlist(playlist_id, creds.get("netease", ""))
        if key == "qq":
            return await self._qq_playlist(playlist_id, creds.get("qq", ""))
        if key in {"kugou", "soda"} and self._platform_auth:
            cookie = creds.get(key, "")
            if not cookie:
                raise ValueError(f"读取 {key} 个人歌单需要先登录")
            songs = await self._platform_auth.playlist_songs(key, playlist_id, cookie)
            return songs, {"name": f"{key}:{playlist_id}", "track_count": len(songs)}
        raise ValueError(f"直连发现暂不支持该平台歌单：{source}（当前支持 QQ、网易云、酷狗、汽水）")

    async def _netease_playlist(self, playlist_id: str, cookie: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
        c = await self.client()
        response = await c.get(
            "https://music.163.com/api/v6/playlist/detail",
            params={"id": playlist_id, "n": 1000, "s": 8},
            headers=_cookie_headers(cookie, "https://music.163.com/"),
        )
        response.raise_for_status()
        data = response.json()
        playlist = data.get("playlist") or data.get("result") or {}
        tracks = list(playlist.get("tracks") or [])
        present = {str(item.get("id") or "") for item in tracks}
        missing_ids = [
            str(item.get("id") or "") for item in (playlist.get("trackIds") or [])
            if item.get("id") and str(item.get("id")) not in present
        ]
        # 新版网易云公开接口只在 playlist.tracks 返回前 10 首，其余 ID 在 trackIds。
        # 分块补取详情，确保别人维护的长歌单不会悄悄只订阅前 10 首。
        for start in range(0, len(missing_ids), 200):
            chunk = missing_ids[start:start + 200]
            detail = await c.get(
                "https://music.163.com/api/song/detail",
                params={"ids": json.dumps(chunk)},
                headers=_cookie_headers(cookie, "https://music.163.com/"),
            )
            detail.raise_for_status()
            tracks.extend(detail.json().get("songs") or [])
        songs = []
        for item in tracks:
            song_id = str(item.get("id") or "")
            if not song_id:
                continue
            artists = item.get("ar") or item.get("artists") or []
            album = item.get("al") or item.get("album") or {}
            songs.append(_song(
                id=song_id, source="netease", name=item.get("name"),
                artist="/".join(str(a.get("name") or "") for a in artists if a.get("name")),
                album=album.get("name") or "",
                duration=int((item.get("dt") or item.get("duration") or 0) / 1000),
                cover=album.get("picUrl") or "", extra={"songId": song_id},
            ))
        return songs, {
            "name": playlist.get("name") or "",
            "creator": (playlist.get("creator") or {}).get("nickname") or "",
            "track_count": playlist.get("trackCount") or len(songs),
            "cover": playlist.get("coverImgUrl") or "",
        }

    async def _qq_playlist(self, playlist_id: str, cookie: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
        c = await self.client()
        response = await c.get(
            "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg",
            params={
                "type": 1, "json": 1, "utf8": 1, "onlysong": 0, "disstid": playlist_id,
                "format": "json", "g_tk": 5381, "platform": "yqq", "needNewCode": 0,
            },
            headers=_cookie_headers(cookie, "https://y.qq.com/"),
        )
        response.raise_for_status()
        data = _json_or_jsonp(response.text)
        cd = ((data.get("cdlist") or [{}])[0])
        songs = [_map_qq_song(item) for item in (cd.get("songlist") or [])]
        songs = [item for item in songs if item.get("id")]
        return songs, {
            "name": cd.get("dissname") or "",
            "creator": cd.get("nickname") or "",
            "track_count": cd.get("songnum") or len(songs),
            "cover": cd.get("logo") or "",
        }

    async def _netease_user_playlists(self, cookie: str) -> list[dict[str, Any]]:
        c = await self.client()
        headers = _cookie_headers(cookie, "https://music.163.com/")
        account = await c.get("https://music.163.com/api/nuser/account/get", headers=headers)
        account.raise_for_status()
        raw = account.json()
        user_id = str((raw.get("profile") or {}).get("userId") or (raw.get("account") or {}).get("id") or "")
        if not user_id:
            return []
        response = await c.get(
            "https://music.163.com/api/user/playlist/",
            params={"uid": user_id, "limit": 1000, "offset": 0},
            headers=headers,
        )
        response.raise_for_status()
        return [{
            "id": str(item.get("id") or ""), "source": "netease", "name": item.get("name") or "",
            "creator": (item.get("creator") or {}).get("nickname") or "",
            "track_count": item.get("trackCount") or 0, "cover": item.get("coverImgUrl") or "", "link": "",
        } for item in response.json().get("playlist") or [] if item.get("id")]

    async def _qq_user_playlists(self, cookie: str) -> list[dict[str, Any]]:
        match = re.search(r"(?:^|;\s*)(?:uin|p_uin|wxuin)=o?(\d+)", cookie)
        if not match:
            return []
        uin = match.group(1)
        c = await self.client()
        response = await c.get(
            "https://c.y.qq.com/rsc/fcgi-bin/fcg_user_created_diss",
            params={"hostuin": uin, "sin": 0, "size": 1000, "format": "json", "g_tk": 5381, "platform": "yqq.json", "needNewCode": 0},
            headers=_cookie_headers(cookie, "https://y.qq.com/"),
        )
        response.raise_for_status()
        data = _json_or_jsonp(response.text)
        items = (data.get("data") or {}).get("disslist") or []
        return [{
            "id": str(item.get("tid") or item.get("dissid") or item.get("dirid") or ""),
            "source": "qq", "name": item.get("diss_name") or item.get("dissname") or "",
            "creator": item.get("nickname") or "", "track_count": item.get("song_cnt") or 0,
            "cover": item.get("diss_cover") or "", "link": "",
        } for item in items if item.get("tid") or item.get("dissid") or item.get("dirid")]


def normalize_source(source: str) -> str:
    aliases = {"wy": "netease", "tx": "qq", "kg": "kugou", "kw": "kuwo", "mg": "migu"}
    key = str(source or "").strip().lower()
    return aliases.get(key, key)


def parse_playlist_input(raw: str, sources: list[str]) -> tuple[str, str] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if ":" in text and not text.startswith("http"):
        source, playlist_id = text.split(":", 1)
        return normalize_source(source), playlist_id.strip()
    parsed = urlparse(text)
    host = parsed.netloc.lower()
    query = parse_qs(parsed.query)
    fragment_query = parse_qs(parsed.fragment.split("?", 1)[1]) if "?" in parsed.fragment else {}
    if "music.163.com" in host:
        playlist_id = (query.get("id") or fragment_query.get("id") or [""])[0]
        return ("netease", playlist_id) if playlist_id else None
    if "y.qq.com" in host or "qq.com" in host:
        playlist_id = (query.get("id") or query.get("disstid") or [""])[0]
        if not playlist_id:
            match = re.search(r"/(?:playlist|playsquare)/(\d+)", parsed.path)
            playlist_id = match.group(1) if match else ""
        return ("qq", playlist_id) if playlist_id else None
    if len(sources) == 1 and text.isdigit():
        return normalize_source(sources[0]), text
    return None


def _cookie_headers(cookie: str, referer: str) -> dict[str, str]:
    headers = {"Referer": referer}
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _parse_cookie(cookie: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in str(cookie or "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.strip():
            values[key.strip().lower()] = value.strip()
    return values


def _json_or_jsonp(text: str) -> dict[str, Any]:
    raw = text.strip().lstrip("\ufeff")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        value = json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}
    return value if isinstance(value, dict) else {}


def _song(**values: Any) -> dict[str, Any]:
    base = {"id": "", "source": "", "name": "", "artist": "", "album": "", "duration": 0, "cover": "", "extra": {}, "link": ""}
    base.update(values)
    return base


def _map_qq_song(item: dict[str, Any]) -> dict[str, Any]:
    mid = str(item.get("songmid") or item.get("mid") or "")
    singers = item.get("singer") or []
    album_mid = str(item.get("albummid") or "")
    return _song(
        id=mid, source="qq", name=item.get("songname") or item.get("name") or "",
        artist="/".join(str(s.get("name") or "") for s in singers if s.get("name")),
        album=item.get("albumname") or "", duration=int(item.get("interval") or 0),
        cover=f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{album_mid}.jpg" if album_mid else "",
        extra={"songmid": mid, "song_id": str(item.get("songid") or "")},
    )
