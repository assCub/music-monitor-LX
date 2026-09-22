"""一轮监控的执行流水线：发现曲目 → 增量比对 → 按目标音质择优 → 交给引擎下载 → 记录结果。"""
from __future__ import annotations

import asyncio
import difflib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from . import quality as q
from .charts import resolve_chart
from .config import settings
from .db import Database, fingerprint, now_iso
from .lx_gateway import LxGateway

log = logging.getLogger("monitor.pipeline")

# 容器内下载目录（只读挂载）。用于判断文件是否还在、以及把引擎给的裸文件名还原成路径。
DOWNLOAD_ROOT = os.getenv("DOWNLOADS_MOUNT", "/downloads")


# --------------------------------------------------------------------------- 工具
def _norm(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[\(\[（【].*?[\)\]）】]", "", text)          # 去掉 (Live) / [Remix] 之类后缀
    text = re.sub(r"\b(feat|ft|remaster(ed)?|version|live|explicit|hq|hires)\b", "", text)
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def similarity(name_a: str, artist_a: str, name_b: str, artist_b: str) -> float:
    """歌名 70% + 歌手 30% 的相似度，口径与上游换源逻辑接近。"""
    title = difflib.SequenceMatcher(None, _norm(name_a), _norm(name_b)).ratio()
    a1, a2 = _norm(artist_a), _norm(artist_b)
    if not a1 or not a2:
        artist = 0.5
    else:
        artist = difflib.SequenceMatcher(None, a1, a2).ratio()
        # 只要有一方包含另一方（"周杰伦" vs "周杰伦/杨瑞代"）就给高分
        if a1 in a2 or a2 in a1:
            artist = max(artist, 0.9)
    return round(title * 0.7 + artist * 0.3, 4)


def _artist_matches(song_artist: str, wanted: str, *, threshold: float = 0.72) -> bool:
    """判断一首歌的歌手字段是否属于"我们要关注的这位歌手"。

    搜索接口常把翻唱、同名曲、合作曲一起返回，所以这里按「包含」优先、
    相似度兜底来判定；「周杰伦/杨瑞代」这类合作曲也算。"""
    a = _norm(song_artist)
    b = _norm(wanted)
    if not a or not b:
        return False
    if a == b:
        return True
    # 「周杰伦/杨瑞代」「周杰伦、方文山」——歌手字段里含目标歌手即算
    if b in a:
        return True
    if a in b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def _keywords(raw: str) -> list[str]:
    return [k.strip().lower() for k in re.split(r"[,，;；\s]+", raw or "") if k.strip()]


def _fingerprint_aliases(name: str, artist: str) -> set[str]:
    """返回正序与反序两种指纹，兼容历史反向文件名/元数据解析。"""
    direct = fingerprint(name, artist)
    reverse = fingerprint(artist, name)
    return {direct, reverse}


_AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".ape", ".alac", ".wv",
    ".dsf", ".dff", ".mka", ".mp4", ".webm",
}


def _file_exists(file_path: str) -> bool:
    if not file_path:
        return False
    path = Path(file_path)
    if path.is_absolute():
        candidates = [path]
        marker = "/home/appuser/data/downloads/"
        if marker in path.as_posix():
            candidates.append(Path(DOWNLOAD_ROOT) / path.as_posix().split(marker, 1)[1])
        mount_marker = "/downloads/"
        if path.as_posix().startswith(mount_marker):
            candidates.append(Path(DOWNLOAD_ROOT) / path.as_posix().split(mount_marker, 1)[1])
        return any(candidate.is_file() for candidate in candidates)
    # 历史记录路径统一使用 data/downloads/...，对应 monitor 的只读 /downloads。
    if str(path).startswith("data/downloads/"):
        return (Path(DOWNLOAD_ROOT) / path.relative_to("data/downloads")).is_file()
    # 兜底：上游在「已有该歌曲、跳过下载」时 path 是空字符串，只剩一个**没有扩展名**的
    # filename（如「陈奕迅 - 富士山下」）。当路径存下来时，按主干去下载目录里找真实文件，
    # 否则会被判成「文件不存在」→ 每轮重复推送同一批歌（引擎侧堆出一大堆 skipped 记录）。
    return bool(resolve_download_path(file_path))


