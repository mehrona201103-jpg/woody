"""Точка запуска: порт читается из переменной окружения PORT внутри Python,
поэтому команда запуска не зависит от подстановки $PORT в оболочке."""
from __future__ import annotations

import logging
import os

import uvicorn

logger = logging.getLogger("woody.run")


def resolve_port(raw: str | None) -> int:
    try:
        port = int(raw) if raw else 8000
    except ValueError:
        logger.warning("PORT=%r — не число, использую 8000", raw)
        return 8000
    return port if 0 < port < 65536 else 8000


def main() -> None:
    uvicorn.run("backend.main:app", host="0.0.0.0", port=resolve_port(os.getenv("PORT")))


if __name__ == "__main__":
    main()
