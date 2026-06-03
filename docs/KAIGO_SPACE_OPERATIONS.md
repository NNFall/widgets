# Kaigo.space Operations

## Routing

`kaigo.space` обслуживается nginx-конфигом:

```text
/etc/nginx/sites-available/kaigo.space
```

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
cp /etc/nginx/sites-available/kaigo.space.bak-YYYYMMDD-HHMMSS /etc/nginx/sites-available/kaigo.space
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
