from __future__ import annotations

import unittest

from app import api


class PlatformLoginTests(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
