# Контакты и правовые документы: handoff в backend

Статус: frontend-контракт подготовлен, backend-хранилище ещё не реализовано.
Эта запись нужна для интеграции backend-чата и не является разрешением на
production-деплой, миграцию или перезапуск сервисов.

## Что пользователь видит сейчас

На лендинге и в Studio есть единая форма «Помощь и обратная связь»:

- пользователь выбирает одну тему: вопрос, ошибка, идея по улучшению или
  сотрудничество;
- пишет сообщение и отправляет его без имени, email, телефона или другого
  контактного поля;
- frontend показывает «Сообщение сохранено» только после валидного ответа API;
- при временной ошибке текст остаётся в форме, а повтор использует тот же
  `Idempotency-Key` и тело запроса;
- согласие ведёт на отдельную страницу персональных данных и отправляется как
  версия документа `feedback-v2`.

`support@kaigo.space` не является подтверждённым почтовым ящиком и удалён из
пользовательского сценария. Frontend не обещает письмо, личный ответ или
Telegram-доставку. Реальная история обращений появится только после backend
интеграции.

## Frontend API-контракт

### Получить параметры сессии

```http
GET /api/feedback/session
Accept: application/json
```

Ожидаемый ответ `200 OK`:

```json
{
  "csrf_token": "opaque-csrf-token",
  "consent_version": "feedback-v2",
  "message_max_length": 4000
}
```

`csrf_token` и идентификаторы — непрозрачные значения. Не логировать их вместе
с cookies или содержимым сообщения. Landing получает этот ответ перед первой
отправкой. Studio уже получает CSRF из авторизованной сессии и передаёт его
напрямую в POST.

### Сохранить обращение

```http
POST /api/feedback
Content-Type: application/json
Accept: application/json
X-CSRF-Token: opaque-csrf-token
Idempotency-Key: feedback-<uuid>
```

Frontend отправляет только этот whitelist:

```json
{
  "topic": "question",
  "message": "Текст обращения",
  "source": "landing_contact",
  "consent": {
    "version": "feedback-v2",
    "accepted": true
  }
}
```

`topic` — только `question`, `bug`, `improvement` или `cooperation`. `source` —
только `landing_contact`, `studio_account` или `studio_auth_error`. `message`
обязателен, очищается и ограничивается `message_max_length` (product limit —
4000 символов). В запросе нет `email`, `contact`, `name`, `page`, `domain`,
`project_id`, `run_id`, `context` или клиентского времени.

Успешный ответ должен быть `201 Created` (допустим `200 OK` для безопасной
идемпотентной повтора) и иметь строгую форму:

```json
{
  "receipt_id": "opaque-server-receipt",
  "status": "stored",
  "received_at": "2026-08-17T10:00:00Z"
}
```

`receipt_id` не должен содержать email, текст сообщения, project/run ID или
секрет. `received_at` создаётся сервером в UTC и не берётся из тела запроса.
Никакой ответ `202 queued` не должен трактоваться текущим frontend как успех.

### Повторы и ошибки

Одинаковый ключ идемпотентности у одного субъекта с тем же нормализованным
телом должен вернуть тот же receipt и не создавать вторую запись. Тот же ключ
с другим телом должен дать `409 Conflict` и не менять исходную запись.

Минимальный HTTP-поверхностный контракт:

| Код | Смысл | Что ожидает frontend |
| --- | --- | --- |
| `400` / `422` | тело или enum не прошли валидацию | безопасная ошибка, без записи |
| `403` | CSRF/session устарели | frontend просит обновить сессию или страницу |
| `409` | конфликт idempotency key | безопасная ошибка, без второй записи |
| `429` | rate limit | `Retry-After` в целых секундах, повтор после паузы |
| `5xx` / network | временная неопределённость | текст сохраняется, пользователь может повторить тем же ключом |

Ошибки не должны возвращать тело сообщения, cookies, токены или внутренние
стектрейсы. В логах допустимы request ID, тема, результат проверки, размер,
анонимизированный субъект и receipt ID.

## Серверная модель PostgreSQL

Backend должен добавить таблицу `feedback_submissions` в своей миграции. Схему
нужно сверить с существующими naming/tenant-конвенциями, но минимум должен
включать:

| Поле | Назначение |
| --- | --- |
| `id` | внутренний UUID/целочисленный PK |
| `receipt_id` | публичный непрозрачный уникальный receipt |
| `tenant_id` | tenant пользователя Studio, nullable для landing |
| `actor_user_id` | авторизованный пользователь, nullable для landing |
| `source` | whitelist источника формы |
| `topic` | whitelist темы |
| `message` | нормализованный текст обращения |
| `consent_version` | точная версия документа |
| `consent_accepted` | зафиксированное `true` после валидации |
| `consent_accepted_at` | серверное UTC-время |
| `idempotency_key` | ключ с уникальностью в области субъекта |
| `status` | минимум `stored`, затем операционные статусы |
| `created_at`, `updated_at` | серверные timestamps |

