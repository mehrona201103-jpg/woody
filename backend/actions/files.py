"""Работа с файлами — только внутри песочницы WOODY_SHARED_DIR."""
from __future__ import annotations

from pathlib import Path

from backend.config import get_settings


def shared_dir() -> Path:
    d = get_settings().shared_dir
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def find_file(name: str) -> Path | None:
    """Ищет файл по имени в песочнице. Выход за её пределы невозможен."""
    root = shared_dir()
    clean = Path(name.strip()).name
    if not clean:
        return None
    for candidate in root.rglob("*"):
        if candidate.is_file() and candidate.name.lower() == clean.lower():
            resolved = candidate.resolve()
            if root in resolved.parents:
                return resolved
    return None


def delete_file(path: Path) -> None:
    """Вызывать только после строгого подтверждения через Permission Manager."""
    root = shared_dir()
    resolved = path.resolve()
    if root not in resolved.parents:
        raise PermissionError("Файл вне разрешённой папки")
    resolved.unlink()
