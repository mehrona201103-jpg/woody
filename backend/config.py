"""Конфигурация Woody. Все секреты читаются из окружения (.env / Render Variables)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    tasks: tuple[str, ...]
    timeout: float = 30.0


def load_providers() -> list[ProviderConfig]:
    """AI_PROVIDER_1_* ... AI_PROVIDER_5_* (любой OpenAI-совместимый API)."""
    providers: list[ProviderConfig] = []
    for i in range(1, 6):
        prefix = f"AI_PROVIDER_{i}_"
        api_key = os.getenv(prefix + "API_KEY", "").strip()
        base_url = os.getenv(prefix + "BASE_URL", "").strip()
        model = os.getenv(prefix + "MODEL", "").strip()
        if not (api_key and base_url and model):
            continue
        tasks = tuple(
            t.strip().lower()
            for t in os.getenv(prefix + "TASKS", "conversation").split(",")
            if t.strip()
        )
        providers.append(
            ProviderConfig(
                name=os.getenv(prefix + "NAME", f"provider_{i}").strip(),
                base_url=base_url.rstrip("/"),
                api_key=api_key,
                model=model,
                tasks=tasks,
                timeout=float(os.getenv(prefix + "TIMEOUT", "30")),
            )
        )
    return providers


def resolve_public_base_url() -> str:
    """Публичный адрес для QR/join-ссылок: PUBLIC_BASE_URL → Railway → Render."""
    explicit = os.getenv("PUBLIC_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    railway = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()  # без схемы, например woody.up.railway.app
    if railway:
        return "https://" + railway.removeprefix("https://").rstrip("/")
    return os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")


def provider_config_problems() -> list[str]:
    """Подсказки по неполной настройке AI-провайдеров (без значений ключей)."""
    problems: list[str] = []
    for i in range(1, 6):
        prefix = f"AI_PROVIDER_{i}_"
        present = {k for k in ("API_KEY", "BASE_URL", "MODEL") if os.getenv(prefix + k, "").strip()}
        if present and len(present) < 3:
            missing = ", ".join(prefix + k for k in ("API_KEY", "BASE_URL", "MODEL") if k not in present)
            problems.append(f"Провайдер {i} настроен не полностью, не хватает: {missing}")
    wrong = [k for k in os.environ if k.startswith("AI_PROVIDER_") and not k[len("AI_PROVIDER_"):][:1].isdigit()]
    if wrong:
        problems.append("Переменные названы неверно (нужен номер, например AI_PROVIDER_1_API_KEY): " + ", ".join(sorted(wrong)))
    return problems


def _volume_dir() -> Path | None:
    """Постоянный диск (Volume/Disk), если он подключён: Railway, Render, /data."""
    for raw in (os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ""), "/data", "/var/data"):
        if not raw:
            continue
        path = Path(raw)
        try:
            if path.is_dir() and os.access(path, os.W_OK):
                return path
        except OSError:
            continue
    return None


def resolve_database_url() -> str:
    """DATABASE_URL (Postgres) — самый надёжный способ: база живёт отдельно от сервера и не стирается."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings:
    def __init__(self) -> None:
        self.app_env: str = os.getenv("APP_ENV", "development")
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
        volume = _volume_dir()
        default_db = (volume / "woody.db") if volume else (BASE_DIR / "data" / "woody.db")
        default_shared = (volume / "shared") if volume else (BASE_DIR / "data" / "shared")
        self.database_url: str = resolve_database_url()
        self.db_path: Path = Path(os.getenv("WOODY_DB_PATH", str(default_db)))
        self.shared_dir: Path = Path(os.getenv("WOODY_SHARED_DIR", str(default_shared)))
        # Данные переживают перезапуск, если это внешняя БД либо файл вне папки приложения (на Volume).
        local_default = (BASE_DIR / "data").resolve()
        self.persistent: bool = bool(self.database_url) or local_default not in self.db_path.resolve().parents
        # Озвучка «как у человека» через OpenAI-совместимый /audio/speech (по умолчанию тот же ключ, что у AI_PROVIDER_1).
        self.tts_model: str = os.getenv("TTS_MODEL", "gpt-4o-mini-tts").strip()
        self.tts_base_url: str = os.getenv("TTS_BASE_URL", "").strip().rstrip("/")
        self.tts_api_key: str = os.getenv("TTS_API_KEY", "").strip()
        self.self_learn_max: int = int(os.getenv("SELF_LEARN_MAX", "400"))
        # Если задан — все /api/* (кроме /api/health) требуют Authorization: Bearer <token>.
        self.access_token: str = os.getenv("WOODY_ACCESS_TOKEN", "").strip()
        self.public_base_url: str = resolve_public_base_url()
        self.providers: list[ProviderConfig] = load_providers()


def get_settings() -> Settings:
    return Settings()


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