Индексы нужны для операторской истории по `created_at`, `status`, `topic` и
tenant/пользователю. Срок хранения должен задаваться конфигурацией, быть
документированным и включать процедуру удаления/экспорта по запросу субъекта.
Не добавлять frontend-поля для имени, email или произвольного client context.

Для Studio проект и текущий run сервер получает из авторизованной сессии,
проверяет принадлежность и сохраняет как server-derived metadata (или
связанную запись), но не принимает `project_id`/`run_id` из JSON как доказательство
прав. Для landing такой контекст отсутствует.

## CSRF, rate limit и операторская история

- Проверять CSRF для браузерной сессии до записи.
- Для landing применить anti-spam и rate limit по IP/сессии; для Studio — по
  пользователю/tenant и IP.
- Ограничить размер JSON и длину нормализованного сообщения до записи.
- Не выводить содержимое сообщения в access/error logs.
- Сделать операторский список с фильтрами по статусу/теме/дате и audit trail
  изменения статуса; доступ только у уполномоченных операторов.
- Настроить retention и удаление в конфигурации, а не хардкодить срок в UI.

Отправка email или Telegram, если она будет нужна, выполняется только
server-side. После записи можно добавить транзакционный outbox с повторной
доставкой и dead-letter очередью. Telegram bot token, SMTP/API credentials и
адреса назначения не должны попадать во frontend, HTML, ответы API, архивы или
логи. Повторная доставка не должна создавать второй `receipt_id`.

## Правовые страницы

Frontend содержит отдельные маршруты:

- `/privacy/` — политика конфиденциальности;
- `/personal-data-consent/` — согласие, версия которого отправляется в API;
- `/terms/` — условия использования;
- `/offer/` — предварительная публичная оферта.

Они являются тестовой редакцией. Оператор, ИНН/реквизиты, цели, категории,
сроки хранения, доступ субъектов, локализация, трансграничные передачи и
платёжные условия должны быть проверены владельцем и юристом до production.

## BACKEND REQUEST

### Problem

Текущий frontend уже собрал единый UX для вопросов, ошибок, улучшений и
сотрудничества, но без настоящего endpoint обращения не сохраняются на сервере.
Нельзя оставлять `mailto:` или показывать пользователю ложное «сохранено».

### Current frontend

Рабочая ветка: `codex/product-ui`. Базовый commit перед Task 5:
`0ab09f3`. После принятия этого handoff backend-чат должен интегрировать именно
финальный commit задачи `test(frontend): verify stored feedback journey`:
`<FINAL_TASK_5_COMMIT_SHA>` (`test(frontend): verify stored feedback journey`;
fill with `git rev-parse HEAD` immediately before backend integration).

### Requested implementation

1. Реализовать `GET /api/feedback/session` с CSRF, `feedback-v2` и server
   message limit.
2. Реализовать `POST /api/feedback` с whitelist/CSRF/rate limit,
   нормализацией, PostgreSQL `feedback_submissions` и строгим `201 stored`.
3. Реализовать идемпотентность: тот же ключ + тело возвращает тот же receipt;
   тот же ключ + другое тело даёт `409`.
4. Для Studio вычислять project/run context из авторизованной серверной
   сессии; не доверять JSON client context.
5. Добавить операторскую историю, retention config и подготовить
   транзакционный Telegram outbox без включения Telegram-доставки по умолчанию.

### User benefit

Пользователь пишет сообщение один раз, не ищет email и не вводит контактные
данные. Kaigo честно подтверждает только принятие сервером, а команда получает
централизованную историю для ответа и улучшения продукта.

### Temporary behavior

До backend-интеграции endpoint и таблица отсутствуют; это не live storage и не
production success. Контрактные Playwright-тесты используют локальный mock
ответов `GET 200` и `POST 201 stored`, поэтому подтверждают только frontend
поведение. Production checkout, nginx, сервисы и база этим frontend-commit не
изменяются.

### Acceptance criteria

- `GET /api/feedback/session` возвращает валидные `csrf_token`,
  `consent_version`, `message_max_length`.
- Валидный POST сохраняется ровно один раз и отвечает `201` с opaque receipt и
  server UTC `received_at`.
- В БД нет обязательных name/email/contact полей, а согласие хранит точную
  версию и server `accepted_at`.
- Повтор с тем же ключом и телом идемпотентен; изменение тела получает `409`.
- `400/422/403/429/5xx` не создают ложный success; `429` содержит
  `Retry-After`.
- Studio context derived server-side, landing context отсутствует.
- Операторская история, rate limit, retention и audit trail покрыты тестами или
  отдельным backend evidence.
- Только после backend review и live end-to-end проверки backend-чат выполняет
  отдельный согласованный production deployment.

До выполнения этих критериев frontend не заявляет реальное сохранение,
почтовую доставку или Telegram-доставку.
