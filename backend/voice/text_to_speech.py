"""TTS выполняется в браузере (speechSynthesis). Сервер лишь задаёт стиль голоса по эмоциям."""
from __future__ import annotations

ENGINE = {"engine": "browser-speech-synthesis", "server_side": False, "language": "ru-RU"}
