"""Woody Code Agent: план → параллельная генерация всеми ИИ → слияние лучшего → проверка → автоисправление → запуск-скрипты → ZIP.
Работает как агент (в духе Codex / Claude Code): помнит проект и умеет дорабатывать его по следующим просьбам."""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from backend.core import codegen

logger = logging.getLogger("woody.agent")

OPTS = {"max_tokens": 8000, "temperature": 0.25, "timeout": 150}
MAX_CTX = 28_000

PLAN_SYSTEM = (
    "Ты — архитектор в команде Woody Code (уровень Codex / Claude Code). Составь план решения задачи. "
    "Ответь ТОЛЬКО JSON без пояснений: "
    '{"project":"короткое_имя_латиницей","kind":"python|node|web|cpp|java|other","summary":"1-2 фразы",'
    '"files":[{"path":"относительный/путь","purpose":"что внутри"}],"run":"команда запуска"}. '
    "Простая задача — 1 файл. Большая — полноценная структура (код, зависимости, README, при необходимости тесты). "
    "Если проект уже есть (дан в контексте) — перечисли только файлы, которые нужно создать или изменить."
)

BUILD_SYSTEM = (
    "Ты — Woody Code: сильный senior-инженер уровня Codex / Claude Code. Пиши ПОЛНЫЙ, рабочий, продуманный код "
    "промышленного качества: обработка ошибок, валидация ввода, понятная структура, комментарии только по делу. "
    "Никаких заглушек, TODO и «...». Не упрощай задачу до игрушки — реализуй всё, что просили, и разумные детали. "
    "Используй современные библиотеки; зависимости перечисли в requirements.txt / package.json. "
    "Формат ответа — СТРОГО:\n"
    "Краткое введение (1-2 строки).\n"
    "=== file: путь/к/файлу ===\n```язык\nполный код файла\n```\n(так для каждого файла)\n"
    "Затем 'Запуск:' с командами. Пути относительные, без корневой папки. "
    "Если проект уже существует — выведи только изменённые/новые файлы ЦЕЛИКОМ."
)

MERGE_SYSTEM = (
    "Ты — главный ревьюер Woody Code. Ниже несколько вариантов решения одной задачи от разных ИИ. "
    "Собери ЛУЧШИЙ итоговый проект: возьми самые полные, корректные и аккуратные части, исправь ошибки и несостыковки "
    "(импорты, имена, зависимости, пути), убери дубли. Результат — полный набор файлов в формате "
    "=== file: путь ===\n```язык\nкод\n```\nбез лишних пояснений, в конце 'Запуск:'."
)

FIX_SYSTEM = (
    "Ты — Woody Code, исправляешь ошибки в проекте. Тебе дают найденные ошибки и текущие файлы. "
    "Выведи ТОЛЬКО исправленные файлы целиком в формате === file: путь ===\n```язык\nкод\n```"
)


def _ctx(files: dict[str, str]) -> str:
    out, total = [], 0
    for p, c in files.items():
        chunk = f"=== file: {p} ===\n```\n{c}\n```\n"
        if total + len(chunk) > MAX_CTX:
            out.append(f"=== file: {p} === (обрезан по размеру)\n")
            continue
        total += len(chunk); out.append(chunk)
    return "".join(out)


def _json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def check_files(files: dict[str, str]) -> list[str]:
    """Статическая проверка (код пользователя НЕ запускается): синтаксис Python / JSON / JS."""
    errs: list[str] = []
    node = shutil.which("node")
    tmp = Path(tempfile.mkdtemp(prefix="wc_"))
    try:
        for path, code in files.items():
            low = path.lower()
            try:
                if low.endswith(".py"):
                    ast.parse(code)
                elif low.endswith(".json"):
                    json.loads(code)
                elif node and low.endswith((".js", ".mjs", ".cjs")):
                    f = tmp / "c.js"; f.write_text(code, encoding="utf-8")
                    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=15)
                    if r.returncode:
                        errs.append(f"{path}: {r.stderr.strip().splitlines()[-1][:200] if r.stderr.strip() else 'ошибка синтаксиса'}")
            except SyntaxError as e:
                errs.append(f"{path}: строка {e.lineno}: {e.msg}")
            except json.JSONDecodeError as e:
                errs.append(f"{path}: JSON строка {e.lineno}: {e.msg}")
            except Exception as e:  # noqa: BLE001
                logger.debug("check %s: %s", path, e)
            if len(code.strip()) < 3:
                errs.append(f"{path}: файл пустой")
            if re.search(r"^\s*(#|//)?\s*(TODO|\.\.\.)\s*$", code, re.M) and low.endswith((".py", ".js", ".ts")):
                errs.append(f"{path}: остались заглушки (TODO / ...), нужно дописать реальный код")
        if "requirements.txt" in files and any(f.endswith(".py") for f in files):
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errs


