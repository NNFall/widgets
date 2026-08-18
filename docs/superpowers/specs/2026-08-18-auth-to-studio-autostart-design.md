# OAuth-to-Studio automatic start design

## Goal

После успешной авторизации по Google, Яндексу или VK проект, созданный из
сохранённого anonymous draft, должен сразу перейти в существующий express-run.
Промежуточная форма `StudioComposer` с повторным вводом URL не показывается.

## Scope and non-goals

Меняется только post-OAuth путь для проекта, который был создан из draft в этом
же браузере. Ручной путь `/studio?project=<id>` для уже существующего проекта
сохраняет текущую форму запуска. Backend не получает новый способ генерации:
автозапуск использует тот же authenticated `POST /api/projects/{id}/runs`,
idempotency key, trial/credit reservation и SSE/polling, что и ручная кнопка.

## User flow

1. Пользователь отправляет публичный URL на лендинге; backend сохраняет draft.
2. Если нужна авторизация, OAuth стартует с тем же draft id.
3. После callback backend claims draft и редиректит на
   `/studio?project=<id>&autostart=1`. Если claim выполнен frontend-ом для
   уже созданной сессии, он использует тот же query marker.
4. `StudioPage` загружает проект. При `autostart=1` и отсутствии active run
   один раз вызывает существующий `createRun` с URL/brief проекта.
5. Пока запрос запуска выполняется, показывается компактный progress-state
   «Запускаем создание виджета…» с доменом сайта, без повторной формы и
   инструкционного journey.
6. После получения run id отображается обычный `StudioProjectWorkbench` и
   существующая генерация продолжает обновляться через SSE/polling.
7. Marker `autostart` удаляется из URL перед запуском. Повторная загрузка не
   создаёт новый запуск: active run гидратируется обычным путём, а
   idempotency guard остаётся последней защитой.

## State and failure handling

- Autostart разрешён только для валидного `project` query и exact marker
  `autostart=1`; произвольные значения игнорируются.
- Если проект уже имеет active run, новый POST не отправляется; открывается
  текущий run.
- Если запуск отклонён (trial/credit, сеть или backend error), вместо формы
  показывается компактная ошибка с кнопкой «Повторить запуск». Повтор меняет
  только локальный attempt state и снова вызывает тот же idempotent API.
- Если claim не удался, существующая AuthGate error/recovery UI сохраняется.
- Direct `/studio?project=<id>` и переходы «Новый виджет» не получают marker и
  не меняют текущую ручную механику.

## Files and contracts

- `app/auth/routes.py`: callback redirect добавляет `autostart=1`, только когда
  callback действительно создал/вернул project.
- `frontend/src/auth/AuthGate.tsx`: frontend claim redirect добавляет marker.
- `frontend/src/studio/StudioPage.tsx`: распознаёт marker, запускает один
  express run после hydration и рендерит compact loading/error state вместо
  `StudioComposer`.
- `frontend/src/auth/AuthGate.test.tsx`: проверяет marker после bound-draft
  claim.
- `frontend/src/studio/SaasStudioFlow.test.tsx`: проверяет autostart request,
  отсутствие composer, active-run no-duplicate и сохранение manual path.
- `tests/saas_cases/test_auth_routes.py`: проверяет callback Location для
  claimed project и отсутствие marker при callback без project.

## Acceptance criteria

- Google OAuth with a draft lands directly in the generation progress view;
  URL/brief are not requested again and `StudioComposer` is absent.
- Exactly one `POST /api/projects/{project_id}/runs` is made for a draft
  project with no active run.
- Reload or callback replay with an existing active run makes zero additional
  run POSTs.
- Existing manual project route still renders the composer and does not
  autostart.
- AuthGate, Studio flow, backend auth-route tests, typecheck, lint and focused
  browser smoke pass.
