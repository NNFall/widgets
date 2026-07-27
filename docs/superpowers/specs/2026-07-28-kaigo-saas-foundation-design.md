# Kaigo SaaS Foundation — утверждённая архитектура

**Дата:** 2026-07-28  
**Статус:** утверждено пользователем, готово к реализации  
**Ветка:** `codex/saas-foundation`

## 1. Цель

Превратить текущий Builder Lab в основу SaaS-платформы, не переписывая уже
работающий генератор. Пользователь должен пройти цельный путь:

1. увидеть публичный лендинг и вставить ссылку на действующий сайт;
2. перейти в Studio с сохранённой ссылкой и пожеланием;
3. авторизоваться до первого платного вызова модели;
4. бесплатно получить одну законченную экспресс-версию AI-виджета;
5. проверить её в живом preview;
6. оплатить доработку и публикацию только после просмотра результата;
7. получить стабильный embed-код и управлять опубликованной версией.

Основное продуктовое обещание: **сначала результат — потом оплата**. Бесплатный
результат не является искусственно обрезанным «30% черновиком»: это пригодный к
просмотру express-build с ограничениями на повторные генерации, доработку и
публикацию.

## 2. Что сохраняем из текущей системы

- существующий staged Gemini Builder и его видимые ревизии;
- анализ исходного сайта, desktop/mobile screenshots и browser audit;
- три визуальных критика, независимого судью и repair-цикл;
- настоящий chat runtime внутри preview;
- текущие сущности tenant, user, widget, asset и binding;
- PostgreSQL как единственный источник истины для SaaS-данных;
- SQLite только для существующей локальной истории разговоров виджета, пока она
  не будет мигрирована отдельно.

Builder Lab перестаёт быть отдельной in-memory лабораторией и становится
прикладным сервисом, использующим общую аутентификацию, PostgreSQL и политики
доступа Kaigo.

## 3. Пользовательский сценарий

### 3.1 Публичный вход

На лендинге URL и необязательное пожелание можно ввести без регистрации. После
нажатия «Создать AI-виджет» данные сохраняются в короткоживущий server-side
draft, а пользователь переходит в Studio.

### 3.2 Authentication gate

До сетевого анализа сайта и вызова модели Studio показывает короткий gate:
«Войдите, чтобы сохранить проект и бесплатно получить первую версию».

MVP-провайдеры:

- Google OAuth / Google Identity Services;
- Яндекс OAuth;
- email/password не показывается публично в первой версии, но существующие
  manager-created аккаунты продолжают работать для обратной совместимости.

После OAuth пользователь возвращается в тот же draft. Повторно вводить URL и
пожелание нельзя заставлять.

### 3.3 Бесплатная экспресс-генерация

Новый подтверждённый пользователь получает один trial entitlement. Он
расходуется атомарно при постановке первого run в очередь. Сбой инфраструктуры
до создания первого валидного артефакта возвращает entitlement автоматически;
пользовательская отмена после начала model spend — нет.

Экспресс-режим создаёт завершённый виджет с меньшим числом дорогих ревизий, но
сохраняет обязательные проверки чата, desktop/mobile и последний рабочий
артефакт. Пользователь может открыть, закрыть и протестировать чат. Недоступны:

- публикация на production;
- неограниченные follow-up доработки;
- расширенная база знаний и интеграции;
- удаление брендинга Kaigo;
- коммерческое использование без активного плана.

### 3.4 Платное продолжение

После express-result интерфейс предлагает не «купить кота в мешке», а конкретно:

- доработать выбранный результат;
- запустить более глубокий визуальный и разговорный review;
- подключить дополнительные источники знаний;
- опубликовать и получить embed;
- включить runtime, аналитику и историю версий.

## 4. Состояния продукта

Каноническое состояние проекта отделено от состояния отдельного запуска.

```text
draft
  -> awaiting_auth
  -> queued
  -> analyzing
  -> generating
  -> reviewing
  -> free_result_ready
  -> awaiting_upgrade
  -> refining
  -> ready_to_publish
  -> published
```

Terminal-состояния run: `completed`, `failed`, `cancelled`. Ошибка отдельного
этапа не удаляет последнюю принятую ревизию. Refresh браузера всегда
восстанавливает проект, run, события и preview из PostgreSQL.

