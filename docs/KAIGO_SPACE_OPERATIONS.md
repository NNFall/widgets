# Kaigo.space Operations

## Routing

На текущем сервере `kaigo.space` обслуживается активным обычным файлом, а не
symlink на `sites-available`:

```text
/etc/nginx/sites-enabled/kaigo.space
```

Перед изменением всегда подтверждать фактический источник через `nginx -T` и
`ls -la /etc/nginx/sites-enabled`. Редактирование одноимённого файла только в
`sites-available` не меняет живую маршрутизацию.

Ожидаемая схема:

| Путь | Назначение |
| --- | --- |
| `/` | Widgets home, `127.0.0.1:8080` |
| `/client/*` | Widgets client cabinet |
| `/admin/*` | Widgets admin |
| `/w/*` | Public widgets |
| `/api/health`, `/api/health/ai` | Widgets health endpoints |
| `/real-time/` | Realtime voice agents index page, `127.0.0.1:8001/index.html` |
| `/real-time/api/*` | Realtime API, prefix stripped by nginx |

## Services

Widgets:

```bash
cd /root/ai_project
docker compose ps
docker compose logs -f app
```

Realtime:

```bash
cd /root/realtime
docker compose ps
docker logs -f voice-agent-container
```

Gemini proxy:

```bash
systemctl status gemini-proxy-tunnel
ssh root@<us-proxy-host> 'systemctl status gemini-proxy'
```

## Deploy Checks

```bash
nginx -t
systemctl reload nginx
curl -k https://kaigo.space/
curl -k https://kaigo.space/real-time/
curl -k https://kaigo.space/real-time/api/agents
curl -k https://kaigo.space/api/health
curl -k https://kaigo.space/api/health/ai
```

## Лендинг и Studio

Статические файлы в production хранятся как неизменяемые релизы:

```text
/var/www/kaigo-marketing/releases/<release-id>/
/var/www/kaigo-marketing/current -> /var/www/kaigo-marketing/releases/<release-id>
```

Контракт маршрутов находится в
`deploy/nginx/kaigo-marketing-site.conf`. Это фрагмент существующего блока
`server`, а не полный виртуальный хост. При первой интеграции:

1. Найти активный конфиг через `nginx -T`; не считать автоматически, что это
   файл из `sites-available`.
2. Сохранить все существующие exact/prefix location, включая `/real-time/`,
   `/builder-demo/`, `/builder-comparison/` и маршруты ACME/сертификатов.
3. Заменить только существующий fallback `location /` и существующий блок
   `/builder/` соответствующими блоками из фрагмента.
4. Добавить exact-блоки `/`, `/favicon.svg`, `/studio`, `/studio/` и
   `/assets/`.
5. Подтвердить, что `/etc/nginx/.htpasswd-kaigo-builder` продолжает защищать
   только legacy `/builder/`. SaaS Studio публично открывает оболочку, а
   пользовательские данные и действия защищает application session.
6. Хранить backup вне `sites-enabled`: glob nginx читает любой файл в этой
   директории, включая `.bak`, и такой backup создаёт конфликтующий server.
7. Выполнить `nginx -t` до reload nginx.

Deploy-скрипт собирает frontend, копирует его в новый каталог релиза, проверяет
nginx, атомарно переключает `current`, повторно проверяет nginx и только затем
выполняет reload:

```bash
cd /root/ai_project
bash scripts/deploy_marketing_site.sh "$(git rev-parse HEAD)"
```

`release-id` неизменяем: повторный deploy существующего ID завершается ошибкой
и не перезаписывает файлы. Если проверка или reload падает после переключения,
скрипт восстанавливает предыдущий symlink `current` и пытается перезагрузить
nginx с прежним релизом. Неудачный релиз остаётся без активной ссылки для
диагностики.

Проверка маршрутов после deploy:

```bash
curl -fsS https://kaigo.space/ >/dev/null
curl -fsSI https://kaigo.space/favicon.svg
curl -fsSI https://kaigo.space/assets/ACTUAL_HASHED_ASSET.js
curl -fsS https://kaigo.space/studio >/dev/null
test "$(curl -sS -o /dev/null -w '%{http_code}' https://kaigo.space/builder/)" = 401
curl -fsS -u "$KAIGO_BUILDER_USER:$KAIGO_BUILDER_PASSWORD" \
  https://kaigo.space/builder/ >/dev/null
curl -fsS https://kaigo.space/api/health
curl -fsS https://kaigo.space/w/demka >/dev/null
```

Ожидаемая граница авторизации:

- `/`, `/favicon.svg` и `/assets/*` публичны;
- `/favicon.svg` получает `no-cache`, чтобы новый брендовый значок появлялся
  сразу после переключения релиза;
- файлы с Vite-hash в имени получают `immutable`, а stable-name assets —
  `no-cache`, чтобы новый релиз не оставался со старым изображением;
- `/studio` и `/studio/` публичны; `/builder/` сохраняет Basic Auth;
- существующие `/w/*`, `/client/*`, `/admin/*`, `/api/*` и другие более
  специфичные location сохраняют прежние обработчики.

Для ручного rollback атомарно направить `current` на заведомо рабочий релиз
через временный symlink, затем проверить конфигурацию и выполнить reload:

```bash
deploy_root=/var/www/kaigo-marketing
release_id=<known-good-release-id>
rollback_link="$deploy_root/.current.rollback.$$"
ln -s "$deploy_root/releases/$release_id" "$rollback_link"
mv -Tf "$rollback_link" "$deploy_root/current"
nginx -t
systemctl reload nginx
```

`/real-time/` is intentionally handled as an exact nginx location that proxies to `/index.html`; otherwise the realtime aiohttp static handler returns a directory listing.

Text widget smoke test:

```bash
curl -k -H 'Content-Type: application/json' \
  --data '{"message":"Проверка связи","user_id":"ops-smoke"}' \
  https://kaigo.space/w/demka/api/send
```

## Rollback

Nginx backups are created before routing changes:

```bash
ls -l /etc/nginx/sites-available/kaigo.space.bak-*
```

To roll back nginx:

```bash
cp /etc/nginx/sites-available/kaigo.space.bak-YYYYMMDD-HHMMSS /etc/nginx/sites-enabled/kaigo.space
nginx -t
systemctl reload nginx
```

Realtime frontend backups:

```bash
ls -l /root/realtime/index.html.bak-*
```

To roll back realtime frontend:

```bash
cp /root/realtime/index.html.bak-YYYYMMDD-HHMMSS /root/realtime/index.html
cd /root/realtime
docker compose up -d --build voice-agent
```