# 下载目录索引（主干 → 真实文件名）。目录不会频繁变化，缓存 30 秒足够。
_INDEX: dict[str, list[str]] = {}
_INDEX_AT = 0.0
_INDEX_TTL = 30.0
_INDEX_ROOT = ""


def _stem_index() -> dict[str, list[str]]:
    global _INDEX, _INDEX_AT, _INDEX_ROOT
    now = time.time()
    root = str(Path(DOWNLOAD_ROOT).resolve())
    if _INDEX and root == _INDEX_ROOT and now - _INDEX_AT < _INDEX_TTL:
        return _INDEX
    index: dict[str, list[str]] = {}
    try:
        root_path = Path(DOWNLOAD_ROOT)
        for entry in _iter_downloads():
            index.setdefault(entry.stem, []).append(entry.relative_to(root_path).as_posix())
    except OSError:  # 目录不存在 / 没权限 —— 交给调用方按「找不到」处理
        index = {}
    _INDEX, _INDEX_AT, _INDEX_ROOT = index, now, root
    return index


def resolve_download_path(file_path: str) -> str:
    """把引擎给的 path / filename 归一成 `data/downloads/<真实文件名>`；找不到就返回空串。

    引擎「跳过下载」时只回 filename（无扩展名），直接存下来会让下一轮判定文件不存在，
    所以这里统一还原成能校验、能展示的相对路径。
    """
    if not file_path:
        return ""
    name = Path(file_path).name
    if not name:
        return ""
    matched = _stem_index().get(Path(name).stem)
    if matched:
        # 同名文件可能位于歌手目录/用户目录中，优先返回第一个真实相对路径。
        for candidate in matched:
            path = Path(DOWNLOAD_ROOT) / candidate
            if path.is_file():
                return f"data/downloads/{path.relative_to(Path(DOWNLOAD_ROOT)).as_posix()}"
    # 刚才下载完的可能还没进索引，按主干再查一次目录
    for entry in _iter_downloads():
        if entry.stem == Path(name).stem:
            return f"data/downloads/{entry.relative_to(Path(DOWNLOAD_ROOT)).as_posix()}"
    return ""


def _iter_downloads() -> list[Path]:
    try:
        root = Path(DOWNLOAD_ROOT)
        if not root.is_dir():
            return []
        return [
            e for e in root.rglob("*")
            if e.is_file() and e.suffix.lower() in _AUDIO_EXTENSIONS and not e.name.endswith(".part")
        ]
    except OSError:
        return []


def _existing_song_file(
    song: dict[str, Any], *, user_id: str = "", entries: list[Path] | None = None
) -> str:
    """递归扫描下载目录，按歌名+歌手识别手工放入的已有音频。

    默认文件名是「歌手 - 歌名」，也兼容「歌名 - 歌手」、歌手/歌名以及
    用户自定义子目录；只要求歌名和歌手同时出现在文件名或其父目录中，避免
    反向命名的文件被当成另一首歌。
    """
    title = _norm(str(song.get("name") or ""))
    artist = _norm(str(song.get("artist") or song.get("singer") or ""))
    if not title or not artist:
        return ""
    raw_artist = str(song.get("artist") or song.get("singer") or "")
    artist_tokens = [
        _norm(part)
        for part in re.split(r"\s*(?:/|、|，|,|;|；|\||&|＆)\s*", raw_artist)
        if _norm(part)
    ] or [artist]
    root = Path(DOWNLOAD_ROOT)
    wanted_user = str(user_id or "").lower()
    files = _iter_downloads() if entries is None else entries
    for entry in files:
        try:
            relative = entry.relative_to(root)
        except ValueError:
            continue
        parts = relative.parts
        # 多用户模式下不能把别的用户目录里的歌当成当前用户的已有文件；
        # 根目录下的旧版文件仍允许复用，兼容升级前的下载结果。
        if wanted_user and "users" in {part.lower() for part in parts[:-1]}:
            try:
                users_index = next(i for i, part in enumerate(parts[:-1]) if part.lower() == "users")
                if users_index + 1 >= len(parts) - 1 or parts[users_index + 1].lower() != wanted_user:
                    continue
            except StopIteration:
                pass
        stem = _norm(entry.stem)
        ancestors = [_norm(part) for part in parts[:-1]]
        if title not in stem:
            continue
        if any(token in stem or any(token in parent for parent in ancestors) for token in artist_tokens):
            return f"data/downloads/{relative.as_posix()}"
    return ""


