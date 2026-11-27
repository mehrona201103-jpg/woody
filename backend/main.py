from __future__ import annotations

import hmac
import json
import logging
import re
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, File, Form
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
from backend.database.models import Feedback, PaymentRequest, Referral, GroupRead, PushSubscription, TelegramBinding, UserAdminState
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
              or path.startswith("/api/auth/") or path == "/api/tts/greeting" or path == "/api/crypto/webhook")
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


def require_user(user: User | None = Depends(get_user_optional), db: Session = Depends(get_db)) -> User:
    if user is None:
        raise HTTPException(403, {"code": "not_registered", "message": "Сначала нужно познакомиться."})
    state = db.scalar(select(UserAdminState).where(UserAdminState.user_id == user.id))
    if state and state.blocked:
        raise HTTPException(403, {"code": "account_blocked", "message": "Доступ к аккаунту временно ограничен администратором."})
    return user


class UserBody(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class PaymentBody(BaseModel):
    plan: str = Field(min_length=3, max_length=20)
    period: str = Field(default="month", max_length=20)
    receipt: str = Field(default="", max_length=6_000_000)
    currency: str = Field(default="TJS", max_length=8)
    method: str = Field(default="manual", max_length=20)

class ReferralAcceptBody(BaseModel):
    accepted: bool

class ReferralCodeBody(BaseModel):
    code: str = Field(min_length=3, max_length=24)

class PushSubscriptionBody(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2000)
    keys: dict[str, str]


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=60000)
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
    currency: str | None = None


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
    referral_code: str | None = Field(default=None, max_length=24)


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class GoogleBody(BaseModel):
    access_token: str = Field(min_length=10, max_length=4000)
    referral_code: str | None = Field(default=None, max_length=24)


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=50)


