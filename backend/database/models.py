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
    email: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    picture: Mapped[str | None] = mapped_column(String(500), nullable=True)  # фото из Google-аккаунта
    plan: Mapped[str] = mapped_column(String(20), default="free")  # free | pro | ultra
    plan_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    plan_period: Mapped[str | None] = mapped_column(String(10), nullable=True, default="month")  # month | year
    limit_hit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # когда кончился лимит (для сброса через 2 ч)
    referral_code: Mapped[str | None] = mapped_column(String(24), unique=True, nullable=True, index=True)
    referred_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    referral_terms_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UserSession(Base):
    """Сессия входа (по одной на устройство). В БД только хэш секрета."""

    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
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
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(10), nullable=True)  # text / voice (голосовые не показываются в чате)
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


# ---------------------------------------------------------------- мозг Woody
class LearnedAnswer(Base):
    """Собственный «мозг» Woody: вопрос → ответ, выученный у AI-инструмента. Общий, без личных данных."""

    __tablename__ = "learned_answers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    norm: Mapped[str] = mapped_column(Text, index=True)  # нормализованные токены вопроса
    answer: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="AI")  # AI | SELF (самообучение) | USER
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------- история чатов и группы
class ChatSession(Base):
    """Отдельный разговор пользователя с Woody (как «чаты» в ChatGPT)."""

    __tablename__ = "chat_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="Новый чат")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class GroupMessage(Base):
    """user_id = NULL — сообщение от Woody (он состоит в каждой группе автоматически)."""

    __tablename__ = "group_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PaymentRequest(Base):
    """Заявка на оплату тарифа: пользователь шлёт скрин чека, админ одобряет в Telegram."""

    __tablename__ = "payment_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(20))  # pro | ultra
    period: Mapped[str] = mapped_column(String(20), default="month")  # month | year
    amount: Mapped[str] = mapped_column(String(20), default="9.99")
    receipt_data: Mapped[str] = mapped_column(Text, default="")  # data URL или пусто
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="TJS")
    method: Mapped[str] = mapped_column(String(20), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)


class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    invitee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    reward: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GroupRead(Base):
    __tablename__ = "group_reads"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_read"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    last_read_id: Mapped[int] = mapped_column(Integer, default=0)


class Feedback(Base):
    """Пожелания пользователей по улучшению Woody (дублируются админу в Telegram)."""

    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
