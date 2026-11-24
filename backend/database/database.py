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


def _is_postgres() -> bool:
    return bool(_settings.database_url) and "postgres" in _settings.database_url.lower()


def _added_columns() -> list[tuple[str, str, str]]:
    """DDL совместим с SQLite и PostgreSQL (TIMESTAMP вместо DATETIME)."""
    ts = "TIMESTAMP" if _is_postgres() else "DATETIME"
    # DEFAULT в ADD COLUMN ок и в Postgres, и в SQLite
    return [
        ("users", "session_hash", "VARCHAR(64)"),
        ("users", "email", "VARCHAR(200)"),
        ("users", "password_hash", "VARCHAR(300)"),
        ("users", "google_sub", "VARCHAR(64)"),
        ("emotions", "user_id", "INTEGER"),
        ("knowledge", "user_id", "INTEGER"),
        ("conversations", "source", "VARCHAR(10)"),
        ("conversations", "chat_id", "INTEGER"),
        ("users", "picture", "VARCHAR(500)"),
        ("users", "plan", "VARCHAR(20) DEFAULT 'free'"),
        ("users", "plan_until", ts),
        ("users", "request_count", "INTEGER DEFAULT 0"),
    ]


def _migrate() -> None:
    """Лёгкая миграция существующей БД без потери данных."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        if "permissions" in tables and "user_id" not in {c["name"] for c in insp.get_columns("permissions")}:
            conn.execute(text("DROP TABLE permissions"))
            logger.warning("Таблица permissions пересоздана: разрешения стали персональными")
        for table, column, ddl in _added_columns():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if column in existing:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                logger.info("Добавлена колонка %s.%s (%s)", table, column, ddl)
            except Exception as exc:
                # если тип не подошёл — пробуем без DEFAULT / TIMESTAMP
                logger.warning("Миграция %s.%s не удалась (%s), пробуем упрощённый DDL", table, column, exc)
                simple = ddl.split(" DEFAULT ")[0]
                if simple.upper() == "DATETIME":
                    simple = "TIMESTAMP"
                try:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {simple}"))
                    logger.info("Добавлена колонка %s.%s (simple %s)", table, column, simple)
                except Exception as exc2:
                    logger.error("Не удалось добавить %s.%s: %s", table, column, exc2)
                    raise


def init_db() -> None:
    from backend.database import models  # noqa: F401
    from backend.database.models import Base

    _migrate()
    Base.metadata.create_all(engine)
    logger.info(
        "База данных готова: %s (постоянная: %s)",
        "DATABASE_URL" if _settings.database_url else _settings.db_path,
        _settings.persistent,
    )


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
