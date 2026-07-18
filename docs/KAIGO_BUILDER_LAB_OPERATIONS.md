# Эксплуатация Kaigo Gemini Builder Lab

## Назначение

Builder Lab — отдельная экспериментальная среда, которая по текстовому заданию
собирает виджет через Gemini, показывает реальные промежуточные ревизии и
пропускает каждую из них через детерминированную проверку Kaigo.

Доступны два режима:

- `Gemini staged` — последовательная сборка: арт-дирекция, каркас,
  идентичность, диалог, движение и полировка;
- `Antigravity agent` — автономная сборка в удалённой среде Google с загрузкой
  и независимой проверкой итогового архива.

Lab не импортирует production-сервер, не подключается к PostgreSQL, не изменяет
таблицы виджетов и не умеет публиковать embed-код. Запуски хранятся в памяти.
Отдельно может сохраняться один финальный проверенный демо-артефакт.

## Публичный доступ

- `https://kaigo.space/builder-demo/` — открытый сохранённый пример. Модель с
  этой страницы запустить нельзя.
- `https://kaigo.space/builder/` — полный Builder Lab. Доступ закрыт логином и
  паролем nginx, чтобы посторонние не расходовали Gemini-баланс.

Порт процесса не открыт наружу: Docker публикует его только как
`127.0.0.1:8091`. Nginx проксирует строго перечисленные маршруты. Статический
`kaigo.online` и корневые production-маршруты не меняются.

## Настройки

Для Gemini нужен один из ключей, в порядке приоритета:

```text
GEMINI_API_KEY
GOOGLE_AI_API_KEY
GOOGLE_API_KEY
```

Основные параметры:

```env
GOOGLE_AI_NATIVE_BASE_URL=https://generativelanguage.googleapis.com/v1beta
GEMINI_BUILDER_MODEL=gemini-3.5-flash
GEMINI_BUILDER_TEMPERATURE=0.9
GEMINI_BUILDER_MAX_REPAIRS=3
KAIGO_BUILDER_DEFAULT_ENGINE=direct
KAIGO_BUILDER_ENABLE_ANTIGRAVITY=true
KAIGO_BUILDER_RUN_TTL_SECONDS=3600
KAIGO_BUILDER_MAX_RUNS=100
KAIGO_BUILDER_DEMO_PATH=/app/data/builder-demo/latest.json
```

Ключи нельзя передавать в URL, записывать в Git или выводить в логи. На рабочем
сервере `GOOGLE_AI_NATIVE_BASE_URL` может указывать на защищённый Gemini-only
маршрут через американский сервер.

## Локальный запуск

```powershell
$env:GEMINI_API_KEY = "значение-из-хранилища-секретов"
python scripts/run_builder_lab.py
```

Открыть `http://127.0.0.1:8091`.

Проверка без браузера:

```powershell
python scripts/smoke_builder_lab.py --engine direct
```

Сохранение постоянного демо после успешного реального прогона:

```powershell
python scripts/smoke_builder_lab.py `
  --engine direct `
  --demo-output data/builder-demo/latest.json `
  --model gemini-3.5-flash
```

Файл содержит только исходный запрос, модель, длительность, расход токенов и
финальный артефакт. Ключи, provider diagnostics и полная история ответов туда не
попадают. Перед каждым показом файл и артефакт проверяются заново.

## Docker на сервере

```bash
mkdir -p data/builder-demo
docker compose --profile builder-lab build builder-lab
docker compose --profile builder-lab up -d --no-deps builder-lab
docker compose --profile builder-lab logs -f --tail=200 builder-lab
```

Проверка изоляции:

```bash
docker compose --profile builder-lab ps
ss -lntp | grep 8091
curl -I http://127.0.0.1:8091/
curl -I http://127.0.0.1:8091/demo
```

В `ss` должен быть только `127.0.0.1:8091`, а не внешний `0.0.0.0:8091`.

## Маршруты nginx

Полный Builder требует Basic Auth и удаляет `/builder/` перед передачей во
внутренний сервис:

```nginx
location = /builder {
    return 301 /builder/;
}

location ^~ /builder/ {
    auth_basic "Kaigo Builder";
    auth_basic_user_file /etc/nginx/.htpasswd-kaigo-builder;
    proxy_pass http://127.0.0.1:8091/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 900s;
}
```

Открытое демо проксируется только на read-only обработчики:

```nginx
location = /builder-demo {
    return 301 /builder-demo/;
}

location = /builder-demo/ {
    proxy_pass http://127.0.0.1:8091/demo;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location = /builder-demo/preview {
    proxy_pass http://127.0.0.1:8091/demo/preview;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Перед изменением нужно сделать timestamped backup, затем выполнить:

```bash
nginx -t
systemctl reload nginx
```

## Как читать журнал стадий

Preview обновляется только после последовательности:

```text
artifact.validated  status=completed
artifact.committed  status=completed
```

Окончание ответа модели само по себе не означает, что артефакт принят. Если
валидатор нашёл проблему, предыдущий preview сохраняется, а Gemini получает
структурированный список ошибок. Максимум выполняются две repair-попытки.

Нормальный direct-прогон содержит пять зафиксированных ревизий:

```text
art_direction -> foundation -> identity -> conversation -> motion_polish
```

## Проверка после публикации

```bash
curl -fsS https://kaigo.space/builder-demo/ >/dev/null
curl -sS -o /dev/null -w '%{http_code}\n' https://kaigo.space/builder/
curl -fsS https://kaigo.space/ >/dev/null
curl -fsS https://kaigo.space/w/demka >/dev/null
curl -fsS https://kaigo.online/ >/dev/null
```

Без авторизации `/builder/` должен отвечать `401`. С авторизацией — `200`.
Дополнительно проверяются app/db контейнеры, loopback-порт и browser console.

## Остановка и откат

Остановить только лабораторию:

```bash
docker compose --profile builder-lab stop builder-lab
docker compose --profile builder-lab rm -f builder-lab
```

Для отключения публичного доступа восстановить резервную копию
`/etc/nginx/sites-available/kaigo.space`, проверить `nginx -t` и выполнить
`systemctl reload nginx`. Production app, db и статический сайт при этом не
перезапускаются.

## Текущие ограничения

- Один полный пятиэтапный прогон может быть медленным и дорогим; перед
  коммерческим запуском нужно уменьшить повторную передачу полного артефакта и
  объём repair-контекста.
- Текущий публичный полный Builder рассчитан на владельца проекта, а не на
  нескольких арендаторов: нет аккаунтов, биллинга, очереди durable jobs и
  автоматической публикации embed-кода.
- Antigravity остаётся сравнительным экспериментом и не является fallback для
  рабочего direct-режима.
