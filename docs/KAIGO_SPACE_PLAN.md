# Kaigo.space Migration Plan

Дата: 2026-06-03

## Цель

Разделить домен `kaigo.space` на два независимых пользовательских входа:

- `https://kaigo.space/` - главная страница сервиса Kaigo Widgets.
- `https://kaigo.space/real-time/` - старый интерфейс real-time voice agents из `/root/realtime`.

## Текущее состояние

- Widgets backend: Docker service `ai_project_app`, порт `127.0.0.1:8080`.
- Widgets database: Docker service `ai_project_db`, порт `127.0.0.1:5432`.
- Realtime backend: Docker container `voice-agent-container`, порт `127.0.0.1:8001`.
- Gemini API: идет через SSH tunnel `gemini-proxy-tunnel.service` на основной машине и `gemini-proxy.service` на US-сервере.
- `kaigo.space` до миграции полностью проксировался на realtime service.

## Этапы

1. Обновить widgets backend root `/`, чтобы вместо редиректа в `/admin` показывалась главная страница сервиса.
2. Сделать realtime frontend prefix-aware: все запросы `/api/...` должны работать через `/real-time/api/...`, когда страница открыта под `/real-time/`.
3. Перенастроить nginx:
   - `/real-time/` отдавать как realtime `index.html`;
   - `/real-time/api/*` проксировать на `127.0.0.1:8001`;
   - `/real-time`, `/Real-time`, `/realtime` редиректить на `/real-time/`;
   - все остальные пути отдавать widgets backend на `127.0.0.1:8080`.
4. Проверить:
   - `GET https://kaigo.space/`;
   - `GET https://kaigo.space/real-time/`;
   - `GET https://kaigo.space/real-time/api/agents`;
   - `GET https://kaigo.space/api/health`;
   - `GET https://kaigo.space/api/health/ai`;
   - `POST https://kaigo.space/w/demka/api/send`.
5. Обновить GitHub для файлов widgets repo.

## Будущие улучшения

- Вынести realtime `OPENAI_API_KEY` и `ADMIN_API_KEY` из кода `/root/realtime/server.py` в `.env`.
- Заменить клиентский hardcoded admin key в `/root/realtime/index.html` на серверную авторизацию.
- Добавить отдельный README для `/root/realtime`, потому что этот каталог сейчас не является git-репозиторием.
- Добавить скриншоты `kaigo.space/` и `kaigo.space/real-time/` после стабилизации дизайна.
