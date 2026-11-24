"""Уведомления админу в Telegram + простые команды approve/reject."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from backend.core.billing import activate_plan
from backend.database.models import PaymentRequest, User

logger = logging.getLogger("woody.admin")


def _token() -> str:
    return (os.getenv("TELEGRAM_ADMIN_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def _chat() -> str:
    return (os.getenv("TELEGRAM_ADMIN_CHAT_ID") or "").strip()


async def notify_payment(session: Session, req: PaymentRequest, user: User) -> bool:
    token, chat = _token(), _chat()
    if not token or not chat:
        logger.warning("Telegram admin bot не настроен (TELEGRAM_ADMIN_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID)")
        return False
    text = (
        f"💳 Заявка #{req.id}\n"
        f"Пользователь: {user.name} (id={user.id})\n"
        f"Email: {user.email or '—'}\n"
        f"Тариф: {req.plan.upper()} / {req.period}\n"
        f"Сумма: {req.amount} сомони\n"
        f"Статус: {req.status}\n\n"
        f"Одобрить: /approve_{req.id}\n"
        f"Отклонить: /reject_{req.id}"
    )
    api = f"https://api.telegram.org/bot{token}"
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            if req.receipt_data and req.receipt_data.startswith("data:image"):
                # отправляем как photo через sendPhoto нельзя напрямую data URL —
                # шлём текст + просим смотреть в админке; либо sendDocument base64 не поддерживается.
                # Отправим caption + отдельное сообщение.
                await client.post(f"{api}/sendMessage", json={"chat_id": chat, "text": text})
                # Попробуем sendPhoto с URL если это http; иначе только текст.
                await client.post(
                    f"{api}/sendMessage",
                    json={"chat_id": chat, "text": f"Чек приложен к заявке #{req.id} (в базе, data URL). Открой админ-API или одобри по команде."},
                )
            else:
                await client.post(f"{api}/sendMessage", json={"chat_id": chat, "text": text})
        return True
    except Exception as exc:
        logger.error("Telegram notify failed: %s", exc)
        return False


def decide_payment(session: Session, req_id: int, approve: bool, note: str = "") -> str:
    row = session.get(PaymentRequest, req_id)
    if not row:
        return "Заявка не найдена"
    if row.status != "pending":
        return f"Уже обработана: {row.status}"
    user = session.get(User, row.user_id)
    if not user:
        return "Пользователь не найден"
    row.status = "approved" if approve else "rejected"
    row.admin_note = note or ""
    row.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(row)
    if approve:
        activate_plan(session, user, row.plan, row.period)
    else:
        session.commit()
    return f"Заявка #{req_id} → {row.status}" + (f" ({user.name})" if user else "")
