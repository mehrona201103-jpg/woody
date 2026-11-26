from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
import asyncio
import httpx
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.actions.device import capabilities
from backend.config import BASE_DIR, get_settings, provider_config_problems, setup_logging
from backend.core.ai_router import AIRouter
from backend.core.brain import PENDING, Brain, load_emotion, split_tone
from backend.core.billing import PLANS, FREE_LIMIT, PRICES, CURRENCY, RESET_HOURS, can_request, bump_usage, create_payment, effective_plan, remaining_requests, current_limit
from backend.core import codegen, agent as code_agent
from backend.core.admin_notify import notify_feedback, notify_payment, decide_payment, handle_update, setup_webhook, bot_token
from backend.core import admin_panel
from backend.database.models import Feedback, PaymentRequest
from backend.core.identity import IdentityService
from backend.core.learning import LearningService
from backend.core.mind import Mind
from backend.core.selflearn import LEARNER
from backend.core.memory import MemoryService
from backend.core.user_settings import CHOICES as SETTING_CHOICES
from backend.core.user_settings import DEFAULTS as SETTING_DEFAULTS
from backend.core.user_settings import UserSettings
from backend.core.users import (AuthError, create_user, find_user_by_secret, login_email, login_google, logout as logout_session,
                                register_email)
from backend.database.database import SessionLocal, get_db, init_db
from backend.database.models import ChatSession, Conversation, Group, GroupMember, GroupMessage, User
from backend.permissions.manager import ACTIONS, Decision, PermissionManager
from backend.transfer.providers import PROVIDERS
from backend.transfer.service import TransferError, TransferService

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("woody.api")
router = AIRouter.from_config(settings.providers)
FRONTEND_DIR = Path(BASE_DIR) / "frontend"


def _adopt_legacy_conversations(session: Session) -> None:
    """Старые сообщения без чата складываем в один чат «Прошлые разговоры» на каждого пользователя."""
    uids = session.scalars(select(Conversation.user_id).where(Conversation.chat_id.is_(None)).distinct()).all()
    for uid in uids:
        chat = ChatSession(user_id=uid, title="Прошлые разговоры")
        session.add(chat)
        session.flush()
        session.execute(Conversation.__table__.update().where(Conversation.user_id == uid, Conversation.chat_id.is_(None)).values(chat_id=chat.id))
    if uids:
        session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as session:
        _adopt_legacy_conversations(session)
        LearningService(session).seed()
        IdentityService(session).ensure()
        seeded = Mind(session).seed_from_file(Path(BASE_DIR) / "data" / "brain_seed.json")
        if seeded:
            logger.info("Мозг Woody восстановлен из data/brain_seed.json: %s записей", seeded)
    if not settings.persistent:
        logger.warning(
            "База НЕ постоянная: при каждом деплое она сотрётся. Подключите Volume (/data) или задайте DATABASE_URL (Postgres) — см. README."
        )
    for problem in provider_config_problems():
        logger.warning(problem)
    if not settings.providers:
        logger.warning("AI-провайдеры не настроены (AI_PROVIDER_1_*). Woody не сможет отвечать через AI.")
    else:
        logger.info("AI-провайдеры: %s", ", ".join(p["name"] for p in router.status()))
    if not settings.access_token and settings.app_env == "production":
        logger.warning("WOODY_ACCESS_TOKEN не задан: приложение открыто для всех, у кого есть URL!")
    # Telegram admin bot: ставим webhook автоматически
    base = settings.public_base_url or ""
    if bot_token() and base:
        asyncio.create_task(setup_webhook(base))
        logger.info("Telegram webhook → %s/api/admin/telegram", base.rstrip("/"))
    elif bot_token():
        logger.warning("Есть TELEGRAM_ADMIN_BOT_TOKEN, но нет PUBLIC_BASE_URL — бот не получит /start. Задай PUBLIC_BASE_URL=https://woodyai.up.railway.app")
    else:
        logger.warning("TELEGRAM_ADMIN_BOT_TOKEN не задан — админ-бот выключен")
    yield


app = FastAPI(title="Woody", version="0.7.0", lifespan=lifespan)


@app.middleware("http")
async def require_token(request: Request, call_next):
    path = request.url.path
    # Публичны: health, страницы и проверка/погашение transfer-токена (их защищает сам одноразовый токен).
    public = (path in ("/api/health", "/api/version", "/api/admin/telegram", "/api/admin/bot-status", "/api/admin/setup-webhook") or path in ("/api/transfer/check", "/api/transfer/redeem")
              or path.startswith("/api/auth/") or path == "/api/tts/greeting")
    if settings.access_token and path.startswith("/api/") and not public:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        authorized = hmac.compare_digest(supplied, settings.access_token)
        if not authorized:
            # Люди, которым Woody передали по QR, не знают пароль владельца — их пускает личная сессия.
            session_secret = request.headers.get("x-woody-session")
            if session_secret:
                with SessionLocal() as db:
                    authorized = find_user_by_secret(db, session_secret) is not None
        if not authorized:
            return JSONResponse({"detail": "Неверный или отсутствующий токен доступа"}, status_code=401)
    return await call_next(request)


# ------------------------------------------------------------------ пользователи
def get_user_optional(
    x_woody_session: str | None = Header(default=None), db: Session = Depends(get_db)
) -> User | None:
    return find_user_by_secret(db, x_woody_session)


def require_user(user: User | None = Depends(get_user_optional)) -> User:
    if user is None:
        raise HTTPException(403, {"code": "not_registered", "message": "Сначала нужно познакомиться."})
    return user


