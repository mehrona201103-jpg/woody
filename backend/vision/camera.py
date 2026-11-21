"""Камера работает только в браузере и только после проверки Permission Manager."""
from __future__ import annotations

from backend.permissions.manager import PermissionManager


def camera_status(pm: PermissionManager) -> str:
    return pm.status("camera")


# TODO: анализ кадров через vision-провайдера AI Router (задача "vision").