def launchers(project: str, files: dict[str, str], run_hint: str = "") -> dict[str, str]:
    """Готовые скрипты запуска: start.bat (Windows) и start.sh (Linux/macOS)."""
    names = set(files)
    py_entry = next((n for n in ("main.py", "app.py", "bot.py", "run.py", "manage.py", "src/main.py", "app/main.py") if n in names), None) \
        or next((n for n in names if n.endswith(".py") and "/" not in n), None)
    out: dict[str, str] = {}
    if "package.json" in names:
        sh = "#!/usr/bin/env bash\ncd \"$(dirname \"$0\")\"\nnpm install && npm start\n"
        bat = "@echo off\r\ncd /d %~dp0\r\ncall npm install\r\ncall npm start\r\npause\r\n"
    elif py_entry:
        req_sh = "pip install -r requirements.txt\n" if "requirements.txt" in names else ""
        req_bat = "pip install -r requirements.txt\r\n" if "requirements.txt" in names else ""
        sh = f"#!/usr/bin/env bash\ncd \"$(dirname \"$0\")\"\npython3 -m venv .venv && source .venv/bin/activate\n{req_sh}python {py_entry}\n"
        bat = f"@echo off\r\ncd /d %~dp0\r\nif not exist .venv python -m venv .venv\r\ncall .venv\\Scripts\\activate.bat\r\n{req_bat}python {py_entry}\r\npause\r\n"
    elif "index.html" in names:
        sh = "#!/usr/bin/env bash\ncd \"$(dirname \"$0\")\"\n(xdg-open index.html || open index.html) 2>/dev/null\n"
        bat = "@echo off\r\ncd /d %~dp0\r\nstart index.html\r\n"
    else:
        return out
    if "start.sh" not in names: out["start.sh"] = sh
    if "start.bat" not in names: out["start.bat"] = bat
    return out


class Job:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.steps: list[dict] = []
        self.done = False
        self.result: dict | None = None
        self.created = time.time()

    def step(self, text: str, kind: str = "step") -> None:
        self.steps.append({"kind": kind, "text": text})


# Вопросы / болтовня — отвечаем текстом, НЕ генерируем новый проект
_CHAT_RE = re.compile(
    r"(?i)^\s*("
    r"как\s+(запустить|поставить|установить|скачать|открыть|пользоваться|работать)|"
    r"что\s+(это|здесь|внутри|дальше|делать)|"
    r"почему|зачем|объясни|поясни|расскажи|подскажи|"
    r"где\s+(файл|папка|лежит)|"
    r"можно\s+ли|а\s+если|ок|спасибо|понял|ясно|норм|"
    r"привет|хай|здарова|hello|hi|"
    r"какой\s+(язык|фреймворк|стек)|"
    r"покажи\s+(структуру|файлы|readme)|"
    r"help|/help"
    r")\b"
)
_GEN_RE = re.compile(
    r"(?i)(создай|напиши|сделай|сгенерируй|добавь|исправь|почини|перепиши|"
    r"доработай|refactor|implement|build|create|write|fix|add\s+file|"
    r"новый\s+проект|сделай\s+приложение|сделай\s+сайт|сделай\s+бота)"
)

def _is_chat(request: str, has_project: bool) -> bool:
    r = (request or "").strip()
    if not r:
        return True
    if r.startswith("/new"):
        return False
    if _GEN_RE.search(r):
        return False
    if has_project and _CHAT_RE.search(r):
        return True
    if has_project and len(r) < 120 and not any(x in r for x in ("```", "{", "def ", "function ", "class ", "import ")):
        # короткое уточнение без явного «сделай» — скорее вопрос
        if any(w in r.lower() for w in ("как", "что", "где", "почему", "зачем", "можно", "нужно ли", "?")):
            return True
    return False


CHAT_SYSTEM = (
    "Ты — Woody Code, злой но полезный senior-инженер в красном терминале. "
    "Отвечай по-русски коротко и по делу. У пользователя УЖЕ есть проект в контексте — "
    "НЕ создавай новые файлы и НЕ предлагай «сейчас сгенерирую», если тебя не просят явно "
    "написать/добавить/исправить код. Если спрашивают как запустить — дай точные команды "
    "из README/start.sh/start.bat. Если просят объяснить файл — объясни. "
    "Стиль: жёсткий, без воды, можно с лёгкой злостью."
)


