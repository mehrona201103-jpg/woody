"""Woody Core: объединяет личность, память, эмоции, права и AI Router. AI — лишь инструмент."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.actions import files as files_action
from backend.actions import messages as messages_action
from backend.core.ai_router import AIRouter, AIUnavailable
from backend.core.emotion import EmotionState, PARAMS, detect_events
from backend.core.identity import IdentityService
from backend.core.learning import LearningService
from backend.core.memory import MemoryService
from backend.core.mind import Mind, cacheable, try_math
from backend.core.selflearn import LEARNER
from backend.core.personality import build_system_prompt
from backend.core.user_settings import UserSettings
from backend.database.models import Emotion, User
from backend.permissions.confirmation import parse_confirmation
from backend.permissions.manager import PermissionManager
from backend.database.database import SessionLocal
from backend.config import get_settings

logger = logging.getLogger("woody.brain")


@dataclass
class PendingAction:
    kind: str  # "message" | "delete_file"
    permission: str
    prompt: str
    payload: dict[str, Any] = field(default_factory=dict)
    strict_phrase: str | None = None


# Ожидающие подтверждения действия по user.id (один процесс; для MVP достаточно).
PENDING: dict[int, PendingAction] = {}

CREATOR_REPLY = "Меня создал 17-летний мальчик Ибрагимов Мухаммад. Я постоянно учусь и развиваюсь вместе с вами."
_CREATOR_Q = re.compile(
    r"(кто|кем)\b.{0,30}\b(созда|сдела|разработ|написа|придума|programmed|made|built|created)|"
    r"\b(твой|ваш)\s+(создател|автор|разработчик|отец|папа|хозяин)|who\s+(made|created|built|developed)\s+you|"
    r"(ты|вы)\s+(от|из|продукт|модель)?\s*(openai|опенай|chatgpt|чатгпт|чат\s*gpt|gpt)",
    re.IGNORECASE,
)
_ABOUT_YOU = re.compile(r"\b(тебя|тебе|тобой|ты|вас|вам|вуди|woody|you|твой|твоя|ваш)\b", re.IGNORECASE)
_LEARN_START = re.compile(r"(обучай|обучись|учи|учись|изучай|изучи|прокачай)\w*\s+(сам[а-я]*\s+)?(себя|сам)|(?:^|[\s,])(учись|обучайся|изучай мир)", re.IGNORECASE)
_LEARN_STOP = re.compile(r"(хватит|прекрати|перестань|останови|стоп)\w*.{0,20}(учи|обуч|изуч)", re.IGNORECASE)


TONE_RE = re.compile(r"^\s*\[(joy|laugh|tender|calm|surprise|sad|cry|angry|think)\]\s*", re.IGNORECASE)
TONE_FACE = {"joy": "happy", "laugh": "laughing", "tender": "happy", "calm": "neutral", "surprise": "surprised",
             "sad": "sad", "cry": "crying", "angry": "angry", "think": "confused"}


def split_tone(reply: str) -> tuple[str, str | None]:
    """Отделяет тег настроения [joy] от текста ответа."""
    m = TONE_RE.match(reply or "")
    clean = TONE_RE.sub("", reply or "", count=1).strip() if m else (reply or "").strip()
    clean = re.sub(r"\s*\[(?:joy|laugh|tender|calm|surprise|sad|cry|angry|think)\]\s*", " ", clean, flags=re.IGNORECASE).strip()
    return clean, (m.group(1).lower() if m else None)


def load_emotion(session: Session, user_id: int) -> EmotionState:
    row = session.scalars(select(Emotion).where(Emotion.user_id == user_id).order_by(Emotion.id.desc())).first()
    state = EmotionState()
    if row:
        state.values = {p: float(getattr(row, p)) for p in PARAMS}
    return state


def save_emotion(session: Session, user_id: int, state: EmotionState) -> None:
    session.add(Emotion(user_id=user_id, **{p: state.values[p] for p in PARAMS}))
    session.commit()



PRO_MODE_HINTS = {
    "study": (
        "Режим УЧЁБА (Pro): объясняй понятно, по шагам, с примерами. "
        "Если задача — дай решение и коротко почему так. Можно чуть длиннее обычного."
    ),
    "code": (
        "Режим КОД (Pro): пиши полный рабочий код в блоках ```язык, с комментариями. "
        "Сначала кратко идея, потом код, потом как запустить. Без воды."
    ),
    "write": (
        "Режим ТЕКСТЫ (Pro): помогай писать посты, письма, сочинения. "
        "Давай 1–2 сильных варианта, правь стиль под аудиторию."
    ),
    "deal": (
        "Режим ПЕРЕГОВОРЫ (Pro): помогай формулировать жёстко, но уважительно. "
        "Дай скрипт реплик и запасные ходы."
    ),
    "agent": (
        "Режим АГЕНТ (Pro): пользователь дал цель. Разбей на конкретные шаги (1. 2. 3.), "
        "сделай первый шаг в ответе сам (текст/план/черновик), спроси что нужно дальше. "
        "Не рассипайся — веди к результату."
    ),
    "ghost": (
        "Режим ИНКОГНИТО (Pro): отвечай как обычно, но не ссылайся на старую личную память "
        "и не предлагай «запомнить» это. Тема чувствительная."
    ),
}


class Brain:
    def __init__(self, session: Session, router: AIRouter) -> None:
        self.session = session
        self.router = router
        self.memory = MemoryService(session)
        self.learning = LearningService(session)
        self.identity = IdentityService(session)
        self.permissions: PermissionManager  # создаётся в handle() для конкретного пользователя

    def _result(self, reply: str, emotion: EmotionState, **extra: Any) -> dict[str, Any]:
        face = extra.pop("face", emotion.face_state())
        state = emotion.state("speaking")
        state["emotion"] = face
        return {
            "reply": reply,
            "face": face,
            "state": state,
            "emotion": emotion.snapshot(),
            "voice": emotion.voice_style(),
            **extra,
        }

    async def handle(self, user: User, text: str, source: str = "text", chat_id: int | None = None, pro_mode: str | None = None, pro_persona: str | None = None) -> dict[str, Any]:
        self.memory.chat_id = chat_id
        self.permissions = PermissionManager(self.session, user.id)
        emotion = load_emotion(self.session, user.id)
        self.memory.add_message(user.id, "USER", text, source)
        mind = Mind(self.session)

        def finish(reply: str, **extra: Any) -> dict[str, Any]:
            self.memory.add_message(user.id, "WOODY", reply, source)
            save_emotion(self.session, user.id, emotion)
            return self._result(reply, emotion, **extra)

        pending = PENDING.get(user.id)
        if pending:
            reply = self._resolve_pending(user, pending, text, emotion)
            if reply is not None:
                return finish(reply)

        action_reply = self._detect_action(user, text)
        if action_reply is not None:
            self.memory.add_message(user.id, "WOODY", action_reply, source)
            return self._result(action_reply, emotion, awaiting_confirmation=user.id in PENDING)

        # --- Woody про себя: создатель, самообучение, простая арифметика (без траты лимита ключа)
        if _CREATOR_Q.search(text) and _ABOUT_YOU.search(text):
            emotion.apply("helped_user")
            return finish(CREATOR_REPLY, face="happy")
        if _LEARN_STOP.search(text):
            LEARNER.stop()
            return finish("Хорошо, заканчиваю учиться.", learning=False)
        if _LEARN_START.search(text):
            started = LEARNER.start(self.router, SessionLocal, get_settings().self_learn_max)
            if started:
                return finish("Закрываю глазки и начинаю учиться. Я буду изучать мир, пока у ключа не кончится лимит, а потом отвечу всё, что запомнил.", learning=True)
            if LEARNER.active:
                return finish("Я уже учусь. Скажи «хватит учиться», когда захочешь остановить.", learning=True)
            return finish("Мне не к чему подключиться, чтобы учиться: ключ AI не настроен.", face="confused", learning=False)
        calc = try_math(text)
        if calc is not None:
            emotion.apply("task_success")
            return finish(f"Это будет {calc}.", provider="calc")

        for event in detect_events(text):
            emotion.apply(event)
        if (pro_mode or "") != "ghost":
            self.memory.learn_from_text(user.id, text)

        # --- сначала собственный мозг: если вопрос почти точно знаком — отвечаем сами и экономим лимит ключа
        known = mind.recall(text, 0.9)
        if known:
            emotion.decay(0.05)
            return finish(known[0], provider="brain")

        system = build_system_prompt(
            user.name,
            emotion,
            self.memory.context_lines(user.id) if (pro_mode or "") != "ghost" else [],
            self.learning.context_lines(user.id),
            persona=self.identity.personality(),
        )
        mode = (pro_mode or "").strip().lower()
        if mode in PRO_MODE_HINTS:
            system += "\n\n" + PRO_MODE_HINTS[mode]
        if pro_persona and pro_persona.strip():
            system += "\n\nДоп. правила стиля от пользователя (Pro, соблюдай постоянно): " + pro_persona.strip()[:500]
        if source != "voice":
            system += ("\n\nЕсли просят код — пиши полностью рабочий, проверенный и без ошибок код, обязательно в блоке ```язык … ```, "
                       "с короткими комментариями и краткой подсказкой, как запустить. Ответы оформляй аккуратно: абзацы, списки, **жирный** для главного.")
        if source == "voice":
            system += "\n\nСейчас голосовой разговор: отвечай одной-двумя короткими живыми фразами, как человек в разговоре."
        history = [
            {"role": "user" if m.role == "USER" else "assistant", "content": m.text}
            for m in self.memory.recent_messages(user.id, 10)
        ]
        if not history or history[-1]["content"] != text:
            history.append({"role": "user", "content": text})

        try:
            reply, provider = await self.router.complete([{"role": "system", "content": system}, *history])
            emotion.apply("helped_user")
            emotion.apply("task_success")
        except AIUnavailable as exc:
            logger.error("AI недоступен: %s", exc)
            # Лимит закончился или сеть пропала — Woody ищет подходящий ответ в своём мозге.
            fallback = mind.recall(text, 0.45)
            if fallback:
                emotion.decay(0.05)
                return finish(fallback[0], provider="brain")
            emotion.apply("task_failure")
            emotion.decay()
            save_emotion(self.session, user.id, emotion)
            if getattr(exc, "quota", False):
                msg = f"{user.name}, у моего ключа закончился лимит, а этого я ещё не выучил. Спроси позже — я запомню, когда смогу."
            else:
                msg = f"{user.name}, сейчас я не могу связаться с AI-моделью, а в моём мозге такого ответа нет. Попробуй позже."
            return self._result(msg, emotion, face="confused", error="ai_unavailable")

        reply, tone = split_tone(reply)
        if (pro_mode or "") != "ghost" and cacheable(text, reply) and UserSettings(self.session, user.id).get("learn_from_chats"):
            mind.remember(text, reply, "AI")  # Woody запоминает то, чего не знал
        emotion.decay(0.05)
        save_emotion(self.session, user.id, emotion)
        self.memory.add_message(user.id, "WOODY", reply, source)
        return self._result(reply, emotion, provider=provider, tone=tone or "calm", face=TONE_FACE.get(tone or "", emotion.face_state()))

    # --- действия -----------------------------------------------------------------

    def _detect_action(self, user: User, text: str) -> str | None:
        low = text.lower()
        draft = messages_action.parse_draft_request(text)
        if draft:
            if self.permissions.status("messages.send") == "denied":
                return "Отправка сообщений для меня запрещена в настройках разрешений."
            PENDING[user.id] = PendingAction(
                "message", "messages.send", "", {"recipient": draft.recipient, "text": draft.text}
            )
            return f"Я подготовил сообщение для «{draft.recipient}»: «{draft.text}» Отправить?"

        if "удали файл" in low:
            name = low.split("удали файл", 1)[1].strip(" .,!?\"'«»")
            if self.permissions.status("files.delete") == "denied":
                return "Удаление файлов для меня запрещено в настройках разрешений."
            found = files_action.find_file(name) if name else None
            if not found:
                return "Я не нашёл такой файл в разрешённой папке."
            phrase = "подтверждаю удаление"
            PENDING[user.id] = PendingAction("delete_file", "files.delete", "", {"path": str(found)}, phrase)
            return f"Я нашёл файл «{found.name}». Чтобы удалить его, скажи: «{phrase}»."
        return None

    def _resolve_pending(self, user: User, pending: PendingAction, text: str, emotion: EmotionState) -> str | None:
        answer = parse_confirmation(text, pending.strict_phrase)
        if answer == "unclear":
            if pending.strict_phrase:
                return f"Для этого действия нужна точная фраза: «{pending.strict_phrase}». Или скажи «отмена»."
            return None  # не ответ на вопрос — обычный диалог, подтверждение остаётся в силе
        del PENDING[user.id]
        if answer == "no":
            return "Хорошо, отменил."
        if self.permissions.check(pending.permission) == "denied":
            return "Это действие запрещено в настройках разрешений."

        if pending.kind == "message":
            ok, info = messages_action.send_message(
                messages_action.MessageDraft(pending.payload["recipient"], pending.payload["text"])
            )
            emotion.apply("task_success" if ok else "task_failure")
            self.learning.log_experience("Отправка сообщения", "success" if ok else "not_implemented")
            return info
        if pending.kind == "delete_file":
            try:
                files_action.delete_file(Path(pending.payload["path"]))
            except (OSError, PermissionError) as exc:
                logger.error("Удаление не удалось: %s", exc)
                emotion.apply("task_failure")
                return "Не получилось удалить файл."
            emotion.apply("task_success")
            return "Файл удалён."
        return "Неизвестное действие."
