# Достоверная модель и cache usage в provider adapters

Дата: 2 августа 2026 года.

## Что было не так

AgentRouter и Gemini возвращали больше данных, чем сохранял Kaigo. Терялись
cache-токены и подтверждённая фактическая модель, а отрицательные, строковые или
повреждённые usage-поля молча превращались в нули. В результате будущая
аналитика могла приписать вызову неверную модель или показать нулевой расход.

## Что реализовано

- AgentRouter переносит `cache_read_input_tokens` и подтверждённую модель из
  `system/init` в provider-neutral contract.
- Gemini переносит `cached_content_token_count` и подтверждённый
  `model_version`.
- Requested target не копируется в actual identity, если upstream не подтвердил
  фактически обслужившую модель.
- Negative, boolean, string, malformed-container и cross-bucket usage теперь
  отклоняются как `InvalidModelResponse`.
- При невалидном ответе сохраняются корректные buckets, request ID и actual
  identity, чтобы уже понесённый расход не исчезал.
- Прямой Gemini-движок Builder Lab преобразует такую ошибку в безопасный
  `BuilderEngineError` и сохраняет usage всех предыдущих semantic attempts.
- Денежная стоимость не выдумывается: без документированного provider charge
  `reported_cost_microusd` остаётся `None`.

## Проверки

Финальный локальный gate:

```text
132 passed in 13.96s
```

В него вошли AgentRouter/Gemini adapters, прямой Gemini engine, builder usage,
model cost contracts и comparison runner.

Дополнительно:

- Ruff: без ошибок;
- `compileall`: без ошибок;
- `git diff --check`: без ошибок;
- независимый spec review: PASS;
- независимый code-quality review: PASS.

## Граница результата

Этот срез делает provider-ответы достоверными, но ещё не переносит новые
actual/cache/cost-state поля через router в SQL ledger. Это следующий отдельный
этап. Production deploy и платный live-прогон в этот срез не входят.
