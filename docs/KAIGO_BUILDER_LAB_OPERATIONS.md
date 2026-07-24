# Эксплуатация Kaigo Gemini Builder Lab

## Назначение

Builder Lab — отдельная экспериментальная среда с минимальным пользовательским
интерфейсом. Пользователь вводит публичную HTTPS-ссылку и необязательное
пожелание. Слева отображаются реальные сообщения анализа, генерации и проверки,
справа — live preview принятой ревизии.

Перед генерацией Kaigo прокручивает одну главную страницу с задержками для
анимаций и сохраняет шесть временных кадров: desktop/mobile, верх/середина/низ.
Gemini получает JPEG inline и возвращает grounded visual brief: только видимые
факты и визуальные токены с привязкой к кадрам. Временные файлы удаляются после
анализа. После генерации BrowserAudit и Gemini visual critic проверяют сам
виджет. Доработка сообщением создаёт новый run из последней принятой ревизии,
а не запускает полную генерацию с нуля.

Доступны два режима:

- `Gemini staged` — последовательная сборка: арт-дирекция, каркас,
  идентичность, диалог, движение и полировка;
- `Antigravity agent` — автономная сборка в удалённой среде Google с загрузкой
  и независимой проверкой итогового архива.

Lab не импортирует production-сервер, не подключается к PostgreSQL, не изменяет
таблицы виджетов и не умеет публиковать embed-код. Запуски хранятся в памяти.
Отдельно может сохраняться один финальный проверенный демо-артефакт.

Текущие ограничения функционального MVP:

- анализируется одна главная HTTPS-страница без query, fragment, credentials и
  нестандартного порта;
- ссылка должна быть публичной и проходить SSRF/robots/egress проверки;
- после перезапуска контейнера пользовательские запуски исчезают;
- кнопки публикации и embed-кода пока нет;
- интерфейс временный: он доказывает сценарий, а не фиксирует будущий дизайн
  платформы.

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
GEMINI_REFERENCE_ANALYZER_MODEL=gemini-3.5-flash
GEMINI_REFERENCE_ANALYZER_THINKING_LEVEL=high
KAIGO_REFERENCE_MAX_PAGES=1
KAIGO_REFERENCE_MAX_DEPTH=0
KAIGO_REFERENCE_TIMEOUT_SECONDS=300
KAIGO_REFERENCE_PAGE_TIMEOUT_SECONDS=45
KAIGO_REFERENCE_MAX_SCROLL_STEPS=40
KAIGO_REFERENCE_SCROLL_DELAY_MS=750
KAIGO_REFERENCE_WARMUP_MS=5000
KAIGO_REFERENCE_FINAL_SETTLE_MS=1500
KAIGO_REFERENCE_RESPECT_ROBOTS=true
CRAWLEE_MEMORY_MBYTES=4096
CRAWLEE_DISABLE_BROWSER_SANDBOX=true
GEMINI_CHAT_MODEL=gemini-3.5-flash
GEMINI_CHAT_TIMEOUT_SECONDS=45
KAIGO_CHAT_SESSION_TTL_SECONDS=3600
KAIGO_CHAT_MAX_SESSIONS=500
KAIGO_CHAT_RATE_LIMIT_REQUESTS=12
KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS=60
KAIGO_CHAT_RATE_LIMIT_WINDOW_SECONDS=60
KAIGO_CHAT_MAX_REQUESTS_PER_SESSION=40
KAIGO_CHAT_GLOBAL_CONCURRENCY=4
KAIGO_CHAT_SECURE_COOKIE=true
```

`CRAWLEE_MEMORY_MBYTES` задаёт Crawlee рабочий бюджет RAM + swap. На сервере с
2 ГБ RAM нужен активный swap не меньше 2 ГБ, иначе autoscaler не запустит
Chromium при высокой общей загрузке памяти.

`CRAWLEE_DISABLE_BROWSER_SANDBOX=true` нужен только контейнеру Builder Lab:
Ubuntu запрещает user namespace sandbox для непривилегированного Chromium, а
сам контейнер уже изолирован `cap_drop: ALL`, `no-new-privileges` и отдельной
egress-сетью.

Ключи нельзя передавать в URL, записывать в Git или выводить в логи. На рабочем
сервере `GOOGLE_AI_NATIVE_BASE_URL` может указывать на защищённый Gemini-only
маршрут через американский сервер.

Для подписания cookie публичного чата production-серверу нужен постоянный
`KAIGO_CHAT_SESSION_SECRET` длиной не менее 32 URL-safe символов. В
`.env.example` он намеренно пустой. На сервере секрет создаётся один раз,
хранится только в `/root/ai_project/.env` с режимом `0600`, повторно
используется после перезапуска и никогда не выводится в терминал или лог:

```bash
set -euo pipefail
env_file=/root/ai_project/.env
umask 077
touch "$env_file"
chmod 0600 "$env_file"
secret_definition_pattern='^[[:space:]]*(export[[:space:]]+)?KAIGO_CHAT_SESSION_SECRET([^A-Za-z0-9_]|$)'
secret_definition_count="$(grep -Ec "$secret_definition_pattern" "$env_file" || true)"
case "$secret_definition_count" in
  0)
    secret="$(openssl rand -base64 48 | tr -d '\n' | tr '+/' '-_' | tr -d '=')"
    printf '\nKAIGO_CHAT_SESSION_SECRET=%s\n' "$secret" >> "$env_file"
    unset secret
    ;;
  1)
    if ! grep -Eq '^KAIGO_CHAT_SESSION_SECRET=[A-Za-z0-9_-]{32,}$' "$env_file"; then
      printf '%s\n' 'KAIGO_CHAT_SESSION_SECRET существует, но пуст или некорректен; остановка без ротации' >&2
      exit 1
    fi
    ;;
  *)
    printf '%s\n' 'KAIGO_CHAT_SESSION_SECRET определён больше одного раза; остановка без ротации' >&2
    exit 1
    ;;
