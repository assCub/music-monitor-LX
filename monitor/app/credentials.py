"""按用户加密保存平台 Cookie。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CredentialStore:
    def __init__(self, users_root: Path, data_root: Path) -> None:
        self.users_root = users_root
        self.users_root.mkdir(parents=True, exist_ok=True)
        configured = os.getenv("CREDENTIALS_MASTER_KEY", "").strip().encode()
        key_path = data_root / ".credentials.key"
        if configured:
            key = configured
        elif key_path.exists():
            key = key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            key_path.write_bytes(key + b"\n")
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
        self.fernet = Fernet(key)

    def user_dir(self, user_id: str) -> Path:
        if not user_id or any(ch not in "0123456789abcdef-" for ch in user_id.lower()):
            raise ValueError("用户 ID 不合法")
        path = self.users_root / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load(self, user_id: str) -> dict[str, str]:
        path = self.user_dir(user_id) / "credentials.enc"
        if not path.exists():
            return {}
        try:
            value = json.loads(self.fernet.decrypt(path.read_bytes()).decode())
            return {str(k): str(v) for k, v in value.items() if v} if isinstance(value, dict) else {}
        except (InvalidToken, ValueError, json.JSONDecodeError):
            raise RuntimeError("用户平台凭据无法解密，请检查 CREDENTIALS_MASTER_KEY") from None

    def save(self, user_id: str, mapping: dict[str, str]) -> None:
        path = self.user_dir(user_id) / "credentials.enc"
        clean = {str(k).strip(): str(v).strip() for k, v in mapping.items() if str(k).strip() and str(v).strip()}
        temp = path.with_suffix(".tmp")
        temp.write_bytes(self.fernet.encrypt(json.dumps(clean, ensure_ascii=False).encode()))
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        temp.replace(path)

    def configured(self, user_id: str) -> list[str]:
        return sorted(self.load(user_id).keys())

    def set_platform(self, user_id: str, source: str, cookie: str) -> None:
        mapping = self.load(user_id)
        if cookie.strip():
            mapping[source] = cookie.strip()
        else:
            mapping.pop(source, None)
        self.save(user_id, mapping)

    def remove_platform(self, user_id: str, source: str) -> None:
        mapping = self.load(user_id)
        mapping.pop(source, None)
        self.save(user_id, mapping)
