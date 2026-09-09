# Kaigo Widgets: операционные и административные инструкции

Этот файл сохраняет операционные разделы прежнего README.md и docs/KAIGO_SPACE_OPERATIONS.md. Значения секретов и credentials здесь не приводятся.

## Что развернуто

- Домены: kaigo.online, kaigo.space.
- Docker-сервисы: ai_project_app, ai_project_db.
- Widgets backend: 127.0.0.1:8080.
- PostgreSQL: 127.0.0.1:5432.
- История сообщений: SQLite-файл из MESSAGE_DATABASE_URL, по умолчанию /app/data/dialogs.sqlite3.
- Статическая визитка и widgets используют отдельные маршруты Nginx.
- kaigo.space/real-time/ обслуживается отдельным realtime-сервисом, не этим приложением.

Публичные маршруты, сохраненные из прежнего README:

- демо-виджеты: /w/demka, /w/dental-consultant, /w/realty-consultant, /w/auto-service-consultant, /w/beauty-consultant;
- клиентский кабинет: /client/login, /client, /client/widgets/{id}/dialogs;
- healthcheck: /api/health, /api/health/ai;
- админка приложения: /admin и /admin/widgets при прямом доступе к aiohttp на 127.0.0.1:8080.

Публичный /admin/... может перехватываться статической SPA. Для внешнего доступа к aiohttp-админке нужен отдельный Nginx location ^~ /admin с proxy_pass http://127.0.0.1:8080 перед статическими SPA-location.

## Маршрутизация Nginx

Файл сервера: /etc/nginx/sites-available/kaigo.space.