class UserBody(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class PaymentBody(BaseModel):
    plan: str = Field(min_length=3, max_length=20)  # pro | ultra
    period: str = Field(default="month", max_length=20)
    receipt: str = Field(default="", max_length=6_000_000)  # data URL скрина (после сжатия на клиенте)


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    source: str = "text"
    chat_id: int | None = None
    image: str | None = Field(default=None, max_length=1_800_000)  # data:image/jpeg;base64,… — фото или снимок камеры
    pro_mode: str | None = Field(default=None, max_length=32)  # study|code|write|deal|agent|ghost|default
    pro_persona: str | None = Field(default=None, max_length=500)  # пользовательский характер (Pro)


class TruncateBody(BaseModel):
    from_id: int


class GroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class MemberBody(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class GroupMsgBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class PermissionBody(BaseModel):
    decision: Decision


class PermissionSetBody(BaseModel):
    action: str
    decision: Decision


class SettingsBody(BaseModel):
    memory_enabled: bool | None = None
    camera_enabled: bool | None = None
    microphone_enabled: bool | None = None
    hand_tracking_enabled: bool | None = None
    transfer_enabled: bool | None = None
    voice_replies: bool | None = None
    always_on_camera: bool | None = None
    learn_from_chats: bool | None = None
    voice_engine: str | None = None
    voice_name: str | None = None


class TTSBody(BaseModel):
    text: str = Field(min_length=1, max_length=700)
    voice: str | None = None
    tone: str | None = None


class SeeBody(BaseModel):
    image: str = Field(min_length=100, max_length=1_500_000)  # data:image/jpeg;base64,...
    question: str = Field(default="Что ты видишь? Опиши коротко.", max_length=300)


class BrainImport(BaseModel):
    items: list[dict] = Field(max_length=20000)


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class RedeemBody(TokenBody):
    name: str = Field(min_length=1, max_length=50)


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=50)


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class GoogleBody(BaseModel):
    access_token: str = Field(min_length=10, max_length=4000)


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=50)


