from __future__ import annotations

import re
from dataclasses import dataclass

_DRAFT = re.compile(r"(?:вуди,?\s*)?напиши\s+(\S+?)\s*,?\s+что\s+(.+)", re.IGNORECASE | re.DOTALL)


@dataclass
class MessageDraft:
    recipient: str
    text: str


def parse_draft_request(text: str) -> MessageDraft | None:
    m = _DRAFT.search(text.strip())
    if not m:
        return None
    body = m.group(2).strip().rstrip(".")
    return MessageDraft(recipient=m.group(1), text=body[:1].upper() + body[1:] + ".")


def send_message(draft: MessageDraft) -> tuple[bool, str]:
    """TODO: реальный канал отправки (Telegram/SMS/e-mail) пока не подключён.

    Намеренно возвращает неуспех, чтобы Woody не сообщал о фиктивной отправке.
    """
    return False, "Канал отправки сообщений ещё не подключён, поэтому я ничего не отправил."
