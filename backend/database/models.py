from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- Woody identity
class WoodyIdentity(Base):
    """Личность самого Woody: общая для всех пользователей, не содержит личных данных."""

    __tablename__ = "woody_identity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), default="Woody")
    personality: Mapped[str] = mapped_column(Text)
    core_config: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Skill(Base):
    __tablename__ = "skills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------- user profile
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # sha256 от секрета сессии; сам секрет хранится только в браузере пользователя
    session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(20))  # SHORT_TERM, LONG_TERM, FACT, PREFERENCE, EVENT, EXPERIENCE, SKILL
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Knowledge(Base):
    """user_id = NULL — базовое знание Woody; иначе знание принадлежит конкретному пользователю."""

    __tablename__ = "knowledge"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))  # USER, AI, SYSTEM, WEB, DOCUMENT
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Experience(Base):
    __tablename__ = "experiences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Emotion(Base):
    """Снимок настроения Woody в общении с конкретным пользователем."""

    __tablename__ = "emotions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    happiness: Mapped[float] = mapped_column(Float, default=50)
    sadness: Mapped[float] = mapped_column(Float, default=10)
    anger: Mapped[float] = mapped_column(Float, default=5)
    curiosity: Mapped[float] = mapped_column(Float, default=60)
    energy: Mapped[float] = mapped_column(Float, default=60)
    calmness: Mapped[float] = mapped_column(Float, default=60)
    confidence: Mapped[float] = mapped_column(Float, default=50)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("user_id", "action", name="uq_permission_user_action"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(50))
    decision: Mapped[str] = mapped_column(String(20))  # ALLOW_ONCE, ALLOW_ALWAYS, DENY
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # USER / WOODY
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Setting(Base):
    """Ключи пользовательских настроек имеют вид 'u<id>:<name>'."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- transfer
class TransferToken(Base):
    """Одноразовый короткоживущий токен передачи Woody. Хранится только хэш; личных данных нет."""

    __tablename__ = "transfer_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)  # naive UTC
    expires_at: Mapped[datetime] = mapped_column(DateTime)  # naive UTC
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    redeemed_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
