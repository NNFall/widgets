# Контролируемый AntiGravity-canary в Kaigo Builder

Дата: 2026-08-17

Статус: release `ec45ecb` развёрнут в production, AntiGravity включён только для
контролируемого canary; широкий rollout — `hold`.

## Коротко

Kaigo теперь умеет пробовать AntiGravity для трёх параллельных идей будущего
виджета и автоматически возвращаться к Codex при допустимой ошибке. Транспорт,
изоляция и публикация проверены на production, но общая квота с AMIX пока не
даёт считать новый маршрут стабильным для всех пользователей.

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
- Прямой post-restart smoke из worker: HTTP 200,
  `actual_provider=gemini`, `actual_model=gemini-3.7-flash`,
  `conversation_deleted=true`.

## Два production-прогона

### Run `6704…`: реальное использование Gemini, но без публикации

- Два из трёх direction candidates завершились через Gemini.
- Первый кандидат получил quota error и штатно ушёл в Codex fallback.
- Судья выбрал `candidate-3`.
- Собранный затем артефакт не прошёл visual quality gate. Его не публиковали и
  не используем как доказательство готового виджета.

### Строгий Basecamp-run `61d…`: verified-путь через fallback

- Run завершился, а опубликованная версия имеет quality status `verified`.
- Все три AntiGravity-вызова упёрлись в общую квоту и были завершены через
  Codex fallback.
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
- Канонический rollback пересобран из `bc27…`.
- Перед переключением сохранён дамп production-базы.
- Изменение не требует миграции и откатывается переключением worker release и
  feature flag.

## Ограничения

- Общая с AMIX квота уже вызвала один смешанный run и один полный fallback.
- Флаг остаётся `true` только для контролируемого canary; широкий rollout
  запрещён до следующего gate.
- Успешный provider smoke не заменяет серию полных verified-генераций.

## Следующий gate

Провести 10 контролируемых запусков и получить 30 candidate-вызовов. Перед
широким rollout сравнить долю quota/fallback, latency, валидность structured-
ответов и качество выбранных направлений. При неприемлемой доле fallback
оставить маршрут только экспериментальным или выделить отдельную квоту.

## Возможные темы

- «Два из трёх Gemini-кандидатов сработали — почему виджет всё равно нельзя
  публиковать без visual gate».
- «Fallback спас Basecamp-run, но честно показал: общей AI-квоты недостаточно
  для широкого запуска».

## Визуальные материалы

- Публичный runtime:
  <https://canary.5-129-236-90.sslip.io/?key=zokrD57Pe07T_WaXCEF306LUGEVyzMC->.
- Отдельные кадры можно добавлять только после сохранения подтверждённых
  desktop/mobile screenshots этого canary.

## Призыв

Не использовать canary как массовый пользовательский запуск. Следующее решение
принимается после gate 10 runs / 30 candidates.
