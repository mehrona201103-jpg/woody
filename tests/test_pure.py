"""Тесты без внешних зависимостей (эмоции, подтверждения, извлечение памяти, сообщения)."""
from backend.actions.messages import parse_draft_request, send_message
from backend.core.emotion import EmotionState, detect_events
from backend.permissions.confirmation import parse_confirmation


def test_emotion_events_change_state():
    s = EmotionState()
    s.apply("pleasant_chat")
    assert s.values["happiness"] == 60
    s.apply("insult")
    assert s.values["sadness"] == 15 and s.values["anger"] == 10
    s.apply("task_success")
    assert s.values["confidence"] == 53


def test_emotion_clamped_and_face():
    s = EmotionState()
    for _ in range(30):
        s.apply("insult")
    assert max(s.values.values()) <= 100
    assert s.face_state() == "angry"


def test_emotion_voice_style_in_range():
    s = EmotionState()
    st = s.voice_style()
    assert 0.7 <= st["rate"] <= 1.3 and 0.7 <= st["pitch"] <= 1.3


def test_detect_events():
    assert detect_events("Спасибо!") == ["pleasant_chat"]
    assert "insult" in detect_events("ты дурак")


def test_confirmation_yes_variants():
    for phrase in ["да", "Конечно!", "отправляй", "можно", "Подтверждаю", "Да, отправляй."]:
        assert parse_confirmation(phrase) == "yes", phrase


def test_confirmation_no_wins():
    assert parse_confirmation("нет") == "no"
    assert parse_confirmation("не отправляй, отмена") == "no"
    assert parse_confirmation("да, но не надо") == "no"
    assert parse_confirmation("расскажи анекдот") == "unclear"


def test_strict_confirmation():
    assert parse_confirmation("да", "подтверждаю удаление") == "unclear"
    assert parse_confirmation("Подтверждаю удаление!", "подтверждаю удаление") == "yes"
    assert parse_confirmation("нет", "подтверждаю удаление") == "no"


def test_message_draft_and_send_is_honest():
    d = parse_draft_request("Вуди, напиши Алику, что я занят и перезвоню вечером")
    assert d and d.recipient == "Алику" and d.text.startswith("Я занят")
    ok, info = send_message(d)
    assert ok is False and info  # отправка не подключена — успех не имитируется
