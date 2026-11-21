"""Permission Manager — единственная точка, через которую проходят действия на устройстве.

Разрешения персональные: у каждого пользователя свои, при передаче Woody они не переносятся.
"""
from __future__ import annotations

import logging
from enum import Enum

from sqlalchemy.orm import Session

from backend.database.models import Permission

logger = logging.getLogger("woody.permissions")


class Decision(str, Enum):
    ALLOW_ONCE = "ALLOW_ONCE"
    ALLOW_ALWAYS = "ALLOW_ALWAYS"
    DENY = "DENY"


ACTIONS: dict[str, str] = {
    "camera": "Использовать камеру",
    "microphone": "Использовать микрофон",
    "messages.send": "Отправлять сообщения",
    "files.send": "Отправлять файлы",
    "files.delete": "Удалять файлы",
    "background": "Работать в фоне",
    "notifications": "Показывать уведомления",
}
DANGEROUS_ACTIONS = {"files.delete"}


class PermissionManager:
    def __init__(self, session: Session, user_id: int) -> None:
        self.session = session
        self.user_id = user_id

    @staticmethod
    def _validate(action: str) -> None:
        if action not in ACTIONS:
            raise ValueError(f"Неизвестное действие: {action}")

    def _row(self, action: str) -> Permission | None:
        return self.session.query(Permission).filter_by(user_id=self.user_id, action=action).first()

    def status(self, action: str) -> str:
        """'allowed' | 'ask' | 'denied' — без побочных эффектов."""
        self._validate(action)
        row = self._row(action)
        if row is None:
            return "ask"
        if row.decision == Decision.DENY:
            return "denied"
        return "allowed"

    def check(self, action: str) -> str:
        """Как status(), но ALLOW_ONCE расходуется при использовании."""
        result = self.status(action)
        row = self._row(action)
        if result == "allowed" and row is not None and row.decision == Decision.ALLOW_ONCE:
            self.session.delete(row)
            self.session.commit()
            logger.info("Разрешение ALLOW_ONCE использовано: %s (user %s)", action, self.user_id)
        return result

    def set(self, action: str, decision: Decision) -> None:
        self._validate(action)
        row = self._row(action)
        if row is None:
            self.session.add(Permission(user_id=self.user_id, action=action, decision=decision.value))
        else:
            row.decision = decision.value
        self.session.commit()
        logger.info("Разрешение %s → %s (user %s)", action, decision.value, self.user_id)

    def revoke(self, action: str) -> None:
        self._validate(action)
        row = self._row(action)
        if row is not None:
            self.session.delete(row)
            self.session.commit()

    def list(self) -> list[dict[str, object]]:
        out = []
        for action, label in ACTIONS.items():
            row = self._row(action)
            out.append(
                {
                    "action": action,
                    "label": label,
                    "status": self.status(action),
                    "decision": row.decision if row else "",
                    "dangerous": action in DANGEROUS_ACTIONS,
                }
            )
        return out
