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
from sqlalchemy.orm import Session

from backend.actions.device import capabilities
from backend.config import BASE_DIR, get_settings, provider_config_problems, setup_logging
from backend.core.ai_router import AIRouter
from backend.core.brain import PENDING, Brain, load_emotion
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
from backend.database.models import User
from backend.permissions.manager import ACTIONS, Decision, PermissionManager
from backend.transfer.providers import PROVIDERS
from backend.transfer.service import TransferError, TransferService

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("woody.api")
router = AIRouter.from_config(settings.providers)
FRONTEND_DIR = Path(BASE_DIR) / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as session:
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
    yield


app = FastAPI(title="Woody", version="0.3.0", lifespan=lifespan)


@app.middleware("http")
async def require_token(request: Request, call_next):
    path = request.url.path
    # Публичны: health, страницы и проверка/погашение transfer-токена (их защищает сам одноразовый токен).
    public = (path == "/api/health" or path in ("/api/transfer/check", "/api/transfer/redeem")
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


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    source: str = "text"


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
    user, secret, created = login_google(db, profile["sub"], profile.get("email", ""), name)
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
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "woody"}


def _user_payload(db: Session, user: User | None) -> dict:
    if user is None:
        return {"registered": False, "name": None}
    emotion = load_emotion(db, user.id)
    return {
        "registered": True,
        "name": user.name,
        "email": user.email,
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
async def _chat(body: ChatBody, user: User, db: Session, source: str = "text") -> dict:
    return await Brain(db, router).handle(user, body.text.strip(), source)


@app.post("/api/chat")
async def chat(body: ChatBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return await _chat(body, user, db, "text")


@app.post("/api/voice")
async def voice(body: ChatBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Текст, распознанный браузером (STT выполняется на устройстве). Дальше — тот же Woody Core."""
    if not UserSettings(db, user.id).get("microphone_enabled"):
        raise HTTPException(403, {"code": "mic_disabled", "message": "Микрофон отключён в настройках."})
    return await _chat(body, user, db, "voice")


@app.get("/api/history")
def history(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return [{"role": r.role, "text": r.text} for r in MemoryService(db).recent_messages(user.id, 60, include_voice=True)]


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
