# Kaigo: проверяемый SaaS MVP — технический дизайн

Дата: 2026-07-30
Статус: утверждено пользователем для поэтапной реализации

## Цель

Довести существующий Kaigo SaaS-контур до проверяемого MVP без переписывания
уже работающих частей. Пользователь должен пройти путь:

`лендинг → OAuth → бесплатная генерация → история проекта → доработка →
оплата → публикация → дальнейшее использование`.

Бесплатная версия остаётся доступна для просмотра и доработки. Текущий
publication-контракт не ослабляется: стабильный embed выдаётся только после
активного тарифа. Если позже появится отдельное бесплатное право публикации,
оно должно быть спроектировано и проверено отдельно.

Оператор должен уметь восстановить любой запуск по событиям, увидеть реальные
расходы моделей, причину fallback/ошибки и состояние платежа без повторного
обращения к модели.

## Подтверждённая существующая основа

Следующие части не создаются заново:

- PostgreSQL, Alembic и owner/tenant-scoped проекты;
- durable worker, lease/retry, SSE replay и trial settlement;
- immutable artifacts и model-call accounting;
- OAuth и восстановление Studio после обновления страницы;
- стабильный embed, immutable publication releases и rollback;
- server-owned тарифы, redirect checkout ЮKassa, webhook verification и
  exactly-once fulfillment;
- privacy-safe запись funnel events;
- Pattern Registry с 10 начальными паттернами по пяти слотам.

## Порядок реализации

### 1. Надёжный AI-маршрут

Сначала устраняется подтверждённый production-дефект:

- timeout AgentRouter должен быть `generation_timeout`, а не
  `provider_unavailable`;
- routed provider errors переводятся в provider-neutral `BuilderEngineError`;
- GPT/GLM роли получают Gemini как независимый fallback;
- production deadline одного логического вызова роли ограничивается
  120–360 секундами, включает primary, fallback и cleanup и резервирует время
  для независимого fallback; 900 секунд остаётся только отдельному
  benchmark-режиму;
- внешний stage timeout не может завершиться раньше route deadline;
- публичные ошибки остаются provider-neutral, а фактические provider/model и
  цепочка попыток записываются только в приватный diagnostic;
- fallback разрешён для timeout/unavailable/quota и bounded semantic invalid
  response; deterministic policy/safety/artifact violations не переключают
  провайдера вслепую;
- исчерпание всех targets завершает один логический вызов и не запускает три
  полных повтора дорогого stage;
- новый платный live-прогон запускается только после локальных RED/GREEN тестов
  и production smoke.

### 2. Единый реестр событий и forensic-слой

Операционный stream остаётся единственным источником порядка событий. Над ним
добавляются:

- typed registry всех generation event types;
- server-side public projection;
- рекурсивная redaction до записи;
- пятитидневный private forensic storage на persistent volume;
- manifest, checksum, quota, hourly cleanup;
- admin-only timeline/search/export с отдельным fail-closed правом доступа.

Core runs/events/model accounting живут по product retention. Через пять суток
удаляются только private prompts/responses/screenshots/diagnostics.

### 3. Учёт стоимости и маршрутизация

Каждый вызов связывается со stage attempt, semantic attempt, candidate/persona,
fallback chain и фактическим artifact. Стоимость различает:

- подтверждённую;
- оценочную;
- неизвестную после dispatch/cancel/timeout;
- точно не списанную.

Owner API отдаёт безопасные totals; admin API — waterfall и агрегаты. Выбор
GPT-5.5/GLM-5.2/Gemini меняется только на основе stage-specific benchmark и
publishability, а не только цены за миллион токенов.

### 4. Библиотека UI-паттернов

Сначала усиливается manifest/runtime contract существующих паттернов. Затем
добавляются по два новых варианта для launcher, shell, messages, composer и
motion. Fixed runtime Kaigo остаётся единственным владельцем open/close/send,
transcript, retry, attention timer и network.

### 5. История проекта и управляемая доработка

Промежуточные artifacts не считаются пользовательскими версиями. Добавляется
`project_versions`:

- project-wide ordinal;
- exact artifact/run;
- parent version;
- initial/refinement/restore;
- bounded change request.

Refinement создаёт отдельный durable run с idempotency и compare-and-swap.
Неудачная доработка не заменяет предыдущую активную/опубликованную версию.

### 6. Реальная внешняя публикация

Помимо существующих loopback-тестов создаётся отдельный статический HTTPS canary
origin. Acceptance проверяет:

- allowlisted origin загружает launcher, open/close и chat;
- stable embed URL показывает новый release после reload;
- rollback возвращает прежний release;
- запрещённый origin блокируется;
- на canary нет cookie, токенов и секретов.

### 7. ЮKassa и подписки

Используется тестовый магазин. Секреты хранятся только в серверном окружении и
никогда не попадают в Git, журналы, документацию или frontend.

Платёжный контракт:

- первый redirect payment с явным согласием и
  `save_payment_method=true`;
- method сохраняется только после verified `succeeded` и
  `payment_method.saved=true`;
- frontend читает локальный статус каждые 3 секунды максимум 20 минут;
- сервер сверяет pending attempts с ЮKassa не чаще раза в 60 секунд;
- webhook и reconciliation используют один exactly-once fulfillment;
- renewal создаётся один раз на billing period через сохранённый method;
- `auto_renew=false` не сокращает уже оплаченный период;
- повтор неоднозначного POST запрещён после 24-часового окна
  provider idempotency;
- renewal остаётся за feature flag до test-shop E2E.

Существующие подписки мигрируются только с `auto_renew=false`.

### 8. Funnel analytics

Добавляется opaque `journey_id` и immutable first-touch campaign. Он проходит
через entry, draft/OAuth, project, run, payment и publication.

Основная воронка:

`landing_entered → authenticated_project → run_queued → free_result →
payment_completed → published`.

Admin получает только агрегаты без raw prompt, URL, email, IP и entity IDs.

## Порядок миграций

- `0014_generation_forensics`
- `0015_yookassa_recurring_foundation`
- `0016_project_versions`
- `0017_funnel_journeys`

Миграции только additive до отдельного доказанного cleanup/cutover. Каждый
production rollout проходит schema-first и feature-flags-off.

## Безопасность и приватность

- Никаких provider/OAuth/YooKassa secrets в исходниках и артефактах.
- Публичные event payloads строятся только registry allowlist.
- Private forensic данные маскируют Authorization, Cookie, API keys, email,
  телефон и query secrets до persistent write.
- Cross-tenant forensic доступ запрещён; глобальный доступ требует отдельной
  capability и записывается в access log.
- Сохранённый `payment_method_id` opaque и никогда не возвращается браузеру.
- Publication сохраняет exact-origin allowlist, sandbox и trusted runtime.

## Критерии готовности

1. Production timeout модели классифицируется правильно и переключается на
   независимый fallback без 15-минутного зависания.
2. Failed/completed/cancelled run воспроизводится из timeline без нового model
   call.
3. Private evidence переживает restart и удаляется через 120 часов.
4. Operator видит стоимость и fallback chain по stage.
5. Пользователь видит проекты и пользовательские версии, может доработать и
   опубликовать выбранную версию.
6. Внешний HTTPS canary проходит publish/update/rollback acceptance.
7. Тестовый redirect payment, lost-webhook reconciliation, auto-renew off и
   stored-method renewal доказаны.
8. Воронка считает уникальные journeys и не раскрывает PII.