def _auth_http(exc: AuthError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": exc.message})


@app.get("/api/auth/config")
def auth_config() -> dict:
    return {"google_client_id": settings.google_client_id or None}


@app.post("/api/auth/register")
def auth_register(body: RegisterBody, db: Session = Depends(get_db)) -> dict:
    try:
        user, secret = register_email(db, body.email, body.password, body.name)
    except AuthError as exc:
        raise _auth_http(exc) from exc
    return {"session": secret, "name": user.name, "new": True}


@app.post("/api/auth/login")
def auth_login(body: LoginBody, request: Request, db: Session = Depends(get_db)) -> dict:
    ip = request.client.host if request.client else ""
    try:
        user, secret = login_email(db, body.email, body.password, ip)
    except AuthError as exc:
        raise _auth_http(exc) from exc
    return {"session": secret, "name": user.name, "new": False}


@app.post("/api/auth/google")
async def auth_google(body: GoogleBody, db: Session = Depends(get_db)) -> dict:
    cid = settings.google_client_id
    if not cid:
        raise HTTPException(503, {"code": "google_off", "message": "Вход через Google ещё не настроен (GOOGLE_CLIENT_ID)."})
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            ti = await client.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": body.access_token})
            info = ti.json() if ti.status_code == 200 else {}
            if cid not in (info.get("aud"), info.get("azp")):
                raise ValueError("audience")
            ui = await client.get("https://openidconnect.googleapis.com/v1/userinfo",
                                  headers={"Authorization": f"Bearer {body.access_token}"})
            ui.raise_for_status()
            profile = ui.json()
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(401, {"code": "google_failed", "message": "Не удалось войти через Google. Попробуй ещё раз."}) from None
    if not profile.get("email_verified", True) or not profile.get("sub"):
        raise HTTPException(401, {"code": "google_failed", "message": "Почта Google не подтверждена."})
    name = profile.get("given_name") or profile.get("name") or ""
    user, secret, created = login_google(db, profile["sub"], profile.get("email", ""), name, profile.get("picture"))
    return {"session": secret, "name": user.name, "new": created}


@app.post("/api/auth/logout")
def auth_logout(x_woody_session: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict:
    logout_session(db, x_woody_session)
    return {"ok": True}


@app.put("/api/user")
def rename_user(body: RenameBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    user.name = body.name.strip()
    db.commit()
    return {"name": user.name}


# ------------------------------------------------------------------ health / user

@app.get("/admin")
@app.get("/admin/")
def admin_page() -> FileResponse:
    return FileResponse(Path(BASE_DIR) / "frontend" / "admin.html")


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"status": "ok", "service": "woody", "version": "0.7.0", "build": "2026-11-26b"}

@app.get("/api/version")
def app_version() -> dict[str, str]:
    return {"version": "0.7.0", "build": "2026-11-26b"}


# ------------------------------------------------------------------ веб-админка
def _admin_auth(request: Request) -> None:
    import os, hmac
    key = (request.headers.get("x-admin-key") or "").strip()
    expected = (os.getenv("ADMIN_PANEL_KEY") or settings.access_token or "").strip()
    if not expected or not key or not hmac.compare_digest(key, expected):
        raise HTTPException(401, {"code": "admin_auth", "message": "Неверный ключ админки"})


@app.get("/api/admin/panel/stats")
def admin_panel_stats(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin_auth(request)
    return admin_panel.stats(db, providers_n=len(router.status()))


@app.get("/api/admin/panel/users")
def admin_panel_users(request: Request, db: Session = Depends(get_db)) -> list:
    _admin_auth(request)
    return admin_panel.list_users(db)


@app.get("/api/admin/panel/payments")
def admin_panel_payments(request: Request, plan: str | None = None, db: Session = Depends(get_db)) -> list:
    _admin_auth(request)
    return admin_panel.list_payments(db, plan=plan)


@app.get("/api/admin/panel/feedback")
def admin_panel_feedback(request: Request, db: Session = Depends(get_db)) -> list:
    _admin_auth(request)
    return admin_panel.list_feedback(db)


@app.get("/api/admin/panel/settings")
def admin_panel_get_settings(request: Request) -> dict:
    _admin_auth(request)
    return admin_panel.load_settings()


@app.post("/api/admin/panel/settings")
async def admin_panel_set_settings(request: Request) -> dict:
    _admin_auth(request)
    body = await request.json()
    return admin_panel.save_settings(body)


@app.post("/api/admin/panel/decide")
async def admin_panel_decide(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin_auth(request)
    body = await request.json()
    msg = decide_payment(db, int(body.get("id") or 0), bool(body.get("approve")), str(body.get("note") or ""))
    return {"message": msg}



# ------------------------------------------------------------------ тарифы / оплата
@app.get("/api/plans")
def api_plans(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    import os
    plan = effective_plan(user)
    return {
        "plans": PLANS,
        "prices": PRICES,
        "currency": CURRENCY,
        "reset_hours": RESET_HOURS,
        "period": getattr(user, "plan_period", None) or "month",
        "current": plan,
        "plan_until": user.plan_until.isoformat() if user.plan_until else None,
        "used": int(user.request_count or 0),
        "remaining": remaining_requests(user),
        "free_limit": FREE_LIMIT,
        "payment": {
            "requisites": (admin_panel.load_settings().get("payment_requisites") or os.getenv("PAYMENT_REQUISITES") or "Укажите реквизиты в админке или PAYMENT_REQUISITES").strip(),
            "holder": (admin_panel.load_settings().get("payment_holder") or os.getenv("PAYMENT_HOLDER") or "").strip(),
            "note": "После оплаты загрузите скрин чека. Админ проверит и активирует тариф.",
        },
    }


@app.post("/api/payments")
async def api_payment(body: PaymentBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    plan = body.plan.lower().strip()
    if plan not in ("pro", "ultra"):
        raise HTTPException(422, {"code": "bad_plan", "message": "Только pro или ultra."})
    if not body.receipt or not str(body.receipt).startswith("data:image"):
        raise HTTPException(422, {"code": "need_receipt", "message": "Нужен скриншот чека (фото JPG/PNG)."})
    if len(body.receipt) < 200:
        raise HTTPException(422, {"code": "need_receipt", "message": "Файл чека пустой или повреждён."})
    try:
        req = create_payment(db, user, plan, body.period, body.receipt)
    except Exception as exc:
        logger.exception("create_payment: %s", exc)
        raise HTTPException(500, {"code": "pay_save", "message": "Не удалось сохранить заявку. Попробуй ещё раз."})
    try:
        sent = await notify_payment(db, req, user)
    except Exception as exc:
        logger.exception("notify_payment: %s", exc)
        sent = False
    msg = "Заявка отправлена. Админ проверит чек и активирует тариф."
    if not sent:
        msg = "Заявка сохранена. Если админ-бот настроен — уведомление уйдёт. Иначе проверь TELEGRAM_ADMIN_BOT_TOKEN и CHAT_ID."
    return {"id": req.id, "status": req.status, "notified": bool(sent), "message": msg}


class FeedbackBody(BaseModel):
    text: str = Field(min_length=5, max_length=3000)


class DonationBody(BaseModel):
    amount: str = Field(min_length=1, max_length=12)
    note: str = Field(default="", max_length=300)
    receipt: str = Field(default="", max_length=6_000_000)


@app.post("/api/feedback")
async def api_feedback(body: FeedbackBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    recent = db.scalar(select(func.count()).select_from(Feedback).where(Feedback.user_id == user.id, Feedback.created_at > since)) or 0
    if recent >= 5:
        raise HTTPException(429, {"code": "too_many", "message": "Много пожеланий за час. Попробуй позже."})
    fb = Feedback(user_id=user.id, text=body.text.strip())
    db.add(fb); db.commit(); db.refresh(fb)
    sent = await notify_feedback(fb, user)
    return {"ok": True, "notified": sent, "message": "Спасибо! Пожелание отправлено — админ рассмотрит и улучшит Woody."}


@app.post("/api/donations")
async def api_donation(body: DonationBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    try:
        amount = float(body.amount.replace(",", ".").strip())
    except ValueError:
        raise HTTPException(422, {"code": "bad_amount", "message": "Укажи сумму числом."})
    if not (0 < amount <= 1_000_000):
        raise HTTPException(422, {"code": "bad_amount", "message": "Сумма должна быть больше нуля."})
    if not body.receipt or not body.receipt.startswith("data:image"):
        raise HTTPException(422, {"code": "need_receipt", "message": "Нужен скриншот чека (фото)."})
    row = PaymentRequest(user_id=user.id, plan="donate", period="once", amount=f"{amount:g}", receipt_data=body.receipt[:1_500_000],
                         status="pending", admin_note=body.note.strip())
    db.add(row); db.commit(); db.refresh(row)
    sent = await notify_payment(db, row, user)
    return {"ok": True, "notified": sent, "message": "Спасибо за поддержку! Чек отправлен админу."}


@app.get("/api/usage")
def api_usage(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return {
        "plan": effective_plan(user),
        "used": int(user.request_count or 0),
        "remaining": remaining_requests(user),
        "limit": current_limit(user),
        "period": getattr(user, "plan_period", None) or "month",
        "plan_until": user.plan_until.isoformat() if user.plan_until else None,
    }



@app.get("/api/admin/bot-status")
async def admin_bot_status() -> dict:
    """Проверка токена бота и webhook (без секретов)."""
    import os
    import httpx
    token = bot_token()
    if not token:
        return {"ok": False, "error": "no_token", "hint": "Задай TELEGRAM_ADMIN_BOT_TOKEN"}
    base = settings.public_base_url or ""
    async with httpx.AsyncClient(timeout=20) as client:
        me = (await client.get(f"https://api.telegram.org/bot{token}/getMe")).json()
        wh = (await client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")).json()
    return {
        "ok": True,
        "bot": (me.get("result") or {}).get("username"),
        "webhook": (wh.get("result") or {}).get("url"),
        "pending_updates": (wh.get("result") or {}).get("pending_update_count"),
        "last_error": (wh.get("result") or {}).get("last_error_message"),
        "public_base_url": base,
        "admin_chat_set": bool(os.getenv("TELEGRAM_ADMIN_CHAT_ID")),
        "expected_webhook": (base.rstrip("/") + "/api/admin/telegram") if base else None,
    }


@app.post("/api/admin/setup-webhook")
async def admin_setup_webhook() -> dict:
    base = settings.public_base_url or ""
    if not base:
        raise HTTPException(400, {"message": "Нужен PUBLIC_BASE_URL"})
    await setup_webhook(base)
    return await admin_bot_status()


@app.post("/api/admin/telegram")
async def admin_telegram_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Webhook Telegram-бота (админ)."""
    import os
    secret = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    if secret and request.headers.get("x-telegram-bot-api-secret-token") != secret:
        raise HTTPException(403, {"detail": "forbidden"})
    try:
        data = await request.json()
    except Exception:
        return {"ok": True}
    try:
        await handle_update(db, data, providers_count=len(router.status()))
    except Exception as exc:
        logger.exception("admin telegram: %s", exc)
    return {"ok": True}




def _user_payload(db: Session, user: User | None) -> dict:
    if user is None:
        return {"registered": False, "name": None}
    emotion = load_emotion(db, user.id)
    return {
        "registered": True,
        "name": user.name,
        "email": user.email,
        "picture": user.picture,
        "settings": UserSettings(db, user.id).all(),
        "state": emotion.state("idle"),
        "face": emotion.face_state(),
        "voice": emotion.voice_style(),
    }


@app.get("/api/user")
def get_user(user: User | None = Depends(get_user_optional), db: Session = Depends(get_db)) -> dict:
    return _user_payload(db, user)


@app.post("/api/user")
def create_user_endpoint(
    body: UserBody, user: User | None = Depends(get_user_optional), db: Session = Depends(get_db)
) -> dict:
    # Повторное обращение с уже действующей сессией (например, обновление страницы) нового пользователя не создаёт.
    if user is not None:
        raise HTTPException(409, {"code": "already_registered", "message": "Мы уже знакомы."})
    new_user, secret = create_user(db, body.name)
    return {"name": new_user.name, "greeting": f"Приятно познакомиться, {new_user.name}.", "session": secret}


app.post("/api/register")(create_user_endpoint)  # совместимость с v0.1


@app.get("/api/status")
def status(user: User | None = Depends(get_user_optional), db: Session = Depends(get_db)) -> dict:
    payload = _user_payload(db, user)
    payload.update(
        {
            "providers": router.status(),
            "background": capabilities()["background"],
            "memory_enabled": bool(payload.get("settings", {}).get("memory_enabled", True)),
            "persistent": settings.persistent,
            "brain": Mind(db).stats(),
            "learning": LEARNER.status(),
            "tts": True,
        }
    )
    return payload


# ------------------------------------------------------------------ chat / voice
def _own_chat(db: Session, user: User, chat_id: int | None, title_hint: str = "") -> ChatSession:
    """Возвращает чат пользователя; если chat_id нет — создаёт новый."""
    if chat_id:
        chat = db.get(ChatSession, chat_id)
        if chat is None or chat.user_id != user.id:
            raise HTTPException(404, {"code": "no_chat", "message": "Чат не найден."})
        return chat
    chat = ChatSession(user_id=user.id, title=(title_hint.strip().replace("\n", " ")[:40] or "Новый чат"))
    db.add(chat)
    db.commit()
    return chat


async def _see(body: ChatBody, user: User, db: Session, chat: ChatSession) -> dict:
    """Woody разглядывает присланное фото/снимок и отвечает на вопрос к нему. Сам кадр нигде не хранится."""
    if not body.image or not body.image.startswith("data:image/"):
        raise HTTPException(422, {"code": "bad_image", "message": "Нужно изображение."})
    memory = MemoryService(db)
    memory.chat_id = chat.id
    memory.add_message(user.id, "USER", "📷 " + body.text.strip(), body.source)
    messages = [
        {"role": "system", "content": f"Ты — Woody, дружелюбный помощник. Пользователя зовут {user.name}. Внимательно рассмотри изображение, распознай что на нём "
                                      "(предметы, люди в общих чертах, текст, код, ошибки) и ответь по-русски по делу. Если на фото код или задача — реши её."},
        {"role": "user", "content": [{"type": "text", "text": body.text.strip()}, {"type": "image_url", "image_url": {"url": body.image}}]},
    ]
    reply, error = None, False
    for task in ("vision", "conversation"):
        try:
            reply, _ = await router.complete(messages, task=task)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vision (%s) недоступен: %s", task, exc)
    if reply is None:
        reply, error = "Сейчас я не могу разглядеть фото: модель с поддержкой изображений недоступна.", True
    memory.add_message(user.id, "WOODY", reply, body.source)
    emotion = load_emotion(db, user.id)
    state = emotion.state("speaking")
    state["emotion"] = "confused" if error else "happy"
    return {"reply": reply, "state": state, "tone": "calm", "error": "vision_unavailable" if error else None, "chat_id": chat.id, "ids": memory.last_ids}


async def _chat(body: ChatBody, user: User, db: Session, source: str = "text") -> dict:
    ok, usage = can_request(user, db)
    if not ok:
        if usage.get("plan") in ("pro", "ultra"):
            mins = usage.get("reset_in_minutes", RESET_HOURS * 60)
            msg = f"Лимит на этот месяц закончился. Подожди — через {mins // 60} ч {mins % 60} мин счётчик обнулится, и можно писать дальше. Или возьми тариф на год — там безлимит."
        else:
            msg = "Твой бесплатный лимит закончился (200 000 запросов). Подключи Pro или Ultra, чтобы продолжить."
        return {
            "reply": msg,
            "error": "limit_exceeded",
            "usage": usage,
            "face": "sad",
            "tone": "sad",
            "state": {"emotion": "sad", "activity": "idle"},
        }
    chat = _own_chat(db, user, body.chat_id, body.text)
    if body.image:
        result = await _see(body, user, db, chat)
    else:
        brain = Brain(db, router)
        # Woody Code (ultra): принудительный красный code-режим
        pro_mode = body.pro_mode
        if effective_plan(user) == "ultra" and (pro_mode or "") in ("woody_code", "code_red"):
            pro_mode = "woody_code"
        paid = effective_plan(user) in ("pro", "ultra")
        if not paid:
            pro_mode, body.pro_persona = None, None  # режимы и характер — только на платных тарифах
        result = await brain.handle(user, body.text.strip(), source, chat.id, pro_mode=pro_mode, pro_persona=body.pro_persona, paid=paid)
        result["chat_id"] = chat.id
        result["ids"] = dict(brain.memory.last_ids)
    bump_usage(db, user)
    chat.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if chat.title == "Новый чат":
        chat.title = body.text.strip().replace("\n", " ")[:40] or chat.title
    db.commit()
    result["usage"] = {"plan": effective_plan(user), "used": int(user.request_count or 0), "remaining": remaining_requests(user)}
    return result


# ------------------------------------------------------------------ Woody Code (Ultra)
EXPORT_DIR = Path(settings.db_path).parent / "exports"


class CodeBody(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    new_project: bool = False


CODE_JOBS: dict[str, "code_agent.Job"] = {}
CODE_WS: dict[int, dict] = {}          # рабочий проект каждого пользователя (для доработок)


def _gc_jobs() -> None:
    import time
    for k in [k for k, j in CODE_JOBS.items() if time.time() - j.created > 3600]:
        CODE_JOBS.pop(k, None)


@app.post("/api/code")
async def woody_code(body: CodeBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Запускает агента Woody Code. Прогресс — GET /api/code/job/{id}."""
    if effective_plan(user) != "ultra":
        raise HTTPException(403, {"code": "need_ultra", "message": "Woody Code доступен на тарифе Ultra."})
    ok, usage = can_request(user, db)
    if not ok:
        return {"error": "limit_exceeded", "reply": "Лимит закончился. Подожди сброса или возьми тариф на год.", "usage": usage}
    if any(j.user_id == user.id and not j.done for j in CODE_JOBS.values()):
        raise HTTPException(409, {"code": "busy", "message": "Woody Code ещё работает над прошлой задачей."})
    import secrets
    _gc_jobs()
    job = code_agent.Job(user.id)
    jid = secrets.token_urlsafe(8).replace("-", "a").replace("_", "b")
    CODE_JOBS[jid] = job
    uid = user.id

    def count() -> None:
        with SessionLocal() as s2:
            u2 = s2.get(User, uid)
            if u2:
                bump_usage(s2, u2)

    ws = CODE_WS.setdefault(uid, {})
    if body.new_project:
        ws.clear()
    asyncio.create_task(code_agent.run_agent(router, job, body.text.strip(), ws, EXPORT_DIR, body.new_project, count))
    return {"job": jid}


@app.get("/api/code/job/{jid}")
def woody_code_job(jid: str, since: int = 0, user: User = Depends(require_user)) -> dict:
    job = CODE_JOBS.get(jid)
    if job is None or job.user_id != user.id:
        raise HTTPException(404, {"code": "no_job", "message": "Задача не найдена."})
    return {"steps": job.steps[since:], "next": len(job.steps), "done": job.done, "result": job.result if job.done else None}


@app.get("/api/code/download/{token}")
def woody_code_download(token: str, user: User = Depends(require_user)) -> Response:
    found = codegen.locate(EXPORT_DIR, user.id, token)
    if not found:
        raise HTTPException(404, {"code": "no_file", "message": "Файл не найден или устарел."})
    path, name = found
    from urllib.parse import quote
    media = "application/zip" if name.endswith(".zip") else "application/octet-stream"
    return Response(path.read_bytes(), media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.post("/api/chat")
async def chat(body: ChatBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return await _chat(body, user, db, "text")


@app.post("/api/voice")
async def voice(body: ChatBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Текст, распознанный браузером (STT выполняется на устройстве). Дальше — тот же Woody Core."""
    if not UserSettings(db, user.id).get("microphone_enabled"):
        raise HTTPException(403, {"code": "mic_disabled", "message": "Микрофон отключён в настройках."})
    body.image = None
    return await _chat(body, user, db, "voice")


@app.get("/api/history")
def history(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return [{"role": r.role, "text": r.text} for r in MemoryService(db).recent_messages(user.id, 60, include_voice=True)]


# ------------------------------------------------------------------ история чатов (как в ChatGPT)
@app.get("/api/chats")
def list_chats(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).limit(200))
    return [{"id": c.id, "title": c.title} for c in rows]


@app.post("/api/chats")
def new_chat(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    chat = _own_chat(db, user, None)
    return {"id": chat.id, "title": chat.title}


@app.get("/api/chats/{chat_id}/messages")
def chat_messages(chat_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    _own_chat(db, user, chat_id)
    rows = db.scalars(select(Conversation).where(Conversation.user_id == user.id, Conversation.chat_id == chat_id).order_by(Conversation.id))
    return [{"id": r.id, "role": r.role, "text": r.text} for r in rows]


@app.put("/api/chats/{chat_id}")
def rename_chat(chat_id: int, body: GroupBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    chat = _own_chat(db, user, chat_id)
    chat.title = body.name.strip()[:100]
    db.commit()
    return {"id": chat.id, "title": chat.title}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    chat = _own_chat(db, user, chat_id)
    db.execute(delete(Conversation).where(Conversation.user_id == user.id, Conversation.chat_id == chat_id))
    db.delete(chat)
    db.commit()
    return {"deleted": chat_id}


@app.post("/api/chats/{chat_id}/truncate")
def truncate_chat(chat_id: int, body: TruncateBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Изменение сообщения: удаляем его и всё после него — потом фронтенд отправляет исправленный текст заново."""
    _own_chat(db, user, chat_id)
    res = db.execute(delete(Conversation).where(Conversation.user_id == user.id, Conversation.chat_id == chat_id, Conversation.id >= body.from_id))
    db.commit()
    return {"removed": res.rowcount}


# ------------------------------------------------------------------ групповые чаты (Woody состоит в каждой группе)
def _member(db: Session, group_id: int, user: User) -> Group:
    group = db.get(Group, group_id)
    ok = group is not None and db.scalars(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user.id)).first()
    if not ok:
        raise HTTPException(404, {"code": "no_group", "message": "Группа не найдена."})
    return group


def _members_payload(db: Session, group_id: int) -> list[dict]:
    rows = db.scalars(select(User).join(GroupMember, GroupMember.user_id == User.id).where(GroupMember.group_id == group_id).order_by(User.id))
    return [{"id": u.id, "name": u.name, "picture": u.picture} for u in rows]


def _msg_payload(m: GroupMessage) -> dict:
    return {"id": m.id, "user_id": m.user_id, "text": m.text}


@app.get("/api/groups")
def list_groups(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Group).join(GroupMember, GroupMember.group_id == Group.id).where(GroupMember.user_id == user.id).order_by(Group.id.desc())).all()
    out = []
    for g in rows:
        last = db.scalars(select(GroupMessage).where(GroupMessage.group_id == g.id).order_by(GroupMessage.id.desc()).limit(1)).first()
        out.append({
            "id": g.id,
            "name": g.name,
            "last_text": (last.text[:80] if last else ""),
            "last_at": last.created_at.isoformat() if last and last.created_at else None,
            "members": db.scalar(select(func.count()).select_from(GroupMember).where(GroupMember.group_id == g.id)) or 0,
        })
    return out


@app.post("/api/groups")
def create_group(body: GroupBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    group = Group(name=body.name.strip(), owner_id=user.id)
    db.add(group)
    db.flush()
    db.add(GroupMember(group_id=group.id, user_id=user.id))
    db.commit()
    return {"id": group.id, "name": group.name}


@app.get("/api/groups/{group_id}")
def group_info(group_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    group = _member(db, group_id, user)
    return {"id": group.id, "name": group.name, "me": user.id, "members": _members_payload(db, group_id)}


@app.post("/api/groups/{group_id}/members")
def add_member(group_id: int, body: MemberBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    _member(db, group_id, user)
    found = db.scalars(select(User).where(func.lower(User.name) == body.name.strip().lower())).all()
    if not found:
        raise HTTPException(404, {"code": "no_user", "message": f"Не нашёл пользователя «{body.name.strip()}». Проверь имя."})
    if len(found) > 1:
        raise HTTPException(409, {"code": "ambiguous", "message": "Людей с таким именем несколько — попроси человека сменить имя в настройках на уникальное."})
    target = found[0]
    if db.scalars(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == target.id)).first():
        raise HTTPException(409, {"code": "already", "message": f"{target.name} уже в группе."})
    db.add(GroupMember(group_id=group_id, user_id=target.id))
    db.commit()
    return {"members": _members_payload(db, group_id)}


@app.delete("/api/groups/{group_id}")
def leave_group(group_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    _member(db, group_id, user)
    db.execute(delete(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user.id))
    db.commit()
    return {"left": group_id}


@app.get("/api/groups/{group_id}/messages")
def group_messages(group_id: int, after: int = 0, user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    _member(db, group_id, user)
    rows = db.scalars(select(GroupMessage).where(GroupMessage.group_id == group_id, GroupMessage.id > after).order_by(GroupMessage.id).limit(200))
    return [_msg_payload(m) for m in rows]


@app.post("/api/groups/{group_id}/messages")
async def post_group_message(group_id: int, body: GroupMsgBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    """Сообщение участника + ответ Woody. Woody общается со всеми одинаково и пользуется общим мозгом."""
    group = _member(db, group_id, user)
    mine = GroupMessage(group_id=group_id, user_id=user.id, text=body.text.strip())
    db.add(mine)
    db.commit()
    names = {u["id"]: u["name"] for u in _members_payload(db, group_id)}
    recent = list(reversed(db.scalars(select(GroupMessage).where(GroupMessage.group_id == group_id).order_by(GroupMessage.id.desc()).limit(14)).all()))
    convo = [{"role": "assistant" if m.user_id is None else "user",
              "content": m.text if m.user_id is None else f"{names.get(m.user_id, 'Кто-то')}: {m.text}"} for m in recent]
    system = (f"Ты — Woody, участник группового чата «{group.name}». В чате люди: {', '.join(names.values())}. "
              "Отвечай по-русски коротко и дружелюбно, обращайся к тому, кто написал, по имени. Ко всем относись одинаково. "
              "Код пиши в блоках ```язык.")
    mind = Mind(db)
    known = mind.recall(body.text, 0.9)
    if known:
        reply = known[0]
    else:
        try:
            reply, _ = await router.complete([{"role": "system", "content": system}, *convo])
            reply, _tone = split_tone(reply)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Woody в группе: AI недоступен: %s", exc)
            fb = mind.recall(body.text, 0.45)
            reply = fb[0] if fb else "Сейчас я не могу ответить — у моего AI-ключа проблема. Попробуйте позже."
    answer = GroupMessage(group_id=group_id, user_id=None, text=reply)
    db.add(answer)
    db.commit()
    return [_msg_payload(mine), _msg_payload(answer)]


# ------------------------------------------------------------------ permissions
def _require_known(action: str) -> None:
    if action not in ACTIONS:
        raise HTTPException(404, f"Неизвестное действие: {action}")


@app.get("/api/permissions")
def list_permissions(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return PermissionManager(db, user.id).list()


@app.post("/api/permissions")
def post_permission(body: PermissionSetBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    _require_known(body.action)
    PermissionManager(db, user.id).set(body.action, body.decision)
    return {"action": body.action, "decision": body.decision.value}


@app.put("/api/permissions/{action}")
def set_permission(
    action: str, body: PermissionBody, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    _require_known(action)
    PermissionManager(db, user.id).set(action, body.decision)
    return {"action": action, "decision": body.decision.value}


@app.delete("/api/permissions/{action}")
def revoke_permission(action: str, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    _require_known(action)
    PermissionManager(db, user.id).revoke(action)
    return {"action": action, "status": "ask"}


@app.post("/api/permissions/{action}/check")
def check_permission(action: str, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Фронтенд вызывает перед использованием камеры/микрофона. ALLOW_ONCE тут расходуется."""
    _require_known(action)
    return {"action": action, "status": PermissionManager(db, user.id).check(action)}


# ------------------------------------------------------------------ memory / settings
@app.get("/api/memory")
def list_memory(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return [
        {"id": m.id, "category": m.category, "key": m.key, "value": m.value}
        for m in MemoryService(db).list(user.id)
    ]


@app.delete("/api/memory/{memory_id}")
def delete_memory(memory_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    if not MemoryService(db).delete(user.id, memory_id):
        raise HTTPException(404, "Запись памяти не найдена")
    return {"deleted": memory_id}


@app.delete("/api/memory")
def clear_memory(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    MemoryService(db).clear(user.id)
    db.execute(delete(ChatSession).where(ChatSession.user_id == user.id))
    db.commit()
    PENDING.pop(user.id, None)
    return {"cleared": True}


class PinFileBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    summary: str = Field(..., min_length=1, max_length=4000)
    content: str | None = Field(default=None, max_length=120000)


@app.get("/api/pins")
def list_pins(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return MemoryService(db).list_pins(user.id)


@app.post("/api/pins")
async def pin_file(body: PinFileBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Закрепить файл: сохраняем имя + краткое описание (и при желании содержимое для анализа)."""
    summary = body.summary.strip()
    if body.content and len(body.content) > 50:
        # Быстрый разбор типа файла по расширению и первым строкам
        ext = (body.name.rsplit(".", 1)[-1] if "." in body.name else "").lower()
        preview = body.content[:800].replace("\r", "")
        kind = {
            "py": "Python", "js": "JavaScript", "ts": "TypeScript", "html": "HTML",
            "css": "CSS", "json": "JSON", "md": "Markdown", "txt": "текст",
            "sh": "Shell", "sql": "SQL", "go": "Go", "rs": "Rust", "java": "Java",
        }.get(ext, "файл")
        summary = f"{kind}: {summary}\nПревью:\n{preview[:500]}"
    mem = MemoryService(db).pin_file(user.id, body.name.strip(), summary)
    return {"ok": True, "name": body.name.strip(), "id": getattr(mem, "id", None)}


@app.delete("/api/pins/{name}")
def unpin_file(name: str, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    ok = MemoryService(db).unpin_file(user.id, name)
    if not ok:
        raise HTTPException(404, "Закреплённый файл не найден")
    return {"deleted": name}


@app.post("/api/pins/analyze")
async def analyze_pinned_or_text(
    body: PinFileBody,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Изучить содержимое файла: тип, структура, что делает, как запускать."""
    text = (body.content or body.summary or "").strip()
    if not text:
        raise HTTPException(422, "Нет содержимого для анализа")
    name = body.name.strip() or "file"
    system = (
        "Ты — Woody, анализируешь файлы пользователя. Ответь кратко и по делу на русском:\n"
        "1) Тип файла и назначение\n"
        "2) Ключевые части / структура\n"
        "3) Как запускать или использовать (если применимо)\n"
        "4) Замечания / риски / улучшения\n"
        "Без воды, списком."
    )
    try:
        reply, provider = await router.complete([
            {"role": "system", "content": system},
            {"role": "user", "content": f"Файл: {name}\n\n```\n{text[:30000]}\n```"},
        ])
    except Exception as exc:
        raise HTTPException(502, f"AI недоступен: {exc}") from exc
    # Авто-pin с кратким summary
    short = (reply or "")[:600]
    MemoryService(db).pin_file(user.id, name, short)
    return {"ok": True, "name": name, "analysis": reply, "provider": provider}


@app.get("/api/settings")
def get_settings_endpoint(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return UserSettings(db, user.id).all()


@app.put("/api/settings")
def update_settings(body: SettingsBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    us = UserSettings(db, user.id)
    for name in SETTING_DEFAULTS:
        value = getattr(body, name)
        if value is not None:
            try:
                us.set(name, value)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    return us.all()


@app.get("/api/providers")
def providers() -> dict:
    return {"providers": router.status(), "configured": bool(router.providers)}


@app.get("/api/background")
def background() -> dict:
    return capabilities()["background"]


# ------------------------------------------------------------------ мозг / самообучение
@app.get("/api/brain")
def brain_stats(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return {**Mind(db).stats(), "persistent": settings.persistent, "learning": LEARNER.status()}


@app.get("/api/brain/export")
def brain_export(user: User = Depends(require_user), db: Session = Depends(get_db)) -> Response:
    """Резервная копия мозга (без личных данных). Положите её в data/brain_seed.json — мозг вернётся после любого деплоя."""
    data = json.dumps(Mind(db).export(), ensure_ascii=False)
    return Response(data, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="brain_seed.json"'})


@app.post("/api/brain/import")
def brain_import(body: BrainImport, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return {"added": Mind(db).import_items(body.items)}


@app.get("/api/learn/status")
def learn_status(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return {**LEARNER.status(), "brain": Mind(db).stats()}


@app.post("/api/learn/start")
async def learn_start(user: User = Depends(require_user)) -> dict:
    started = LEARNER.start(router, SessionLocal, settings.self_learn_max)
    return {**LEARNER.status(), "started": started}


@app.post("/api/learn/stop")
def learn_stop(user: User = Depends(require_user)) -> dict:
    LEARNER.stop()
    return LEARNER.status()


# ------------------------------------------------------------------ голос (человеческий) и зрение
def _tts_target() -> tuple[str, str] | None:
    base, key = settings.tts_base_url, settings.tts_api_key
    if not (base and key) and router.providers:
        first = router.providers[0]
        cfg = getattr(first, "cfg", None)
        if cfg is not None:
            base, key = base or cfg.base_url, key or cfg.api_key
    return (base, key) if base and key else None


_OPENAI_TONE = {
    "laugh": "Ты искренне смеёшься: говори весело, с лёгким смехом в голосе, можешь коротко рассмеяться.",
    "joy": "Говори радостно, живо и тепло, с улыбкой в голосе.",
    "tender": "Говори мягко, нежно и тепло, чуть тише обычного.",
    "calm": "Говори спокойно и естественно, как друг в разговоре.",
    "surprise": "Говори удивлённо, с живой повышающейся интонацией.",
    "sad": "Говори грустно, тише и медленнее, с печалью в голосе.",
    "cry": "Говори сквозь слёзы: дрожащий голос, паузы, всхлипы, но слова остаются понятными.",
    "angry": "Говори раздражённо, но сдержанно, резче и короче.",
    "think": "Говори задумчиво, с паузами, как будто размышляешь вслух.",
}
_EDGE_TONE = {  # (скорость, высота) для бесплатного нейронного голоса
    "laugh": ("+10%", "+6Hz"), "joy": ("+6%", "+3Hz"), "tender": ("-6%", "+1Hz"), "calm": ("+0%", "+0Hz"),
    "surprise": ("+8%", "+8Hz"), "sad": ("-12%", "-6Hz"), "cry": ("-16%", "-4Hz"), "angry": ("+8%", "-3Hz"), "think": ("-4%", "+0Hz"),
}
_MALE_VOICES = {"ash", "echo", "onyx"}
_tts_client = httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_keepalive_connections=8))
_GREETINGS = {
    "welcome": "Привет! Я Вуди. Чтобы начать общаться со мной, зарегистрируйся. А если мы уже знакомы, просто войди.",
    "login": "Рад тебя снова видеть! Входи, и продолжим разговор.",
}
_greeting_cache: dict[str, bytes] = {}


_openai_down_until = 0.0


async def _openai_tts(text: str, voice: str, tone: str) -> bytes | None:
    global _openai_down_until
    import time as _t
    target = _tts_target()
    if not target or _t.time() < _openai_down_until:
        return None
    base, key = target
    payload = {"model": settings.tts_model, "voice": voice, "input": text, "response_format": "mp3",
               "instructions": "Говори по-русски как обычный живой человек в разговоре, не как диктор. " + _OPENAI_TONE.get(tone, _OPENAI_TONE["calm"])}
    try:
        resp = await _tts_client.post(f"{base}/audio/speech", headers={"Authorization": f"Bearer {key}"}, json=payload)
        if resp.status_code in (400, 404, 422) and settings.tts_model != "tts-1":
            payload.update(model="tts-1"); payload.pop("instructions", None)
            resp = await _tts_client.post(f"{base}/audio/speech", headers={"Authorization": f"Bearer {key}"}, json=payload)
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPError as exc:
        logger.warning("OpenAI TTS недоступен: %s", type(exc).__name__)
        _openai_down_until = _t.time() + 600
        return None


async def _edge_tts(text: str, voice: str, tone: str) -> bytes | None:
    try:
        import edge_tts
    except ImportError:
        return None
    rate, pitch = _EDGE_TONE.get(tone, _EDGE_TONE["calm"])
    name = "ru-RU-DmitryNeural" if voice in _MALE_VOICES else "ru-RU-SvetlanaNeural"
    try:
        async def run() -> bytes:
            out = bytearray()
            async for chunk in edge_tts.Communicate(text, name, rate=rate, pitch=pitch).stream():
                if chunk["type"] == "audio":
                    out += chunk["data"]
            return bytes(out)
        data = await asyncio.wait_for(run(), timeout=20)
        return data or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Edge TTS недоступен: %s", type(exc).__name__)
        return None


async def _synthesize(text: str, voice: str, tone: str) -> bytes:
    data = await _openai_tts(text, voice, tone) or await _edge_tts(text, voice, tone)
    if not data:
        raise HTTPException(503, {"code": "tts_unavailable", "message": "Голос сейчас недоступен."})
    return data


@app.post("/api/tts")
async def tts(body: TTSBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> Response:
    voice = body.voice or UserSettings(db, user.id).get("voice_name")
    if voice not in SETTING_CHOICES["voice_name"]:
        voice = "coral"
    data = await _synthesize(body.text, voice, (body.tone or "calm").lower())
    return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/tts/greeting")
async def tts_greeting(kind: str = "welcome") -> Response:
    """Публичная озвучка фиксированных фраз для экрана входа (произвольный текст синтезировать нельзя)."""
    if kind not in _GREETINGS:
        raise HTTPException(404, "Нет такой фразы")
    if kind not in _greeting_cache:
        _greeting_cache[kind] = await _synthesize(_GREETINGS[kind], "coral", "joy")
    return Response(_greeting_cache[kind], media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/see")
async def see(body: SeeBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Woody смотрит на кадр с камеры только по прямой просьбе пользователя; кадр нигде не сохраняется."""
    if not body.image.startswith("data:image/"):
        raise HTTPException(422, "Нужно изображение")
    if not UserSettings(db, user.id).get("camera_enabled"):
        raise HTTPException(403, {"code": "camera_disabled", "message": "Камера отключена в настройках."})
    messages = [
        {"role": "system", "content": "Ты — Woody. Опиши по-русски, что видишь на кадре, живо и коротко (1–2 предложения)."},
        {"role": "user", "content": [{"type": "text", "text": body.question}, {"type": "image_url", "image_url": {"url": body.image}}]},
    ]
    try:
        reply, _ = await router.complete(messages, task="vision")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vision недоступен: %s", exc)
        return {"reply": "Сейчас я не могу разглядеть кадр: модель с поддержкой изображений недоступна."}
    return {"reply": reply}


# ------------------------------------------------------------------ transfer
def _public_base_url(request: Request) -> str:
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    host = (urlparse(base).hostname or "").lower()
    if settings.app_env == "production" and host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise HTTPException(
            503, {"code": "no_public_url", "message": "Не настроен публичный адрес Woody (PUBLIC_BASE_URL)."}
        )
    return base


@app.post("/api/transfer/create")
def transfer_create(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    if not UserSettings(db, user.id).get("transfer_enabled"):
        raise HTTPException(403, {"code": "transfer_disabled", "message": "Передача Woody отключена в настройках."})
    base = _public_base_url(request)
    token, expires = TransferService(db).create(user.id)
    payload = PROVIDERS["qr"].build_payload(token, base)
    return {**payload, "expires_at": expires.isoformat() + "Z", "ttl_seconds": 300}


def _transfer_http_error(exc: TransferError) -> HTTPException:
    status = 400 if exc.code == "invalid" else 410
    return HTTPException(status, {"code": exc.code, "message": exc.message})


@app.post("/api/transfer/check")
def transfer_check(body: TokenBody, db: Session = Depends(get_db)) -> dict:
    """Не расходует токен: нужно, чтобы показать «ссылка использована/истекла» до ввода имени."""
    try:
        TransferService(db).check(body.token)
    except TransferError as exc:
        raise _transfer_http_error(exc) from exc
    return {"valid": True}


@app.post("/api/transfer/redeem")
def transfer_redeem(body: RedeemBody, db: Session = Depends(get_db)) -> dict:
    try:
        user, secret = TransferService(db).redeem(body.token, body.name)
    except TransferError as exc:
        raise _transfer_http_error(exc) from exc
    return {"name": user.name, "greeting": f"Приятно познакомиться, {user.name}.", "session": secret}


# ------------------------------------------------------------------ frontend
@app.get("/join/{token}", include_in_schema=False)
def join_page(token: str):
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "Frontend не найден")
    return FileResponse(index)


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