| Путь | Назначение |
| --- | --- |
| / | Главная страница Widgets, 127.0.0.1:8080 |
| /client/* | Кабинет заказчика |
| /admin/* | Админка Widgets |
| /w/* | Публичные виджеты |
| /api/health, /api/health/ai | Healthcheck Widgets |
| /real-time/ | Отдельный realtime voice agents frontend, 127.0.0.1:8001/index.html |
| /real-time/api/* | API realtime-сервиса с удалением префикса Nginx |

Карта aiohttp-маршрутов:

| Маршрут | Назначение |
| --- | --- |
| /api/health | Доступность приложения |
| /api/health/ai | Проверка провайдера Gemini, модели и ключа |
| /w/{slug} | Рендер публичной страницы виджета |
| /w/{slug}/api/send | Текстовое сообщение в AI |
| /w/{slug}/api/history | История текущего пользователя |
| /w/{slug}/api/audio | Распознавание и обработка аудио |
| /client/login | Вход заказчика |
| /client | Список виджетов заказчика |
| /client/widgets/{id}/dialogs | Последние диалоги виджета |
| /admin | Внутренняя админка |

## Сервисы и проверки

Widgets:

~~~
cd /root/ai_project
docker compose ps
docker compose logs -f app
~~~

Realtime:

~~~
cd /root/realtime
docker compose ps
docker logs -f voice-agent-container
~~~

Gemini proxy:

~~~
systemctl status gemini-proxy-tunnel
ssh root@<us-proxy-host> 'systemctl status gemini-proxy'
~~~

Перед и после изменения маршрутов:

~~~
nginx -t
systemctl reload nginx
curl -k https://kaigo.space/
curl -k https://kaigo.space/real-time/
curl -k https://kaigo.space/real-time/api/agents
curl -k https://kaigo.space/api/health
curl -k https://kaigo.space/api/health/ai
~~~

Smoke-тест текстового виджета:

~~~
curl -k -H 'Content-Type: application/json' \
  --data '{"message":"Проверка связи","user_id":"ops-smoke"}' \
  https://kaigo.space/w/demka/api/send
~~~

## Переменные окружения

Скопировать .env.example в .env и заполнить значения вне Git:

~~~
cp .env.example .env
~~~

Ключевые группы настроек:

- DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB — PostgreSQL;
- APP_HOST, APP_PORT — адрес aiohttp;
- MESSAGE_DATABASE_URL — SQLite для истории сообщений;
- GOOGLE_AI_API_KEY или поддерживаемые алиасы, GOOGLE_AI_MODEL, GOOGLE_AI_STT_MODEL;
- GOOGLE_AI_BASE_URL, GOOGLE_AI_NATIVE_BASE_URL, GOOGLE_AI_REQUEST_TIMEOUT, GOOGLE_AI_MAX_RETRIES;
- PROMPT_GOOGLE_DOC_URL, SERVICE_ACCOUNT_FILE, DEFAULT_SYSTEM_PROMPT;
- ADMIN_PASSWORD, ADMIN_EMAILS;
- Telegram-параметры TELEGRAM_API_ID, TELEGRAM_API_HASH, ADMIN_BOT_TOKEN, ADMIN_CHANNEL_ID.

Не коммитить .env, credentials JSON, .session, логи и локальные базы.

## База данных

PostgreSQL хранит:

- tenants;
- users;
- widgets;
- widget_assets;
- widget_bindings.

Схема создается при старте через SQLAlchemy metadata. Alembic-миграции лежат в migrations/; прежняя серверная заметка отдельно указывает, что на текущем сервере таблица alembic_version не была заведена. SQLite хранит историю сообщений и в Docker вынесен в ./data:/app/data.

## Gemini и промпты

Текстовые запросы идут в Google AI Studio через OpenAI-compatible endpoint. Голосовые сообщения обрабатываются native Gemini generateContent с аудио-входом. Если Google Docs недоступен, используется DEFAULT_SYSTEM_PROMPT.

prompt_source поддерживает URL Google Docs и inline-текст. Inline-промпты используются preset-виджетами, чтобы разные отраслевые сценарии имели разные инструкции.

Проверка chat endpoint:

~~~
curl http://127.0.0.1:8080/api/health/ai
~~~

Применяемые коды проблем: missing_api_key, invalid_api_key, model_not_found, location_unsupported, ошибки соединения и таймауты. После изменения ключа пересоздать контейнер:

~~~
docker compose up -d --force-recreate app
~~~

При проблемах DNS внутри контейнера прежняя инструкция предлагает проверить generativelanguage.googleapis.com и oauth2.googleapis.com; не выводить ключи в логи и чат.

## Готовые шаблоны

Список и описание preset-ов находятся в [docs/WIDGET_PRESETS.md](https://github.com/NNFall/widgets/blob/main/docs/WIDGET_PRESETS.md). Применение на сервере:

~~~
docker exec ai_project_app python scripts/seed_widget_presets.py
~~~

Скрипт создает недостающие виджеты, обновляет preset-настройки и добавляет новую asset-версию только при изменении HTML; пользовательские виджеты не удаляет.

Smoke-тесты preset-ов:

~~~
curl -k https://kaigo.space/
curl -k https://kaigo.space/w/demka
curl -k https://kaigo.space/w/dental-consultant
curl -k https://kaigo.space/w/realty-consultant
curl -k https://kaigo.space/w/auto-service-consultant
curl -k https://kaigo.space/w/beauty-consultant
~~~

Пример AI-запроса:

~~~
curl -k -H 'Content-Type: application/json' \
  --data '{"message":"У меня болит зуб, хочу записаться завтра вечером","user_id":"preset-smoke"}' \
  https://kaigo.space/w/dental-consultant/api/send
~~~

Tracked screenshots:

- docs/screenshots/widget-demka.jpg — публичный чат-виджет;
- docs/screenshots/client-login.jpg — вход заказчика;
- docs/screenshots/admin-login.jpg — вход в админку приложения;
- docs/screenshots/site-root.jpg — отдельная статическая Kaigo-визитка, не доказательство интерфейса Widgets backend.

## Безопасность и обслуживание

- Приложение и PostgreSQL в Compose привязаны к 127.0.0.1; наружу их публикует Nginx.
- На сервере задавать непустой ADMIN_PASSWORD.
- Для ограничения админки по email использовать ADMIN_EMAILS.
- Секреты не должны попадать в Docker image и Git; это поддерживается .gitignore и .dockerignore.

Контейнеры и логи:

~~~
docker compose ps
docker compose logs -f app
~~~

Бэкап PostgreSQL:

~~~
docker exec ai_project_db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
~~~

Бэкап истории сообщений:

~~~
cp data/dialogs.sqlite3 dialogs.sqlite3.backup
~~~

## Rollback Nginx и realtime

Перед routing-изменениями создаются резервные копии:

~~~
ls -l /etc/nginx/sites-available/kaigo.space.bak-*
~~~

Откат Nginx:

~~~
cp /etc/nginx/sites-available/kaigo.space.bak-YYYYMMDD-HHMMSS /etc/nginx/sites-available/kaigo.space
nginx -t
systemctl reload nginx
~~~

Откат realtime frontend:

~~~
ls -l /root/realtime/index.html.bak-*
cp /root/realtime/index.html.bak-YYYYMMDD-HHMMSS /root/realtime/index.html
cd /root/realtime
docker compose up -d --build voice-agent
~~~
