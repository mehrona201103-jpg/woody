from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings

logger = logging.getLogger("woody.db")

_settings = get_settings()
if _settings.database_url:
    engine = create_engine(_settings.database_url, pool_pre_ping=True)
else:
    _settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{Path(_settings.db_path)}",
        connect_args={"check_same_thread": False},
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# (таблица, колонка, DDL) — колонки, добавленные после v0.1
_ADDED_COLUMNS = [
    ("users", "session_hash", "VARCHAR(64)"),
    ("users", "email", "VARCHAR(200)"),
    ("users", "password_hash", "VARCHAR(300)"),
    ("users", "google_sub", "VARCHAR(64)"),
    ("emotions", "user_id", "INTEGER"),
    ("knowledge", "user_id", "INTEGER"),
    ("conversations", "source", "VARCHAR(10)"),
]


def _migrate() -> None:
    """Лёгкая миграция существующей БД v0.1 без потери данных (кроме глобальных разрешений)."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        if "permissions" in tables and "user_id" not in {c["name"] for c in insp.get_columns("permissions")}:
            # Разрешения v0.1 были глобальными. Теперь они персональные — старые не переносятся.
            conn.execute(text("DROP TABLE permissions"))
            logger.warning("Таблица permissions пересоздана: разрешения стали персональными")
        for table, column, ddl in _ADDED_COLUMNS:
            if table in tables and column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                logger.info("Добавлена колонка %s.%s", table, column)


def init_db() -> None:
    from backend.database import models  # noqa: F401  (регистрирует таблицы)
    from backend.database.models import Base

    _migrate()
    Base.metadata.create_all(engine)
    logger.info("База данных готова: %s (постоянная: %s)", "DATABASE_URL" if _settings.database_url else _settings.db_path, _settings.persistent)


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
