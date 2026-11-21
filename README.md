# Woody v0.2

Голосовой AI-компаньон: FastAPI + SQLite + vanilla JS. Woody Core (личность, память, эмоции, права) отделён от AI-моделей (AI Router).

## Локальный запуск
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # впишите AI_PROVIDER_1_API_KEY
uvicorn backend.main:app --reload
# открыть http://localhost:8000
pytest
```

## Деплой на Render
1. Загрузите папку на GitHub (`.env` в репозиторий не попадает).
2. Render → **New → Blueprint** → выберите репозиторий (файл `render.yaml` подхватится).
3. В **Environment** задайте Variables (см. ниже) и нажмите Deploy.
4. Откройте URL сервиса; при первом входе браузер спросит `WOODY_ACCESS_TOKEN` (возьмите его в Environment).

### Variables
| Ключ | Обязательно | Значение |
|---|---|---|
| `AI_PROVIDER_1_BASE_URL` | да | напр. `https://api.openai.com/v1` (любой OpenAI-совместимый API) |
| `AI_PROVIDER_1_API_KEY` | да | ключ провайдера (секрет) |
| `AI_PROVIDER_1_MODEL` | да | напр. `gpt-4o-mini` |
| `AI_PROVIDER_1_NAME` / `_TASKS` | нет | `main` / `conversation` (также `vision,coding,translation`) |
| `AI_PROVIDER_2_*` … `5_*` | нет | резервные провайдеры, тот же формат |
| `WOODY_ACCESS_TOKEN` | **да** | пароль доступа к API (в render.yaml генерируется автоматически) |
| `PYTHON_VERSION` | да | `3.12.8` |
| `APP_ENV` | нет | `production` |
| `LOG_LEVEL` | нет | `INFO` |
| `PUBLIC_BASE_URL` | нет | Адрес для QR (`https://woody.onrender.com`). На Render берётся из `RENDER_EXTERNAL_URL` автоматически; на своём домене задайте вручную |
| `WOODY_DB_PATH`, `WOODY_SHARED_DIR` | нет | `/var/data/woody.db`, `/var/data/shared` — только с Persistent Disk |

## Деплой на Railway
1. Загрузите папку на GitHub (`.env` в репозиторий не попадает).
2. Railway → **New Project → Deploy from GitHub repo**. Конфигурация берётся из `railway.json` (старт, healthcheck `/api/health`).
3. **Settings → Networking → Generate Domain** (публичный адрес; от него строится QR).
4. **Variables** — добавьте:

| Ключ | Значение |
|---|---|
| `AI_PROVIDER_1_BASE_URL` | напр. `https://api.openai.com/v1` |
| `AI_PROVIDER_1_API_KEY` | ключ провайдера (секрет) |
| `AI_PROVIDER_1_MODEL` | напр. `gpt-4o-mini` |
| `WOODY_ACCESS_TOKEN` | придуманный длинный пароль (**обязательно**) |
| `APP_ENV` | `production` |
| `WOODY_DB_PATH` | `/data/woody.db` |
| `WOODY_SHARED_DIR` | `/data/shared` |
| `PUBLIC_BASE_URL` | необязательно; по умолчанию `https://$RAILWAY_PUBLIC_DOMAIN` |

5. **Постоянная память:** сервис → правый клик / `⌘K` → **Add Volume**, mount path `/data`. Без Volume база SQLite стирается при каждом деплое.
6. Откройте домен: браузер спросит `WOODY_ACCESS_TOKEN`. Держите ровно **1 реплику** (SQLite и ожидающие подтверждения живут в одном процессе).

## Что нового в v0.2
- Единый API-клиент (`js/api.js`), проверка `GET /api/health` → `{"status":"ok","service":"woody"}`, баннер «Не удалось подключиться к Woody» + «Повторить»; реальные ошибки — в console.
- Face Engine: 11 состояний, плавные переходы, моргание, взгляд, talking-анимация; вход `{emotion, activity, energy}`.
- Hand Transfer: ✋ → разрешение камеры → MediaPipe Hands (в браузере) → OPEN_PALM→FIST (удержание 600 мс, ≥6 кадров, cooldown 2,5 с) → QR `/join/{token}`.
- Токены: 192 бита CSPRNG, TTL 5 мин, одноразовые, в БД только sha256.
- Изоляция: память, разрешения, настройки, история, эмоции — по `user_id`; Woody Identity общая. Сессия пользователя — секрет в браузере (в БД хэш).
- Миграция существующей БД v0.1 автоматическая; **старые глобальные разрешения сбрасываются** (теперь они персональные).

## Тесты
`pytest` (backend + статические проверки) и `node tests/test_gestures.js` (жесты и защита от случайного кулака).

## Известные ограничения
- Free-план Render: SQLite и память Woody стираются при перезапуске. Для постоянной памяти — платный план + Disk.
- Голос (STT/TTS) работает через браузер; нужен HTTPS (Render даёт) и Chrome/Edge/Safari.
- Отправка сообщений/файлов не подключена к реальным каналам — Woody честно сообщает об этом.
- Пороги классификации жестов — эвристика; проверить на реальной руке и при необходимости подстроить в `gestures.js`.
- MediaPipe и qrcode.js грузятся с CDN (jsdelivr/cdnjs): нужен интернет на телефоне.
- Фоновый режим: только интерфейс-заглушка, Background Companion — TODO.
- Ожидающие подтверждения хранятся в памяти процесса (один worker).
