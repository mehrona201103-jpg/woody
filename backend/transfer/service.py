"""Одноразовые токены передачи. В БД хранится только sha256 токена, личных данных в токене нет."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.core.users import create_user
from backend.database.models import TransferToken, User

TOKEN_TTL_SECONDS = 300

MESSAGES = {
    "invalid": "Ссылка Woody недействительна.",
    "used": "Эта ссылка Woody уже использована.",
    "expired": "Ссылка Woody истекла.",
}


class TransferError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = MESSAGES[code]


def utcnow() -> datetime:
    """Naive UTC: SQLite не хранит часовой пояс, поэтому сравниваем одинаково везде."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_token() -> str:
    return secrets.token_urlsafe(24)  # 192 бита из CSPRNG


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def classify(row: TransferToken | None, now: datetime) -> str | None:
    """None — токен годен; иначе 'invalid' | 'used' | 'expired'."""
    if row is None:
        return "invalid"
    if row.used_at is not None:
        return "used"
    if row.expires_at <= now:
        return "expired"
    return None


class TransferService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: int, ttl_seconds: int = TOKEN_TTL_SECONDS, now: datetime | None = None) -> tuple[str, datetime]:
        now = now or utcnow()
        token = new_token()
        expires = now + timedelta(seconds=ttl_seconds)
        self.session.add(
            TransferToken(token_hash=hash_token(token), created_by=user_id, created_at=now, expires_at=expires)
        )
        self.session.commit()
        return token, expires

    def _row(self, token: str) -> TransferToken | None:
        return self.session.scalars(select(TransferToken).where(TransferToken.token_hash == hash_token(token))).first()

    def check(self, token: str, now: datetime | None = None) -> None:
        """Проверка без расходования токена. Бросает TransferError."""
        problem = classify(self._row(token), now or utcnow())
        if problem:
            raise TransferError(problem)

    def redeem(self, token: str, name: str, now: datetime | None = None) -> tuple[User, str]:
        """Атомарно гасит токен и создаёт НОВОГО пользователя. Ничего не наследуется от создателя токена."""
        now = now or utcnow()
        h = hash_token(token)
        result = self.session.execute(
            update(TransferToken)
            .where(TransferToken.token_hash == h, TransferToken.used_at.is_(None), TransferToken.expires_at > now)
            .values(used_at=now)
        )
        self.session.commit()
        if result.rowcount != 1:
            self.session.expire_all()
            raise TransferError(classify(self._row(token), now) or "invalid")
        user, secret = create_user(self.session, name)
        row = self._row(token)
        if row is not None:
            row.redeemed_user_id = user.id
            self.session.commit()
        return user, secret
