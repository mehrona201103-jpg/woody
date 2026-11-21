"""Профили пользователей. Секрет сессии выдаётся один раз и хранится в браузере; в БД лежит только хэш."""
from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import User


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_user(session: Session, name: str) -> tuple[User, str]:
    secret = secrets.token_urlsafe(32)
    user = User(name=name.strip(), session_hash=hash_secret(secret))
    session.add(user)
    session.commit()
    return user, secret


def find_user_by_secret(session: Session, secret: str | None) -> User | None:
    if not secret:
        return None
    return session.scalars(select(User).where(User.session_hash == hash_secret(secret))).first()
