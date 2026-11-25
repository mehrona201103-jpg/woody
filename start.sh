#!/usr/bin/env bash
# Запуск Woody одной командой (Linux / macOS)
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "Woody: http://localhost:8000  (ключи ИИ — в файле .env)"
uvicorn backend.main:app --host 0.0.0.0 --port 8000
