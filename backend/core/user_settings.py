"""Персональные настройки пользователя (изолированы по user_id)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.database.models import Setting

DEFAULTS: dict[str, bool] = {
    "memory_enabled": True,
    "camera_enabled": True,
    "microphone_enabled": True,
    "hand_tracking_enabled": True,
    "transfer_enabled": True,
    "voice_replies": True,
}


class UserSettings:
    def __init__(self, session: Session, user_id: int) -> None:
        self.session = session
        self.user_id = user_id

    def _key(self, name: str) -> str:
        if name not in DEFAULTS:
            raise ValueError(f"Неизвестная настройка: {name}")
        return f"u{self.user_id}:{name}"

    def get(self, name: str) -> bool:
        row = self.session.get(Setting, self._key(name))
        return DEFAULTS[name] if row is None else row.value == "true"

    def set(self, name: str, value: bool) -> None:
        key = self._key(name)
        row = self.session.get(Setting, key)
        raw = "true" if value else "false"
        if row is None:
            self.session.add(Setting(key=key, value=raw))
        else:
            row.value = raw
        self.session.commit()

    def all(self) -> dict[str, bool]:
        return {name: self.get(name) for name in DEFAULTS}
