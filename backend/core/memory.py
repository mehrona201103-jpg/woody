from __future__ import annotations

import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.core.user_settings import UserSettings
from backend.database.models import Conversation, Memory

CATEGORIES = ("SHORT_TERM", "LONG_TERM", "FACT", "PREFERENCE", "EVENT", "EXPERIENCE", "SKILL")

_COLORS = {
    "синий": "blue", "красный": "red", "зелёный": "green", "зеленый": "green", "жёлтый": "yellow",
    "желтый": "yellow", "чёрный": "black", "черный": "black", "белый": "white", "фиолетовый": "purple",
    "оранжевый": "orange", "розовый": "pink", "голубой": "light_blue",
}
_LIKE = re.compile(r"\bя\s+люблю\s+([^.!?\n]{1,80})", re.IGNORECASE)
_LIVE = re.compile(r"\bя\s+живу\s+(?:в|на)\s+([^.!?\n]{1,60})", re.IGNORECASE)


def extract_facts(text: str) -> list[tuple[str, str, str]]:
    """Возвращает (category, key, value). Только явные утверждения пользователя о себе."""
    facts: list[tuple[str, str, str]] = []
    m = _LIKE.search(text)
    if m:
        value = m.group(1).strip().lower()
        if value in _COLORS:
            facts.append(("PREFERENCE", "favorite_color", _COLORS[value]))
        else:
            facts.append(("PREFERENCE", "likes", value))
    m = _LIVE.search(text)
    if m:
        facts.append(("FACT", "lives_in", m.group(1).strip()))
    return facts


class MemoryService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.chat_id: int | None = None          # текущий чат: сообщения и контекст привязаны к нему
        self.last_ids: dict[str, int] = {}

    def is_enabled(self, user_id: int) -> bool:
        return UserSettings(self.session, user_id).get("memory_enabled")

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        UserSettings(self.session, user_id).set("memory_enabled", enabled)

    def remember(self, user_id: int, category: str, key: str, value: str) -> Memory | None:
        if not self.is_enabled(user_id):
            return None
        if category not in CATEGORIES:
            raise ValueError(f"Неизвестная категория памяти: {category}")
        existing = self.session.scalars(
            select(Memory).where(Memory.user_id == user_id, Memory.key == key, Memory.category == category)
        ).first()
        if existing:
            existing.value = value
            self.session.commit()
            return existing
        mem = Memory(user_id=user_id, category=category, key=key, value=value)
        self.session.add(mem)
        self.session.commit()
        return mem

    def learn_from_text(self, user_id: int, text: str) -> list[Memory]:
        saved = []
        for category, key, value in extract_facts(text):
            mem = self.remember(user_id, category, key, value)
            if mem:
                saved.append(mem)
        return saved

    def list(self, user_id: int) -> list[Memory]:
        return list(self.session.scalars(select(Memory).where(Memory.user_id == user_id).order_by(Memory.id.desc())))

    def context_lines(self, user_id: int, limit: int = 15) -> list[str]:
        if not self.is_enabled(user_id):
            return []
        return [f"{m.key} = {m.value} ({m.category})" for m in self.list(user_id)[:limit]]

    def delete(self, user_id: int, memory_id: int) -> bool:
        res = self.session.execute(delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
        self.session.commit()
        return res.rowcount > 0

    def clear(self, user_id: int, include_conversations: bool = True) -> None:
        self.session.execute(delete(Memory).where(Memory.user_id == user_id))
        if include_conversations:
            self.session.execute(delete(Conversation).where(Conversation.user_id == user_id))
        self.session.commit()

    def add_message(self, user_id: int, role: str, text: str, source: str = "text") -> None:
        if self.is_enabled(user_id):
            row = Conversation(user_id=user_id, role=role, text=text, source=source, chat_id=self.chat_id)
            self.session.add(row)
            self.session.commit()
            self.last_ids[role] = row.id

    def recent_messages(self, user_id: int, limit: int = 10, include_voice: bool = True) -> list[Conversation]:
        query = select(Conversation).where(Conversation.user_id == user_id)
        if self.chat_id is not None:
            query = query.where(Conversation.chat_id == self.chat_id)
        if not include_voice:  # голосовые разговоры Woody «держит в себе» — в чате их нет
            query = query.where((Conversation.source.is_(None)) | (Conversation.source != "voice"))
        rows = self.session.scalars(query.order_by(Conversation.id.desc()).limit(limit))
        return list(reversed(list(rows)))
