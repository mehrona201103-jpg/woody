"""Регистрация, вход, сессии, тон ответа и OpenRouter."""
import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ.setdefault("WOODY_DB_PATH", os.path.join(_tmp, "auth.db"))
os.environ.setdefault("WOODY_SHARED_DIR", os.path.join(_tmp, "shared"))
os.environ.pop("WOODY_ACCESS_TOKEN", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend.config import load_providers  # noqa: E402
from backend.core.brain import split_tone  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_register_login_session_and_logout(client):
    r = client.post("/api/auth/register", json={"email": "Maga@Mail.com", "password": "secret1", "name": "Мага"})
    assert r.status_code == 200 and r.json()["new"] is True
    s1 = r.json()["session"]
    me = client.get("/api/user", headers={"X-Woody-Session": s1}).json()
    assert me["registered"] and me["name"] == "Мага" and me["email"] == "maga@mail.com"

    # повторная регистрация и неверный пароль
    assert client.post("/api/auth/register", json={"email": "maga@mail.com", "password": "secret1", "name": "X"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "maga@mail.com", "password": "wrong"}).status_code == 401

    # вход с другого устройства не выкидывает первое
    s2 = client.post("/api/auth/login", json={"email": "maga@mail.com", "password": "secret1"}).json()["session"]
    assert s2 != s1
    assert client.get("/api/user", headers={"X-Woody-Session": s1}).json()["registered"]

    client.post("/api/auth/logout", headers={"X-Woody-Session": s2})
    assert not client.get("/api/user", headers={"X-Woody-Session": s2}).json()["registered"]
    assert client.get("/api/user", headers={"X-Woody-Session": s1}).json()["registered"]


def test_register_validation(client):
    assert client.post("/api/auth/register", json={"email": "bad", "password": "secret1", "name": "A"}).status_code == 400
    assert client.post("/api/auth/register", json={"email": "a@b.co", "password": "123", "name": "A"}).status_code == 400


def test_google_not_configured_and_config_public(client):
    assert client.get("/api/auth/config").status_code == 200
    assert client.post("/api/auth/google", json={"access_token": "x" * 20}).status_code in (503, 401)


def test_greeting_rejects_unknown_phrase(client):
    assert client.get("/api/tts/greeting?kind=evil").status_code == 404


def test_tone_tag_is_stripped():
    assert split_tone("[laugh] Ха-ха, смешно!") == ("Ха-ха, смешно!", "laugh")
    assert split_tone("Просто текст") == ("Просто текст", None)
    assert split_tone("[SAD] Грустно... [joy] но ладно")[1] == "sad"
    assert "[" not in split_tone("[cry] Ох [joy] ладно")[0]


def test_openrouter_shortcut(monkeypatch):
    for k in [k for k in os.environ if k.startswith("AI_PROVIDER_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-3.5-haiku")
    ps = load_providers()
    assert ps and ps[-1].name == "openrouter" and ps[-1].base_url == "https://openrouter.ai/api/v1" and ps[-1].model == "anthropic/claude-3.5-haiku"
