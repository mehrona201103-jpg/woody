"""Персональные настройки пользователя (изолированы по user_id)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.database.models import Setting

DEFAULTS: dict[str, bool | str] = {
    "memory_enabled": True,
    "camera_enabled": True,
    "microphone_enabled": True,
    "hand_tracking_enabled": True,
    "transfer_enabled": True,
    "voice_replies": True,
    "always_on_camera": True,
    "learn_from_chats": True,
    "voice_engine": "browser",  # browser — голос браузера (временно), natural — живой голос провайдера
    "voice_name": "coral",
    "currency": "TJS",
}
CHOICES: dict[str, tuple[str, ...]] = {
    "voice_engine": ("natural", "browser"),
    "voice_name": ("coral", "nova", "shimmer", "sage", "alloy", "ash", "echo", "onyx"),
    "currency": ("TJS", "RUB", "USD"),
}


class UserSettings:
    def __init__(self, session: Session, user_id: int) -> None:
        self.session = session
        self.user_id = user_id

    def _key(self, name: str) -> str:
        if name not in DEFAULTS:
            raise ValueError(f"Неизвестная настройка: {name}")
        return f"u{self.user_id}:{name}"

    def get(self, name: str):
        row = self.session.get(Setting, self._key(name))
        default = DEFAULTS[name]
        if row is None:
            return default
        return row.value == "true" if isinstance(default, bool) else row.value

    def set(self, name: str, value) -> None:
        key = self._key(name)
        default = DEFAULTS[name]
        if isinstance(default, bool):
            raw = "true" if value else "false"
        else:
            raw = str(value)
            if name in CHOICES and raw not in CHOICES[name]:
                raise ValueError(f"Недопустимое значение для {name}")
        row = self.session.get(Setting, key)
        if row is None:
            self.session.add(Setting(key=key, value=raw))
        else:
            row.value = raw
        self.session.commit()

    def all(self) -> dict[str, bool | str]:
        return {name: self.get(name) for name in DEFAULTS}
