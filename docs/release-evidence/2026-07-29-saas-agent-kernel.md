# Kaigo SaaS Foundation + Agent Kernel — release evidence

Дата проверки: 2026-07-29.

## Что вошло в кандидат

- owner-scoped SaaS-путь: landing → anonymous draft → OAuth gate → project → trial → durable generation → preview → publish → stable embed;
- PostgreSQL 15 и Alembic до `0013_pattern_registry`;
- Git-backed Pattern Registry, structured Composition Planner, deterministic resolver, bounded custom escape и terminal outcomes;
- синхронизация `PatternOutcome.published` с активным release при публикации, обновлении и rollback; `adopted` не выставляется без отдельного достоверного события;
- одинаковый frozen input для GLM-5.2 и GPT-5.5 с отчётом по времени, токенам, стоимости, repair-циклам, visual score и publishability;
- отдельный loopback-only Browser acceptance harness без внешнего OAuth, платёжного провайдера и model API.

## Фактические проверки

### PostgreSQL 15

В disposable `postgres:15-alpine` выполнен marker-suite:

```text
9 passed, 1172 deselected in 186.06s
```

Набор включает upgrade до `0013_pattern_registry`, drift-check, downgrade → upgrade и конкурентный idempotent start. Временный контейнер после проверки удалён.

### Browser acceptance

Во встроенном Codex Browser вручную пройден реальный React-путь:

1. landing и ввод URL/brief;
2. сохранение opaque draft и восстановление после reload;
3. локальный Google consent, реальный callback/session и claim draft;
4. создание project и бесплатный trial;
5. deterministic worker, revision 5 и preview;
6. восстановление project/run/preview после reload;
7. localhost-only активация тестовой подписки без payment provider;
8. публикация и получение stable embed snippet;
9. открытие runtime URL.

Проверены размеры Studio:

```text
desktop 1440x900: horizontal overflow = false
mobile 390x844: horizontal overflow = false
```

Автотест harness дополнительно запрашивает реальный embed loader и runtime document, проверяет stable key, artifact ID, revision и содержимое опубликованного artifact.

Harness привязан только к literal loopback и отсутствует в production app. Общий `/api/health/ai` в нём не регистрируется: локальный endpoint статический и не может вызвать модель даже при наличии `GOOGLE_AI_API_KEY`.

### Регрессии

```text
488 passed, 6 skipped, 8 deselected
```

Один Chromium security-test в общем прогоне получил только navigation timeout; немедленный изолированный повтор прошёл:

```text
1 passed in 16.11s
```

Новые focused tests и style gates:

```text
10 passed
ruff check: passed
ruff format --check: passed
git diff --check: passed
```

Frontend:

```text
17 files / 159 tests passed
eslint: passed
TypeScript + Vite production build: passed
```

### Production snapshot до нового promotion

Проверен текущий production commit `64d64332698364a1a291c4d12d966e76a44ad5de`:

- remote tree clean;
- app, builder-lab, systemd builder-worker и PostgreSQL 15 запущены;
- `/api/ready` возвращает `200`, database `ok`, worker `ok` со свежим heartbeat;
- `/`, `/studio`, `/api/auth/session` возвращают `200`;
- закрытый `/builder/` возвращает ожидаемый `401`;
- свежих traceback/fatal/uncaught/error в app/builder-lab logs не найдено.

## Frozen model comparison

| Model | Время | Input | Output | Всего | Стоимость | Visual score | Publishable |
|---|---:|---:|---:|---:|---:|---:|---|
| GLM-5.2 | 786.888 с | 220 294 | 50 334 | 270 628 | ₽162.38 | 0.616 | нет |
| GPT-5.5 | 1482.472 с | 109 645 | 34 783 | 144 428 | ₽101.10 | 0.772 | нет |

Оба результата честно отклонены browser gate из-за horizontal overflow. Отчёт не подменяет неуспешный результат успешным: [сводка](../model-benchmarks/2026-07-29-agent-kernel-frozen-v1.md) и [машиночитаемые данные](../model-benchmarks/2026-07-29-agent-kernel-frozen-v1.json).

## Осознанно не выполнялось

- реальный вход Google/Яндекс;
- реальное списание или webhook YooKassa;
- удаление subagent storage без подтверждённого списка закрытых descendants.

Эти действия требуют отдельных production credentials/решения пользователя. Локальная приёмка использует только disposable identity, SQLite и тестовую активацию тарифа.