## 5. Данные PostgreSQL

Существующая схема расширяется следующими сущностями.

### Identity и session

- `user_identities`: provider, provider subject, verified email, profile data;
- `auth_sessions`: случайный hash токена, user, expiry, last_seen, revoked_at,
  IP/user-agent fingerprints с ограниченным сроком хранения;
- `oauth_states`: одноразовый state/PKCE verifier/return path/expiry;
- `anonymous_drafts`: URL, пожелание, expiry, claim token hash.

Браузер получает только opaque cookie `kaigo_session`: `Secure`, `HttpOnly`,
`SameSite=Lax`, узкий Path и ротация после входа. Cookie не содержит user data и
не является источником прав доступа.

### Проекты и генерация

- `projects`: tenant/user, source URL, brief, status, active run/revision;
- `generation_runs`: режим, state, progress, timestamps, error, idempotency key;
- `generation_events`: монотонный sequence, public message, technical payload;
- `generation_artifacts`: immutable revision, stage, HTML/CSS/JS/config,
  quality status, provenance;
- `artifact_evidence`: screenshot/audit type, object path, checksum, metadata;
- `model_calls`: provider, model, role, prompt version, request id, tokens,
  latency, status, normalized error and cost snapshot;
- `usage_ledger`: immutable debit/credit entries in internal Kaigo units;
- `trial_entitlements`: grant, reservation, consumption and compensation;
- `subscriptions`, `payment_attempts`, `payment_webhook_events`;
- `publications`: project, revision, stable public key, allowed domains, state;
- `publication_releases`: immutable deployed asset snapshots and rollback link.

Mutable summaries (`project.status`, balances, active revision) всегда выводимы
из immutable events/ledger и обновляются в одной транзакции с ними.

## 6. Durable execution

HTTP-запрос только валидирует ввод, создаёт project/run и возвращает `202`.
Длительная генерация выполняется worker-процессом.

Первая реализация использует PostgreSQL-backed queue с `FOR UPDATE SKIP LOCKED`,
lease/heartbeat и idempotent stage checkpoints. Это не требует отдельного Redis
в MVP и позволяет позднее заменить транспорт очереди без изменения доменной
модели.

Каждый этап:

1. атомарно захватывает lease;
2. читает последний checkpoint;
3. пишет `stage.started`;
4. выполняет работу вне транзакции;
5. сохраняет immutable artifact/model_call/evidence;
6. одной транзакцией пишет `stage.completed` и следующий state;
7. при повторном запуске не повторяет уже подтверждённый внешний side effect.

Клиент получает события через SSE с `Last-Event-ID`; REST polling остаётся
fallback. События имеют `run_id + sequence`, поэтому refresh и reconnect не
создают дубликаты.

## 7. Model router

Оркестратор зависит не от Gemini-класса, а от ролей:

- `reference_analysis`;
- `art_direction`;
- `widget_generation`;
- `code_review`;
- `visual_critic`;
- `visual_judge`;
- `repair`;
- `visitor_chat`.

Policy resolver выбирает provider/model по роли, режиму (`express`, `standard`,
`premium`), бюджету и health-state. Adapter нормализует messages, structured
output, изображения, tool calls, usage, thought signatures и ошибки. Fallback
допустим только для совместимой роли и записывается как отдельная попытка.

Ни в одном provider request не вводится искусственный общий `max_output_tokens`.
За бюджет отвечают число этапов, модель, retry policy и cost guard до вызова.
Точные модели находятся в конфигурации и меняются без миграции данных.

## 8. Стоимость и продуктовые кредиты

Пользователь не должен разбираться в разных токенах Gemini, GLM или Kimi.

- Внутренний `model_calls` хранит реальные input/output/thinking tokens и
  рассчитанную себестоимость по versioned price table.
- `usage_ledger` хранит Kaigo credits, отдельно build credits и runtime credits.
- UI показывает понятные действия и остаток, а технический экран может показать
  токены и поставщика для прозрачности.
- Цена вызова фиксируется snapshot-ом на момент выполнения; изменение тарифа
  провайдера не переписывает историю.

MVP billing adapter проектируется provider-agnostic. Первым production-провайдером
может быть YooKassa; Stripe добавляется отдельным adapter-ом. Webhook всегда
проверяется, дедуплицируется по provider event id и не доверяет redirect браузера.

