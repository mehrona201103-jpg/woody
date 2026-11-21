"""Интеграционные тесты backend. Требуют зависимости: pip install -r requirements.txt"""
import asyncio
import os
import tempfile
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

_tmp = tempfile.mkdtemp()
os.environ["WOODY_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["WOODY_SHARED_DIR"] = os.path.join(_tmp, "shared")
os.environ.pop("WOODY_ACCESS_TOKEN", None)
for k in list(os.environ):
    if k.startswith("AI_PROVIDER_"):
        del os.environ[k]

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.core.ai_router import AIProvider, AIRouter, AIUnavailable, ProviderError  # noqa: E402
from backend.core.identity import IdentityService  # noqa: E402
from backend.database.database import SessionLocal  # noqa: E402
from backend.database.models import TransferToken, WoodyIdentity  # noqa: E402
from backend.main import app, router  # noqa: E402
from backend.transfer.service import TransferService, classify, hash_token, utcnow  # noqa: E402


class FakeProvider(AIProvider):
    def __init__(self, name, reply=None, tasks=("conversation",)):
        self.name, self.reply, self.tasks = name, reply, tasks

    async def complete(self, messages):
        if self.reply is None:
            raise ProviderError(f"{self.name}: down")
        return self.reply


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(secret):
    return {"X-Woody-Session": secret}


@pytest.fixture(scope="module")
def alik(client):
    r = client.post("/api/user", json={"name": "Алик"})
    assert r.status_code == 200
    return r.json()["session"]


def test_health_exact(client):
    assert client.get("/api/health").json() == {"status": "ok", "service": "woody"}


def test_user_created_once_per_session(client, alik):
    assert client.get("/api/user").json()["registered"] is False  # нет сессии — нет пользователя
    me = client.get("/api/user", headers=H(alik)).json()
    assert me["registered"] and me["name"] == "Алик" and "state" in me
    assert me["state"].keys() >= {"emotion", "activity", "energy"}
    # обновление страницы (та же сессия) не создаёт нового пользователя
    assert client.post("/api/user", json={"name": "Другой"}, headers=H(alik)).status_code == 409
    assert client.get("/api/user", headers=H(alik)).json()["name"] == "Алик"


def test_user_endpoints_require_session(client):
    assert client.post("/api/chat", json={"text": "привет"}).status_code == 403
    assert client.get("/api/permissions").status_code == 403
    assert client.get("/api/memory").status_code == 403


def test_chat_ai_unavailable_is_reported(client, alik):
    router.providers = []
    data = client.post("/api/chat", json={"text": "привет"}, headers=H(alik)).json()
    assert data["error"] == "ai_unavailable" and data["face"] == "confused"


def test_chat_voice_memory_and_clear(client, alik):
    router.providers = [FakeProvider("a", "Привет, Алик!")]
    data = client.post("/api/chat", json={"text": "Вуди, привет."}, headers=H(alik)).json()
    assert data["reply"] == "Привет, Алик!" and data["state"]["activity"] == "speaking"
    data = client.post("/api/voice", json={"text": "Я люблю синий", "source": "voice"}, headers=H(alik)).json()
    assert data["reply"] == "Привет, Алик!"
    mem = client.get("/api/memory", headers=H(alik)).json()
    assert any(m["key"] == "favorite_color" and m["value"] == "blue" and m["category"] == "PREFERENCE" for m in mem)
    assert client.delete(f"/api/memory/{mem[0]['id']}", headers=H(alik)).status_code == 200
    assert client.delete("/api/memory", headers=H(alik)).json()["cleared"] is True
    assert client.get("/api/memory", headers=H(alik)).json() == []


def test_voice_blocked_when_microphone_disabled(client, alik):
    client.put("/api/settings", json={"microphone_enabled": False}, headers=H(alik))
    r = client.post("/api/voice", json={"text": "привет"}, headers=H(alik))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "mic_disabled"
    client.put("/api/settings", json={"microphone_enabled": True}, headers=H(alik))


def test_memory_can_be_disabled(client, alik):
    client.put("/api/settings", json={"memory_enabled": False}, headers=H(alik))
    router.providers = [FakeProvider("a", "ok")]
    client.post("/api/chat", json={"text": "Я люблю красный"}, headers=H(alik))
    assert client.get("/api/memory", headers=H(alik)).json() == []
    client.put("/api/settings", json={"memory_enabled": True}, headers=H(alik))


def test_permissions_flow(client, alik):
    h = H(alik)
    assert client.post("/api/permissions/camera/check", headers=h).json()["status"] == "ask"
    client.put("/api/permissions/camera", json={"decision": "ALLOW_ONCE"}, headers=h)
    assert client.post("/api/permissions/camera/check", headers=h).json()["status"] == "allowed"
    assert client.post("/api/permissions/camera/check", headers=h).json()["status"] == "ask"  # ONCE израсходован
    client.post("/api/permissions", json={"action": "camera", "decision": "ALLOW_ALWAYS"}, headers=h)
    for _ in range(3):
        assert client.post("/api/permissions/camera/check", headers=h).json()["status"] == "allowed"
    client.put("/api/permissions/camera", json={"decision": "DENY"}, headers=h)
    assert client.post("/api/permissions/camera/check", headers=h).json()["status"] == "denied"
    client.delete("/api/permissions/camera", headers=h)
    assert client.post("/api/permissions/camera/check", headers=h).json()["status"] == "ask"
    assert client.put("/api/permissions/rm_rf", json={"decision": "DENY"}, headers=h).status_code == 404


def test_voice_confirmation_message_flow(client, alik):
    h = H(alik)
    router.providers = [FakeProvider("a", "x")]
    r = client.post("/api/chat", json={"text": "Вуди, напиши Алику, что я занят"}, headers=h).json()
    assert "Отправить?" in r["reply"]
    r = client.post("/api/chat", json={"text": "Да, отправляй"}, headers=h).json()
    assert "не подключён" in r["reply"]  # честный ответ: отправка не реализована
    client.post("/api/chat", json={"text": "Вуди, напиши Алику, что я занят"}, headers=h)
    r = client.post("/api/chat", json={"text": "нет"}, headers=h).json()
    assert "отменил" in r["reply"].lower()


def test_delete_requires_strict_phrase(client, alik):
    from backend.actions.files import shared_dir

    h = H(alik)
    f = shared_dir() / "old.txt"
    f.write_text("x")
    r = client.post("/api/chat", json={"text": "удали файл old.txt"}, headers=h).json()
    assert "подтверждаю удаление" in r["reply"]
    client.post("/api/chat", json={"text": "да"}, headers=h)
    assert f.exists()
    client.post("/api/chat", json={"text": "подтверждаю удаление"}, headers=h)
    assert not f.exists()


# ------------------------------------------------------------------ transfer + изоляция
def test_transfer_create_requires_session_and_has_no_personal_data(client, alik):
    assert client.post("/api/transfer/create").status_code == 403
    r = client.post("/api/transfer/create", headers=H(alik)).json()
    assert "/join/" in r["join_url"] and r["ttl_seconds"] == 300
    token = r["join_url"].rsplit("/", 1)[1]
    assert len(token) >= 30 and "Алик" not in r["join_url"]
    # в БД лежит только хэш токена
    with SessionLocal() as s:
        row = s.scalars(select(TransferToken).where(TransferToken.token_hash == hash_token(token))).first()
        assert row is not None and row.token_hash != token


def test_transfer_redeem_once_and_isolation(client, alik):
    h_a = H(alik)
    router.providers = [FakeProvider("a", "ok")]
    client.post("/api/chat", json={"text": "Я люблю синий"}, headers=h_a)
    client.post("/api/permissions", json={"action": "camera", "decision": "ALLOW_ALWAYS"}, headers=h_a)

    url = client.post("/api/transfer/create", headers=h_a).json()["join_url"]
    token = url.rsplit("/", 1)[1]
    assert client.post("/api/transfer/check", json={"token": token}).status_code == 200

    r = client.post("/api/transfer/redeem", json={"token": token, "name": "Алекс"})
    assert r.status_code == 200 and r.json()["name"] == "Алекс"
    alex = r.json()["session"]
    assert alex != alik

    # токен одноразовый
    again = client.post("/api/transfer/redeem", json={"token": token, "name": "Хакер"})
    assert again.status_code == 410 and again.json()["detail"]["code"] == "used"
    assert again.json()["detail"]["message"] == "Эта ссылка Woody уже использована."
    assert client.post("/api/transfer/check", json={"token": token}).json()["detail"]["code"] == "used"

    h_b = H(alex)
    assert client.get("/api/user", headers=h_b).json()["name"] == "Алекс"
    assert client.get("/api/user", headers=h_a).json()["name"] == "Алик"
    # память, разрешения и история не переносятся
    assert client.get("/api/memory", headers=h_b).json() == []
    assert client.get("/api/history", headers=h_b).json() == []
    assert client.post("/api/permissions/camera/check", headers=h_b).json()["status"] == "ask"
    assert client.post("/api/permissions/camera/check", headers=h_a).json()["status"] == "allowed"
    # данные Алика на месте
    assert any(m["key"] == "favorite_color" for m in client.get("/api/memory", headers=h_a).json())
    # очистка памяти Алекса не трогает Алика
    client.delete("/api/memory", headers=h_b)
    assert client.get("/api/memory", headers=h_a).json() != []


def test_transfer_expired_and_invalid(client, alik):
    url = client.post("/api/transfer/create", headers=H(alik)).json()["join_url"]
    token = url.rsplit("/", 1)[1]
    with SessionLocal() as s:  # состариваем токен
        row = s.scalars(select(TransferToken).where(TransferToken.token_hash == hash_token(token))).first()
        row.expires_at = utcnow() - timedelta(seconds=1)
        s.commit()
    r = client.post("/api/transfer/redeem", json={"token": token, "name": "Поздно"})
    assert r.status_code == 410 and r.json()["detail"]["code"] == "expired"
    assert r.json()["detail"]["message"] == "Ссылка Woody истекла."
    r = client.post("/api/transfer/check", json={"token": "x" * 32})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid"


def test_transfer_can_be_disabled(client, alik):
    client.put("/api/settings", json={"transfer_enabled": False}, headers=H(alik))
    r = client.post("/api/transfer/create", headers=H(alik))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "transfer_disabled"
    client.put("/api/settings", json={"transfer_enabled": True}, headers=H(alik))


def test_transfer_classify_pure_logic():
    from backend.database.models import TransferToken as T

    now = utcnow()
    assert classify(None, now) == "invalid"
    assert classify(T(token_hash="h", created_by=1, created_at=now, expires_at=now + timedelta(60)), now) is None
    assert classify(T(token_hash="h", created_by=1, created_at=now, expires_at=now - timedelta(1)), now) == "expired"
    assert classify(T(token_hash="h", created_by=1, created_at=now, expires_at=now + timedelta(60), used_at=now), now) == "used"


def test_woody_identity_single_and_shared(client, alik):
    with SessionLocal() as s:
        IdentityService(s).ensure()
        IdentityService(s).ensure()
        assert len(list(s.scalars(select(WoodyIdentity)))) == 1  # одна личность на всех
        assert "conversation" in IdentityService(s).skills()
        assert "Woody" in IdentityService(s).personality()


def test_router_fallback_and_unavailable():
    ok = AIRouter([FakeProvider("bad"), FakeProvider("good", "hi")])
    assert asyncio.run(ok.complete([]))[1] == "good"
    with pytest.raises(AIUnavailable):
        asyncio.run(AIRouter([FakeProvider("bad")]).complete([]))
    vision = AIRouter([FakeProvider("chat", "x"), FakeProvider("eyes", "y", ("vision",))])
    assert asyncio.run(vision.complete([], "vision"))[1] == "eyes"


def test_frontend_routes(client):
    assert client.get("/api/background").json()["supported"] is False
    assert client.get("/").status_code == 200
    page = client.get("/join/sometoken")  # SPA отдаёт index.html, а не 404
    assert page.status_code == 200 and "Woody" in page.text
    assert client.get("/js/api.js").status_code == 200
