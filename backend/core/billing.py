"""Тарифы, лимиты запросов и заявки на оплату."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.database.models import PaymentRequest, User

logger = logging.getLogger("woody.billing")

FREE_LIMIT = 200_000

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
        "price_month": "9.99",
        "price_year": None,
        "currency": "сомони",
        "limit": None,  # без лимита
        "features": [
            "Безлимит запросов",
            "Режимы: Учёба, Код, Агент, Инкогнито",
            "Свой характер Woody",
            "Приоритет ответа",
            "Глубокий разбор фото",
        ],
    },
    "ultra": {
        "title": "Ultra",
        "price_month": None,
        "price_year": "19.99",
        "currency": "сомони",
        "limit": None,
        "features": [
            "Всё из Pro",
            "Woody Code — красный режим кодинга",
            "Python, JS, Java, C++, Linux/Kali",
            "Сборка проекта в ZIP + инструкция",
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


def remaining_requests(user: User) -> int | None:
    plan = effective_plan(user)
    limit = PLANS[plan]["limit"]
    if limit is None:
        return None
    used = int(user.request_count or 0)
    return max(0, limit - used)


def can_request(user: User) -> tuple[bool, dict]:
    """True если можно сделать запрос. Иначе info для UI."""
    plan = effective_plan(user)
    left = remaining_requests(user)
    info = {
        "plan": plan,
        "used": int(user.request_count or 0),
        "limit": PLANS[plan]["limit"],
        "remaining": left,
    }
    if left is None:
        return True, info
    if left <= 0:
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
    days = 365 if period == "year" or plan == "ultra" else 30
    user.plan = plan
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
    if plan == "pro":
        amount = PLANS["pro"]["price_month"] or "9.99"
        period = "month"
    elif plan == "ultra":
        amount = PLANS["ultra"]["price_year"] or "19.99"
        period = "year"
    else:
        raise ValueError("plan")
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
