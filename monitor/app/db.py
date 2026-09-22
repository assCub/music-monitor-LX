"""SQLite 持久化层。表结构简单，直接写 SQL，避免引入 ORM 依赖。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY(user_id, key)
);

CREATE TABLE IF NOT EXISTS monitors (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT,
    name              TEXT    NOT NULL,
    kind              TEXT    NOT NULL,              -- chart | playlist | favorites
    enabled           INTEGER NOT NULL DEFAULT 1,
    sources           TEXT    NOT NULL DEFAULT '[]', -- JSON: 参与的平台列表
    target            TEXT    NOT NULL DEFAULT '{}', -- JSON: 榜单/链接/收藏夹/歌手的具体目标
    quality           TEXT    NOT NULL DEFAULT 'master',
    fallback          TEXT    NOT NULL DEFAULT 'best_effort', -- best_effort | skip
    auto_download     INTEGER NOT NULL DEFAULT 1,
    embed             INTEGER NOT NULL DEFAULT 1,
    interval_minutes  INTEGER NOT NULL DEFAULT 360,
    max_downloads     INTEGER NOT NULL DEFAULT 30,
    include_kw        TEXT    NOT NULL DEFAULT '',
    exclude_kw        TEXT    NOT NULL DEFAULT '',
    last_run_at       TEXT,
    next_run_at       TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id     INTEGER NOT NULL,
    source         TEXT    NOT NULL,
    song_id        TEXT    NOT NULL,
    name           TEXT    NOT NULL DEFAULT '',
    artist         TEXT    NOT NULL DEFAULT '',
    album          TEXT    NOT NULL DEFAULT '',
    duration       INTEGER NOT NULL DEFAULT 0,
    cover          TEXT    NOT NULL DEFAULT '',
    extra          TEXT    NOT NULL DEFAULT '{}',
    status         TEXT    NOT NULL DEFAULT 'pending', -- pending|downloaded|skipped|failed|missing
    quality_actual TEXT    NOT NULL DEFAULT '',
    bitrate        TEXT    NOT NULL DEFAULT '',
    file_path      TEXT    NOT NULL DEFAULT '',
    error          TEXT    NOT NULL DEFAULT '',
    hit_count      INTEGER NOT NULL DEFAULT 1,
    first_seen     TEXT    NOT NULL,
    last_seen      TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL,
    UNIQUE(monitor_id, source, song_id)
);
CREATE INDEX IF NOT EXISTS idx_tracks_monitor ON tracks(monitor_id);
CREATE INDEX IF NOT EXISTS idx_tracks_status  ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_name    ON tracks(name, artist);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id  INTEGER NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    status      TEXT    NOT NULL DEFAULT 'running', -- running|ok|partial|error
    found       INTEGER NOT NULL DEFAULT 0,
    new_items   INTEGER NOT NULL DEFAULT 0,
    downloaded  INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    engine_skipped INTEGER NOT NULL DEFAULT 0,      -- 其中「引擎库里已有、没产生新文件」的数量
    message     TEXT    NOT NULL DEFAULT '',
    log         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_monitor ON runs(monitor_id, id DESC);
"""

