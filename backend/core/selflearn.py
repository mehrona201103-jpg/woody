"""Самообучение: Woody сам изучает мир через AI-ключ и складывает знания в свой мозг,
пока лимит ключа не закончится. После этого отвечает из собственного мозга."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Callable

from sqlalchemy.orm import Session

from backend.core.ai_router import AIRouter, AIUnavailable
from backend.core.mind import Mind, normalize

logger = logging.getLogger("woody.selflearn")

SEED_TOPICS = """фотосинтез;гравитация;атом;ДНК;клетка;эволюция;вулканы;землетрясения;облака и дождь;радуга;электричество;магнетизм;звуковые волны;
свет и цвет;чёрные дыры;Солнечная система;Луна;Марс;звёзды;Большой взрыв;периодическая таблица;вода и её свойства;кислоты и щёлочи;
химические реакции;плотность;температура;давление;энергия;скорость и ускорение;законы Ньютона;простые числа;дроби;проценты;уравнения;
геометрия;треугольник;окружность;теорема Пифагора;вероятность;алгебра;логарифмы;статистика;интернет;компьютер;процессор;оперативная память;
операционная система;программирование;Python;JavaScript;алгоритм;база данных;искусственный интеллект;нейронная сеть;Wi-Fi;Bluetooth;
GPS;смартфон;батарея;шифрование;пароли и безопасность;вирусы и антивирусы;Древний Египет;Древняя Греция;Римская империя;Средние века;
Возрождение;Великая французская революция;Первая мировая война;Вторая мировая война;Холодная война;космическая гонка;Россия;Узбекистан;
Казахстан;Европа;Азия;Африка;Америка;океаны;горы;реки;пустыни;климат;времена года;экология;переработка отходов;возобновляемая энергия;
здоровье и сон;питание;витамины;спорт;сердце;мозг;иммунитет;первая помощь;эмоции;память человека;мотивация;привычки;общение;дружба;
умение учиться;тайм-менеджмент;публичные выступления;деньги и бюджет;инфляция;банки;налоги;предпринимательство;маркетинг;музыка;ноты;
живопись;кино;литература;поэзия;архитектура;шахматы;футбол;языки мира;английский язык;грамматика русского языка;мифология;религии мира;
философия;этика;право и законы;права человека;демократия;ООН;космонавтика;самолёты;автомобили;поезда;корабли;изобретения;Леонардо да Винчи;
Эйнштейн;Тесла;кулинария;растения;животные;птицы;насекомые;динозавры;океанская жизнь;домашние питомцы""".replace("\n", "").split(";")


def _parse_pairs(raw: str) -> list[tuple[str, str]]:
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for it in data if isinstance(data, list) else []:
        if isinstance(it, dict) and it.get("q") and it.get("a"):
            out.append((str(it["q"]).strip(), str(it["a"]).strip()))
    return out


class SelfLearner:
    """Один фоновый процесс на всё приложение: ключ AI общий."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop_flag = False
        self.state: dict = {"active": False, "learned": 0, "topic": "", "reason": "", "started_at": 0.0}

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> dict:
        return {**self.state, "active": self.active}

    def stop(self) -> None:
        self.stop_flag = True

    def start(self, router: AIRouter, session_factory: Callable[[], Session], max_items: int = 400, delay: float = 1.0) -> bool:
        if self.active:
            return False
        if not router.providers:
            self.state = {"active": False, "learned": 0, "topic": "", "reason": "no_key", "started_at": time.time()}
            return False
        self.stop_flag = False
        self.state = {"active": True, "learned": 0, "topic": "", "reason": "", "started_at": time.time()}
        self.task = asyncio.create_task(self._run(router, session_factory, max_items, delay))
        return True

    async def _ask(self, router: AIRouter, system: str, user: str) -> str:
        reply, _ = await router.complete([{"role": "system", "content": system}, {"role": "user", "content": user}])
        return reply

    async def _run(self, router: AIRouter, session_factory: Callable[[], Session], max_items: int, delay: float) -> None:
        fails = 0
        queue = list(SEED_TOPICS)
        asked: set[str] = set()
        try:
            while not self.stop_flag and self.state["learned"] < max_items:
                if not queue:
                    try:
                        raw = await self._ask(router, "Отвечай только списком через точку с запятой, без нумерации.",
                                              "Назови 25 разных интересных тем для изучения (наука, история, жизнь, техника, культура), которых нет в списке: "
                                              + "; ".join(list(asked)[-60:]))
                        queue = [t.strip(" .\n") for t in raw.split(";") if 2 < len(t.strip()) < 60 and t.strip().lower() not in asked]
                    except AIUnavailable as exc:
                        self.state["reason"] = "limit" if getattr(exc, "quota", False) else "error"
                        break
                    if not queue:
                        self.state["reason"] = "done"
                        break
                topic = queue.pop(0)
                if topic.lower() in asked:
                    continue
                asked.add(topic.lower())
                self.state["topic"] = topic
                with session_factory() as session:
                    mind = Mind(session)
                    known = mind.known_norms()
                    if normalize(topic) in known or normalize(f"Расскажи про {topic}") in known:  # уже выучено — не повторяем
                        continue
                try:
                    raw = await self._ask(
                        router,
                        "Ты учитель. Отвечай по-русски коротко и точно. Верни ТОЛЬКО JSON-массив из 3 объектов "
                        '{"q":"вопрос человека","a":"ответ в 1-3 предложениях"} по теме; вопросы — такие, какие люди задают голосом.',
                        f"Тема: {topic}",
                    )
                    fails = 0
                except AIUnavailable as exc:
                    fails += 1
                    if getattr(exc, "quota", False) or fails >= 3:
                        self.state["reason"] = "limit" if getattr(exc, "quota", False) else "error"
                        break
                    await asyncio.sleep(3)
                    continue
                pairs = _parse_pairs(raw) or [(f"Что такое {topic}?", raw.strip()[:1200])]
                with session_factory() as session:
                    mind = Mind(session)
                    for q, a in pairs:
                        if mind.remember(q, a, "SELF"):
                            self.state["learned"] += 1
                    # сама тема тоже запоминается: «расскажи про X»
                    mind.remember(f"Расскажи про {topic}", pairs[0][1], "SELF")
                await asyncio.sleep(delay)
            if not self.state["reason"]:
                self.state["reason"] = "stopped" if self.stop_flag else "done"
        except Exception:  # noqa: BLE001 — фоновая задача не должна падать молча
            logger.exception("Самообучение остановлено из-за ошибки")
            self.state["reason"] = "error"
        finally:
            self.state["active"] = False
            self.state["topic"] = ""


LEARNER = SelfLearner()