async def run_agent(router, job: Job, request: str, workspace: dict, export_dir: Path, new_project: bool,
                    count_cb: Callable[[], None] | None = None) -> None:
    try:
        existing: dict[str, str] = {} if new_project else dict(workspace.get("files") or {})
        names = [p.name for p in router.candidates("conversation")]
        if not names:
            raise RuntimeError("Нет настроенных ИИ-провайдеров.")
        edit = bool(existing)
        ctx = f"\n\nТЕКУЩИЙ ПРОЕКТ (дорабатывай его):\n{_ctx(existing)}" if edit else ""

        # --- режим разговора: не генерируем ZIP, просто отвечаем ---
        if not new_project and _is_chat(request, bool(existing)):
            job.step("Думаю над ответом…", "step")
            msgs = [
                {"role": "system", "content": CHAT_SYSTEM},
                {"role": "user", "content": (request + ctx)[:MAX_CTX]},
            ]
            try:
                reply, who = await router.complete(msgs, max_tokens=1200, temperature=0.4, timeout=60)
            except Exception as exc:  # noqa: BLE001
                reply, who = f"Сейчас не могу ответить: {exc}", "?"
            job.step("Готово", "ok")
            job.result = {
                "reply": (reply or "").strip() or "Нечего сказать.",
                "chat": True,
                "provider": who,
                "project": workspace.get("project") or "project",
                "files": [],
            }
            if count_cb:
                count_cb()
            job.done = True
            return

        # 1) план
        job.step("Составляю план проекта…")
        try:
            plan_txt, _ = await router.complete([{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": request + ctx}], **{**OPTS, "max_tokens": 1500, "timeout": 60})
            plan = _json(plan_txt) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("plan failed: %s", exc); plan = {}
        project = codegen.slug(plan.get("project") or request)
        plist = [f.get("path") for f in plan.get("files", []) if isinstance(f, dict) and f.get("path")]
        job.step(f"План: {plan.get('summary') or 'ок'}" + (f" · файлов: {len(plist)}" if plist else ""), "plan")

        # 2) параллельная генерация ВСЕМИ ИИ
        job.step(f"Пишу код одновременно: {', '.join(names)}…")
        prompt = request + (f"\n\nПЛАН: {json.dumps(plan, ensure_ascii=False)[:3000]}" if plan else "") + ctx
        msgs = [{"role": "system", "content": BUILD_SYSTEM}, {"role": "user", "content": prompt}]
        cands = await router.complete_all(msgs, **OPTS)
        parsed = [(n, codegen.parse_files(t), t) for n, t in cands]
        parsed = [x for x in parsed if x[1]]
        if not parsed:
            raise RuntimeError("ИИ не вернули код. Попробуй уточнить задачу.")
        job.step(f"Ответили моделей: {len(parsed)} из {len(names)}")

        # 3) слияние лучшего
        if len(parsed) > 1:
            job.step("Сравниваю варианты и собираю лучший…")
            body = "\n\n".join(f"##### ВАРИАНТ {i + 1} ({n}) #####\n{_ctx(dict(fs))[:14000]}" for i, (n, fs, _) in enumerate(parsed))
            try:
                merged_txt, who = await router.complete([{"role": "system", "content": MERGE_SYSTEM}, {"role": "user", "content": f"ЗАДАЧА: {request}\n\n{body}"}], **{**OPTS, "max_tokens": 10000, "timeout": 180})
                mf = codegen.parse_files(merged_txt)
                final_txt = merged_txt if mf else parsed[0][2]
                new_files = dict(mf or parsed[0][1])
                if mf: job.step(f"Итоговый вариант собрал: {who}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("merge failed: %s", exc)
                final_txt, new_files = parsed[0][2], dict(parsed[0][1])
        else:
            final_txt, new_files = parsed[0][2], dict(parsed[0][1])

        files = {**existing, **new_files}

        # 4) проверка + автоисправление
        for attempt in range(3):
            errs = check_files(files)
            if not errs:
                job.step("Проверка пройдена: синтаксис и структура в порядке", "ok")
                break
            job.step(f"Нашёл проблем: {len(errs)}. Исправляю (попытка {attempt + 1})…", "warn")
            if attempt == 2:
                job.step("Часть замечаний осталась: " + "; ".join(errs[:3]), "warn")
                break
            try:
                bad = {p: files[p] for p in files if any(e.startswith(p + ":") for e in errs)} or files
                fix_txt, _ = await router.complete([{"role": "system", "content": FIX_SYSTEM},
                                                    {"role": "user", "content": "ОШИБКИ:\n" + "\n".join(errs) + "\n\nФАЙЛЫ:\n" + _ctx(bad)}], **OPTS)
                fixed = codegen.parse_files(fix_txt)
                if not fixed: break
                files.update(dict(fixed))
            except Exception as exc:  # noqa: BLE001
                logger.warning("fix failed: %s", exc); break

        # 5) README + запуск
        if len(files) > 1 and not any(p.lower() == "readme.md" for p in files):
            run_line = (plan.get("run") or "").strip()
            files["README.md"] = f"# {project}\n\n{plan.get('summary') or request}\n\n## Запуск\n\n" + (f"```\n{run_line}\n```\n" if run_line else "Запусти `start.bat` (Windows) или `./start.sh` (Linux/macOS).\n")
        files.update(launchers(project, files, plan.get("run") or ""))

        # 6) упаковка
        job.step("Упаковываю проект…")
        flist = list(files.items())
        info = codegen.build(export_dir, job.user_id, project if len(flist) > 1 else request, flist, folder=project)
        workspace["files"] = files
        workspace["name"] = project
        text = codegen.summary(final_txt) or "Готово."
        job.result = {"reply": text, "file": info, "files": info["files"], "edited": edit, "models": [n for n, _, _ in parsed]}
        if count_cb: count_cb()
        job.step("Готово", "ok")
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent failed")
        job.result = {"error": "agent_failed", "reply": f"Не получилось: {exc}"}
        job.step("Ошибка: " + str(exc)[:200], "err")
    finally:
        job.done = True
