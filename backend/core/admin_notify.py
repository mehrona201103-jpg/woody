"""Админ-бот Telegram: заявки на оплату, /start, approve/reject, статистика."""
from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.billing import activate_plan
from backend.core.admin_panel import is_moderator
from backend.database.models import Feedback, PaymentRequest, User

logger = logging.getLogger("woody.admin")


def bot_token() -> str:
    return (os.getenv("TELEGRAM_ADMIN_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def admin_chat_id() -> str:
    return (os.getenv("TELEGRAM_ADMIN_CHAT_ID") or "").strip()


def _api() -> str:
    return f"https://api.telegram.org/bot{bot_token()}"


async def tg_send(method: str, **kwargs) -> dict:
    token = bot_token()
    if not token:
        logger.warning("TELEGRAM_ADMIN_BOT_TOKEN не задан")
        return {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{_api()}/{method}", json=kwargs)
            data = r.json()
            if not data.get("ok"):
                logger.error("Telegram %s: %s", method, data)
            return data
    except Exception as exc:
        logger.error("Telegram %s failed: %s", method, exc)
        return {}


async def tg_send_photo_bytes(chat_id: str, photo_bytes: bytes, caption: str = "") -> dict:
    token = bot_token()
    if not token:
        return {}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            files = {"photo": ("receipt.jpg", photo_bytes, "image/jpeg")}
            data = {"chat_id": chat_id, "caption": caption[:1000]}
            r = await client.post(f"{_api()}/sendPhoto", data=data, files=files)
            return r.json()
    except Exception as exc:
        logger.error("sendPhoto failed: %s", exc)
        return {}


def _decode_data_url(data_url: str) -> bytes | None:
    if not data_url or not data_url.startswith("data:"):
        return None
    try:
        header, b64 = data_url.split(",", 1)
        return base64.b64decode(b64)
    except Exception:
        return None


async def notify_payment(session: Session, req: PaymentRequest, user: User) -> bool:
    chat = admin_chat_id()
    if not bot_token():
        logger.warning("Бот: нет TELEGRAM_ADMIN_BOT_TOKEN")
        return False
    if not chat:
        logger.warning("Бот: нет TELEGRAM_ADMIN_CHAT_ID — укажи id чата админа")
        return False

    admin_web = (os.getenv("ADMIN_WEBAPP_URL") or os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    web_hint = f"\n🌐 WEB-админка: {admin_web}/admin" if admin_web else "\n🌐 Одобряй в WEB-админке (/admin)"
    if req.plan == "donate":
        text = (
            f"🎁 Пожертвование #{req.id}\n"
            f"👤 {user.name} (id={user.id})\n"
            f"📧 {user.email or '—'}\n"
            f"💰 {req.amount} сомони\n"
            f"💬 {(req.admin_note or '').strip() or '—'}\n"
            f"{web_hint}\n"
            f"Резерв: /approve_{req.id} · /reject_{req.id}"
        )
        photo = _decode_data_url(req.receipt_data or "")
        if photo:
            res = await tg_send_photo_bytes(chat, photo, caption=text)
            if res.get("ok"):
                return True
        return bool((await tg_send("sendMessage", chat_id=chat, text=text)).get("ok"))

    text = (
        f"💳 Заявка #{req.id}\n"
        f"👤 {user.name} (id={user.id})\n"
        f"📧 {user.email or '—'}\n"
        f"📦 Тариф: {req.plan.upper()} / {req.period}\n"
        f"💰 {req.amount} сомони\n"
        f"⏳ status: {req.status}\n"
        f"{web_hint}\n"
        f"Резерв: /approve_{req.id} · /reject_{req.id}"
    )
    photo = _decode_data_url(req.receipt_data or "")
    if photo:
        res = await tg_send_photo_bytes(chat, photo, caption=text)
        if res.get("ok"):
            return True
        # fallback text
    res = await tg_send("sendMessage", chat_id=chat, text=text)
    return bool(res.get("ok"))


def decide_payment(session: Session, req_id: int, approve: bool, note: str = "") -> str:
    row = session.get(PaymentRequest, req_id)
    if not row:
        return f"❌ Заявка #{req_id} не найдена"
    if row.status != "pending":
        return f"⚠️ Заявка #{req_id} уже: {row.status}"
    user = session.get(User, row.user_id)
    if not user:
        return "❌ Пользователь не найден"
    row.status = "approved" if approve else "rejected"
    row.admin_note = note or ""
    row.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(row)
    try:
        if approve and row.plan == "donate":
            session.commit()
            return f"💚 Пожертвование #{req_id} получено ({row.amount} сомони от {user.name}). Спасибо ему!"
        if approve:
            activate_plan(session, user, row.plan, row.period)  # commits inside
            return f"✅ Заявка #{req_id} одобрена → {user.name} получил {row.plan.upper()}"
        session.commit()
        return f"❌ Заявка #{req_id} отклонена ({user.name})"
    except Exception as exc:
        session.rollback()
        logger.exception("decide_payment failed: %s", exc)
        return f"❌ Ошибка при обработке заявки #{req_id}: {exc}"


HELP = (
    "🤖 Woody Admin Bot\n\n"
    "/start — это меню\n"
    "/stats — статистика приложения\n"
    "/users — последние пользователи\n"
    "/pending — заявки на оплату и пожертвования\n"
    "/wishes — последние пожелания пользователей\n"
    "/approve_ID — одобрить заявку (резерв)\n"
    "/reject_ID — отклонить (резерв)\n"
    "/me — узнать свой chat id\n\n"
    "Основное: уведомления о заявках приходят сюда.\n"
    "Одобрять и отклонять — в WEB-админке (второй домен /admin).\n"
    "Команды /approve и /reject оставлены как резерв."
)


async def handle_update(db: Session, data: dict, providers_count: int = 0) -> None:
    """Обработка любого update от Telegram (webhook или polling)."""
    msg = data.get("message") or data.get("edited_message") or {}
    if not msg:
        return
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return

    # /me и /start доступны всем (чтобы админ узнал свой id)
    if text in ("/start", "/help", "start"):
        await tg_send("sendMessage", chat_id=chat_id, text=HELP)
        # если TELEGRAM_ADMIN_CHAT_ID пуст — подскажем
        if not admin_chat_id():
            await tg_send(
                "sendMessage",
                chat_id=chat_id,
                text=f"⚠️ TELEGRAM_ADMIN_CHAT_ID не задан.\nТвой chat id: `{chat_id}`\nДобавь его в Variables Railway.",
                parse_mode="Markdown",
            )
        elif chat_id != admin_chat_id():
            await tg_send(
                "sendMessage",
                chat_id=chat_id,
                text=f"Твой id: {chat_id}\nАдмин-чат в настройках: {admin_chat_id()}\nКоманды статистики только из админ-чата.",
            )
        return

    if text.startswith("/me"):
        await tg_send("sendMessage", chat_id=chat_id, text=f"Твой chat id: `{chat_id}`", parse_mode="Markdown")
        return

    # дальше только админ или модератор
    if not is_moderator(chat_id):
        await tg_send("sendMessage", chat_id=chat_id, text="⛔ Только для админа / модератора Woody.")
        return

    if text.startswith("/approve_"):
        m = re.search(r"/approve_(\d+)", text)
        if not m:
            await tg_send("sendMessage", chat_id=chat_id, text="Формат: /approve_12")
            return
        result = decide_payment(db, int(m.group(1)), True)
        await tg_send("sendMessage", chat_id=chat_id, text=result)
        return

    if text.startswith("/reject_"):
        m = re.search(r"/reject_(\d+)", text)
        if not m:
            await tg_send("sendMessage", chat_id=chat_id, text="Формат: /reject_12")
            return
        result = decide_payment(db, int(m.group(1)), False)
        await tg_send("sendMessage", chat_id=chat_id, text=result)
        return

    if text.startswith("/wishes"):
        rows = db.scalars(select(Feedback).order_by(Feedback.id.desc()).limit(10)).all()
        if not rows:
            await tg_send("sendMessage", chat_id=chat_id, text="Пока нет пожеланий.")
        else:
            await tg_send("sendMessage", chat_id=chat_id, text="💡 Последние пожелания:\n\n" + "\n\n".join(f"#{r.id}: {r.text[:300]}" for r in rows))
        return

    if text.startswith("/stats"):
        users_n = db.scalar(select(func.count()).select_from(User)) or 0
        pending = db.scalar(select(func.count()).select_from(PaymentRequest).where(PaymentRequest.status == "pending")) or 0
        pro_n = db.scalar(select(func.count()).select_from(User).where(User.plan.in_(["pro", "ultra"]))) or 0
        txt = (
            f"📊 Woody\n"
            f"Users: {users_n}\n"
            f"Pro/Ultra: {pro_n}\n"
            f"Pending payments: {pending}\n"
            f"AI providers: {providers_count}"
        )
        await tg_send("sendMessage", chat_id=chat_id, text=txt)
        return

    if text.startswith("/users"):
        rows = db.scalars(select(User).order_by(User.id.desc()).limit(40)).all()
        lines = [f"{u.id}. {u.name} | {u.plan or 'free'} | req={u.request_count or 0}" for u in rows] or ["пусто"]
        await tg_send("sendMessage", chat_id=chat_id, text="Users:\n" + "\n".join(lines))
        return

    if text.startswith("/pending"):
        rows = db.scalars(
            select(PaymentRequest).where(PaymentRequest.status == "pending").order_by(PaymentRequest.id.desc()).limit(20)
        ).all()
        if not rows:
            await tg_send("sendMessage", chat_id=chat_id, text="Нет заявок в ожидании.")
            return
        lines = []
        for r in rows:
            u = db.get(User, r.user_id)
            lines.append(f"#{r.id} {r.plan} {r.amount} — {u.name if u else r.user_id} → /approve_{r.id} /reject_{r.id}")
        await tg_send("sendMessage", chat_id=chat_id, text="Pending:\n" + "\n".join(lines))
        return

    # любое другое сообщение админу
    adm = admin_chat_id()
    if adm and chat_id == adm:
        await tg_send("sendMessage", chat_id=chat_id, text="Не понял. /help")


async def setup_webhook(public_base: str) -> None:
    """Ставит webhook на /api/admin/telegram при старте сервера."""
    token = bot_token()
    if not token or not public_base:
        return
    url = public_base.rstrip("/") + "/api/admin/telegram"
    secret = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    payload = {"url": url, "allowed_updates": ["message"]}
    if secret:
        payload["secret_token"] = secret
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{_api()}/setWebhook", json=payload)
            data = r.json()
            logger.info("setWebhook %s → %s", url, data)
            # проверка
            info = await client.get(f"{_api()}/getWebhookInfo")
            logger.info("webhook info: %s", info.json())
    except Exception as exc:
        logger.error("setWebhook failed: %s", exc)


async def notify_feedback(fb: Feedback, user: User) -> bool:
    chat = admin_chat_id()
    if not bot_token() or not chat:
        return False
    text = (
        f"💡 Пожелание #{fb.id}\n👤 {user.name} (id={user.id}) · {user.plan or 'free'}\n📧 {user.email or '—'}\n\n{fb.text[:3500]}"
    )
    return bool((await tg_send("sendMessage", chat_id=chat, text=text)).get("ok"))