## 9. Публикация и embed

Публикация — детерминированное действие, а не новая AI-генерация.

1. Пользователь выбирает принятую revision.
2. Система проверяет entitlement/plan, quality gate и allowed domains.
3. Создаётся immutable publication release.
4. Stable key начинает указывать на release атомарно.
5. Пользователь получает один script snippet; последующие publish не требуют
   менять код сайта.

Runtime loader не отдаёт editor payload, проверяет domain policy и поддерживает
rollback на предыдущую release. Preview и production используют разные ключи и
политики кэширования.

## 10. Studio UI

До run Studio показывает центральный composer: URL, пожелание, режим и понятное
объяснение trial. После старта он плавно переходит в split workspace:

- слева — диалог, этапы, понятные статусы и раскрываемый технический лог;
- справа — живой preview, desktop/mobile, revisions и before/after;
- сверху — project state, стоимость/credits и действия stop/retry/publish.

Каждая принятая revision появляется сразу и содержит короткое русское описание
для обычного пользователя. Технический payload не обрезает пользовательский
текст многоточием и доступен отдельно.

## 11. Защита trial и границы безопасности

- verified OAuth identity — основной уникальный ключ бесплатного запуска;
- rate limits по account, IP prefix и device risk signal;
- один активный express-run на пользователя;
- URL нормализуется, блокируются private/loopback/link-local адреса и DNS rebinding;
- OAuth redirect allowlist фиксирован конфигурацией;
- session token хранится только hash-ом;
- CSRF-защита для cookie-auth mutations;
- generated code работает в sandboxed iframe, publication loader не получает
  секреты Kaigo;
- audit/evidence хранилище имеет quota и retention policy;
- abuse-сигналы не становятся вечным fingerprint пользователя.

## 12. Совместимость и миграция

- существующий manager login сохраняется до отдельной миграции;
- Builder Basic Auth остаётся аварийным административным контуром до проверки
  публичной OAuth-схемы, затем снимается с пользовательского Studio;
- текущие in-memory run API получают repository interface, после чего production
  переключается на PostgreSQL implementation;
- старые публичные demo routes и widget routes не меняются;
- миграции Alembic становятся обязательными; `metadata.create_all` допускается
  только в тестовом окружении.

## 13. Наблюдаемость и критерии готовности

Обязательные метрики: auth conversion, draft→run, time-to-first-artifact,
time-to-free-result, stage failure rate, retry count, provider/model cost,
trial cost, upgrade conversion, publication success и runtime latency.

Фундамент считается готовым, когда доказаны:

- Google и Яндекс OAuth на тестовых приложениях;
- opaque session survives refresh, rotates on login and revokes on logout;
- один trial нельзя потратить дважды конкурентными запросами;
- worker переживает restart и продолжает с последнего checkpoint;
- Studio восстанавливается через API/SSE после refresh;
- каждый model call имеет provenance, usage и cost snapshot;
- fallback не скрывается от журнала;
- express-result доступен даже при exhausted visual repair budget;
- publish создаёт stable embed и rollback возвращает прошлый release;
- webhook replay не начисляет credits повторно;
- старые routes и текущий генератор проходят regression suite.

## 14. Порядок реализации

1. Schema + repositories + secure sessions.
2. OAuth Google/Яндекс и claim anonymous draft.
3. Durable projects/runs/events/artifacts + worker leases.
4. Provider adapters, role policies, model call audit and usage ledger.
5. Trial entitlement and express orchestration.
6. Studio state recovery/SSE and auth transition.
7. Publication/embed/rollback.
8. Billing adapter contracts, YooKassa test flow and plans.
9. Landing block «Сначала результат — потом оплата» and funnel analytics.
10. Production migration, canary, rollback drill and evidence package.

## 15. Осознанно не фиксируем сейчас

- точные цены подписок и число пользовательских credits;
- окончательный набор моделей для каждой роли;
- email/password self-registration;
- white-label и reseller mode;
- CRM, Telegram и email integrations;
- полный multi-page knowledge crawler и автоматический retrain.

Все эти пункты добавляются поверх описанных contracts без смены основного
пользовательского пути и модели данных.
