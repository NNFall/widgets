# Kaigo SaaS: статус проверяемого MVP на 1 августа 2026

Этот документ отделяет реализованный код от фактически доказанного production-поведения. Наличие теста или таблицы само по себе не считается подтверждением живого пользовательского сценария.

| Область | Текущий статус | Фактическое доказательство | Что ещё требуется |
|---|---|---|---|
| Контрольная генерация | Ожидает live-прогон | Production AI health: Gemini `gemini-3-flash-preview`, database/worker readiness — `200 ready` | Войти через Яндекс во встроенном браузере и пройти полный запуск для `rawbureau.ru` |
| Пятидневные forensic-логи | Развёрнуто, но ещё не заполнено новым live-run | `KAIGO_GENERATION_FORENSICS_ENABLED=true`; retention в коде — 120 часов; migration `0014`; production manifest count до контрольного запуска — 0 | Доказать создание manifest, redacted blobs и операторской временной шкалы на свежем запуске |
| Единый реестр событий | Развёрнуто | Production содержит 17 generation events; registry и timeline входят в успешный связанный набор тестов | Проверить полноту последовательности на новом полном запуске |
| Model lineage и стоимость | Частично доказано | Production содержит 7 model calls; frozen benchmark GLM-5.2/GPT-5.5 фиксирует время, токены, рубли и visual score | После live-run сравнить затраты по стадиям; текущий production использует Gemini, AgentRouter пока не включён |
| UI-паттерны | Код и Git-каталог развёрнуты | Registry/planner/resolver/quality tests входят в успешный набор; migration `0013` применена | Production pattern outcomes до нового запуска — 0; нужен живой composition plan и outcome |
| История и управляемая доработка | Развёрнуто | `KAIGO_PROJECT_VERSIONS_ENABLED=true`; production содержит 2 версии тестового проекта; version/refine/restore tests проходят | Проверить создание пользовательской версии и refine через Studio после live-run |
| Внешняя публикация | Доказано | Полный публичный HTTPS-canary: загрузка, чат, candidate, rollback, denied origin и CSP — `passed` | Для MVP дополнительных действий не требуется; красивый canary-домен можно настроить позже |
| ЮKassa и тарифы | Полный sandbox-цикл доказан | Test-mode checkout на 1 990 ₽, успешная оплата официальной тестовой картой, сохранение способа, повторное списание, продление периода и безопасное отключение автопродления; подробности — `2026-08-02-yookassa-recurring-test-shop.md` | Перед боевым запуском отдельно проверить настоящий магазин, юридические данные и чеки |
| Воронка | Развёрнута и получает данные | `KAIGO_FUNNEL_JOURNEYS_ENABLED=true`; production содержит 29 funnel events; reporting/retention/operator tests проходят | После нового запуска проверить путь landing → OAuth → generation → publication одной journey |
| Production readiness | Доказано | Migration head `0017_funnel_journeys`; token-authenticated `/api/ready` вернул database `ok`, worker `ok` | Продолжать использовать приватный readiness token; публичный запрос намеренно получает 404 |

## Свежая локальная регрессия

Связанный набор для forensic/event/model routing/patterns/versions/publication/funnel:

```text
462 passed, 14 skipped, 114 warnings in 109.22s
```

Пропуски относятся к необязательным интеграционным окружениям. Предупреждения — существующие `aiohttp` AppKey/RequestKey и несколько deprecation warnings; падений нет.

Биллинг отдельно:

```text
123 passed, 28 warnings in 46.36s
```

Внешняя публикация отдельно:

```text
14 passed in 3.09s
```

## Честный вывод

Архитектурные части дорожной карты уже находятся в production, но MVP нельзя объявлять полностью проверенным до одного нового полноценного пользовательского запуска. Именно он должен одновременно доказать заполнение forensic-слоя, stage attempts, model lineage, pattern outcome, project version и funnel journey. После него можно переходить к оптимизации времени и стоимости, опираясь на реальные данные одной и той же генерации.
