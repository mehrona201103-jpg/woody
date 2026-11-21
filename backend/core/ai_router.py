"""Единый AI Router: Woody не привязан к одному AI-провайдеру."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from backend.config import ProviderConfig

logger = logging.getLogger("woody.ai")

TASKS = ("conversation", "vision", "coding", "translation")


class ProviderError(Exception):
    pass


class AIUnavailable(Exception):
    """Ни один провайдер не смог ответить."""


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
        for provider in self.candidates(task):
            try:
                return await provider.complete(messages), provider.name
            except ProviderError as exc:
                logger.warning("Провайдер недоступен: %s", exc)
                errors.append(str(exc))
        raise AIUnavailable("; ".join(errors) or f"Нет настроенных провайдеров для задачи '{task}'")

    def status(self) -> list[dict[str, object]]:
        return [{"name": p.name, "tasks": list(p.tasks)} for p in self.providers]
