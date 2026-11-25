"""Woody Code: разбор ответа модели на файлы, сборка папки проекта и ZIP."""
from __future__ import annotations

import io
import re
import secrets
import zipfile
from pathlib import Path

CODE_SYSTEM = (
    "Ты — Woody Code, жёсткий и точный senior-инженер (Python, JS/TS, Java, C++, C#, bash/Linux/Kali). "
    "Пользователь просит написать код. Верни ТОЛЬКО так:\n"
    "1) Одна короткая строка-введение.\n"
    "2) Каждый файл строго в формате:\n"
    "=== file: путь/к/файлу.расширение ===\n```язык\nполный рабочий код файла\n```\n"
    "Если задача простая — ровно один файл. Если проект большой — все файлы проекта (код, requirements/package.json, README.md), "
    "пути относительные, без вложенной корневой папки.\n"
    "3) В конце 2–4 строки «Запуск:» с командами.\n"
    "Код полный, рабочий, без заглушек вроде «...». Не пиши «как ИИ»."
)

_FILE_RE = re.compile(r"^={2,}\s*file:\s*(.+?)\s*={2,}\s*\n```[^\n]*\n(.*?)\n```", re.S | re.M)
_BLOCK_RE = re.compile(r"```([\w+#.-]*)\n(.*?)\n```", re.S)
_EXT = {"python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts", "java": "java",
        "cpp": "cpp", "c++": "cpp", "c": "c", "csharp": "cs", "cs": "cs", "bash": "sh", "sh": "sh", "html": "html",
        "css": "css", "json": "json", "go": "go", "rust": "rs", "php": "php", "sql": "sql", "kotlin": "kt", "swift": "swift"}


def clean_path(p: str) -> str | None:
    p = p.strip().strip("`'\"").replace("\\", "/")
    parts = [x for x in p.split("/") if x not in ("", ".", "..")]
    if not parts:
        return None
    return "/".join(re.sub(r"[^\w.\- ]", "_", x)[:80] for x in parts)[:200]


def parse_files(reply: str) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for m in _FILE_RE.finditer(reply):
        path = clean_path(m.group(1))
        if path:
            files.append((path, m.group(2)))
    if files:
        return files
    blocks = _BLOCK_RE.findall(reply)
    for i, (lang, code) in enumerate(blocks):
        ext = _EXT.get(lang.lower(), lang.lower() or "txt")
        files.append((f"main.{ext}" if i == 0 else f"file{i + 1}.{ext}", code))
    return files


def slug(text: str) -> str:
    s = re.sub(r"[^\w\- ]", "", text, flags=re.U).strip().lower()
    s = re.sub(r"\s+", "_", s)[:40].strip("_")
    return s or "project"


def summary(reply: str) -> str:
    """Текст ответа без самих файлов — коротко для терминала."""
    text = _FILE_RE.sub("", reply)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def build(export_dir: Path, user_id: int, request: str, files: list[tuple[str, str]], folder: str | None = None) -> dict:
    """Один файл → просто файл. Несколько → папка (имя по запросу) упакована в ZIP."""
    export_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(10).replace("-", "a").replace("_", "b")
    if len(files) == 1:
        name = files[0][0].split("/")[-1]
        data = files[0][1].encode("utf-8")
        kind = "file"
    else:
        folder = folder or slug(request)
        name = folder + ".zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path, code in files:
                z.writestr(f"{folder}/{path}", code)
        data = buf.getvalue()
        kind = "zip"
    (export_dir / f"u{user_id}_{token}.bin").write_bytes(data)
    (export_dir / f"u{user_id}_{token}.name").write_text(name, encoding="utf-8")
    return {"id": token, "name": name, "kind": kind, "files": [f[0] for f in files], "size": len(data)}


def locate(export_dir: Path, user_id: int, token: str) -> tuple[Path, str] | None:
    if not re.fullmatch(r"[A-Za-z0-9]{6,20}", token or ""):
        return None
    b = export_dir / f"u{user_id}_{token}.bin"
    n = export_dir / f"u{user_id}_{token}.name"
    if not b.exists() or not n.exists():
        return None
    return b, n.read_text(encoding="utf-8")
