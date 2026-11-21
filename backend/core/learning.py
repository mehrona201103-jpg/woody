from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.database.models import Experience, Knowledge

SOURCES = ("USER", "AI", "SYSTEM", "WEB", "DOCUMENT")

BASE_KNOWLEDGE = [
    "Меня зовут Woody, я цифровой компаньон.",
    "Я могу разговаривать голосом и текстом.",
    "Я не выполняю действия на устройстве без разрешения пользователя.",
    "Я многого ещё не знаю и учусь из разговоров.",
]


class LearningService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def seed(self) -> None:
        """Базовые знания Woody (user_id = NULL) — часть его идентичности, передаются новым людям."""
        if self.session.scalars(select(Knowledge).where(Knowledge.user_id.is_(None)).limit(1)).first():
            return
        for text in BASE_KNOWLEDGE:
            self.session.add(Knowledge(content=text, source="SYSTEM", confidence=1.0, verified=True))
        self.session.commit()

    def add(self, content: str, source: str, confidence: float, verified: bool = False, user_id: int | None = None) -> Knowledge:
        if source not in SOURCES:
            raise ValueError(f"Неизвестный источник знания: {source}")
        item = Knowledge(
            user_id=user_id, content=content, source=source,
            confidence=max(0.0, min(1.0, confidence)), verified=verified,
        )
        self.session.add(item)
        self.session.commit()
        return item

    def context_lines(self, user_id: int | None = None, limit: int = 8) -> list[str]:
        """Базовые знания + знания именно этого пользователя. Чужие знания не попадают в контекст."""
        cond = Knowledge.user_id.is_(None) if user_id is None else or_(Knowledge.user_id.is_(None), Knowledge.user_id == user_id)
        rows = self.session.scalars(
            select(Knowledge).where(cond).order_by(Knowledge.verified.desc(), Knowledge.id.desc()).limit(limit)
        )
        return [self.label(k) for k in rows]

    @staticmethod
    def label(k: Knowledge) -> str:
        if k.verified:
            return k.content
        return f"предположение ({k.source}, уверенность {k.confidence:.0%}): {k.content}"

    def log_experience(self, description: str, outcome: str) -> None:
        self.session.add(Experience(description=description, outcome=outcome))
        self.session.commit()