def _match_keywords(song: dict[str, Any], include: list[str], exclude: list[str]) -> bool:
    haystack = f"{song.get('name', '')} {song.get('artist', '')} {song.get('album', '')}".lower()
    if include and not any(k in haystack for k in include):
        return False
    if exclude and any(k in haystack for k in exclude):
        return False
    return True


def _as_candidate(song: dict[str, Any], *, primary: bool, score: float) -> dict[str, Any]:
    return {
        "id": str(song.get("id", "")),
        "source": str(song.get("source", "")),
        "name": song.get("name", ""),
        "artist": song.get("artist", ""),
        "album": song.get("album", ""),
        "duration": int(song.get("duration") or 0),
        "cover": song.get("cover", ""),
        "extra": song.get("extra") or {},
        "is_primary": primary,
        "similarity": score,
    }


# 试听片段和现场录音经常能被搜索接口返回，但不应混入正式歌曲下载。
_VERSION_MARKERS = re.compile(
    r"(?:live|现场|演唱会|演唱會|跨年|音乐会|音樂會|演出|acoustic|demo|试听|試聽|片段|snippet|remix|dj|混音|加长版|加長版|伴奏|instrumental|广播剧|廣播劇|radio edit|sped up|slowed)",
    re.IGNORECASE,
)


def _candidate_text(candidate: dict[str, Any]) -> str:
    return " ".join(str(candidate.get(k) or "") for k in ("name", "album"))


def _is_variant(candidate: dict[str, Any]) -> bool:
    return bool(_VERSION_MARKERS.search(_candidate_text(candidate)))


def _is_preview(candidate: dict[str, Any]) -> bool:
    text = _candidate_text(candidate)
    return bool(re.search(r"(?:demo|试听|試聽|片段|snippet)", text, re.IGNORECASE)) or (
        0 < int(candidate.get("duration") or 0) <= 75
    )


def _reject_candidate(
    candidate: dict[str, Any], reference_duration: int = 0, *, allow_variant: bool = True
) -> str:
    """返回原因；正式版优先，改编版只能由调用方作为兜底。"""
    if _is_preview(candidate):
        return "疑似试听片段"
    if _is_variant(candidate) and not allow_variant:
        return "改编版本仅允许作为正式版不可用时的兜底"

    duration = int(candidate.get("duration") or 0)
    reference = int(reference_duration or 0)
    if duration and duration <= 75:
        return f"时长仅 {duration} 秒，疑似试听片段"
    if duration and reference and abs(duration - reference) > max(10, round(reference * 0.15)):
        return f"时长 {duration} 秒与原曲 {reference} 秒不匹配"
    return ""


