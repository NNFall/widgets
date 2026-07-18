# План публикации Kaigo Builder и постоянного демо

> **Для агентных исполнителей:** ОБЯЗАТЕЛЬНЫЙ НАВЫК: выполнять этот план через `executing-plans` или `subagent-driven-development`, отмечая пункты `[ ]` по мере выполнения.

**Цель:** открыть Builder Lab на `kaigo.space` без SSH, сохранить интерактивный результат реальной Gemini-генерации и перевести операторские документы на русский язык.

**Архитектура:** полный Builder проксируется под `/builder/` и защищается Basic Auth; два read-only демо-маршрута доступны под `/builder-demo/` без авторизации. Финальный валидированный артефакт и безопасные метаданные сохраняются атомарно в подключённый каталог и повторно валидируются перед каждым показом.

**Стек:** Python 3.11, aiohttp, unittest, Docker Compose, nginx, Gemini API, Playwright CLI.

---

## Задача 1. Относительные browser-маршруты

**Файлы:**

- Изменить: `builder_lab/ui.py`
- Изменить: `tests/builder_lab_cases/test_web.py`

- [ ] Добавить тест, который запрещает литералы `'/api/` и `` `/api/ `` в HTML и требует `api/runs` для `fetch`, `EventSource` и preview iframe.
- [ ] Запустить `python -m unittest tests.builder_lab_cases.test_web -v` и получить падение на абсолютных адресах.
- [ ] Добавить JS-функцию `labUrl(path)`, которая строит адрес через `new URL(path, document.baseURI)`, и перевести все обращения на относительные `api/...`.
- [ ] Повторно запустить тесты и закоммитить изменение сообщением `fix: support builder reverse proxy prefix`.

## Задача 2. Безопасное постоянное демо

**Файлы:**

- Создать: `builder_lab/demo.py`
- Изменить: `builder_lab/web.py`
- Изменить: `scripts/run_builder_lab.py`
- Изменить: `scripts/smoke_builder_lab.py`
- Создать: `tests/builder_lab_cases/test_demo.py`
- Изменить: `tests/builder_lab_cases/test_web.py`
- Изменить: `tests/builder_lab_cases/test_runner.py`

- [ ] Тестами определить JSON-контракт `BuilderDemo`: версия, время создания, модель, request, usage, elapsed_seconds и финальный `WidgetArtifact`.
- [ ] Проверить, что `save_demo(path, snapshot, model)` отказывается сохранять незавершённый run, отсутствие артефакта и невалидный артефакт; запись должна идти через временный файл и `os.replace`.
- [ ] Проверить, что `load_demo(path)` повторно валидирует артефакт, а повреждённый JSON или неизвестная версия возвращают безопасную ошибку без provider diagnostics.
- [ ] Реализовать `render_demo_page(demo)` с экранированием prompt/метаданных и iframe `src="preview"`; добавить `/demo` и `/demo/preview` с CSP и `no-store`.
- [ ] Добавить `KAIGO_BUILDER_DEMO_PATH` в конфигурацию runner и передавать путь в `create_builder_lab_app`; при отсутствии файла `/demo` возвращает русскую страницу состояния 503.
- [ ] Добавить smoke-параметры `--demo-output` и `--model`; вызывать `save_demo` только после `validate_smoke_evidence` и проверки preview.
- [ ] Запустить целевые и полные тесты, затем закоммитить сообщением `feat: persist public builder demo`.

## Задача 3. Упаковка и русская документация

**Файлы:**

- Изменить: `docker-compose.yml`
- Изменить: `.env.example`
- Изменить: `docs/KAIGO_BUILDER_LAB_OPERATIONS.md`
- Переписать: `docs/superpowers/plans/2026-07-18-gemini-builder-lab.md`
- Изменить: `README.md`
- Изменить: `tests/builder_lab_cases/test_packaging.py`

- [ ] Добавить тест на `KAIGO_BUILDER_DEMO_PATH=/app/data/builder-demo/latest.json` и bind mount `./data/builder-demo:/app/data/builder-demo` только у `builder-lab`.
- [ ] Проверить падение packaging-теста, затем добавить конфигурацию Compose и `.env.example`.
- [ ] Полностью изложить назначение, запуск, публичные маршруты, защиту, генерацию демо, откат и проверку на русском языке. Английские названия оставить только для кода, API и официальных технических терминов.
- [ ] Переписать прежний англоязычный implementation plan как русскую фактическую историю архитектуры, этапов и live-доказательств.
- [ ] Запустить полный набор тестов, `compileall`, `pip check`, `docker compose config --quiet` и `git diff --check`; закоммитить сообщением `docs: publish Russian builder operations`.

## Задача 4. Публикация и реальный демо-артефакт

**Серверные файлы (не Git):**

- Изменить с резервной копией: `/etc/nginx/sites-available/kaigo.space`
- Создать: `/etc/nginx/.htpasswd-kaigo-builder`
- Создать через bind mount: `/root/ai_project/data/builder-demo/latest.json`

- [ ] Push feature-ветки и `git pull --ff-only` в чистом `/root/ai_project`.
- [ ] Собрать и перезапустить только `builder-lab`; убедиться, что production app и db не пересозданы.
- [ ] Сгенерировать криптографически случайный пароль, сохранить crypt-хеш в htpasswd-файле с правами `640` и не записывать пароль в Git или shell history.
- [ ] Создать timestamped backup nginx-конфига. Добавить exact redirect `/builder-demo`, read-only demo locations и защищённый prefix `/builder/`; проверить `nginx -t` до reload.
- [ ] Запустить реальный direct Gemini smoke с утверждённым архитектурным brief и `--demo-output /app/data/builder-demo/latest.json`.
- [ ] Проверить публичный demo без авторизации, `401` для полного Builder без авторизации и `200` с авторизацией.
- [ ] Через Playwright проверить открытие/закрытие launcher, desktop/mobile, prompt и метаданные демо, запуск Builder UI, SSE-ready состояние и нулевую browser console.
- [ ] Повторно проверить `https://kaigo.space/`, `/w/demka`, `https://kaigo.online/`, состояние app/db, loopback `8091` и отсутствие новых ошибок.
- [ ] Зафиксировать обезличенные live-доказательства по-русски, push финального docs-коммита и оставить feature-ветку без слияния в `main`.
