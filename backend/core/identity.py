"""Woody Identity: личность, базовые навыки и конфигурация — одна на всех пользователей."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.personality import BASE_PERSONA
from backend.database.models import Skill, WoodyIdentity

BASE_SKILLS = {
    "conversation": "Диалог голосом и текстом",
    "emotion_simulation": "Симуляция настроения и выражений лица",
    "gesture_transfer": "Передача Woody другому человеку жестом и QR",
}


class IdentityService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure(self) -> WoodyIdentity:
        identity = self.session.scalars(select(WoodyIdentity).order_by(WoodyIdentity.id)).first()
        if identity is None:
            identity = WoodyIdentity(
                name="Woody", personality=BASE_PERSONA, core_config=json.dumps({"version": "0.2"})
            )
            self.session.add(identity)
        for name, description in BASE_SKILLS.items():
            if self.session.scalars(select(Skill).where(Skill.name == name)).first() is None:
                self.session.add(Skill(name=name, description=description))
        self.session.commit()
        return identity

    def personality(self) -> str:
        return self.ensure().personality

    def skills(self) -> list[str]:
        return [s.name for s in self.session.scalars(select(Skill).where(Skill.enabled.is_(True)))]
