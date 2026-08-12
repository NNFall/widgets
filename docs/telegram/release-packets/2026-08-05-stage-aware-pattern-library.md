# Kaigo: stage-aware библиотека UI- и motion-паттернов

Дата фиксации: 2026-08-05  
Ветка: `codex/saas-foundation`  
Статус: реализация и технические проверки завершены; production-активация нового selector пока выключена.

## Что подтверждено

В Kaigo появилась версиярованная библиотека небольших строительных блоков виджета. Это не набор жёстких готовых тем: модель получает несколько подходящих вариантов анимации или поведения, видит их полное текстовое объяснение, а затем адаптирует выбранный код под конкретный сайт.

Начальный каталог содержит 14 технических паттернов в 14 отдельных категориях: форма launcher, idle- и attention-анимации, компоновка оболочки, открытие и закрытие, фоновые эффекты, появление сообщений ассистента и пользователя, typing indicator, отправка сообщения, focus поля ввода, hover контролов и responsive-переходы.

Техническая цепочка теперь выглядит так:

1. После выбора визуального направления отдельный selector получает метаданные всех допустимых паттернов.
2. Для каждой категории selector выбирает от 2 до 5 кандидатов. В этот запрос передаётся полный `ai_description`, но не передаются HTML, CSS и JavaScript.
3. Сервер проверяет ответ модели, совместимость, версии, статусы и ограничения. При некорректном ответе используется детерминированный совместимый fallback.
4. На каждом этапе сборки сервер разрешает только нужные этому этапу категории и формирует небольшой immutable pack с точными версиями и кодом.
5. Первичная генерация и все repair-вызовы одного этапа получают один и тот же pack.
6. Выбор, точные версии, model-call lineage, exposure и подтверждённые usage claims сохраняются в PostgreSQL.
7. Перезапуск worker не вызывает selector повторно и не может незаметно заменить уже сохранённую версию паттерна.

## Проверки контракта

1. Selector получает полный `ai_description` каждого кандидата.
2. Selector не получает implementation assets и поэтому не раздувает контекст кодом всего каталога.
3. На категорию разрешено строго 2–5 кандидатов.
4. Ответ selector повторно валидируется обычным серверным кодом.
5. Некорректный ответ модели заменяется совместимым детерминированным fallback.
6. Сохранённый план переживает рестарт и не запускает повторный selector-call.
7. Категории жёстко сопоставлены этапам `foundation`, `identity`, `conversation` и `motion_polish`.
8. В pack попадают точные `pattern_id@version`; проверяются implementation hash, selector manifest и stage mapping.
9. Usage claim принимается только для реально существующего exposure/model call.
10. Старый production-путь остаётся неизменным при выключенном feature flag.
11. Миграция имеет upgrade/downgrade round-trip; текущий Alembic head один: `0018_stage_aware_pattern_library`.
12. Pattern Lab закрыта admin-сессией и allowlist, а preview работает в sandbox без `allow-same-origin`, с ограниченным CSP и фиксированным `postMessage`-протоколом.
13. Worker, router, visual-repair и legacy-регрессии прошли без платного или внешнего модельного прогона.

## Pattern Lab

Техническая страница доступна по адресу `/admin/pattern-lab` после входа администратора.

Она позволяет:

- фильтровать каталог по категории, статусу и review state;
- читать полный `ai_description`, contract, adaptation policy, provenance и hash;
- запускать безопасный preview и фиксированные команды `open`, `close`, сообщения, typing, desktop/mobile и replay;
- добавлять append-only решения `approved` или `rejected` с комментарием;
- видеть последние 100 решений с явным индикатором усечения истории.

## Результаты проверок

- Новая библиотека, selector, resolver, persistence, worker и Pattern Lab: `149 passed, 2 skipped`.
- Legacy registry/planner/worker/outcome regression: `106 passed, 2 skipped`.
- Дополнительная независимая проверка Task 5: до `141 passed, 2 skipped`; verdict обоих reviewers — Ready Yes.
- Pattern Lab: `8 passed`; объединённые admin suites: `16 passed`.
- Project API после отдельного исправления подписочного запуска: `33 passed, 1 skipped`.
- `compileall`, scoped Ruff, `git diff --check` — успешно.
- Два optional PostgreSQL-теста пропущены только потому, что локально не задан `KAIGO_TEST_POSTGRES_URL`; SQLite и migration-контракты пройдены.
- Известен старый Windows timing-flake `test_hanging_critic_close_is_bounded`; связанный набор без этого нестабильного тайминга проходит `43/43`. Он не относится к новой библиотеке.

## Безопасный rollout

Production-интеграция закрыта флагом `KAIGO_PATTERN_CANDIDATE_PLAN_V2_ENABLED`, значение по умолчанию — `false`. В этой итерации код не включался глобально и платный live-run не выполнялся.

Перед включением для всех пользователей остаются три осознанных шага:

1. Наполнить каждую категорию несколькими выразительными паттернами и визуально утвердить их через Pattern Lab.
2. Добавить clone/readiness candidate-плана для последующих refinement-запусков; сейчас этот путь ещё использует legacy composition repository.
3. Провести контролируемый canary на небольшом числе запусков, сравнить стоимость, скорость, ошибки и качество, затем отдельно принять решение о глобальном включении.

## Основные исходники

- Дизайн: `docs/superpowers/specs/2026-08-05-kaigo-stage-aware-pattern-library-design.md`.
- План реализации: `docs/superpowers/plans/2026-08-05-kaigo-stage-aware-pattern-library.md`.
- Каталог: `builder_lab/patterns/atomic_catalog/`.
- Selector: `builder_lab/patterns/candidate_planner.py`.
- Stage resolver: `builder_lab/patterns/candidate_resolver.py`.
- PostgreSQL persistence: `app/patterns/candidate_repository.py`.
- Pattern Lab: `app/admin/pattern_lab.py`.
- Production gate: `builder_lab/worker.py`, `scripts/run_builder_worker.py`.

## Отдельная production-правка аккаунта разработчика

Для `novoseltsevnikitos@yandex.ru` создан внутренний тариф `developer_unlimited` до 2036 года с 1 000 000 000 токенов текущего периода. Дополнительно исправлен Project API: после использования бесплатного trial новый запуск и retry теперь автоматически переходят на токены активной подписки. Реального платежа и автосписания для этой внутренней записи нет. Production `/api/health` после выкладки вернул `ok`.
