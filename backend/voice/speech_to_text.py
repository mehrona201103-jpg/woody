"""STT выполняется в браузере (Web Speech API) — аудио не отправляется на сервер Woody.

Серверный STT (например Whisper через AI Router) — TODO следующей версии.
"""
from __future__ import annotations

ENGINE = {"engine": "browser-web-speech-api", "server_side": False, "language": "ru-RU"}
