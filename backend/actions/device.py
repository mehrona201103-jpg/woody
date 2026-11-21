"""Возможности устройства. Произвольные shell-команды от имени AI не поддерживаются и не будут."""
from __future__ import annotations


def capabilities() -> dict[str, dict[str, object]]:
    return {
        "camera": {"where": "browser", "note": "Доступ через getUserMedia после разрешения пользователя."},
        "microphone": {"where": "browser", "note": "Распознавание речи — Web Speech API браузера."},
        "background": {
            "supported": False,
            "note": "Веб-страница не работает после закрытия браузера. Нужен отдельный Background Companion (TODO).",
        },
        "system_settings": {"supported": False, "note": "Изменение настроек системы не реализовано."},
        "shell": {"supported": False, "note": "Запрещено политикой безопасности."},
    }
