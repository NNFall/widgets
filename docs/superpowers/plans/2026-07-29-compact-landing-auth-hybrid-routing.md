# План реализации compact landing, OAuth и hybrid routing

> Выполнять через TDD: сначала наблюдаем красный тест, затем минимальная реализация, затем полный регресс и браузерная проверка.

**Цель:** исправить первый экран на эффективном viewport 1536×960, вернуть B2B-текст, сделать OAuth-ошибки понятными и направить решения в GPT-5.5, а код/repair — в GLM-5.2.

**Архитектура:** React/Vite отвечает за лендинг и AuthGate; aiohttp + PostgreSQL — за OAuth/сессии; `ModelRouter` выбирает provider/model по `(role, mode)`; `BuilderWorker` разделяет proposal, judge и artifact generation.

---

## Задача 1. Зафиксировать новый B2B-текст

**Файлы:**
- изменить `frontend/src/App.test.tsx`
- изменить `frontend/src/landing/LandingPage.test.tsx`
- изменить `frontend/e2e/landing.spec.ts`
- изменить `frontend/src/landing/HeroSection.tsx`
- изменить `frontend/index.html`

1. Обновить тестовые ожидания на фразу «Через 10 минут вы сможете сказать: наш бизнес использует AI» и бесплатную первую версию.
2. Запустить unit-тесты и получить ожидаемое падение.
3. Изменить JSX, meta description и title.
4. Повторить unit-тесты до зелёного состояния.

## Задача 2. Исправить compact desktop 1536×960

**Файлы:**
- изменить `frontend/e2e/landing.spec.ts`
- изменить `frontend/src/styles.css`
- при необходимости изменить `frontend/src/landing/HeroOrbitScene.test.tsx`

1. Добавить геометрическую проверку: каждый видимый process card заканчивается левее browser mockup с контролируемым зазором.
2. Добавить resize/check для 1536×960 наряду с существующим compact-проектом.
3. Запустить только compact E2E и зафиксировать пересечение примерно 48–70 px.
4. В compact media query уменьшить/перенести карточки, не меняя desktop 1920×1080 и mobile.
5. Повторить compact E2E, desktop E2E и unit-тесты сцены.

## Задача 3. Сделать OAuth-ошибки безопасными и понятными

**Файлы:**
- изменить `tests/saas_cases/test_auth_routes.py`
- изменить `frontend/src/auth/AuthGate.test.tsx` или ближайший существующий тест
- изменить `app/auth/routes.py`
- изменить `frontend/src/auth/AuthGate.tsx`

1. Добавить тесты provider `error` callback, token-exchange `OAuthError` и безопасного redirect-кода.
2. Добавить frontend-тест русского сообщения и повторного входа.
3. Убедиться, что тесты падают на текущем необработанном HTTP 500/игнорируемом query.
4. Сопоставить известные ошибки с короткими публичными кодами, логировать приватную диагностику только на сервере и redirect в `/studio?auth_error=...`.
5. Отобразить понятный текст в AuthGate и сохранить кнопки повторного входа.
6. Прогнать auth-тесты и проверку отсутствия секретов в ответах/логах теста.

## Задача 4. Добавить hybrid-конфигурацию

**Файлы:**
- изменить `builder_lab/config.py`
- изменить `.env.example`
- изменить `docker-compose.yml`
- изменить `tests/builder_lab_cases/test_model_config.py`
- изменить `tests/builder_lab_cases/test_worker.py`

1. Тестами задать AgentRouter key/base/timeout, GPT/GLM model names и отдельные цены.
2. Тестами задать матрицу ролей: GPT — решения/judge/review; GLM — artifact/repair; Gemini — image roles.
3. Получить красные тесты на текущем Gemini-only worker.
4. Добавить fail-closed конфигурацию и две группы `ProviderTarget`.
5. Не передавать image requests в text-only AgentRouter.

## Задача 5. Разделить direction board и art-direction artifact

**Файлы:**
- изменить `builder_lab/directions.py`
- изменить `builder_lab/worker.py`
- изменить/добавить `tests/builder_lab_cases/test_directions.py`
- изменить `tests/builder_lab_cases/test_routed_repair_roles.py`

1. Тестом подтвердить три GPT proposal, один GPT judge и отдельный GLM artifact call.
2. Изменить `run_direction_board`, чтобы proposer и judge могли быть разными engines.
3. В worker создать отдельные routed engines для `direction_candidate`, `direction_judge`, `art_direction_generator`.
4. Сохранить audit role/model на каждом вызове и корректно закрывать engines/providers.
5. Прогнать worker/orchestrator/routed-repair регресс.

## Задача 6. Улучшить benchmark-аудит

**Файлы:**
- изменить `scripts/compare_builder_models.py`
- изменить `tests/model_cases/test_compare_builder_models.py`

1. Добавить падающий тест на `role`, `model`, `prompt_sha256`, `prompt_bytes`, usage и безопасный класс ошибки в каждой попытке.
2. Добавить поля без сохранения API key и лишних персональных данных.
3. Подтвердить, что публичный отчёт не получает приватный raw response.

## Задача 7. Проверить, опубликовать и задокументировать

**Файлы:**
- обновить `docs/product-journal/2026-07.md`
- создать release packet для этой итерации
- при наличии самостоятельной темы обновить content backlog

1. Запустить focused Python, frontend unit, compact/desktop/mobile E2E и production build.
2. Проверить миграции/readiness без выполнения реального OAuth и платежа.
3. Создать точечный commit, не добавляя грязные Telegram-файлы.
4. Push ветки и штатный deploy `kaigo.space` с неизменными секретами.
5. Во встроенном браузере проверить 1536×960 и 1920×1080: текст, отсутствие пересечений/overflow, переход в студию и понятное OAuth-состояние.
6. Реальный вход выполнить после того, как пользователь создаст OAuth-приложение и передаст credentials в production secret store.
