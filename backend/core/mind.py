"""Мозг Woody: запоминает ответы AI-инструмента и сам отвечает на похожие вопросы, когда лимит ключа закончился."""
from __future__ import annotations

import ast
import json
import logging
import operator
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database.models import LearnedAnswer

logger = logging.getLogger("woody.mind")

_STOP = set(
    "и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня еще нет о из ему "
    "теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они "
    "тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому "
    "этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об "
    "другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда "
    "лучше чуть том нельзя такой им более всегда конечно всю между расскажи скажи объясни такое woody вуди привет пожалуйста подскажи".split()
)
_PERSONAL = re.compile(r"\b(меня|мне|мой|моя|моё|мое|мои|моей|моего|мою|мною|я|мы|наш\w*|нас|нам)\b", re.IGNORECASE)
_VOLATILE = re.compile(r"\b(сегодня|сейчас|завтра|вчера|погода|курс|новости|который час|время|дата|сколько времени)\b", re.IGNORECASE)
_SYMBOLS = {"+": " плюс ", "*": " умножить ", "×": " умножить ", "÷": " делить ", "/": " делить ", "=": " "}


def normalize(text: str) -> str:
    t = text.lower().replace("ё", "е")
    for sym, word in _SYMBOLS.items():
        t = t.replace(sym, word)
    t = re.sub(r"(?<=\d)\s*-\s*(?=\d)", " минус ", t)
    tokens = re.findall(r"[a-zа-я0-9]+", t)
    out = []
    for tok in tokens:
        if tok in _STOP:
            continue
        out.append(tok[:5] if len(tok) > 5 and not tok.isdigit() else tok)  # грубый «стемминг»
    return " ".join(out)


def similarity(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, a, b).ratio()
    return 0.65 * jac + 0.35 * seq


def cacheable(question: str, answer: str) -> bool:
    """Личные и «живые» вопросы (имя, погода, время) в общий мозг не попадают."""
    q = question.strip()
    if len(q) < 3 or len(q) > 300 or len(answer) > 1500 or not answer.strip():
        return False
    if _PERSONAL.search(q) or _VOLATILE.search(q):
        return False
    return bool(normalize(q))


# ---------------------------------------------------------------- безопасный калькулятор
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("слишком большая степень")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("недопустимое выражение")


def try_math(text: str) -> str | None:
    t = text.lower().replace("ё", "е")
    t = re.sub(r"(сколько будет|сколько|посчитай|реши|вычисли|чему равно|чему равен|woody|вуди|пожалуйста)", " ", t)
    for word, sym in (("умножить на", "*"), ("умножить", "*"), ("разделить на", "/"), ("делить на", "/"), ("плюс", "+"),
                      ("минус", "-"), ("в степени", "**"), ("×", "*"), ("÷", "/"), ("x", "*"), ("х", "*")):
        t = t.replace(word, sym)
    t = t.replace("^", "**").replace(",", ".").replace("=", " ").replace("?", " ").strip()
    if not re.fullmatch(r"[\d\s.+\-*/()%]+", t) or not re.search(r"\d\s*(\*\*|[+\-*/%])\s*\(?\s*\d", t):
        return None
    try:
        value = _eval(ast.parse(t.strip(), mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    elif isinstance(value, float):
        value = round(value, 6)
    return str(value)


# ---------------------------------------------------------------- хранилище
class Mind:
    def __init__(self, session: Session) -> None:
        self.session = session

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(LearnedAnswer)) or 0

    def stats(self) -> dict[str, int]:
        return {
            "total": self.count(),
            "from_ai": self.session.scalar(select(func.count()).select_from(LearnedAnswer).where(LearnedAnswer.source == "AI")) or 0,
            "self_taught": self.session.scalar(select(func.count()).select_from(LearnedAnswer).where(LearnedAnswer.source == "SELF")) or 0,
        }

    def remember(self, question: str, answer: str, source: str = "AI") -> bool:
        norm = normalize(question)
        if not norm or not answer.strip():
            return False
        hit = self.recall(question, 0.97, touch=False)
        if hit:
            return False  # уже знает
        self.session.add(LearnedAnswer(question=question.strip()[:300], norm=norm, answer=answer.strip(), source=source))
        self.session.commit()
        return True

    def recall(self, question: str, threshold: float = 0.6, touch: bool = True) -> tuple[str, float] | None:
        norm = normalize(question)
        if not norm:
            return None
        tokens = set(norm.split())
        rows = self.session.scalars(select(LearnedAnswer).order_by(LearnedAnswer.id.desc()).limit(6000))
        best: tuple[float, LearnedAnswer] | None = None
        for row in rows:
            if not tokens & set(row.norm.split()):
                continue
            score = similarity(norm, row.norm)
            if best is None or score > best[0]:
                best = (score, row)
        if best is None or best[0] < threshold:
            return None
        if touch:
            best[1].uses += 1
            best[1].last_used = datetime.now(timezone.utc)
            self.session.commit()
        return best[1].answer, best[0]

    def known_norms(self) -> set[str]:
        return set(self.session.scalars(select(LearnedAnswer.norm)))

    # --- резервная копия мозга (чтобы не потерять при потере диска)
    def export(self) -> dict:
        rows = self.session.scalars(select(LearnedAnswer).order_by(LearnedAnswer.id))
        return {"version": 1, "items": [{"q": r.question, "a": r.answer, "s": r.source, "u": r.uses} for r in rows]}

    def import_items(self, items: list[dict]) -> int:
        added = 0
        known = self.known_norms()
        for it in items[:20000]:
            q, a = str(it.get("q", "")).strip(), str(it.get("a", "")).strip()
            norm = normalize(q)
            if not norm or not a or norm in known or len(a) > 4000:
                continue
            self.session.add(LearnedAnswer(question=q[:300], norm=norm, answer=a, source=str(it.get("s", "AI"))[:10], uses=int(it.get("u", 0) or 0)))
            known.add(norm)
            added += 1
        self.session.commit()
        return added

    def seed_from_file(self, path: Path) -> int:
        """Если мозг пуст, а в репозитории лежит data/brain_seed.json — загружает его (переживает любой деплой)."""
        if self.count() or not path.exists():
            return 0
        try:
            return self.import_items(json.loads(path.read_text(encoding="utf-8")).get("items", []))
        except (OSError, ValueError) as exc:
            logger.warning("brain_seed.json не прочитан: %s", exc)
            return 0