# --------------------------------------------------------------------------- 发现
async def discover(
    discovery: Any,
    mon: dict[str, Any],
    *,
    lx_gateway: LxGateway | None = None,
    cookie_broker=None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """按监控类型抓取本轮的全部曲目。返回 (songs, warnings)。"""
    songs: list[dict[str, Any]] = []
    warnings: list[str] = []
    kind = mon["kind"]
    target = mon.get("target") or {}
    sources = mon.get("sources") or []

    if kind == "chart":
        entries = target.get("charts") or []
        if not entries:
            warnings.append("未选择任何榜单")
        for entry in entries:
            res = await resolve_chart(discovery, entry)
            label = entry.get("name") or entry.get("key") or entry.get("link") or entry.get("id") or "未知榜单"
            if not res["ok"]:
                warnings.append(f"榜单「{label}」解析失败：{res['message']}")
                continue
            for s in res["songs"][: settings.max_songs_per_playlist]:
                s["_origin"] = label
                songs.append(s)

    elif kind == "playlist":
        items = target.get("playlists") or []
        if not items:
            warnings.append("未配置任何歌单链接")
        for item in items:
            label = item.get("name") or item.get("link") or item.get("id") or "未知歌单"
            try:
                if item.get("id") and item.get("source"):
                    cur = await discovery.playlist_songs(item["id"], item["source"], link=item.get("link", ""))
                else:
                    found = await discovery.search_playlists(item.get("link", ""), None)
                    cur = []
                    for pl in found:
                        cur = await discovery.playlist_songs(pl["id"], pl["source"], link=pl.get("link") or "")
                        if cur:
                            break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"歌单「{label}」解析失败：{exc}")
                continue
            if not cur:
                warnings.append(f"歌单「{label}」没有解析到曲目（链接可能已失效或需要登录）")
                continue
            for s in cur[: settings.max_songs_per_playlist]:
                s["_origin"] = label
                songs.append(s)

    elif kind == "favorites":
        try:
            playlists = await discovery.user_playlists(sources)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"读取个人收藏失败：{exc}（请先在引擎里配置该平台 Cookie）")
            playlists = []
        if not playlists:
            warnings.append("没有读到任何个人歌单/收藏夹，请确认当前用户已扫码登录对应平台")
        selected = set(target.get("playlist_ids") or [])
        for pl in playlists:
            key = f"{pl['source']}:{pl['id']}"
            if selected and key not in selected:
                continue
            try:
                cur = await discovery.playlist_songs(pl["id"], pl["source"])
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"收藏夹「{pl.get('name')}」解析失败：{exc}")
                continue
            for s in cur[: settings.max_songs_per_playlist]:
                s["_origin"] = pl.get("name") or key
                songs.append(s)
    elif kind == "artist":
        # 上游没有「按歌手取全部歌曲」的接口，只有搜索。所以歌手关注 =
        # 用歌手名去搜，再用 exact_artist 让引擎严格匹配歌手，最后本地按歌手名复核一遍，
        # 把搜索结果里混进来的同名翻唱/别人的歌滤掉。
        artists = target.get("artists") or []
        if not artists:
            warnings.append("未配置任何关注的歌手")
        page_size = max(30, min(1000, int(target.get("page_size") or 100)))
        for item in artists:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            want_sources = item.get("sources") or sources
            try:
                if lx_gateway is not None:
                    cur, filter_stats = await lx_gateway.artist_songs(name, want_sources, limit=page_size)
                    warnings.append(
                        f"歌手「{name}」LX 搜索 {filter_stats['searched']} 条，保留正式曲 {filter_stats['kept']} 首；"
                        f"过滤歌手不符 {filter_stats['artist_mismatch']}、现场/翻唱/混音 {filter_stats['variant']}、"
                        f"试听 {filter_stats['preview']}、重复 {filter_stats['duplicate']}"
                        + (f"；达到上限后还有 {filter_stats['capped']} 首未加入" if filter_stats.get('capped') else "")
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"歌手「{name}」搜索失败：{exc}")
                continue
            kept = [s for s in cur if _artist_matches(s.get("artist", ""), name)]
            if not kept:
                warnings.append(f"歌手「{name}」没有搜到匹配的曲目（可能该平台未收录或需要登录 Cookie）")
                continue
            if len(kept) < len(cur):
                warnings.append(f"歌手「{name}」搜索到 {len(cur)} 首，按歌手名过滤后保留 {len(kept)} 首")
            for s in kept[:page_size]:
                s["_origin"] = name
                songs.append(s)

    else:
        warnings.append(f"未知的监控类型：{kind}")

    return _dedupe_list(songs), warnings