esac
unset secret_definition_count secret_definition_pattern
```

Не запускайте этот блок с `set -x` и не проверяйте значение через `cat`,
`grep` без подавления вывода или `docker compose config`: эти команды могут
раскрыть секрет. Проверять нужно только наличие подходящей непустой строки с
подавленным выводом.
Если переменная уже существует, но пуста или некорректна, блок завершается с
ошибкой без значения секрета и без молчаливой ротации: причину нужно устранить
вручную до запуска контейнера.
Некорректными также считаются определения без `=`, с ведущими пробелами или с
`export`; несколько определений всегда приводят к остановке. Генерация
разрешена только при полном отсутствии определения переменной.

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
bash scripts/deploy_builder_lab.sh
docker compose --profile builder-lab logs -f --tail=200 builder-lab
```

Wrapper сначала создаёт остановленный контейнер и выделенную сеть, затем
устанавливает firewall и только после этого запускает `builder-lab`. Прямой
`docker compose up` для этого сервиса на сервере не используется.

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

location = /builder-demo/chat {
    limit_except POST { deny all; }
    client_max_body_size 4k;
    proxy_pass http://127.0.0.1:8091/demo/chat;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_request_buffering off;
    proxy_read_timeout 60s;
}
```

`/builder-demo/` и `/builder-demo/preview` остаются read-only. Единственное
публичное изменение состояния — точный POST-обработчик
`/builder-demo/chat`; более широкий public proxy на `/demo/` или `/api/`
добавлять нельзя. Полный `/builder/` по-прежнему закрыт Basic Auth.

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
структурированный список ошибок. По умолчанию выполняются до трёх
repair-попыток; четвёртая разрешена только как настраиваемый верхний предел.
Если валидатор повторно получает одинаковый набор ошибок, цикл останавливается
раньше, чтобы не расходовать токены впустую.

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

Для production-чата отдельно проверяются запрет GET и два последовательных
POST в одной cookie-сессии. Актуальную ревизию берут из опубликованного demo,
секрет и cookie в вывод не печатают:

```bash
revision="$(jq -r '.artifact.revision' data/builder-demo/latest.json)"
request_prefix="$(date +%s)"
history_marker="kaigo-history-$request_prefix"
cookie_jar="$(mktemp)"
chmod 0600 "$cookie_jar"
trap 'rm -f "$cookie_jar"' EXIT
test "$(curl -sS -o /dev/null -w '%{http_code}' https://kaigo.space/builder-demo/chat)" = 403
curl -fsS -c "$cookie_jar" -b "$cookie_jar" \
  -H 'Origin: https://kaigo.space' \
  -H 'X-Kaigo-Chat: v2' \
  -H 'Content-Type: application/json' \
  --data "{\"request_id\":\"production-chat-$request_prefix-1\",\"message\":\"Запомните уникальный маркер $history_marker и подтвердите получение\",\"revision\":$revision}" \
  https://kaigo.space/builder-demo/chat | jq -e '.reply | strings | length > 0' >/dev/null
curl -fsS -c "$cookie_jar" -b "$cookie_jar" \
  -H 'Origin: https://kaigo.space' \
  -H 'X-Kaigo-Chat: v2' \
  -H 'Content-Type: application/json' \
  --data "{\"request_id\":\"production-chat-$request_prefix-2\",\"message\":\"Какой уникальный маркер я просил запомнить в предыдущем сообщении? Верните его дословно\",\"revision\":$revision}" \
  https://kaigo.space/builder-demo/chat | jq -e --arg marker "$history_marker" '.reply | strings | contains($marker)' >/dev/null
