from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.account_platforms import ACCOUNT_PLATFORMS, QR_LOGIN_SOURCES
from app.auth import hash_password, verify_password
from app.credentials import CredentialStore
from app.db import Database
from app.discovery import DirectDiscovery


class MultiUserTests(unittest.TestCase):
    def test_account_platforms_separate_qr_and_json_configuration(self) -> None:
        fixed = {key for key, item in ACCOUNT_PLATFORMS.items() if item["fixed"]}
        self.assertEqual(fixed, {"netease", "qq", "kugou", "bilibili", "soda"})
        self.assertEqual(QR_LOGIN_SOURCES, {"netease", "qq", "qq_wx", "kugou", "bilibili", "soda"})
        self.assertFalse(ACCOUNT_PLATFORMS["kuwo"]["qr_sources"])
        self.assertFalse(ACCOUNT_PLATFORMS["migu"]["qr_sources"])
        self.assertTrue(DirectDiscovery._stored_account("kuwo", "kw_token=test")["valid"])

    def test_users_monitors_and_credentials_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="music-monitor-users-") as tmp:
            root = Path(tmp)
            db = Database(str(root / "monitor.db"))
            admin_hash = hash_password("AdminPass123!")
            self.assertTrue(verify_password("AdminPass123!", admin_hash))
            admin = db.create_user("admin", admin_hash, role="admin")
            alice = db.create_user("alice", hash_password("AlicePass123!"))
            db.create_monitor({"name": "admin-monitor", "kind": "artist"}, admin["id"])
            db.create_monitor({"name": "alice-monitor", "kind": "artist"}, alice["id"])
            self.assertEqual([m["name"] for m in db.list_monitors(admin["id"])], ["admin-monitor"])
            self.assertEqual([m["name"] for m in db.list_monitors(alice["id"])], ["alice-monitor"])

            store = CredentialStore(root / "users", root)
            store.save(alice["id"], {"qq": "uin=o123; qm_keyst=secret"})
            encrypted = (root / "users" / alice["id"] / "credentials.enc").read_bytes()
            self.assertNotIn(b"qm_keyst=secret", encrypted)
            self.assertEqual(store.load(alice["id"])["qq"], "uin=o123; qm_keyst=secret")
            self.assertEqual(store.load(admin["id"]), {})

            store.set_platform(alice["id"], "netease", "MUSIC_U=netease-secret")
            self.assertEqual(set(store.configured(alice["id"])), {"qq", "netease"})
            store.remove_platform(alice["id"], "qq")
            self.assertEqual(store.load(alice["id"]), {"netease": "MUSIC_U=netease-secret"})
            self.assertEqual(store.load(admin["id"]), {})


if __name__ == "__main__":
    unittest.main()