def _auth_http(exc: AuthError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": exc.message})


@app.get("/api/auth/config")
def auth_config() -> dict:
    return {"google_client_id": settings.google_client_id or None}


@app.post("/api/auth/register")
def auth_register(body: RegisterBody, db: Session = Depends(get_db)) -> dict:
    ref_owner = None
    if body.referral_code:
        ref_owner = db.scalar(select(User).where(func.lower(User.referral_code) == body.referral_code.strip().lower()))
        if ref_owner is None:
            raise HTTPException(422, {"code": "bad_referral", "message": "Реферальный код не найден."})
    try:
        user, secret = register_email(db, body.email, body.password, body.name)
    except AuthError as exc:
        raise _auth_http(exc) from exc
    if ref_owner and ref_owner.id != user.id:
        user.referred_by = ref_owner.id
        db.add(Referral(owner_id=ref_owner.id, invitee_id=user.id))
        db.commit()
        _apply_referral_reward(db, ref_owner.id)
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
    owner = None
    if body.referral_code:
        owner = db.scalar(select(User).where(func.lower(User.referral_code) == body.referral_code.strip().lower()))
        if owner is None:
            raise HTTPException(422, {"code":"bad_referral", "message":"Реферальный код не найден."})
    user, secret, created = login_google(db, profile["sub"], profile.get("email", ""), name, profile.get("picture"))
    if created and owner and owner.id != user.id:
        user.referred_by = owner.id; db.add(Referral(owner_id=owner.id, invitee_id=user.id)); db.commit(); _apply_referral_reward(db, owner.id)
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
    return {"status": "ok", "service": "woody"}

@app.get("/api/version")
def app_version() -> dict[str, str]:
    return {"version": "0.7.5", "build": "2026-11-27a"}


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
    rows = admin_panel.list_users(db)
    for row in rows:
        state = db.scalar(select(UserAdminState).where(UserAdminState.user_id == row["id"]))
        tg = db.scalar(select(TelegramBinding).where(TelegramBinding.user_id == row["id"]))
        row.update({"blocked":bool(state.blocked) if state else False,"plan_access":bool(state.plan_access) if state else True,
                    "telegram_username":tg.username if tg else None,"telegram_phone":tg.phone if tg else None})
    return rows


@app.get("/api/admin/panel/users/{user_id}")
def admin_panel_user_detail(user_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    _admin_auth(request)
    u = db.get(User, user_id)
    if not u: raise HTTPException(404, "Пользователь не найден")
    state = db.scalar(select(UserAdminState).where(UserAdminState.user_id == user_id))
    tg = db.scalar(select(TelegramBinding).where(TelegramBinding.user_id == user_id))
    return {"id":u.id,"name":u.name,"email":u.email,"plan":u.plan,"plan_until":u.plan_until.isoformat() if u.plan_until else None,
            "blocked":bool(state.blocked) if state else False,"plan_access":bool(state.plan_access) if state else True,
            "telegram_username":tg.username if tg else None,"telegram_phone":tg.phone if tg else None,"telegram_id":tg.telegram_id if tg else None}

@app.post("/api/admin/panel/users/{user_id}/action")
async def admin_panel_user_action(user_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    _admin_auth(request); body = await request.json(); action = str(body.get("action") or "")
    u = db.get(User, user_id)
    if not u: raise HTTPException(404, "Пользователь не найден")
    state = db.scalar(select(UserAdminState).where(UserAdminState.user_id == user_id))
    if state is None: state = UserAdminState(user_id=user_id); db.add(state)
    if action == "block": state.blocked = True
    elif action == "unblock": state.blocked = False
    elif action == "revoke_plan": state.plan_access = False; u.plan = "free"; u.plan_until = None
    elif action == "grant_plan":
        state.plan_access = True
        plan = str(body.get("plan") or "pro")
        if plan not in ("pro", "ultra"): raise HTTPException(400, "Неверный тариф")
        from backend.core.billing import activate_plan
        activate_plan(db, u, plan, str(body.get("period") or "month"))
    elif action == "message":
        tg = db.scalar(select(TelegramBinding).where(TelegramBinding.user_id == user_id))
        msg = str(body.get("text") or "").strip()
        if not tg: raise HTTPException(409, "Telegram не привязан")
        if not msg or len(msg) > 4000: raise HTTPException(400, "Сообщение пустое или слишком длинное")
        token = bot_token()
        if not token: raise HTTPException(503, "Telegram-бот не настроен")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id":tg.telegram_id,"text":msg})
            if r.status_code >= 400: raise HTTPException(502, "Telegram не доставил сообщение")
    else: raise HTTPException(400, "Неизвестное действие")
    db.commit()
    return {"ok":True,"blocked":state.blocked,"plan_access":state.plan_access,"plan":u.plan}

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



# ------------------------------------------------------------------ привязка Telegram пользователя
@app.post("/api/telegram/link")
async def telegram_link_create(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    import secrets
    token = bot_token()
    if not token: raise HTTPException(503, {"code":"telegram_unconfigured","message":"Telegram-бот пока не настроен."})
    me = await _telegram_bot_info(token)
    username = me.get("username")
    if not username: raise HTTPException(503, "У бота не задан username")
    old = db.scalar(select(TelegramBinding).where(TelegramBinding.user_id == user.id))
    if old and old.telegram_id: return {"linked":True,"username":old.username}
    code = secrets.token_urlsafe(24)
    row = old or TelegramBinding(user_id=user.id, telegram_id="pending-"+str(user.id))
    row.link_code = code; row.expires_at = datetime.utcnow() + timedelta(minutes=10)
    db.add(row); db.commit()
    return {"linked":False,"url":f"https://t.me/{username}?start=link_{code}","expires_in":600}

@app.get("/api/telegram/link")
def telegram_link_status(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    row = db.scalar(select(TelegramBinding).where(TelegramBinding.user_id == user.id))
    return {"linked":bool(row and not row.telegram_id.startswith("pending-")),"username":row.username if row else None,"phone":row.phone if row else None}

async def _telegram_bot_info(token: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.telegram.org/bot{token}/getMe"); r.raise_for_status(); data=r.json()
    if not data.get("ok"): raise RuntimeError("Telegram getMe failed")
    return data.get("result") or {}

# ------------------------------------------------------------------ тарифы / оплата
@app.get("/api/plans")
def api_plans(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    plan = effective_plan(user)
    currency = (UserSettings(db, user.id).get("currency") or "TJS").upper()
    price_map = {
        "TJS": {"pro": {"month": "9.99", "year": "29.99"}, "ultra": {"month": "19.99", "year": "39.99"}},
        "RUB": {"pro": {"month": "199", "year": "399"}, "ultra": {"month": "399", "year": "599"}},
        "USD": {"pro": {"month": "0.99", "year": "1.99"}, "ultra": {"month": "1.99", "year": "3.99"}},
    }
    return {"plans": PLANS, "prices": price_map.get(currency, price_map["TJS"]), "currency": currency,
        "currencies": ["TJS", "RUB", "USD"], "reset_hours": RESET_HOURS,
        "period": getattr(user, "plan_period", None) or "month", "current": plan,
        "plan_until": user.plan_until.isoformat() if user.plan_until else None,
        "used": int(user.request_count or 0), "remaining": remaining_requests(user), "free_limit": FREE_LIMIT,
        "payment": {"requisites": (admin_panel.load_settings().get("payment_requisites") or os.getenv("PAYMENT_REQUISITES") or "").strip(),
            "holder": (admin_panel.load_settings().get("payment_holder") or os.getenv("PAYMENT_HOLDER") or "").strip(),
            "telegram_username": os.getenv("TELEGRAM_PAYMENT_USERNAME", "").strip().lstrip("@"),
            "note": "После ручной оплаты приложи скриншот чека. Заявку подтвердит администратор."}}


@app.post("/api/payments")
async def api_payment(body: PaymentBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    import os
    plan, period, currency = body.plan.lower().strip(), ("year" if body.period == "year" else "month"), body.currency.upper()
    if plan not in ("pro", "ultra") or currency not in ("TJS", "RUB", "USD"):
        raise HTTPException(422, {"code": "bad_payment", "message": "Неверный тариф или валюта."})
    prices = {"TJS": {"pro": {"month":"9.99","year":"29.99"},"ultra":{"month":"19.99","year":"39.99"}},
              "RUB": {"pro": {"month":"199","year":"399"},"ultra":{"month":"399","year":"599"}},
              "USD": {"pro": {"month":"0.99","year":"1.99"},"ultra":{"month":"1.99","year":"3.99"}}}
    amount = prices[currency][plan][period]
    # Telegram Stars — автоматическая оплата для RUB. Суммы задаются в целых XTR
    # через переменные окружения; валюта XTR не конвертируется из RUB автоматически.
    if currency == "RUB":
        token = (os.getenv("TELEGRAM_ADMIN_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise HTTPException(503, {"code":"telegram_stars_unconfigured", "message":"Добавь TELEGRAM_ADMIN_BOT_TOKEN в настройки сервера."})
        env_key = f"TELEGRAM_STARS_{plan.upper()}_{period.upper()}"
        try:
            stars = int(os.getenv(env_key, "0"))
        except ValueError:
            stars = 0
        if stars <= 0:
            raise HTTPException(503, {"code":"telegram_stars_price_unconfigured", "message":f"Администратору нужно задать {env_key} — цену в целых Telegram Stars (XTR)."})
        req = PaymentRequest(user_id=user.id, plan=plan, period=period, amount=str(stars), currency="XTR", method="telegram_stars", status="pending")
        db.add(req); db.commit(); db.refresh(req)
        invoice_payload = f"woody:{req.id}:{user.id}"
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(f"https://api.telegram.org/bot{token}/createInvoiceLink", json={
                "title": f"Woody {plan.upper()} — {period}",
                "description": f"Подписка Woody {plan.upper()} на {'год' if period == 'year' else 'месяц'}",
                "payload": invoice_payload,
                "provider_token": "",
                "currency": "XTR",
                "prices": [{"label": f"Woody {plan.upper()}", "amount": stars}],
            })
            result = response.json()
        if not result.get("ok") or not result.get("result"):
            req.status = "failed"; db.add(req); db.commit()
            logger.error("Telegram Stars invoice link failed: %s", result)
            raise HTTPException(502, {"code":"telegram_stars_invoice", "message":"Telegram не смог создать счёт Stars. Попробуй позже."})
        req.external_id = invoice_payload; db.add(req); db.commit()
        return {"method":"telegram_stars", "url":result["result"], "request_id":req.id, "stars":stars, "currency":"XTR", "message":"Счёт Telegram Stars создан. После подтверждения Telegram тариф активируется автоматически."}
    if currency == "USD":
        token = os.getenv("CRYPTO_PAY_API_TOKEN", "").strip()
        if not token:
            raise HTTPException(503, {"code":"crypto_unconfigured", "message":"Оплата в USD пока не подключена: администратору нужно добавить CRYPTO_PAY_API_TOKEN."})
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post("https://pay.crypt.bot/api/createInvoice",
                headers={"Crypto-Pay-API-Token": token},
                json={"asset":"USDT", "amount":amount, "description":f"Woody {plan.upper()} {period} user#{user.id}",
                      "payload":json.dumps({"user_id":user.id,"plan":plan,"period":period}), "allow_comments":False, "allow_anonymous":False})
            payload = response.json()
        if not payload.get("ok") or not (payload.get("result") or {}).get("pay_url"):
            logger.error("Crypto Pay invoice error: %s", payload)
            raise HTTPException(502, {"code":"crypto_invoice", "message":"Crypto Bot не смог создать счёт. Попробуй позже."})
        inv = payload["result"]
        req = PaymentRequest(user_id=user.id, plan=plan, period=period, amount=amount, currency="USD", method="crypto", external_id=str(inv.get("invoice_id")), status="pending")
        db.add(req); db.commit()
        return {"method":"crypto", "url":inv["pay_url"], "invoice_id":inv.get("invoice_id"), "message":"Счёт создан в Crypto Bot. После подтверждения оплаты тариф активируется автоматически через webhook."}
    if not body.receipt or not body.receipt.startswith("data:image") or len(body.receipt) < 200:
        raise HTTPException(422, {"code":"need_receipt", "message":"Нужен скриншот подтверждения оплаты."})
    username = os.getenv("TELEGRAM_PAYMENT_USERNAME", "").strip().lstrip("@")
    if currency == "RUB" and not username:
        raise HTTPException(503, {"code":"telegram_payment_unconfigured", "message":"Добавь TELEGRAM_PAYMENT_USERNAME в настройки сервера, чтобы принимать рубли."})
    req = PaymentRequest(user_id=user.id, plan=plan, period=period, amount=amount, currency=currency,
        method="telegram_manual" if currency == "RUB" else "manual", receipt_data=body.receipt[:5_000_000], status="pending")
    db.add(req); db.commit(); db.refresh(req)
    sent = await notify_payment(db, req, user)
    result = {"method":req.method, "request_id":req.id, "message":"Заявка отправлена администратору. После проверки тариф появится в аккаунте."}
    if currency == "RUB": result["telegram_url"] = f"https://t.me/{username}?text=" + quote(f"Оплата Woody {plan.upper()} {period}: {amount} RUB. Заявка #{req.id}. Скрин оплаты прикреплён в приложении.")
    if not sent: result["message"] += " Внимание: уведомление админу не доставлено — проверь настройки Telegram-бота."
    return result


@app.post("/api/crypto/webhook")
async def crypto_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    import hashlib, hmac, os
    secret = os.getenv("CRYPTO_PAY_API_TOKEN", "").strip()
    body = await request.body()
    signature = request.headers.get("crypto-pay-api-signature", "")
    if not secret or not signature:
        raise HTTPException(403, "forbidden")
    key = hashlib.sha256(secret.encode()).digest()
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature): raise HTTPException(403, "bad signature")
    update = json.loads(body or b"{}")
    invoice = update.get("payload", {})
    if update.get("update_type") == "invoice_paid" and invoice.get("status") == "paid":
        req = db.scalar(select(PaymentRequest).where(PaymentRequest.external_id == str(invoice.get("invoice_id"))))
        if req and req.status == "pending":
            req.status = "approved"; req.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
            from backend.core.billing import activate_plan
            target = db.get(User, req.user_id)
            if target: activate_plan(db, target, req.plan, req.period)
            db.commit()
    return {"ok": True}


@app.post("/api/feedback")
async def api_feedback(body: FeedbackBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    row = Feedback(user_id=user.id, text=body.text.strip())
    db.add(row); db.commit(); db.refresh(row)
    notified = False
    try: notified = await notify_feedback(row, user)
    except Exception as exc: logger.warning("feedback notify failed: %s", exc)
    return {"ok": True, "notified": notified, "message": "Спасибо! Пожелание сохранено."}


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


async def _telegram_send(token: str, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    if not token or not chat_id: return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id":chat_id,"text":text, **({"reply_markup":reply_markup} if reply_markup else {})})


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
        # Telegram Stars: pre-checkout must be answered quickly; successful_payment
        # is the authoritative confirmation (never trust a client redirect).
        pre = data.get("pre_checkout_query") or {}
        if pre:
            payload = str(pre.get("invoice_payload") or "")
            row = db.scalar(select(PaymentRequest).where(PaymentRequest.external_id == payload, PaymentRequest.method == "telegram_stars"))
            valid = bool(row and row.status == "pending" and int(pre.get("total_amount") or 0) == int(row.amount) and pre.get("currency") == "XTR")
            token = bot_token()
            if token:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(f"https://api.telegram.org/bot{token}/answerPreCheckoutQuery", json={"pre_checkout_query_id":pre.get("id"), "ok":valid, **({} if valid else {"error_message":"Счёт недействителен или уже обработан."})})
        msg = data.get("message") or {}
        paid = msg.get("successful_payment") or {}
        if paid:
            payload = str(paid.get("invoice_payload") or "")
            row = db.scalar(select(PaymentRequest).where(PaymentRequest.external_id == payload, PaymentRequest.method == "telegram_stars"))
            if row and row.status == "pending" and paid.get("currency") == "XTR" and int(paid.get("total_amount") or 0) == int(row.amount):
                buyer = db.get(User, row.user_id)
                if buyer:
                    from backend.core.billing import activate_plan
                    row.status = "approved"
                    row.external_id = str(paid.get("telegram_payment_charge_id") or payload)
                    row.decided_at = datetime.utcnow()
                    db.add(row); db.commit()
                    activate_plan(db, buyer, row.plan, row.period)
        if not paid:
            # Персональная привязка через одноразовый deep-link. Telegram ID берём только из update,
            # номер — только из contact, отправленного самим владельцем аккаунта.
            message = data.get("message") or {}
            tg_user = message.get("from") or {}
            chat_id = str((message.get("chat") or {}).get("id") or "")
            text_in = str(message.get("text") or "")
            if text_in.startswith("/start link_") and tg_user.get("id"):
                code = text_in.split("link_",1)[1].strip()
                link = db.scalar(select(TelegramBinding).where(TelegramBinding.link_code == code))
                if link and link.expires_at and link.expires_at >= datetime.utcnow():
                    link.telegram_id=str(tg_user["id"]); link.username=tg_user.get("username"); link.first_name=tg_user.get("first_name"); link.link_code=None; link.expires_at=None
                    db.commit()
                    await _telegram_send(token=bot_token(), chat_id=chat_id, text="Telegram успешно привязан к Woody! Если хочешь, отправь свой номер кнопкой ниже — он будет виден администратору.", reply_markup={"keyboard":[[{"text":"Поделиться номером телефона","request_contact":True}]],"resize_keyboard":True,"one_time_keyboard":True})
                else:
                    await _telegram_send(token=bot_token(), chat_id=chat_id, text="Ссылка устарела. Создай новую привязку в настройках Woody.")
                return {"ok":True}
            if message.get("contact") and tg_user.get("id") and str(message["contact"].get("user_id")) == str(tg_user.get("id")):
                link = db.scalar(select(TelegramBinding).where(TelegramBinding.telegram_id == str(tg_user["id"])))
                if link:
                    link.phone=message["contact"].get("phone_number"); db.commit()
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
    command = body.text.strip().lower()
    if not body.image and re.search(r"(?:начинай|начни|запусти|начать)\s+(?:само\s*)?обучени", command):
        started = LEARNER.start(router, SessionLocal, settings.self_learn_max)
        if not started and not LEARNER.active:
            return {"reply": "Не могу начать самообучение: на сервере пока нет подключённого AI-ключа или лимит недоступен.", "tone":"calm", "learning":LEARNER.status(), "chat_id": body.chat_id}
        return {"reply": "Начинаю самообучение. Буду сохранять полученные знания в мозге Woody. Напиши «хватит обучения», чтобы остановить.", "tone":"calm", "learning":LEARNER.status(), "chat_id": body.chat_id}
    if not body.image and re.search(r"(?:хватит|останови|остановить|прекрати)\s+(?:само\s*)?обучени", command):
        LEARNER.stop()
        return {"reply": "Хорошо, остановил самообучение. Всё изученное сохранено.", "tone":"calm", "learning":LEARNER.status(), "chat_id": body.chat_id}
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


@app.post("/api/files/analyze")
async def analyze_file(file: UploadFile = File(...), question: str = Form(default="Изучи этот файл и объясни его содержимое."),
                       user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Извлекает текст из документов, таблиц и исходников и передаёт Woody для анализа."""
    import io
    name = (file.filename or "document").replace("\\", "/").split("/")[-1][:180]
    raw = await file.read(12 * 1024 * 1024 + 1)
    if len(raw) > 12 * 1024 * 1024: raise HTTPException(413, "Файл больше 12 МБ")
    ext = Path(name).suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw)); content = "\n".join(page.extract_text() or "" for page in reader.pages[:80])
        elif ext == ".docx":
            from docx import Document
            doc = Document(io.BytesIO(raw)); content = "\n".join(p.text for p in doc.paragraphs)
            for table in doc.tables: content += "\n" + "\n".join(" | ".join(c.text for c in row.cells) for row in table.rows)
        elif ext in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            content = "\n".join(f"[{ws.title}]\n" + "\n".join(" | ".join(str(v or "") for v in row) for row in ws.iter_rows(values_only=True)) for ws in wb.worksheets)
        elif ext == ".doc":
            raise ValueError("Формат DOC устарел. Сохрани документ как DOCX или PDF.")
        else:
            content = raw.decode("utf-8-sig", errors="replace")
    except ImportError as exc:
        raise HTTPException(503, "На сервере не установлен модуль для чтения этого формата.") from exc
    except Exception as exc:
        raise HTTPException(422, f"Не удалось прочитать файл: {exc}") from exc
    if not content.strip(): raise HTTPException(422, "В файле не найден читаемый текст. Для сканированных PDF нужен OCR.")
    prompt = f"Проверь файл «{name}» ({len(raw)} байт). Запрос пользователя: {question.strip()[:500]}\n\nСодержимое файла:\n{content[:55000]}"
    return await _chat(ChatBody(text=prompt), user, db, "text")


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


@app.get("/api/push/config")
def push_config(user: User = Depends(require_user)) -> dict:
    return {"public_key": os.getenv("VAPID_PUBLIC_KEY", "").strip()}

@app.post("/api/push/subscribe")
def push_subscribe(body: PushSubscriptionBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    parsed_endpoint = urlparse(body.endpoint)
    host = (parsed_endpoint.hostname or "").lower()
    allowed_hosts = ("fcm.googleapis.com", "push.services.mozilla.com", "web.push.apple.com", "notify.windows.com")
    if parsed_endpoint.scheme != "https" or not any(host == suffix or host.endswith("." + suffix) for suffix in allowed_hosts):
        raise HTTPException(422, "Недопустимый адрес push-сервиса")
    p256dh, auth = body.keys.get("p256dh", ""), body.keys.get("auth", "")
    if not p256dh or not auth: raise HTTPException(422, "Некорректная подписка push")
    row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if row is None: row = PushSubscription(user_id=user.id, endpoint=body.endpoint, p256dh=p256dh, auth=auth)
    else: row.user_id, row.p256dh, row.auth = user.id, p256dh, auth
    db.add(row); db.commit()
    return {"ok":True}

@app.delete("/api/push/subscribe")
def push_unsubscribe(body: PushSubscriptionBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    db.execute(delete(PushSubscription).where(PushSubscription.user_id == user.id, PushSubscription.endpoint == body.endpoint)); db.commit()
    return {"ok":True}

async def _send_group_push(db: Session, group_id: int, sender_id: int | None, sender_name: str, message: str) -> None:
    private_key = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    if not private_key: return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush не установлен; push-уведомления отключены")
        return
    members = db.scalars(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id != (sender_id or -1))).all()
    claims = {"sub": os.getenv("VAPID_CLAIMS_EMAIL", "mailto:admin@example.com")}
    payload = json.dumps({"title":"Новое сообщение · Woody", "body":f"{sender_name}: {message[:140]}", "url":"/", "tag":f"group-{group_id}"}, ensure_ascii=False)
    for member in members:
        subscriptions = db.scalars(select(PushSubscription).where(PushSubscription.user_id == member.user_id)).all()
        for sub in subscriptions:
            info = {"endpoint":sub.endpoint,"keys":{"p256dh":sub.p256dh,"auth":sub.auth}}
            try:
                await asyncio.to_thread(webpush, subscription_info=info, data=payload, vapid_private_key=private_key, vapid_claims=claims)
            except WebPushException as exc:
                if getattr(exc.response, "status_code", None) in (404, 410): db.delete(sub); db.commit()
                else: logger.warning("Web push failed: %s", exc)
            except Exception as exc: logger.warning("Web push error: %s", exc)

@app.get("/api/notifications")
def notifications(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    memberships = db.scalars(select(GroupMember).where(GroupMember.user_id == user.id)).all()
    items = []
    total = 0
    for member in memberships:
        group = db.get(Group, member.group_id)
        read = db.scalar(select(GroupRead).where(GroupRead.group_id == member.group_id, GroupRead.user_id == user.id))
        last_read = read.last_read_id if read else 0
        count = db.scalar(select(func.count()).select_from(GroupMessage).where(
            GroupMessage.group_id == member.group_id, GroupMessage.id > last_read,
            (GroupMessage.user_id.is_(None)) | (GroupMessage.user_id != user.id))) or 0
        if count:
            items.append({"group_id":member.group_id,"name":group.name if group else "Группа","count":count})
            total += count
    return {"unread":total,"groups":items}


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


@app.get("/api/users/suggest")
def suggest_users(q: str = "", user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    term = q.strip().lower()
    if len(term) < 1: return []
    rows = db.scalars(select(User).where(User.id != user.id, func.lower(User.name).like(term + "%")).order_by(User.name).limit(12)).all()
    return [{"id":u.id,"name":u.name,"picture":u.picture} for u in rows]


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
    rows = db.scalars(select(GroupMessage).where(GroupMessage.group_id == group_id, GroupMessage.id > after).order_by(GroupMessage.id).limit(200)).all()
    latest = max([m.id for m in rows], default=after)
    read = db.scalar(select(GroupRead).where(GroupRead.group_id == group_id, GroupRead.user_id == user.id))
    if read is None:
        read = GroupRead(group_id=group_id, user_id=user.id, last_read_id=latest); db.add(read)
    else: read.last_read_id = max(read.last_read_id, latest)
    db.commit()
    return [_msg_payload(m) for m in rows]


@app.post("/api/groups/{group_id}/messages")
async def post_group_message(group_id: int, body: GroupMsgBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    """Сообщение участника + ответ Woody. Woody общается со всеми одинаково и пользуется общим мозгом."""
    group = _member(db, group_id, user)
    mine = GroupMessage(group_id=group_id, user_id=user.id, text=body.text.strip())
    db.add(mine)
    db.commit()
    await _send_group_push(db, group_id, user.id, user.name, body.text.strip())
    # Woody молчит, пока его явно не позвали по имени.
    if not re.search(r"(?i)(?<!\w)(?:вуди|woody)(?!\w)", body.text):
        return [_msg_payload(mine)]
    clean_text = re.sub(r"(?i)(?<!\w)(?:вуди|woody)[,!:;\s]*", "", body.text).strip() or body.text
    names = {u["id"]: u["name"] for u in _members_payload(db, group_id)}
    recent = list(reversed(db.scalars(select(GroupMessage).where(GroupMessage.group_id == group_id).order_by(GroupMessage.id.desc()).limit(14)).all()))
    convo = [{"role": "assistant" if m.user_id is None else "user",
              "content": m.text if m.user_id is None else f"{names.get(m.user_id, 'Кто-то')}: {m.text}"} for m in recent]
    system = (f"Ты — Woody, участник группового чата «{group.name}». В чате люди: {', '.join(names.values())}. "
              "Отвечай по-русски коротко и дружелюбно, обращайся к тому, кто написал, по имени. Ко всем относись одинаково. "
              "Код пиши в блоках ```язык.")
    mind = Mind(db)
    known = mind.recall(clean_text, 0.9)
    if known:
        reply = known[0]
    else:
        try:
            reply, _ = await router.complete([{"role": "system", "content": system}, *convo])
            reply, _tone = split_tone(reply)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Woody в группе: AI недоступен: %s", exc)
            fb = mind.recall(clean_text, 0.45)
            reply = fb[0] if fb else "Сейчас я не могу ответить — у моего AI-ключа проблема. Попробуйте позже."
    answer = GroupMessage(group_id=group_id, user_id=None, text=reply)
    db.add(answer)
    db.commit()
    await _send_group_push(db, group_id, None, "Woody", reply)
    return [_msg_payload(mine), _msg_payload(answer)]


# ------------------------------------------------------------------ referrals
import secrets as _secrets

def _ensure_ref_code(user: User, db: Session) -> str:
    if not user.referral_code:
        while True:
            code = _secrets.token_urlsafe(7).replace("-", "").replace("_", "")[:10].upper()
            if not db.scalar(select(User.id).where(User.referral_code == code)):
                user.referral_code = code; db.commit(); break
    return user.referral_code

def _apply_referral_reward(db: Session, owner_id: int) -> None:
    owner = db.get(User, owner_id)
    if not owner: return
    refs = db.scalars(select(Referral).where(Referral.owner_id == owner_id).order_by(Referral.id)).all()
    count = len(refs)
    milestones = [(30, "ultra_14d"), (20, "pro_30d"), (10, "pro_7d")]
    for threshold, reward in milestones:
        if count >= threshold and not any(r.reward == reward for r in refs):
            days, plan = (14, "ultra") if reward == "ultra_14d" else ((30, "pro") if reward == "pro_30d" else (7, "pro"))
            owner.plan = plan; owner.plan_period = "month"; owner.plan_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
            if refs:
                refs[-1].reward = reward
                db.add(refs[-1])
            db.commit(); return

@app.get("/api/referrals")
def referral_info(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    code = _ensure_ref_code(user, db) if user.referral_terms_accepted else None
    rows = db.scalars(select(Referral).where(Referral.owner_id == user.id, Referral.invitee_id != user.id)).all()
    base_url = (settings.public_base_url or str(request.base_url)).rstrip("/")
    return {"code":code,"link":f"{base_url}/?ref={code}" if code else None,"count":len(rows),"accepted":bool(user.referral_terms_accepted),
        "rewards":[{"at":10,"plan":"Pro","duration":"7 дней"},{"at":20,"plan":"Pro","duration":"30 дней"},{"at":30,"plan":"Ultra","duration":"14 дней"}]}

@app.post("/api/referrals/accept")
def referral_accept(request: Request, body: ReferralAcceptBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    if not body.accepted: raise HTTPException(400, "Для создания ссылки нужно принять условия программы")
    user.referral_terms_accepted = True; code = _ensure_ref_code(user, db); db.commit()
    return referral_info(request, user, db)

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


@app.get("/api/settings")
def get_settings_endpoint(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return UserSettings(db, user.id).all()


@app.put("/api/settings")
def update_settings(body: SettingsBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    us = UserSettings(db, user.id)
    for name in SETTING_DEFAULTS:
        value = getattr(body, name)
        if value is not None:
            if name == "currency" and str(value).upper() not in ("TJS", "RUB", "USD"):
                raise HTTPException(422, "Валюта должна быть TJS, RUB или USD")
            try:
                us.set(name, str(value).upper() if name == "currency" else value)
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


# ------------------------------------------------------------------ диктант (Pro): запись только по явному нажатию и отправка после остановки
@app.post("/api/dictation/transcribe")
async def dictation_transcribe(file: UploadFile = File(...), user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    if effective_plan(user) not in ("pro", "ultra"):
        raise HTTPException(403, {"code":"pro_required","message":"Распознавание диктанта доступно только в Pro и Ultra."})
    raw = await file.read()
    if not raw or len(raw) > 24 * 1024 * 1024:
        raise HTTPException(413, "Аудио пустое или превышает 24 МБ")
    targets = []
    stt_key = (os.getenv("OPENAI_STT_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if stt_key:
        targets.append(((os.getenv("OPENAI_STT_BASE_URL") or "https://api.openai.com/v1").rstrip("/"), stt_key, os.getenv("WOODY_STT_MODEL", "whisper-1")))
    for provider in router.providers:
        cfg = getattr(provider, "cfg", None)
        if cfg and cfg.api_key and cfg.base_url:
            if not any(t[0] == cfg.base_url and t[1] == cfg.api_key for t in targets):
                targets.append((cfg.base_url, cfg.api_key, cfg.model))
    transcript = None; last_error = None
    for base, key, model in targets:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(f"{base}/audio/transcriptions", headers={"Authorization":f"Bearer {key}"},
                    data={"model":os.getenv("WOODY_STT_MODEL", "whisper-1"),"response_format":"json"},
                    files={"file":(file.filename or "dictation.webm", raw, file.content_type or "audio/webm")})
            response.raise_for_status(); transcript = str(response.json().get("text") or "").strip()
            if transcript: break
        except Exception as exc: last_error = type(exc).__name__; logger.warning("dictation STT provider failed: %s", last_error)
    if not transcript:
        raise HTTPException(503, {"code":"stt_unavailable","message":"Сервис распознавания аудио не настроен или недоступен. Нужен совместимый /audio/transcriptions endpoint."})
    prompt = [{"role":"system","content":"Ты редактор диктанта. Исправь только орфографию, пунктуацию и очевидные ошибки распознавания, сохрани язык и смысл исходного текста. Верни строго JSON: {\"corrected\":\"...\",\"changes\":[{\"before\":\"...\",\"after\":\"...\",\"reason\":\"...\"}],\"language\":\"...\"}. Не переводи текст."}, {"role":"user","content":transcript}]
    try:
        fixed, _ = await router.complete(prompt, task="conversation", temperature=0.1)
        match = re.search(r"\{[\s\S]*\}", fixed); data = json.loads(match.group(0)) if match else {}
    except Exception:
        data = {"corrected":transcript,"changes":[],"language":"auto"}
    return {"original":transcript,"corrected":str(data.get("corrected") or transcript),"changes":data.get("changes") if isinstance(data.get("changes"),list) else [],"language":data.get("language") or "auto"}

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
