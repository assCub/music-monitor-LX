from __future__ import annotations

import time
import unittest

from app import api
from app.auth import reset_current_user, set_current_user


class PlatformLoginTests(unittest.IsolatedAsyncioTestCase):
    def test_public_result_hides_protocol_fields(self) -> None:
        result = api._public_platform_login_result({
            "source": "soda", "status": "scanned", "message": "raw upstream text",
            "extra": {
                "need_sms": "true", "mobile": "138****0000",
                "encrypt_uid": "private-user", "verify_params": "private-params", "token": "private-token",
            },
        })
        self.assertEqual(result["message"], "扫码确认成功，需要完成短信安全验证")
        self.assertEqual(result["extra"], {"need_sms": "true", "mobile": "138****0000"})
        self.assertNotIn("private", str(result))

    async def test_soda_action_keeps_real_parameters_on_server(self) -> None:
        user = {"id": "00000000-0000-0000-0000-000000000001", "role": "user"}
        context = set_current_user(user)
        original_check = api.platform_auth.check
        captured: list[tuple[str, str]] = []

        async def fake_check(source: str, key: str):
            captured.append((source, key))
            return {
                "source": "soda", "status": "scanned", "message": "sent",
                "extra": {"need_sms": "true", "need_sms_code": "true", "mobile": "138****0000"},
            }

        try:
            api.platform_auth.check = fake_check
            api._platform_login_sessions["browser-session"] = {
                "user_id": user["id"], "source": "soda", "key": "real-platform-token",
                "expires_at": time.monotonic() + 60,
                "extra": {"encrypt_uid": "server-user", "verify_params": "server-params", "need_sms": "true"},
            }
            result = await api.platform_login_action(
                "soda", api.PlatformLoginActionIn(key="browser-session", action="send_code")
            )
            self.assertEqual(captured, [("soda", "real-platform-token|send_code|server-user|server-params")])
            self.assertEqual(result["extra"]["mobile"], "138****0000")
            self.assertNotIn("server-user", str(result))
            self.assertNotIn("server-params", str(result))
        finally:
            api._platform_login_sessions.pop("browser-session", None)
            api.platform_auth.check = original_check
            reset_current_user(context)


if __name__ == "__main__":
    unittest.main()