# 增量迁移：老库补列，避免升级后启动即报错。SQLite 不支持 ADD COLUMN IF NOT EXISTS。
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("runs", "engine_skipped", "ALTER TABLE runs ADD COLUMN engine_skipped INTEGER NOT NULL DEFAULT 0"),
    ("monitors", "user_id", "ALTER TABLE monitors ADD COLUMN user_id TEXT"),
]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Database:
    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """给老库补上后加的列（新建的库已经在 _SCHEMA 里带了）。"""
        with self._lock:
            for table, column, ddl in _MIGRATIONS:
                cols = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    self._conn.execute(ddl)
            self._conn.commit()

    # ---------------- 通用 ----------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---------------- settings ----------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def all_settings(self) -> dict[str, Any]:
        return {r["key"]: self.get_setting(r["key"]) for r in self.query("SELECT key FROM settings")}

    def get_user_setting(self, user_id: str, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def set_user_setting(self, user_id: str, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO user_settings(user_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
            (user_id, key, json.dumps(value, ensure_ascii=False)),
        )

    def all_user_settings(self, user_id: str) -> dict[str, Any]:
        rows = self.query("SELECT key FROM user_settings WHERE user_id = ?", (user_id,))
        return {r["key"]: self.get_user_setting(user_id, r["key"]) for r in rows}

    # ---------------- users / sessions ----------------
    def user_count(self) -> int:
        row = self.one("SELECT COUNT(*) AS c FROM users")
        return int(row["c"] if row else 0)

    def create_user(self, username: str, password_hash: str, *, display_name: str = "", role: str = "user") -> dict[str, Any]:
        user_id = str(uuid.uuid4())
        ts = now_iso()
        self.execute(
            "INSERT INTO users(id,username,display_name,password_hash,role,enabled,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)",
            (user_id, username.strip(), display_name.strip(), password_hash, role, ts, ts),
        )
        return self.get_user(user_id, include_secret=False) or {}

    def get_user(self, user_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
        fields = "*" if include_secret else "id,username,display_name,role,enabled,created_at,updated_at"
        row = self.one(f"SELECT {fields} FROM users WHERE id = ?", (user_id,))
        if row is None:
            return None
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        return data

    def get_user_by_username(self, username: str, *, include_secret: bool = False) -> dict[str, Any] | None:
        fields = "*" if include_secret else "id,username,display_name,role,enabled,created_at,updated_at"
        row = self.one(f"SELECT {fields} FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),))
        if row is None:
            return None
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        return data

    def list_users(self) -> list[dict[str, Any]]:
        return [dict(r) | {"enabled": bool(r["enabled"])} for r in self.query(
            "SELECT id,username,display_name,role,enabled,created_at,updated_at FROM users ORDER BY created_at"
        )]

    def update_user(self, user_id: str, **changes: Any) -> None:
        allowed = {"display_name", "role", "enabled", "password_hash"}
        fields, params = [], []
        for key, value in changes.items():
            if key in allowed:
                fields.append(f"{key} = ?")
                params.append(int(value) if key == "enabled" else value)
        if not fields:
            return
        fields.append("updated_at = ?")
        params.extend([now_iso(), user_id])
        self.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", tuple(params))

    def assign_orphan_data(self, user_id: str) -> None:
        self.execute("UPDATE monitors SET user_id = ? WHERE user_id IS NULL OR user_id = ''", (user_id,))

    def create_session(self, token_hash: str, user_id: str, expires_at: int) -> None:
        now = int(time.time())
        self.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        self.execute(
            "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
            (token_hash, user_id, expires_at, now),
        )

    def session_user(self, token_hash: str) -> dict[str, Any] | None:
        now = int(time.time())
        row = self.one(
            "SELECT u.id,u.username,u.display_name,u.role,u.enabled,u.created_at,u.updated_at "
            "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1",
            (token_hash, now),
        )
        if row is None:
            return None
        data = dict(row)
        data["enabled"] = True
        return data

    def delete_session(self, token_hash: str) -> None:
        self.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: str) -> None:
        self.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # ---------------- monitors ----------------
    def create_monitor(self, data: dict[str, Any], user_id: str | None = None) -> int:
        ts = now_iso()
        cur = self.execute(
            """INSERT INTO monitors
               (user_id, name, kind, enabled, sources, target, quality, fallback, auto_download, embed,
                interval_minutes, max_downloads, include_kw, exclude_kw,
                last_run_at, next_run_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)""",
            (
                user_id,
                data["name"],
                data["kind"],
                int(data.get("enabled", 1)),
                json.dumps(data.get("sources", []), ensure_ascii=False),
                json.dumps(data.get("target", {}), ensure_ascii=False),
                data.get("quality", "master"),
                data.get("fallback", "best_effort"),
                int(data.get("auto_download", 1)),
                int(data.get("embed", 1)),
                int(data.get("interval_minutes", 360)),
                int(data.get("max_downloads", 30)),
                data.get("include_kw", ""),
                data.get("exclude_kw", ""),
                ts,  # next_run_at（随即被 set_next_run 覆盖）
                ts,  # created_at
                ts,  # updated_at
            ),
        )
        mid = int(cur.lastrowid)
        self.set_next_run(mid, immediately=True)
        return mid

    def update_monitor(self, mid: int, data: dict[str, Any], user_id: str | None = None) -> None:
        fields, params = [], []
        for key in (
            "name", "kind", "quality", "fallback", "include_kw", "exclude_kw",
        ):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(data[key])
        for key in ("enabled", "auto_download", "embed", "interval_minutes", "max_downloads"):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(int(data[key]))
        for key in ("sources", "target"):
            if key in data:
                fields.append(f"{key} = ?")
                params.append(json.dumps(data[key], ensure_ascii=False))
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(now_iso())
        params.append(mid)
        sql = f"UPDATE monitors SET {', '.join(fields)} WHERE id = ?"
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        self.execute(sql, tuple(params))

    def delete_monitor(self, mid: int, user_id: str | None = None) -> None:
        if user_id is not None and self.get_monitor(mid, user_id) is None:
            return
        self.execute("DELETE FROM tracks WHERE monitor_id = ?", (mid,))
        self.execute("DELETE FROM runs WHERE monitor_id = ?", (mid,))
        self.execute("DELETE FROM monitors WHERE id = ?", (mid,))

    def get_monitor(self, mid: int, user_id: str | None = None) -> dict[str, Any] | None:
        sql, params = "SELECT * FROM monitors WHERE id = ?", [mid]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        row = self.one(sql, tuple(params))
        return self._row_to_monitor(row) if row else None

    def list_monitors(self, user_id: str | None = None) -> list[dict[str, Any]]:
        if user_id is None:
            rows = self.query("SELECT * FROM monitors ORDER BY id DESC")
        else:
            rows = self.query("SELECT * FROM monitors WHERE user_id = ? ORDER BY id DESC", (user_id,))
        return [self._row_to_monitor(r) for r in rows]

    def due_monitors(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM monitors WHERE user_id IS NOT NULL AND user_id <> '' AND enabled = 1 "
            "AND (next_run_at IS NULL OR next_run_at <= ?) "
            "ORDER BY COALESCE(next_run_at, '') ASC LIMIT ?",
            (now_iso(), limit),
        )
        return [self._row_to_monitor(r) for r in rows]

    def set_next_run(self, mid: int, *, immediately: bool = False, interval_minutes: int | None = None) -> None:
        if interval_minutes is None:
            mon = self.get_monitor(mid)
            interval_minutes = int(mon["interval_minutes"]) if mon else 360
        nxt = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + interval_minutes * 60))
        if immediately:
            nxt = now_iso()
        self.execute("UPDATE monitors SET next_run_at = ? WHERE id = ?", (nxt, mid))

    @staticmethod
    def _row_to_monitor(row: sqlite3.Row) -> dict[str, Any]:
        m = dict(row)
        m["sources"] = json.loads(m.get("sources") or "[]")
        m["target"] = json.loads(m.get("target") or "{}")
        m["enabled"] = bool(m.get("enabled"))
        m["auto_download"] = bool(m.get("auto_download"))
        m["embed"] = bool(m.get("embed"))
        return m

    # ---------------- runs ----------------
    def recover_running_runs(self) -> int:
        """容器重启后收尾上次未完成的运行，避免网页永久显示 running。"""
        cur = self.execute(
            "UPDATE runs SET finished_at = ?, status = 'error', message = ?, log = CASE WHEN log = '' THEN ? ELSE log END "
            "WHERE status = 'running'",
            (now_iso(), "服务重启，中断了上次运行；可重新执行监控", "服务重启，中断了上次运行"),
        )
        return cur.rowcount

    def start_run(self, monitor_id: int) -> int:
        cur = self.execute(
            "INSERT INTO runs(monitor_id, started_at, status) VALUES(?,?,'running')",
            (monitor_id, now_iso()),
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, **kw: Any) -> None:
        self.execute(
            """UPDATE runs SET finished_at = ?, status = ?, found = ?, new_items = ?,
               downloaded = ?, skipped = ?, failed = ?, engine_skipped = ?, message = ?, log = ? WHERE id = ?""",
            (
                now_iso(),
                kw.get("status", "ok"),
                kw.get("found", 0),
                kw.get("new_items", 0),
                kw.get("downloaded", 0),
                kw.get("skipped", 0),
                kw.get("failed", 0),
                kw.get("engine_skipped", 0),
                kw.get("message", "")[:500],
                kw.get("log", "")[:20000],
                run_id,
            ),
        )

    def recent_runs(self, monitor_id: int | None = None, limit: int = 50, user_id: str | None = None) -> list[dict[str, Any]]:
        if monitor_id:
            rows = self.query("SELECT * FROM runs WHERE monitor_id = ? ORDER BY id DESC LIMIT ?", (monitor_id, limit))
        elif user_id is not None:
            rows = self.query(
                "SELECT r.* FROM runs r JOIN monitors m ON m.id=r.monitor_id WHERE m.user_id=? ORDER BY r.id DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            rows = self.query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ---------------- tracks ----------------
    def existing_song_keys(self, monitor_id: int) -> set[tuple[str, str]]:
        rows = self.query("SELECT source, song_id FROM tracks WHERE monitor_id = ?", (monitor_id,))
        return {(r["source"], r["song_id"]) for r in rows}

    def downloaded_tracks(self, monitor_id: int) -> dict[tuple[str, str], str]:
        rows = self.query(
            "SELECT source, song_id, file_path FROM tracks "
            "WHERE monitor_id = ? AND status = 'downloaded'",
            (monitor_id,),
        )
        return {(r["source"], r["song_id"]): (r["file_path"] or "") for r in rows}

    def downloaded_track_records(self, monitor_id: int) -> list[dict[str, Any]]:
        """返回已下载记录的元数据，供本地文件存在性校验使用。"""
        rows = self.query(
            "SELECT source, song_id, name, artist, album, file_path FROM tracks "
            "WHERE monitor_id = ? AND status = 'downloaded'",
            (monitor_id,),
        )
        return [dict(row) for row in rows]

    def upsert_track(self, monitor_id: int, song: dict[str, Any], status: str, **kw: Any) -> None:
        ts = now_iso()
        self.execute(
            """INSERT INTO tracks
               (monitor_id, source, song_id, name, artist, album, duration, cover, extra,
                status, quality_actual, bitrate, file_path, error, hit_count, first_seen, last_seen, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(monitor_id, source, song_id) DO UPDATE SET
                 status = excluded.status,
                 quality_actual = excluded.quality_actual,
                 bitrate = excluded.bitrate,
                 file_path = CASE WHEN excluded.file_path <> '' THEN excluded.file_path ELSE tracks.file_path END,
                 error = excluded.error,
                 hit_count = tracks.hit_count + 1,
                 last_seen = excluded.last_seen,
                 updated_at = excluded.updated_at""",
            (
                monitor_id,
                song.get("source", ""),
                str(song.get("id", "")),
                song.get("name", ""),
                song.get("artist", ""),
                song.get("album", ""),
                int(song.get("duration") or 0),
                song.get("cover", ""),
                json.dumps(song.get("extra") or {}, ensure_ascii=False),
                status,
                kw.get("quality_actual", ""),
                kw.get("bitrate", ""),
                kw.get("file_path", ""),
                kw.get("error", ""),
                1,   # hit_count
                ts,  # first_seen
                ts,  # last_seen
                ts,  # updated_at
            ),
        )

    def touch_track(self, monitor_id: int, song: dict[str, Any]) -> None:
        """已见过的歌：只刷新命中次数与最后出现时间，不改状态。"""
        ts = now_iso()
        self.execute(
            "UPDATE tracks SET hit_count = hit_count + 1, last_seen = ?, updated_at = ? "
            "WHERE monitor_id = ? AND source = ? AND song_id = ?",
            (ts, ts, monitor_id, song.get("source", ""), str(song.get("id", ""))),
        )

    # 「已下载」指纹（歌名+歌手）。换源后曲目会挂在新 source:id 下，
    # 下一轮用 (source, id) 就对不上了，只能靠指纹认出「这首其实已经下过了」。
    def downloaded_fingerprints(self, monitor_id: int | None = None) -> set[str]:
        if monitor_id:
            rows = self.query(
                "SELECT name, artist FROM tracks WHERE status = 'downloaded' AND monitor_id = ?",
                (monitor_id,),
            )
        else:
            rows = self.query("SELECT name, artist FROM tracks WHERE status = 'downloaded'")
        return {_fingerprint(r["name"], r["artist"]) for r in rows}

    def list_tracks(
        self,
        monitor_id: int | None = None,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT t.* FROM tracks t JOIN monitors m ON m.id=t.monitor_id WHERE 1=1"
        params: list[Any] = []
        if monitor_id:
            sql += " AND t.monitor_id = ?"
            params.append(monitor_id)
        if status:
            sql += " AND t.status = ?"
            params.append(status)
        if user_id is not None:
            sql += " AND m.user_id = ?"
            params.append(user_id)
        sql += " ORDER BY t.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [dict(r) for r in self.query(sql, tuple(params))]

    def track_stats(self, user_id: str | None = None) -> dict[str, int]:
        if user_id is None:
            rows = self.query("SELECT status, COUNT(*) AS c FROM tracks GROUP BY status")
        else:
            rows = self.query(
                "SELECT t.status,COUNT(*) AS c FROM tracks t JOIN monitors m ON m.id=t.monitor_id WHERE m.user_id=? GROUP BY t.status",
                (user_id,),
            )
        stats = {r["status"]: r["c"] for r in rows}
        stats["total"] = sum(stats.values())
        return stats


def _fingerprint(name: str, artist: str) -> str:
    """歌名+歌手的归一化指纹，用于跨监控去重。"""
    def norm(s: str) -> str:
        s = (s or "").lower()
        return "".join(ch for ch in s if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    return f"{norm(name)}|{norm(artist)}"


fingerprint = _fingerprint
