from __future__ import annotations

import unittest

from app.lx_gateway import SOURCE_MAP, candidate_score


class LxCandidateTests(unittest.TestCase):
    def test_platform_mapping(self) -> None:
        self.assertEqual(SOURCE_MAP["qq"], "tx")
        self.assertEqual(SOURCE_MAP["netease"], "wy")

    def test_official_version_beats_live_variant(self) -> None:
        reference = {"name": "稻香", "artist": "周杰伦", "duration": 223}
        official = {"name": "稻香", "artist": "周杰伦", "duration": 223}
        live = {"name": "稻香 (Live)", "artist": "周杰伦", "duration": 222}
        self.assertGreater(candidate_score(reference, official), candidate_score(reference, live))

    def test_wrong_artist_is_rejected_by_threshold(self) -> None:
        reference = {"name": "稻香", "artist": "周杰伦", "duration": 223}
        cover = {"name": "稻香", "artist": "某翻唱歌手", "duration": 223}
        self.assertLess(candidate_score(reference, cover), 0.76)


class LxArtistDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_artist_search_paginates_filters_variants_and_deduplicates(self) -> None:
        from app.lx_gateway import LxGateway

        gateway = LxGateway("http://unused")

        async def fake_search_result(keyword, source, *, page=1, limit=30):
            if page > 2:
                return {"items": [], "total": 60}
            items = []
            start = (page - 1) * 30
            for i in range(30):
                items.append({
                    "id": f"{page}-{i}", "source": source, "name": f"正式歌曲{start+i}",
                    "artist": "周杰伦", "duration": 240,
                })
            if page == 1:
                items[0] = {"id": "live", "source": source, "name": "晴天 (Live)", "artist": "周杰伦", "duration": 240}
                items[1] = {"id": "cover", "source": source, "name": "晴天", "artist": "周杰伦模仿者", "duration": 240}
            if page == 2:
                items[0] = {"id": "duplicate", "source": source, "name": "正式歌曲2", "artist": "周杰伦", "duration": 240}
            return {"items": items, "total": 60}

        gateway.search_result = fake_search_result  # type: ignore[method-assign]
        songs, stats = await gateway.artist_songs("周杰伦", ["qq"], limit=60)
        self.assertEqual(stats["searched"], 60)
        self.assertEqual(stats["variant"], 1)
        self.assertEqual(stats["artist_mismatch"], 1)
        self.assertEqual(stats["duplicate"], 1)
        self.assertEqual(len(songs), 57)
        self.assertTrue(all(song["artist"] == "周杰伦" for song in songs))


if __name__ == "__main__":
    unittest.main()
