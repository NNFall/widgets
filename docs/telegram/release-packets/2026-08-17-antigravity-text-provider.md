# Контролируемый AntiGravity-canary в Kaigo Builder

Дата: 2026-08-17

Статус: release `ec45ecb` развёрнут в production, AntiGravity включён только для
контролируемого canary; широкий rollout — `hold`.

## Коротко

Kaigo теперь умеет пробовать AntiGravity для трёх параллельных идей будущего
виджета и автоматически возвращаться к Codex при допустимой ошибке. Транспорт,
изоляция и публикация проверены на production. После исчерпания дневной квоты
3.7 worker безопасно переключён на 3.6; новый 37signals-run получил 3 из 3
AntiGravity-кандидатов без Codex fallback, но широкий rollout всё ещё ждёт
серии из 10 runs и 30 candidate-вызовов.

## Что изменилось

- `AntigravityTextProvider` переводит структурированные запросы Kaigo в
  существующий `/antigravity-api/v1/respond`, проверяет JSON Schema и
  нормализует usage и ошибки для общего ModelRouter.
- Ответ upstream только с `total_tokens` считается допустимым: общий итог
  проверяется, но не превращается в выдуманное распределение input/output/
  thinking.
- Только три параллельных `direction_candidate` сначала обращаются к
  AntiGravity, затем при допустимой ошибке — к Codex. Судья направлений,
  генерация HTML, repairs, визуальные роли и публичный чат сохраняют прежние
  маршруты.
- Неизменяемые egress-правила G5 разрешают TCP 443 только от фиксированного
  builder worker к одному NL gateway. Соседний builder-lab не получает это
  исключение.
- Bearer-секрет и флаг находятся в отдельном обязательном
  `builder-worker-antigravity.env`; systemd требует владельца `root` и mode 600.

## Проверенные факты

- Production release: `ec45ecb`; pinned app/billing image `6fa87…`, worker image
  `b22fc…`.
- `/api/ready`: `ready`. Схема базы: `0019`; миграция для релиза не нужна.
- Финальный focused-прогон на release commit: 253 passed, 5 skipped.
- Linux state machine egress guard: 17/17 сценариев.
- Геометрия опубликованного launcher: 47 passed, 2 skipped; независимый review
  дал GO.
- Существующая Google-авторизация на US работает; новую авторизацию не
  проводили. `gemini-3.7-flash` видна в model list, но её per-day/per-model
  generate quota исчерпана. Это quota-событие, а не ошибка авторизации.
- Worker безопасно переключён на `gemini-3.6-flash-high`; feature flag остаётся
  `true` для контролируемого canary.
- Прямой post-restart smoke из worker: HTTP 200,
  `actual_provider=gemini`, `actual_model=gemini-3.6-flash`,
  `conversation_deleted=true`.

## Production-прогоны

### 37signals-run `d13db4e4…`: 3 из 3 через AntiGravity

- Все три `direction_candidate` завершились через `antigravity_text`, без
  Codex fallback.
- Запрошенная модель: `gemini-3.6-flash-high`; фактическая:
  `gemini-3.6-flash`; provider `fallback_index=1`.
- Готовы 6 из 6 captures.
- Версия `1480945f…` и artifact `8ee4e484…` получили quality status `verified`.
- Создана отдельная publication/release с `previous_release=NULL`.
- Публичная ссылка:
  <https://canary.5-129-236-90.sslip.io/?key=Tn7aThw4ATAEHog8cM906C6b5L_rk1yT>.
- Desktop: 64 px в закрытом состоянии, 402 × 438 в открытом; геометрия
  стабильна после повторного открытия.
- Mobile: 64 px в закрытом состоянии, 340–341 × 438 в открытом; геометрия
  стабильна после повторного открытия, panel полностью внутри viewport.
- Chat POST отвечает HTTP 200 примерно за 6 секунд. В базе зафиксирован один
  завершённый chat call через `gemini-3.5-flash-lite`, `fallback_index=1`.
- Запрещённый origin блокируется CSP. Предыдущий Basecamp-canary не изменён и
  продолжает отвечать HTTP 200.

Этот run подтверждает полный путь 3/3 AntiGravity candidates → verified
artifact → отдельная публикация → внешний responsive runtime → рабочий chat.

### Run `6704…`: реальное использование Gemini, но без публикации

- Два из трёх direction candidates завершились через Gemini.
- Первый кандидат получил quota error и штатно ушёл в Codex fallback.
- Судья выбрал `candidate-3`.
- Собранный затем артефакт не прошёл visual quality gate. Его не публиковали и
  не используем как доказательство готового виджета.

### Строгий Basecamp-run `61d…`: verified-путь через fallback

- Run завершился, а опубликованная версия имеет quality status `verified`.
- Все три AntiGravity-вызова упёрлись в дневную квоту 3.7 и были завершены
  через Codex fallback.
- Версия опубликована и проверена снаружи по адресу
  <https://canary.5-129-236-90.sslip.io/?key=zokrD57Pe07T_WaXCEF306LUGEVyzMC->.
- Launcher стабилен: 378 × 472 на desktop и 270 × 330 на mobile.
- Chat POST отвечает HTTP 200.
- Для запрещённого origin launcher не добавляется (`0`), ограничение CSP
  сохраняется.

Этот run доказывает production-публикацию, внешний runtime, чат и надёжный
fallback. Он не доказывает успешную генерацию Basecamp-направлений через
AntiGravity.

## Безопасность и rollback

- API-ключи, OAuth-токены и серверные реквизиты в пакет не добавлены.
- После acceptance-сессии выполнен logout, временные секретные файлы удалены —
  PASS.
- Канонический rollback пересобран из `bc27…`.
- Перед переключением сохранён дамп production-базы.
- Изменение не требует миграции и откатывается переключением worker release и
  feature flag.

## Ограничения

- Дневная generate quota `gemini-3.7-flash` для этой модели исчерпана, хотя
  модель остаётся доступной в model list и Google-auth работает.
- Успешный 3.6-run не доказывает, что quota/fallback rate будет приемлем на
  серии запусков.
- Флаг остаётся `true` только для контролируемого canary; широкий rollout
  запрещён до следующего gate.
- Успешный provider smoke не заменяет серию полных verified-генераций.

## Следующий gate

Провести 10 контролируемых запусков и получить 30 candidate-вызовов. Перед
широким rollout сравнить долю quota/fallback, latency, валидность structured-
ответов и качество выбранных направлений. При неприемлемой доле fallback
оставить маршрут только экспериментальным или выделить отдельную квоту.

## Возможные темы

- «Модель видна, но не генерирует: как дневная квота 3.7 отличается от ошибки
  авторизации».
- «3 из 3 AntiGravity-кандидатов и verified 37signals — почему широкий rollout
  всё равно ждёт 30 вызовов».

## Визуальные материалы

- Публичный runtime:
  <https://canary.5-129-236-90.sslip.io/?key=Tn7aThw4ATAEHog8cM906C6b5L_rk1yT>.
- [Desktop, открытый виджет](../assets/2026-08-17-antigravity-canary/37signals-desktop-open.png).
- [Mobile, открытый виджет](../assets/2026-08-17-antigravity-canary/37signals-mobile-open.png).
- [Desktop, публичный ответ](../assets/2026-08-17-antigravity-canary/37signals-desktop-chat.png).

## Призыв

Не использовать canary как массовый пользовательский запуск. Следующее решение
принимается после gate 10 runs / 30 candidates.
