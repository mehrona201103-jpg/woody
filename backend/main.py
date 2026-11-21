from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.actions.device import capabilities
from backend.config import BASE_DIR, get_settings, setup_logging
from backend.core.ai_router import AIRouter
from backend.core.brain import PENDING, Brain, load_emotion
from backend.core.identity import IdentityService
from backend.core.learning import LearningService
from backend.core.memory import MemoryService
from backend.core.user_settings import DEFAULTS as SETTING_DEFAULTS
from backend.core.user_settings import UserSettings
from backend.core.users import create_user, find_user_by_secret
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
    if not settings.providers:
        logger.warning("AI-провайдеры не настроены (AI_PROVIDER_1_*). Woody не сможет отвечать через AI.")
    if not settings.access_token and settings.app_env == "production":
        logger.warning("WOODY_ACCESS_TOKEN не задан: приложение открыто для всех, у кого есть URL!")
    yield


app = FastAPI(title="Woody", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def require_token(request: Request, call_next):
    path = request.url.path
    # Публичны: health, страницы и проверка/погашение transfer-токена (их защищает сам одноразовый токен).
    public = path == "/api/health" or path in ("/api/transfer/check", "/api/transfer/redeem")
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


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class RedeemBody(TokenBody):
    name: str = Field(min_length=1, max_length=50)


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
        }
    )
    return payload


# ------------------------------------------------------------------ chat / voice
async def _chat(body: ChatBody, user: User, db: Session) -> dict:
    return await Brain(db, router).handle(user, body.text.strip())


@app.post("/api/chat")
async def chat(body: ChatBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return await _chat(body, user, db)


@app.post("/api/voice")
async def voice(body: ChatBody, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Текст, распознанный браузером (STT выполняется на устройстве). Дальше — тот же Woody Core."""
    if not UserSettings(db, user.id).get("microphone_enabled"):
        raise HTTPException(403, {"code": "mic_disabled", "message": "Микрофон отключён в настройках."})
    return await _chat(body, user, db)


@app.get("/api/history")
def history(user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return [{"role": r.role, "text": r.text} for r in MemoryService(db).recent_messages(user.id, 50)]


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
            us.set(name, value)
    return us.all()


@app.get("/api/providers")
def providers() -> dict:
    return {"providers": router.status(), "configured": bool(router.providers)}


@app.get("/api/background")
def background() -> dict:
    return capabilities()["background"]


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
