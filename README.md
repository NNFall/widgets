# Kaigo Widgets

Платформа для AI-виджетов на домене [kaigo.online](https://kaigo.online). Проект хранит заказчиков, пользователей и настройки виджетов в PostgreSQL, а историю сообщений виджетов - в отдельной SQLite-базе.

На текущем сервере этот проект живёт рядом с отдельной статической визиткой: корень домена и часть SPA-маршрутов отдаются из `/root/kaigo/dist`, а маршруты виджетов проксируются в aiohttp-приложение на `127.0.0.1:8080`.

## Что сейчас развернуто

- Домен: `kaigo.online`
- Nginx проксирует неперехваченные статикой пути на `127.0.0.1:8080`
- Docker-сервисы: `ai_project_app`, `ai_project_db`
- Статический сайт на корне: `/root/kaigo/dist`
- Публичный демо-виджет: [`/w/demka`](https://kaigo.online/w/demka)
- Админка приложения: `http://127.0.0.1:8080/admin` на сервере
- Кабинет клиента: [`/client/login`](https://kaigo.online/client/login)
- Healthcheck: [`/api/health`](https://kaigo.online/api/health)

Публичный `/admin/...` сейчас перехватывается статическим сайтом и не попадает в aiohttp-админку. Если админка должна открываться снаружи через домен, добавьте отдельный nginx `location ^~ /admin` с `proxy_pass http://127.0.0.1:8080` перед статическими SPA-location.

## Скриншоты

| Страница | Скриншот |
| --- | --- |
| Корень домена | ![Site root](docs/screenshots/site-root.jpg) |
| Публичный виджет | ![Public widget](docs/screenshots/widget-demka.jpg) |
| Вход в админку приложения | ![Admin login](docs/screenshots/admin-login.jpg) |
| Вход клиента | ![Client login](docs/screenshots/client-login.jpg) |

## Стек

- Python 3.11
- aiohttp
- SQLAlchemy + asyncpg
- PostgreSQL
- SQLite + aiosqlite для истории сообщений
- VseGPT/OpenAI-compatible API для чата и STT
- Google Docs API как опциональный источник системного промпта
- Docker Compose + Nginx

## Маршруты nginx/static

| Route | Назначение |
| --- | --- |
| `/` | Статическая визитка из `/root/kaigo/dist` на публичном домене |
| `/assets/*` | Статические assets визитки |
| `/about*`, `/projects*`, `/project/*`, `/login*`, `/auth*`, `/admin*` | SPA-маршруты визитки, отдаются через `index.html` |

## Маршруты aiohttp backend

| Route | Назначение |
| --- | --- |
| `/api/health` | Проверка доступности aiohttp-приложения |
| `/w/{slug}` | Публичная страница виджета |
| `/w/{slug}/api/send` | Отправка текстового сообщения в AI |
| `/w/{slug}/api/history` | История текущего пользователя виджета |
| `/w/{slug}/api/audio` | Отправка аудио на распознавание и обработку |
| `/admin` | Админка виджетов и заказчиков, доступна напрямую на `127.0.0.1:8080` |
| `/client/login` | Вход клиента, проксируется через публичный домен |

Если открыть само aiohttp-приложение напрямую, `/` редиректит в `/admin`.

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните значения:

```bash
cp .env.example .env
```

Ключевые переменные:

- `DATABASE_URL` - PostgreSQL DSN для SQLAlchemy, например `postgresql+asyncpg://user:password@db:5432/dbname`
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` - параметры контейнера PostgreSQL
- `VSEGPT_API_KEY` - ключ API для AI/STT
- `MESSAGE_DATABASE_URL` - путь к SQLite истории сообщений, по умолчанию `/app/data/dialogs.sqlite3`
- `PROMPT_GOOGLE_DOC_URL` - Google Doc с системным промптом и блоком `---TOOLS---`
- `SERVICE_ACCOUNT_FILE` - путь к JSON-credentials Google service account, если нужен Google Docs prompt
- `DEFAULT_SYSTEM_PROMPT` - fallback-промпт, если Google Docs недоступен
- `ADMIN_PASSWORD` - общий пароль для входа в админку
- `ADMIN_EMAILS` - опциональный список разрешенных email через запятую

Не коммитьте `.env`, `credentials.json`, `.session`, логи и локальные базы.

## Запуск

```bash
docker compose up -d --build
docker compose logs -f app
```

Проверка:

```bash
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/w/demka
```

## База данных

PostgreSQL хранит:

- `tenants`
- `users`
- `widgets`
- `widget_assets`
- `widget_bindings`

Схема создается при старте приложения через SQLAlchemy metadata. Alembic-миграции лежат в `migrations/`, но на текущем сервере таблица `alembic_version` не была заведена.

SQLite хранит историю сообщений виджетов. Для Docker она вынесена в volume `./data:/app/data`.

## AI и промпты

Виджет отправляет запросы в VseGPT через OpenAI-compatible API. Если Google Docs credentials отсутствуют или Google Docs недоступен, приложение использует `DEFAULT_SYSTEM_PROMPT` и продолжает отвечать.

Если AI возвращает `Connection error`, проверьте DNS внутри контейнера:

```bash
docker exec -i ai_project_app python - <<'PY'
import socket
for host in ["api.vsegpt.ru", "oauth2.googleapis.com"]:
    print(host, socket.getaddrinfo(host, 443)[0][4])
PY
```

В `docker-compose.yml` для приложения уже задан DNS `1.1.1.1` и `8.8.8.8`; после изменения compose нужно пересоздать контейнер.

Если VseGPT отвечает `User with this API key not found`, сеть уже работает, но значение `VSEGPT_API_KEY` в `.env` не принято API. В этом случае замените ключ на актуальный и пересоздайте контейнер:

```bash
docker compose up -d --force-recreate app
```

## Безопасность

- Приложение и PostgreSQL в compose привязаны к `127.0.0.1`, наружу их должен публиковать только Nginx.
- На продакшене задайте `ADMIN_PASSWORD`.
- Для ограничения админки по email задайте `ADMIN_EMAILS`.
- Секреты не должны попадать в Docker image и Git. Для этого настроены `.gitignore` и `.dockerignore`.

## Обслуживание

Посмотреть контейнеры:

```bash
docker compose ps
```

Логи приложения:

```bash
docker compose logs -f app
```

Бэкап PostgreSQL:

```bash
docker exec ai_project_db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
```

Бэкап истории сообщений:

```bash
cp data/dialogs.sqlite3 dialogs.sqlite3.backup
```