```

Перед каждым reload сначала выполняется `nginx -t`; после reload проверяются
HTTP-коды, сохранение cookie между запросами, два непустых ответа Gemini и
отсутствие `5xx` в логах builder-lab и nginx.

## Фактическая проверка 18 июля 2026 года

Публичный direct-прогон выполнен моделью `gemini-3.5-flash` с настоящим
серверным ключом и русским архитектурным брифом:

- `run.completed`, пять зафиксированных ревизий до `motion_polish`;
- 301,260 секунды;
- 46 568 входных, 52 278 выходных и 23 517 thinking-токенов;
- 122 363 токена суммарно;
- repair исправил отсутствующую подпись composer и превышение лимита анимаций;
- итог сохранён в `/app/data/builder-demo/latest.json` и повторно валидируется
  при каждом публичном открытии.

Отдельный реальный Antigravity-прогон также завершился успешно:

- `run.completed`, стадия `agent_build`;
- 268,846 секунды;
- 370 305 входных, 17 577 выходных и 31 696 thinking-токенов;
- 419 578 токенов суммарно;
- snapshot прошёл встроенную проверку в удалённой среде и независимую проверку
  Kaigo после скачивания;
- этот результат не заменял рабочее direct-демо.

Playwright в видимом Chromium подтвердил публичный prompt, desktop/mobile,
закрытие и повторное открытие launcher, подстановку suggestion, очистку composer
и защищённый Builder UI. На обеих страницах — 0 ошибок и 0 предупреждений
browser console. Полный локальный набор — 103 теста.

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

## Ограничение исходящего трафика и DNS TOCTOU

Builder подключён только к сети `kaigo_builder_research` с фиксированными
`172.30.240.0/28` и Linux bridge `br-kaigo-build`; IPv6 в сети и контейнере
отключён. Guarded deploy устанавливает правила автоматически до запуска. Для
ручной проверки или восстановления правил:

```bash
sudo bash scripts/apply_builder_egress_guard.sh
```

Скрипт сначала собирает неизменяемые generation-цепочки вне активного пути,
проверяет каждое правило, затем одной транзакцией `iptables-restore --noflush`
переключает оба hook. Активная цепочка никогда не очищается на месте. Та же
транзакция удаляет дубли, старые generation-цепочки и source-IP правила ранней
версии guard. Правила привязаны к стабильному bridge, а не к меняющемуся IP
контейнера. Для
новых соединений блокируются private, link-local, loopback, multicast, reserved
и metadata IPv4-диапазоны; established-ответы nginx не затрагиваются. После
перезагрузки хоста правила нужно восстановить до запуска контейнера — поэтому у
`builder-lab` нет автоматической restart-policy, запуск выполняет только wrapper
или эквивалентный systemd unit с `ExecStartPre` на этот firewall-скрипт.
Приложение дополнительно заново разрешает DNS и проверяет каждый URL и redirect
непосредственно перед использованием.

Это эшелонированная защита, а не DNS pinning. Между проверкой в приложении и
соединением Chromium остаётся интервал DNS TOCTOU. Firewall предотвращает
перепривязку к приватным IPv4-адресам, но не доказывает использование ровно того
публичного адреса, который проверило приложение. Для полной защиты от подмены
между публичными адресами всё ещё нужен контролируемый outbound proxy.

## Автономная очистка private evidence

Репозиторий содержит готовые unit-файлы:

- `deploy/systemd/kaigo-reference-cleanup.service`;
- `deploy/systemd/kaigo-reference-cleanup.timer`.

Они используют реальный server checkout `/root/ai_project` и evidence root
`/root/ai_project/data/reference-evidence`. Guarded deploy устанавливает unit-файлы,
сразу выполняет первую очистку, включает timer и проверяет состояния `enabled` и
`active` до запуска builder-контейнера. Ручная установка:

```bash
cd /root/ai_project
sudo bash scripts/install_reference_cleanup_timer.sh
systemctl status kaigo-reference-cleanup.timer --no-pager
```

Cleanup удаляет только каталоги с валидным Kaigo expiry-marker; свежие и
посторонние каталоги не затрагиваются.

## Текущие ограничения

- Если browser URL subresource был same-origin относительно страницы, его
  redirect на другой origin блокируется до обращения к target и записывается в
  `policy_blocks` с source/destination origin. Иначе ручной proxy сделал бы
  cross-origin bytes читаемыми как same-origin и обошёл CORS. Если исходный
  browser URL уже cross-origin, публичные redirect hops разрешены после
  повторной SSRF-проверки, но каждый переход получает новый cookie-less client и
  теряет `Cookie`, `Authorization`, `Proxy-Authorization` и `Referer`. Это
  сознательное ограничение: полностью нативные redirect semantics потребуют
  CDP/network proxy с доказанной per-hop interception.

- Один полный пятиэтапный прогон может быть медленным и дорогим; перед
  коммерческим запуском нужно уменьшить повторную передачу полного артефакта и
  объём repair-контекста.
- Текущий публичный полный Builder рассчитан на владельца проекта, а не на
  нескольких арендаторов: нет аккаунтов, биллинга, очереди durable jobs и
  автоматической публикации embed-кода.
- Antigravity остаётся сравнительным экспериментом и не является fallback для
  рабочего direct-режима.
