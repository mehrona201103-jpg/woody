@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if not exist .env copy .env.example .env
echo Woody: http://localhost:8000  (klyuchi II - v fayle .env)
start "" http://localhost:8000
uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause
