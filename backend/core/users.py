"""Профили и вход. Пароли — scrypt (stdlib). Секрет сессии выдаётся один раз; в БД лежит только хэш."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.models import User, UserSession

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "s1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str | None) -> bool:
    try:
        _, salt_b, dk_b = (stored or "").split("$")
        salt, dk = base64.b64decode(salt_b), base64.b64decode(dk_b)
        test = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(test, dk)
    except Exception:  # noqa: BLE001
        return False


def new_session(session: Session, user: User) -> str:
    secret = secrets.token_urlsafe(32)
    session.add(UserSession(user_id=user.id, token_hash=hash_secret(secret)))
    session.commit()
    return secret


def create_user(session: Session, name: str, **extra) -> tuple[User, str]:
    """Создаёт пользователя и первую сессию. Возвращает (user, secret)."""
    user = User(name=name.strip(), **extra)
    session.add(user)
    session.commit()
    return user, new_session(session, user)


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("bad_email", "Проверь адрес почты: он выглядит неправильно.")
    return email


def register_email(session: Session, email: str, password: str, name: str) -> tuple[User, str]:
    email = normalize_email(email)
    name = (name or "").strip()
    if not name:
        raise AuthError("bad_name", "Напиши, как мне тебя называть.")
    if len(password or "") < 6:
        raise AuthError("weak_password", "Пароль слишком короткий: нужно минимум 6 символов.")
    if session.scalars(select(User).where(User.email == email)).first():
        raise AuthError("email_taken", "Эта почта уже зарегистрирована. Попробуй войти.", 409)
    return create_user(session, name[:50], email=email, password_hash=hash_password(password))


_ATTEMPTS: dict[str, list[float]] = {}


def _throttle(key: str, limit: int = 8, window: int = 300) -> None:
    now = time.time()
    hits = [t for t in _ATTEMPTS.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise AuthError("too_many", "Слишком много попыток. Подожди пару минут.", 429)
    hits.append(now)
    _ATTEMPTS[key] = hits


def login_email(session: Session, email: str, password: str, ip: str = "") -> tuple[User, str]:
    email = normalize_email(email)
    _throttle(f"{ip}|{email}")
    user = session.scalars(select(User).where(User.email == email)).first()
    if user is None or not verify_password(password or "", user.password_hash):
        raise AuthError("bad_credentials", "Неверная почта или пароль.", 401)
    return user, new_session(session, user)


def login_google(session: Session, sub: str, email: str, name: str, picture: str | None = None) -> tuple[User, str, bool]:
    email = (email or "").strip().lower()
    user = session.scalars(select(User).where(User.google_sub == sub)).first()
    if user is None and email:
        user = session.scalars(select(User).where(User.email == email)).first()
        if user is not None:
            user.google_sub = sub
            session.commit()
    created = user is None
    if user is None:
        user, secret = create_user(session, (name or email.split("@")[0] or "Друг")[:50], email=email or None, google_sub=sub, picture=(picture or None))
        return user, secret, True
    if picture and user.picture != picture:
        user.picture = picture[:500]
        session.commit()
    return user, new_session(session, user), created


def find_user_by_secret(session: Session, secret: str | None) -> User | None:
    if not secret:
        return None
    h = hash_secret(secret)
    row = session.scalars(select(UserSession).where(UserSession.token_hash == h)).first()
    if row is not None:
        return session.get(User, row.user_id)
    return session.scalars(select(User).where(User.session_hash == h)).first()  # сессии v0.1–v0.3


def logout(session: Session, secret: str | None) -> None:
    if not secret:
        return
    row = session.scalars(select(UserSession).where(UserSession.token_hash == hash_secret(secret))).first()
    if row is not None:
        session.delete(row)
        session.commit()
