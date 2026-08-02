# Runtime lineage модельных вызовов

Дата: 2 августа 2026 года.

## Что было не так

Колонки lineage уже существовали в `model_calls`, но runtime записывал большую
часть вызовов как `legacy_unclassified`. По строке нельзя было надёжно определить:

- попытку этапа генерации;
- смысл конкретного вызова и номер смысловой доработки;
- кандидата или роль критика;
- primary/fallback одной логической отправки.

Кроме того, terminal-запись могла остаться в `dispatched` при отмене во время
аудита, а две одновременные одинаковые финализации конфликтовали вместо
идемпотентного завершения.

## Что реализовано

- `ModelRouter.generate` получает обязательный `ModelInvocationContext`.
- Один semantic-вызов получает общий `logical_invocation_id`, а каждый
  provider target — отдельный `call_id` и последовательный `fallback_index`.
- Audit сохраняет `stage_attempt_id`, `operation`, `semantic_attempt`,
  `candidate_id` и `persona` во всех исходах.
- Terminal-first записи отклоняются; переход `dispatched -> terminal` выполняется
  через compare-and-swap.
- Одинаковая конкурентная terminal-финализация перечитывает победившую строку и
  считается идемпотентной; конфликтующие данные отклоняются.
- Отмена во время записи `completed`, `failed` или `timed_out` не оставляет
  вызов навсегда в `dispatched`.
- Worker строит базовый контекст из `RunClaim.attempt_id` и текущего этапа.
- Reference retry записывается как `reference_analysis -> schema_correction`.
- Artifact retry записывается как `artifact_generation -> validation_repair`.
- Visual critic/judge/repair/verification сохраняют stage, candidate/persona и
  увеличивают `semantic_attempt` для новой смысловой попытки.
- Visitor chat получает `operation=chat` и намеренно не связывается с builder
  stage attempt.

## Проверки

Контрольный gate:

```text
228 passed, 4 skipped, 10 warnings in 60.17s
```

В него вошли router/audit, SQL CAS, trial recovery, reference analysis,
directions, artifact engine, visual review/repair, worker и project chat.

Дополнительно:

- Ruff: без ошибок;
- `compileall`: без ошибок;
- `git diff --check`: без ошибок;
- независимый spec review: approved.

Четыре skip относятся к необязательным PostgreSQL-интеграциям без
`KAIGO_TEST_POSTGRES_URL`. Основная SQL-семантика проверена на SQLite, включая
реальную конкурентную barrier-сцену.

## Что это даёт продукту

После развёртывания каждый новый модельный вызов можно будет восстановить как
цепочку: запуск -> попытка этапа -> смысловой запрос -> primary/fallback ->
terminal-исход -> токены и стоимость. Это основа для честного сравнения моделей,
поиска дорогих этапов и безопасного сокращения времени генерации.

Live-доказательство в production в эту итерацию не входит: текущие внешние
credentials Gemini и AgentRouter ранее возвращали соответственно permission
denial и HTTP 401. Повторный платный прогон до исправления внешнего доступа не
запускался.