def _dedupe_list(songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for s in songs:
        key = (str(s.get("source", "")), str(s.get("id", "")))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# --------------------------------------------------------------------------- 主流程
async def run_monitor(
    db: Database,
    discovery_engine: Any,
    mon: dict[str, Any],
    *,
    lx_gateway: LxGateway,
) -> dict[str, Any]:
    run_id = db.start_run(mon["id"])
    log_lines: list[str] = []
    warnings: list[str] = []
    found = new_items = downloaded = skipped = failed = engine_skipped = 0

    try:
        songs, warnings = await discover(discovery_engine, mon, lx_gateway=lx_gateway)
        found = len(songs)
        log_lines.append(f"发现 {found} 首曲目" + (f"，{len(warnings)} 条提示" if warnings else ""))
        for w in warnings:
            log_lines.append(f"  ! {w}")

        include = _keywords(mon.get("include_kw", ""))
        exclude = _keywords(mon.get("exclude_kw", ""))
        existing = db.existing_song_keys(mon["id"])
        downloaded_paths = db.downloaded_tracks(mon["id"])
        user_id = str(mon.get("user_id") or "")
        download_files = _iter_downloads()
        # 旧版本只按数据库的 downloaded 状态认定「已下载」，文件被移动/手工放入
        # 子目录后会失去关联。这里重新校验每条记录对应的文件，并建立可复用指纹。
        downloaded_prints: set[str] = set()
        for row in db.downloaded_track_records(mon["id"]):
            row_song = {"name": row.get("name", ""), "artist": row.get("artist", "")}
            if _file_exists(row.get("file_path") or "") or _existing_song_file(
                row_song, user_id=user_id, entries=download_files
            ):
                downloaded_prints.update(_fingerprint_aliases(row_song["name"], row_song["artist"]))
        quality = q.normalize_quality(mon.get("quality"))
        sources = mon.get("sources") or []
        auto_download = bool(mon.get("auto_download"))

        todo: list[dict[str, Any]] = []
        todo_prints: set[str] = set()
        for s in songs:
            key = (str(s.get("source", "")), str(s.get("id", "")))
            if key in existing and key in downloaded_paths and _file_exists(downloaded_paths[key]):
                # 只有数据库记录仍对应真实文件时才跳过；文件被删后必须重新下载。
                db.touch_track(mon["id"], s)
                skipped += 1
                continue
            # 上一轮可能是在别的平台下到的（换源后曲目挂在新 source:id 下），
            # 用 (source, id) 找不到，得按「歌名+歌手」认出它已经下过。
            song_prints = _fingerprint_aliases(s.get("name", ""), s.get("artist", ""))
            if song_prints & downloaded_prints:
                db.touch_track(mon["id"], s)
                skipped += 1
                continue
            # 即使数据库里没有记录，也识别挂载目录中已经存在的音频，避免首次
            # 建立监控或清库后再次下载整批歌曲。
            existing_file = _existing_song_file(s, user_id=user_id, entries=download_files)
            if existing_file:
                db.upsert_track(mon["id"], s, status="downloaded", file_path=existing_file,
                                quality_actual="本地已有文件")
                downloaded_prints.update(song_prints)
                skipped += 1
                continue
            if not _match_keywords(s, include, exclude):
                db.upsert_track(mon["id"], s, status="skipped", error="被关键词规则过滤")
                skipped += 1
                continue
            # 同一轮发现来自不同平台的同一首歌时，只保留一份待下载任务。
            if song_prints & todo_prints:
                skipped += 1
                continue
            todo_prints.update(song_prints)
            # 不用 monitor 数据库的历史记录拦截：文件可能已被用户删除。
            todo.append(s)

        new_items = len(todo)
        log_lines.append(f"其中新曲目 {new_items} 首，跳过 {skipped} 首")

        if not todo:
            db.finish_run(run_id, status="ok", found=found, new_items=0, downloaded=0, skipped=skipped,
                          failed=0, message="无新增曲目", log="\n".join(log_lines))
            _reschedule(db, mon)
            return {"run_id": run_id, "found": found, "new": 0, "downloaded": 0, "skipped": skipped, "failed": 0, "warnings": warnings}

        # 本轮预算：以监控自己的「单次最多下载」为准；MAX_DOWNLOADS_PER_RUN>0 时才再夹一层硬上限
        budget = max(1, int(mon.get("max_downloads") or 30))
        if settings.max_downloads_per_run > 0:
            budget = min(budget, settings.max_downloads_per_run)
        batch = max(1, min(settings.download_batch, budget))
        waves = (min(budget, len(todo)) + batch - 1) // batch
        log_lines.append(
            f"本轮上限 {budget} 首，按每批 {batch} 首分 {waves} 批推送（一批下完再推下一批）"
        )

        sem = asyncio.Semaphore(settings.download_concurrency)
        lock = asyncio.Lock()

        async def handle(song: dict[str, Any]) -> None:
            nonlocal downloaded, failed, engine_skipped
            async with sem:
                head = f"[{song.get('_origin', '')}] {song.get('name', '')} - {song.get('artist', '')}"
                local_log: list[str] = [f"- {head}"]
                try:
                    result = None
                    cand = None
                    retries_used = 0
                    while True:
                        try:
                            lx_candidates = await lx_gateway.candidates(song, sources=sources, log_lines=local_log)
                            if not lx_candidates:
                                raise RuntimeError("LX 没有找到可信的同名歌曲候选")
                            cand = max(lx_candidates, key=lambda item: float(item.get("similarity") or 0.0))
                            if not auto_download:
                                db.upsert_track(mon["id"], cand, status="pending", quality_actual=f"LX 目标 {quality}")
                                local_log.append("  → 仅记录（未开启自动下载）")
                                return
                            result = await lx_gateway.download(
                                song, candidates=lx_candidates, quality=quality, user_id=user_id,
                                download_subdir=db.get_user_setting(user_id, "lx_download_subdir", ""),
                                filename_template=db.get_user_setting(user_id, "lx_filename_template", settings.lx_filename_template),
                                artist_dir=bool(db.get_user_setting(user_id, "lx_artist_dir", settings.lx_artist_dir)),
                            )
                            break
                        except Exception as exc:  # noqa: BLE001
                            retryable = bool(getattr(exc, "retryable", True))
                            if not retryable or retries_used >= settings.download_retries:
                                raise
                            retries_used += 1
                            delay = min(30.0, float(2 ** (retries_used - 1)))
                            local_log.append(
                                f"  ↻ 第 {retries_used} 次自动重试（{delay:g}s 后）：{exc}"
                            )
                            await asyncio.sleep(delay)
                    assert cand is not None and result is not None
                    actual_quality = str(result.get("quality") or quality)
                    file_path = result.get("path") or ""
                    db.upsert_track(
                        mon["id"], cand, status="downloaded",
                        quality_actual=f"LX {actual_quality} / {result.get('source') or cand.get('source', '')}",
                        file_path=file_path,
                    )
                    async with lock:
                        downloaded += 1
                        if result.get("skipped"):
                            engine_skipped += 1
                    local_log.append(
                        f"  → LX {'已有，未写新文件' if result.get('skipped') else '下载完成'} "
                        f"{actual_quality} {result.get('source') or ''} {file_path}"
                    )

                except Exception as exc:  # noqa: BLE001
                    async with lock:
                        failed += 1
                    db.upsert_track(mon["id"], song, status="failed", error=str(exc)[:400])
                    local_log.append(f"  × 失败（自动重试上限 {settings.download_retries} 次）：{exc}")
                finally:
                    async with lock:
                        log_lines.extend(local_log)

        # 分批推送：一批全部结束（含重试）后才推下一批，避免把整份清单一次性灌给引擎
        remaining = list(todo)
        wave_no = 0
        while remaining and budget > 0:
            take = min(batch, budget, len(remaining))
            wave, remaining = remaining[:take], remaining[take:]
            budget -= take
            wave_no += 1
            log_lines.append(
                f"—— 第 {wave_no} 批：推送 {len(wave)} 首"
                + (f"，本批完成后继续" if remaining else "，本轮最后一批")
            )
            await asyncio.gather(*(handle(s) for s in wave))
            if remaining and settings.download_batch_gap > 0:
                await asyncio.sleep(settings.download_batch_gap)

        log_lines.append(
            f"══ 本轮结束：处理 {downloaded} 首（其中引擎侧已存在 {engine_skipped} 首、没有产生新文件），"
            f"失败 {failed} 首，跳过 {skipped} 首"
        )

        status = "ok" if failed == 0 else ("partial" if downloaded else "error")
        message = "；".join(warnings[:3])
        db.finish_run(run_id, status=status, found=found, new_items=new_items, downloaded=downloaded,
                      skipped=skipped, failed=failed, engine_skipped=engine_skipped, message=message, log="\n".join(log_lines))
        _reschedule(db, mon)
        return {
            "run_id": run_id, "found": found, "new": new_items, "downloaded": downloaded,
            "engine_skipped": engine_skipped, "skipped": skipped, "failed": failed,
            "warnings": warnings[:10],
        }

    except asyncio.CancelledError:
        # 容器停止或任务被取消时也必须收尾，否则网页会永久显示 running。
        db.finish_run(run_id, status="error", found=found, new_items=new_items, downloaded=downloaded,
                      skipped=skipped, failed=failed, engine_skipped=engine_skipped, message="任务被取消，中断下载", log="\n".join(log_lines))
        _reschedule(db, mon)
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("监控 %s 执行异常", mon.get("name"))
        db.finish_run(run_id, status="error", found=found, new_items=new_items, downloaded=downloaded,
                      skipped=skipped, failed=failed, engine_skipped=engine_skipped, message=str(exc)[:400], log="\n".join(log_lines))
        _reschedule(db, mon)
        return {"run_id": run_id, "found": found, "new": new_items, "downloaded": downloaded,
                "skipped": skipped, "failed": failed, "error": str(exc)}


def s_key(song: dict[str, Any]) -> str:
    return f"{song.get('source', '')}:{song.get('id', '')}"


# --------------------------------------------------------------------------- 供 API 复用的单曲动作
async def preview_monitor(
    discovery_engine: Any,
    mon: dict[str, Any],
    limit: int = 60,
    *,
    lx_gateway: LxGateway | None = None,
) -> dict[str, Any]:
    """干跑：只做「发现 + 过滤」，不下载。用于新建监控前确认配置是否正确。"""
    songs, warnings = await discover(discovery_engine, mon, lx_gateway=lx_gateway)
    include = _keywords(mon.get("include_kw", ""))
    exclude = _keywords(mon.get("exclude_kw", ""))
    kept = [s for s in songs if _match_keywords(s, include, exclude)]
    return {
        "total": len(songs),
        "filtered": len(kept),
        "warnings": warnings,
        "songs": [
            {
                "id": s.get("id"),
                "source": s.get("source"),
                "name": s.get("name"),
                "artist": s.get("artist"),
                "album": s.get("album"),
                "duration": s.get("duration"),
                "origin": s.get("_origin", ""),
            }
            for s in kept[:limit]
        ],
    }


async def redownload(
    db: Database,
    mon: dict[str, Any],
    song: dict[str, Any],
    *,
    lx_gateway: LxGateway,
) -> dict[str, Any]:
    """对单首歌曲（通常来自失败记录的重试）重新走一遍择优 + 下载。"""
    quality = q.normalize_quality(mon.get("quality"))
    log_lines: list[str] = []
    user_id = str(mon.get("user_id") or "")
    retries_used = 0
    while True:
        try:
            candidates = await lx_gateway.candidates(song, sources=mon.get("sources") or [], log_lines=log_lines)
            if not candidates:
                raise RuntimeError("LX 没有找到可信的同名歌曲候选")
            result = await lx_gateway.download(
                song, candidates=candidates, quality=quality, user_id=user_id,
                download_subdir=db.get_user_setting(user_id, "lx_download_subdir", ""),
                filename_template=db.get_user_setting(user_id, "lx_filename_template", settings.lx_filename_template),
                artist_dir=bool(db.get_user_setting(user_id, "lx_artist_dir", settings.lx_artist_dir)),
            )
            break
        except Exception as exc:  # noqa: BLE001
            retryable = bool(getattr(exc, "retryable", True))
            if not retryable or retries_used >= settings.download_retries:
                db.upsert_track(mon["id"], song, status="failed", error=str(exc)[:400])
                return {"ok": False, "message": str(exc), "log": log_lines}
            retries_used += 1
            delay = min(30.0, float(2 ** (retries_used - 1)))
            log_lines.append(f"↻ 第 {retries_used} 次自动重试（{delay:g}s 后）：{exc}")
            await asyncio.sleep(delay)
    cand = max(candidates, key=lambda item: float(item.get("similarity") or 0.0))
    actual = str(result.get("quality") or quality)
    db.upsert_track(
        mon["id"], cand, status="downloaded", quality_actual=f"LX {actual} / {result.get('source') or ''}",
        file_path=result.get("path") or "",
    )
    return {"ok": True, "message": "LX 重试下载成功", "song": cand, "quality": actual, "log": log_lines}


def _reschedule(db: Database, mon: dict[str, Any]) -> None:
    try:
        db.execute("UPDATE monitors SET last_run_at = ? WHERE id = ?", (now_iso(), mon["id"]))
        db.set_next_run(mon["id"], interval_minutes=int(mon.get("interval_minutes") or 360))
    except Exception:  # noqa: BLE001
        log.exception("更新下次运行时间失败 monitor=%s", mon.get("id"))
