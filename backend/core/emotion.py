"""Эмоциональное состояние Woody. Это симуляция, а не настоящие чувства."""
from __future__ import annotations

from dataclasses import dataclass, field

PARAMS = ("happiness", "sadness", "anger", "curiosity", "energy", "calmness", "confidence")

BASELINE: dict[str, float] = {
    "happiness": 50, "sadness": 10, "anger": 5, "curiosity": 60,
    "energy": 60, "calmness": 60, "confidence": 50,
}

EVENTS: dict[str, dict[str, float]] = {
    "pleasant_chat": {"happiness": 10},
    "insult": {"sadness": 5, "anger": 5, "calmness": -5},
    "helped_user": {"happiness": 5},
    "task_success": {"confidence": 3},
    "task_failure": {"confidence": -3, "sadness": 2},
    "new_topic": {"curiosity": 5},
}

_POSITIVE = ("спасибо", "круто", "отлично", "молодец", "привет", "здорово", "класс", "люблю")
_NEGATIVE = ("дурак", "тупой", "идиот", "ненавижу", "заткнись", "бесишь")


def detect_events(text: str) -> list[str]:
    low = text.lower()
    events: list[str] = []
    if any(w in low for w in _NEGATIVE):
        events.append("insult")
    elif any(w in low for w in _POSITIVE):
        events.append("pleasant_chat")
    if "?" in text:
        events.append("new_topic")
    return events


@dataclass
class EmotionState:
    values: dict[str, float] = field(default_factory=lambda: dict(BASELINE))

    def apply(self, event: str) -> None:
        for param, delta in EVENTS.get(event, {}).items():
            self.values[param] = max(0.0, min(100.0, self.values[param] + delta))

    def decay(self, rate: float = 0.1) -> None:
        """Постепенный возврат к базовому состоянию."""
        for param in PARAMS:
            base = BASELINE[param]
            self.values[param] += (base - self.values[param]) * rate

    def face_state(self) -> str:
        v = self.values
        if v["anger"] >= 40:
            return "angry"
        if v["sadness"] >= 35:
            return "sad"
        if v["energy"] <= 25:
            return "sleepy"
        if v["happiness"] >= 85 and v["energy"] >= 65:
            return "excited"
        if v["happiness"] >= 65:
            return "happy"
        return "neutral"

    def voice_style(self) -> dict[str, float]:
        """Параметры для речи в браузере (speechSynthesis)."""
        v = self.values
        rate = 1.0 + (v["energy"] - 50) / 250 + (v["happiness"] - 50) / 400
        pitch = 1.0 + (v["happiness"] - 50) / 250 - (v["sadness"] - 10) / 300
        if v["sadness"] >= 35:
            rate -= 0.1
        return {"rate": round(max(0.7, min(1.3, rate)), 2), "pitch": round(max(0.7, min(1.3, pitch)), 2)}

    def style_hint(self) -> str:
        state = self.face_state()
        return {
            "angry": "Ты сейчас раздражён: отвечай коротко и сдержанно, но без грубости.",
            "sad": "Ты сейчас грустишь: отвечай спокойно и мягко.",
            "sleepy": "Ты сейчас сонный: отвечай коротко и неторопливо.",
            "excited": "Ты сейчас очень воодушевлён: отвечай энергично.",
            "happy": "Ты в хорошем настроении: отвечай тепло, с лёгким позитивом.",
            "neutral": "Ты в спокойном нейтральном настроении.",
        }[state]

    def state(self, activity: str = "idle") -> dict[str, object]:
        """Состояние для Face Engine: {emotion, activity, energy}."""
        return {"emotion": self.face_state(), "activity": activity, "energy": int(round(self.values["energy"]))}

    def snapshot(self) -> dict[str, float]:
        return {k: round(v, 1) for k, v in self.values.items()}
