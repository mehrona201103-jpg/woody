"""Разбор голосового/текстового подтверждения. Без зависимостей от БД."""
from __future__ import annotations

import re

YES_WORDS = ("да", "конечно", "отправляй", "отправь", "можно", "подтверждаю", "давай", "ага", "хорошо", "ок", "окей")
NO_PHRASES = ("нет", "не надо", "не нужно", "отмена", "отмени", "стоп", "не отправляй", "не удаляй", "не делай", "запрещаю")


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е")).strip()


def parse_confirmation(text: str, strict_phrase: str | None = None) -> str:
    """Возвращает 'yes', 'no' или 'unclear'.

    Отрицание всегда приоритетнее. Если задан strict_phrase (опасные действия),
    обычное «да» НЕ считается подтверждением — нужна точная фраза.
    """
    norm = _normalize(text)
    words = norm.split()
    if not words:
        return "unclear"
    if any(re.search(rf"\b{re.escape(_normalize(p))}\b", norm) for p in NO_PHRASES):
        return "no"
    if strict_phrase:
        return "yes" if _normalize(strict_phrase) in norm else "unclear"
    if any(w in YES_WORDS for w in words):
        return "yes"
    return "unclear"
