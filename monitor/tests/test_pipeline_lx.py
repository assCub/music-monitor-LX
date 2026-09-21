from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app import pipeline
from app.config import settings
from app.db import Database


class FakeDiscoveryEngine:
    async def playlist_songs(self, playlist_id: str, source: str, *, link: str = "") -> list[dict[str, Any]]:
        return [{
            "id": "origin-1",
            "source": source,
            "name": "稻香",
            "artist": "周杰伦",
            "album": "魔杰座",
            "duration": 223,
            "cover": "",
            "extra": {},
        }]


class FakeLxGateway:
    def __init__(self) -> None:
        self.download_calls: list[dict[str, Any]] = []

    async def candidates(self, song, *, sources=None, log_lines=None):
        if log_lines is not None:
            log_lines.append("  LX 搜索 tx：最佳 稻香 - 周杰伦，匹配度 1.00")
        return [{
            "id": "003aAYrm3GE0Ac",
            "source": "tx",
            "name": "稻香",
            "artist": "周杰伦",
            "album": "魔杰座",
            "duration": 223,
            "extra": {"songmid": "003aAYrm3GE0Ac"},
            "similarity": 1.0,
        }]

    async def download(self, song, *, candidates, quality, **kwargs):
        self.download_calls.append({"song": song, "candidates": candidates, "quality": quality})
        return {
            "ok": True,
            "path": "data/downloads/周杰伦 - 稻香.flac",
            "quality": "master",
            "source": "tx",
            "sourceId": "ikun",
            "skipped": False,
        }


class LxPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_uses_lx_gateway_and_records_actual_quality(self) -> None:
        with tempfile.TemporaryDirectory(prefix="music-monitor-lx-pipeline-") as tmp:
            db = Database(str(Path(tmp) / "monitor.db"))
            mid = db.create_monitor({
                "name": "LX test",
                "kind": "playlist",
                "enabled": False,
                "sources": ["qq", "netease"],
                "target": {"playlists": [{"id": "playlist-1", "source": "qq"}]},
                "quality": "master",
                "fallback": "best_effort",
                "auto_download": True,
                "embed": True,
                "max_downloads": 1,
            })
            gateway = FakeLxGateway()
            result = await pipeline.run_monitor(db, FakeDiscoveryEngine(), db.get_monitor(mid), lx_gateway=gateway)

            self.assertEqual(result["downloaded"], 1)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(len(gateway.download_calls), 1)
            track = db.list_tracks(monitor_id=mid, limit=10)[0]
            self.assertEqual(track["status"], "downloaded")
            self.assertEqual(track["quality_actual"], "LX master / tx")
            self.assertEqual(track["file_path"], "data/downloads/周杰伦 - 稻香.flac")


if __name__ == "__main__":
    unittest.main()
