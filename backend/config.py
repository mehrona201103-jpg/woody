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


class Settings:
    def __init__(self) -> None:
        self.app_env: str = os.getenv("APP_ENV", "development")
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
        self.db_path: Path = Path(os.getenv("WOODY_DB_PATH", str(BASE_DIR / "data" / "woody.db")))
        self.shared_dir: Path = Path(os.getenv("WOODY_SHARED_DIR", str(BASE_DIR / "data" / "shared")))
        # Если задан — все /api/* (кроме /api/health) требуют Authorization: Bearer <token>.
        self.access_token: str = os.getenv("WOODY_ACCESS_TOKEN", "").strip()
        self.public_base_url: str = resolve_public_base_url()
        self.providers: list[ProviderConfig] = load_providers()


def get_settings() -> Settings:
    return Settings()


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
