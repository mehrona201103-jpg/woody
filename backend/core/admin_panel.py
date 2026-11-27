"""Веб-админка: статистика, пользователи, заявки, пожертвования, пожелания, реквизиты, модераторы."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database.models import Feedback, PaymentRequest, User

logger = logging.getLogger("woody.admin_panel")

SETTINGS_FILE = Path(os.getenv("WOODY_DATA_DIR") or "data") / "admin_settings.json"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_settings() -> dict:
    defaults = {
        "payment_requisites": os.getenv("PAYMENT_REQUISITES") or "",
        "payment_holder": os.getenv("PAYMENT_HOLDER") or "",
        "moderators": [x.strip() for x in (os.getenv("TELEGRAM_MODERATORS") or "").split(",") if x.strip()],
    }
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            defaults.update({k: v for k, v in data.items() if v is not None})
    except Exception as exc:
        logger.warning("admin settings read: %s", exp if (exp := None) else exc)
    return defaults


def save_settings(patch: dict) -> dict:
    cur = load_settings()
    for k in ("payment_requisites", "payment_holder", "moderators"):
        if k in patch:
            cur[k] = patch[k]
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur


def is_moderator(chat_id: str) -> bool:
    admin = (os.getenv("TELEGRAM_ADMIN_CHAT_ID") or "").strip()
    if chat_id and admin and str(chat_id) == admin:
        return True
    mods = load_settings().get("moderators") or []
    return str(chat_id) in {str(m) for m in mods}


def stats(db: Session, providers_n: int = 0) -> dict:
    users_n = db.scalar(select(func.count()).select_from(User)) or 0
    pro_n = db.scalar(select(func.count()).select_from(User).where(User.plan == "pro")) or 0
    ultra_n = db.scalar(select(func.count()).select_from(User).where(User.plan == "ultra")) or 0
    pending = db.scalar(select(func.count()).select_from(PaymentRequest).where(PaymentRequest.status == "pending", PaymentRequest.plan != "donate")) or 0
    donations = db.scalar(select(func.count()).select_from(PaymentRequest).where(PaymentRequest.plan == "donate")) or 0
    feedback_n = 0
    try:
        feedback_n = db.scalar(select(func.count()).select_from(Feedback)) or 0
    except Exception:
        pass
    req_sum = db.scalar(select(func.coalesce(func.sum(User.request_count), 0))) or 0
    return {
        "users": users_n,
        "pro": pro_n,
        "ultra": ultra_n,
        "free": max(0, users_n - pro_n - ultra_n),
        "pending_payments": pending,
        "donations": donations,
        "feedback": feedback_n,
        "total_requests": int(req_sum),
        "providers": providers_n,
    }


def list_users(db: Session, limit: int = 100) -> list[dict]:
    rows = db.scalars(select(User).order_by(User.id.desc()).limit(limit)).all()
    out = []
    for u in rows:
        out.append({
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "plan": u.plan or "free",
            "plan_until": u.plan_until.isoformat() if u.plan_until else None,
            "request_count": int(u.request_count or 0),
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })
    return out


def list_payments(db: Session, limit: int = 50, plan: str | None = None) -> list[dict]:
    q = select(PaymentRequest).order_by(PaymentRequest.id.desc()).limit(limit)
    if plan:
        q = select(PaymentRequest).where(PaymentRequest.plan == plan).order_by(PaymentRequest.id.desc()).limit(limit)
    rows = db.scalars(q).all()
    out = []
    for r in rows:
        u = db.get(User, r.user_id)
        out.append({
            "id": r.id,
            "user_id": r.user_id,
            "user_name": u.name if u else "?",
            "plan": r.plan,
            "period": r.period,
            "amount": r.amount,
            "currency": getattr(r, "currency", "TJS"),
            "method": getattr(r, "method", "manual"),
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "has_receipt": bool(r.receipt_data),
        })
    return out


def list_feedback(db: Session, limit: int = 50) -> list[dict]:
    try:
        rows = db.scalars(select(Feedback).order_by(Feedback.id.desc()).limit(limit)).all()
    except Exception:
        return []
    out = []
    for r in rows:
        u = db.get(User, r.user_id)
        out.append({
            "id": r.id,
            "user_id": r.user_id,
            "user_name": u.name if u else "?",
            "text": r.text,
            "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
        })
    return out
