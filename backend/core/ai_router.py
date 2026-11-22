"""Единый AI Router: Woody не привязан к одному AI-провайдеру."""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod

import httpx

from backend.config import ProviderConfig

logger = logging.getLogger("woody.ai")

TASKS = ("conversation", "vision", "coding", "translation")


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # HTTP-код провайдера (402/429 — закончился лимит)


class AIUnavailable(Exception):
    """Ни один провайдер не смог ответить."""

    quota: bool = False  # True, если причина — закончился лимит/деньги на ключе


class AIProvider(ABC):
    name: str
    tasks: tuple[str, ...]

    @abstractmethod
    async def complete(self, messages: list[dict[str, str]]) -> str: ...


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self.name = cfg.name
        self.tasks = cfg.tasks

    def _redact(self, text: str) -> str:
        """Убирает из текста ошибки сам ключ и всё, что на него похоже."""
        if self.cfg.api_key:
            text = text.replace(self.cfg.api_key, "***")
        return re.sub(r"(sk|gsk|xai|AIza)[A-Za-z0-9_\-*.]{6,}", "***", text)

    async def complete(self, messages: list[dict[str, str]]) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.cfg.timeout) as client:
                resp = await client.post(
                    f"{self.cfg.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                    json={"model": self.cfg.model, "messages": messages, "temperature": 0.7},
                )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:
            # Только код ответа и код ошибки провайдера (например invalid_api_key) — без заголовков и тела.
            code = ""
            try:
                body = exc.response.json()
                err = body.get("error", body) if isinstance(body, dict) else {}
                if isinstance(err, list) and err:
                    err = err[0].get("error", {}) if isinstance(err[0], dict) else {}
                if isinstance(err, dict):
                    message = str(err.get("message") or err.get("code") or err.get("type") or "")
                    code = self._redact(message)[:200]
            except ValueError:
                pass
            hint = {401: "неверный ключ", 403: "доступ запрещён", 404: "неверный адрес или модель",
                    402: "нет средств на счёте", 429: "лимит запросов"}.get(exc.response.status_code, "")
            detail = " ".join(x for x in (str(exc.response.status_code), code, f"({hint})" if hint else "") if x)
            raise ProviderError(f"{self.name}: HTTP {detail}", exc.response.status_code) from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            # В сообщение не попадают ключи/заголовки — только тип ошибки.
            raise ProviderError(f"{self.name}: {type(exc).__name__}") from exc


class AIRouter:
    def __init__(self, providers: list[AIProvider]) -> None:
        self.providers = providers

    @classmethod
    def from_config(cls, configs: list[ProviderConfig]) -> "AIRouter":
        return cls([OpenAICompatibleProvider(c) for c in configs])

    def candidates(self, task: str) -> list[AIProvider]:
        specific = [p for p in self.providers if task in p.tasks]
        if specific or task != "conversation":
            return specific
        return list(self.providers)

    async def complete(self, messages: list[dict[str, str]], task: str = "conversation") -> tuple[str, str]:
        """Возвращает (текст, имя провайдера). Переключается на следующий при сбое."""
        errors: list[str] = []
        statuses: list[int | None] = []
        for provider in self.candidates(task):
            try:
                return await provider.complete(messages), provider.name
            except ProviderError as exc:
                logger.warning("Провайдер недоступен: %s", exc)
                errors.append(str(exc))
                statuses.append(exc.status)
        err = AIUnavailable("; ".join(errors) or f"Нет настроенных провайдеров для задачи '{task}'")
        err.quota = bool(statuses) and all(st in (402, 429) for st in statuses)
        raise err

    def status(self) -> list[dict[str, object]]:
        return [{"name": p.name, "tasks": list(p.tasks)} for p in self.providers]
