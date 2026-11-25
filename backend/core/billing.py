"""Тарифы, лимиты запросов и заявки на оплату."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.database.models import PaymentRequest, User

logger = logging.getLogger("woody.billing")

FREE_LIMIT = 200_000
PRO_MONTH_LIMIT = FREE_LIMIT * 3      # Pro на месяц: в 3 раза больше Free
ULTRA_MONTH_LIMIT = 1_000_000         # Ultra на месяц
RESET_HOURS = 2                       # после исчерпания лимита ждём 2 часа и счётчик обнуляется

PRICES = {
    "pro": {"month": "9.99", "year": "29.99"},
    "ultra": {"month": "19.99", "year": "39.99"},
}
CURRENCY = "сомони"

PLANS = {
    "free": {
        "title": "Free",
        "price_month": None,
        "price_year": None,
        "limit": FREE_LIMIT,
        "features": [
            "Чат с Woody",
            "Голос и группы",
            f"{FREE_LIMIT:,} запросов всего".replace(",", " "),
            "Базовая память",
            "Самообучение Woody",
        ],
    },
    "pro": {
        "title": "Pro",
        "price_month": PRICES["pro"]["month"],
        "price_year": PRICES["pro"]["year"],
        "currency": CURRENCY,
        "limit": PRO_MONTH_LIMIT,   # на месяц; на год — без лимита
        "limit_year": None,
        "features": [
            f"{PRO_MONTH_LIMIT:,} запросов в месяц (в 3 раза больше Free)".replace(",", " "),
            "На год — безлимит",
            "Режимы рядом с кнопкой отправки",
            "Оформленные ответы по теме запроса",
            "Свой характер Woody и глубокий разбор фото",
        ],
    },
    "ultra": {
        "title": "Ultra",
        "price_month": PRICES["ultra"]["month"],
        "price_year": PRICES["ultra"]["year"],
        "currency": CURRENCY,
        "limit": ULTRA_MONTH_LIMIT,
        "limit_year": None,
        "features": [
            f"{ULTRA_MONTH_LIMIT:,} запросов в месяц".replace(",", " "),
            "На год — безлимит",
            "Всё из Pro",
            "Woody Code: терминал, файлы и ZIP-проекты",
            "Максимум памяти и сильные модели",
        ],
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def effective_plan(user: User) -> str:
    plan = (user.plan or "free").lower()
    if plan in ("pro", "ultra") and user.plan_until and user.plan_until < _now():
        return "free"
    if plan not in PLANS:
        return "free"
    return plan


def is_yearly(user: User) -> bool:
    return (getattr(user, "plan_period", None) or "month") == "year"


def current_limit(user: User) -> int | None:
    """Лимит запросов сейчас: None = безлимит (платный годовой тариф)."""
    plan = effective_plan(user)
    if plan in ("pro", "ultra") and is_yearly(user):
        return None
    return PLANS[plan]["limit"]


def _maybe_reset(session: Session | None, user: User) -> None:
    """Платный месячный тариф: через RESET_HOURS после исчерпания лимита счётчик обнуляется."""
    hit = getattr(user, "limit_hit_at", None)
    if hit is None or effective_plan(user) not in ("pro", "ultra"):
        return
    if _now() - hit >= timedelta(hours=RESET_HOURS):
        user.request_count = 0
        user.limit_hit_at = None
        if session is not None:
            session.add(user)
            session.commit()


def reset_at(user: User) -> datetime | None:
    hit = getattr(user, "limit_hit_at", None)
    return hit + timedelta(hours=RESET_HOURS) if hit else None


def remaining_requests(user: User) -> int | None:
    limit = current_limit(user)
    if limit is None:
        return None
    return max(0, limit - int(user.request_count or 0))


def can_request(user: User, session: Session | None = None) -> tuple[bool, dict]:
    """True если можно сделать запрос. Иначе info для UI."""
    _maybe_reset(session, user)
    plan = effective_plan(user)
    left = remaining_requests(user)
    info = {
        "plan": plan,
        "period": (getattr(user, "plan_period", None) or "month") if plan != "free" else None,
        "used": int(user.request_count or 0),
        "limit": current_limit(user),
        "remaining": left,
    }
    if left is None:
        return True, info
    if left <= 0:
        if plan in ("pro", "ultra"):
            if getattr(user, "limit_hit_at", None) is None:
                user.limit_hit_at = _now()
                if session is not None:
                    session.add(user)
                    session.commit()
            r = reset_at(user)
            info["reset_at"] = r.isoformat() if r else None
            info["reset_in_minutes"] = max(0, int((r - _now()).total_seconds() // 60)) if r else RESET_HOURS * 60
        return False, info
    return True, info


def bump_usage(session: Session, user: User) -> None:
    user.request_count = int(user.request_count or 0) + 1
    session.add(user)
    session.commit()


def activate_plan(session: Session, user: User, plan: str, period: str = "month") -> None:
    plan = plan.lower()
    if plan not in ("pro", "ultra"):
        raise ValueError("bad plan")
    period = "year" if period == "year" else "month"
    days = 365 if period == "year" else 30
    user.plan = plan
    user.plan_period = period
    user.limit_hit_at = None
    user.request_count = 0
    user.plan_until = _now() + timedelta(days=days)
    session.add(user)
    session.commit()


def create_payment(
    session: Session,
    user: User,
    plan: str,
    period: str,
    receipt_data: str = "",
) -> PaymentRequest:
    plan = plan.lower()
    if plan not in PRICES:
        raise ValueError("plan")
    period = "year" if period == "year" else "month"
    amount = PRICES[plan][period]
    row = PaymentRequest(
        user_id=user.id,
        plan=plan,
        period=period,
        amount=str(amount),
        receipt_data=(receipt_data or "")[:1_500_000],
        status="pending",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
