# Kaigo Friendly Studio

## Goal

Turn Builder Lab Studio into a calm, understandable workspace for a business owner who does not know generation terminology. The user should always understand what Kaigo is doing, what has already been completed, what they can do next, and where their previous widgets are stored.

This design was approved in conversation on 5 August 2026. It expands the previously agreed Stage-first Studio direction with a project library, clearer version history, friendlier subscription and publication copy, and a truthful live activity line.

## Product scene and design posture

The primary scene is a business owner using an ordinary laptop during the working day. They have a site, little technical context, and a short attention window. They need reassurance, a visible result, and one obvious next action.

The Studio therefore uses:

- a light warm surface that continues Kaigo's cream, navy, coral, and sage language;
- medium visual density, with one page scroll and no nested scrolling regions;
- restrained motion, limited to short state transitions and the live activity line;
- body text of at least 14 px, secondary text of at least 12 px, and interactive targets of at least 44 px;
- plain Russian copy, with internal identifiers and model names hidden from the default view.

The existing marketing visual concept remains unchanged. This specification concerns the authenticated Studio experience and the user-facing Studio error state.

## Scope

The first implementation covers the complete Studio shell:

1. A library of the current user's projects using the existing `GET /api/projects` endpoint.
2. A friendly project workspace for created, queued, running, completed, failed, and cancelled runs.
3. Seven primary generation stages driven by backend stage and progress fields.
4. A live activity line that presents real incoming work messages for at least two seconds each.
5. A dominant desktop/mobile preview.
6. Refinement and version history with full change requests and restore actions.
7. Clear subscription, payment, and publication guidance after a verified result exists.
8. Friendly loading, empty, session error, generation error, and reduced-motion states.

No backend files, API contracts, database migrations, nginx configuration, or production checkout will be changed. SSE and polling fallback remain in place.

## Routes and information architecture

Studio remains one product surface instead of becoming a multi-step wizard.

- `/studio` after authentication shows the project library and the existing new-widget composer.
- `/studio?draft=<id>` preserves the current draft claim and authentication flow.
- `/studio?project=<id>` opens the selected project workspace.
- Returning to the library never destroys the active run or draft.
- Creating a widget opens the project workspace using the existing project query parameter.

The top-level Studio navigation contains only:

- `Мои виджеты`;
- the current project domain when a project is open;
- `Новый виджет`;
- the context-sensitive publication action after a verified result exists.

There is no separate settings dashboard in this iteration.

## Project library

The library uses the existing owner-scoped `GET /api/projects` response. The frontend adds a typed `getProjects()` function and does not fetch every artifact or version just to decorate the list.

Each project row shows:

- the source domain as the primary label;
- one human status label;
- the last update in local date and time;
- a short description derived from the saved brief when available;
- the primary action `Открыть`.

The rows use typographic hierarchy and dividers, not a grid of identical cards. Status values map to:

| API value | User label |
| --- | --- |
| `created` | Можно начинать |
| `queued` | Ожидает запуска |
| `running` | Создаётся сейчас |
| `completed` | Готов к работе |
| `failed` | Нужен повторный запуск |
| `cancelled` | Создание остановлено |

Unknown values are rendered as `Состояние уточняется`, never as the raw backend value.

The empty state says what the user receives and presents one `Создать первый виджет` action. Loading uses stable text rows or a restrained skeleton with the same geometry. A list failure keeps the new-widget composer available and offers `Повторить загрузку`.

## Project workspace

### Desktop hierarchy

The workspace uses a two-column grid beneath a compact product header:

- the left column is 300 to 340 px and contains the primary progress story;
- the right column owns the remaining width and contains preview, refinement, versions, and publication in that order;
- the document has one vertical scroll;
- neither the progress region nor the version history has its own scrollbar.

### Mobile hierarchy

Below 860 px the order is:

1. project title and primary status;
2. current stage, total progress, and live activity;
3. preview and desktop/mobile switch;
4. the current primary action;
5. refinement;
6. version history;
7. subscription and publication;
8. optional technical details.

The preview never causes document-level horizontal overflow at 390 px.

## Primary generation progress

The user sees seven named stages in backend order:

| API stage | User label |
| --- | --- |
| `art_direction` | Образ и характер |
| `foundation` | Основа виджета |
| `identity` | Стиль и бренд |
| `conversation` | Диалог |
| `motion_polish` | Анимация и детали |
| `validation` | Проверка качества |
| `agent_build` | Подготовка AI-консультанта |

`current_stage`, `last_completed_stage`, and numeric `progress` are authoritative for SaaS runs. Event count is not used to invent progress. The legacy Builder Lab adapter may keep its existing isolated fallback until that route is retired.

Each primary stage has only three visual states: completed, current, and upcoming. The current stage shows one short explanatory sentence. Completed stages remain readable but visually quiet. Upcoming stages do not imitate disabled form controls.

### Run state behavior

- `created`: `Можно начинать`, with `Создать AI-виджет` as the primary action.
- `queued`: `Готовим запуск`, with a secondary cancel action when permitted.
- `running`: `Создаём ваш виджет`, with stage, progress, live activity, and a secondary cancel action.
- `completed`: `Виджет готов`, with preview and refinement as the primary workspace.
- `failed`: a safe explanation, preserved draft and preview, and `Повторить запуск`.
- `cancelled`: `Создание остановлено`, preserved work, and `Запустить снова`.

A terminal state cannot regress to a non-terminal state for the same run, including when an incoming snapshot has the same sequence number.

## Live activity line

The live activity line gives the sense of ongoing work without becoming an engineering log.

- It appears only for queued or running work.
- It consumes real SSE or polling events after they pass through a user-facing formatter.
- A visible message stays in place for at least 2,000 ms.
- When events arrive in a burst, the next update uses the newest unseen safe message and may skip stale intermediate messages.
- When no new event arrives, the latest message remains visible. The frontend does not fabricate a stream.
- Unknown raw messages fall back to the current stage explanation instead of exposing English labels, byte counts, file paths, model names, colors, or internal identifiers.
- The transition is an opacity and transform crossfade of at most 160 ms.
- Under `prefers-reduced-motion`, the text changes without animation.
- Timers are cleaned up on unmount and when the run becomes terminal.

Examples of acceptable copy:

- `Изучаем структуру и содержание сайта`;
- `Собираем основу будущего виджета`;
- `Настраиваем стиль под ваш бренд`;
- `Проверяем, удобно ли вести диалог`;
- `Проверяем результат перед показом`.

Technical details remain available in a collapsed disclosure. They are secondary, capped to the latest useful events, and never control the main progress UI.

## Preview, refinement, and versions

The preview is the dominant result surface. It retains the sandbox, chat bridge, device switch, open/close behavior, and existing security boundaries.

Before a preview exists, the area explains what will appear there instead of showing an empty technical canvas. A restorable draft remains visible after a failure. Desktop and mobile controls use at least 44 px targets and expose pressed state to assistive technology.

After completion, the refinement form asks one plain question: `Что изменить в виджете?` It includes a concrete example and states that the current version is preserved. Submission retains the existing refinement API and idempotency behavior.

Version history is a compact vertical timeline:

- `Версия 1`, `Версия 2`, and so on;
- `Первая версия`, `Доработка`, or `Восстановление` as the kind label;
- creation date;
- full change request with `Показать полностью` and `Свернуть` when long;
- `Посмотреть` for any materialized version;
- `Восстановить` for an eligible inactive version;
- `Текущая версия` instead of a disabled action for the active version.

No user-authored change request is silently truncated.

## Subscription and publication

Payment remains after the user has seen a verified result. The section first explains the value in business language, then shows the available action.

- The current plan and renewal state use Russian labels.
- Auto-renewal copy states the next charge date and how to disable renewal.
- Technical plan codes are hidden.
- Publication copy explains that the widget becomes available on the user's site.
- The ordinary user sees `Подготовить публикацию` or `Опубликовать виджет`, not raw deployment terminology.
- The script snippet, stable embed URL, allowed origins, artifact identity, and release revisions move under `Код для разработчика`.
- Copying developer code remains an explicit action and never happens automatically.

The existing billing, checkout, publication, rollback, and HTTPS-origin APIs are preserved.

## Error handling and user copy

The authentication boundary never renders `session:<status>` in the default interface. Its error state contains:

- `Студия сейчас не открылась`;
- one sentence explaining that the user's work is safe;
- `Повторить`;
- `Вернуться на главную`;
- optional technical details under a disclosure.

Known generation error codes map to specific Russian guidance. Unknown codes use a safe generic explanation. Raw codes may appear only inside the technical disclosure.

The header does not expose run UUID suffixes. Engine names such as `Gemini staged` and `Antigravity agent` are removed from the ordinary project flow. Advanced fields that are not actionable for a business owner are omitted rather than merely disabled.

## Frontend boundaries

The implementation keeps state and presentation responsibilities separate:

- `studioPresentation.ts` owns all stage, status, activity, and error copy mapping.
- `StudioLibrary.tsx` owns project list loading states and rows.
- `StudioProgress.tsx` owns primary stages and progress.
- `StudioActivity.tsx` owns the two-second live activity display and timer cleanup.
- `StudioPage.tsx` composes the workspace and stops owning presentation mappings.
- `ProjectVersionHistory.tsx` owns expandable version copy and version actions.
- `UpgradeGate.tsx` owns friendly subscription and publication disclosure.
- `AuthGate.tsx` owns retryable session errors without exposing raw details.
- `useBuilderRun.ts` retains transport state, SSE, polling, hydration, and monotonic state guards.

The components use the existing React, Motion, Phosphor icon, and CSS stack. No new dependency is required.

## Accessibility and motion

- All interactive controls have a minimum 44 by 44 px target.
- Focus remains visible against cream and navy surfaces.
- Status is communicated with text and semantics, not color alone.
- Primary progress uses an ordered list and exposes the current step.
- Live activity uses a polite live region and does not replay older messages.
- Text remains usable at 200 percent zoom.
- Reduced motion removes crossfades, spring movement, and preview device transitions that are not essential.
- The design avoids infinite decorative animation.

## Data flow and invariants

1. Auth resolves the current session and CSRF token.
2. `/studio` loads `GET /api/projects` for the library.
3. A selected project loads its project record, active run, versions, selected artifact, billing, and publication state through existing endpoints.
4. SSE remains the preferred run update source.
5. Polling remains the recovery fallback and stops after terminal state.
6. Snapshots are applied only when they do not regress sequence or terminal state.
7. Refresh restores the selected project, active run, preview, version, and publication state.
8. UI-only activity timing never changes persisted run state.

## Verification

The implementation uses test-driven development and must prove:

- the project library loads, handles empty and error states, and opens a selected project;
- every backend project and run status maps to user-facing Russian copy;
- unknown statuses and stages never leak raw strings;
- the live activity message stays visible for at least two seconds, advances truthfully, and cleans up its timer;
- reduced motion removes the activity transition;
- terminal SaaS runs cannot regress at equal or lower sequence;
- session errors provide retry and home actions without exposing raw details by default;
- long version requests expand and collapse without data loss;
- subscription and publication keep technical details collapsed;
- desktop and mobile Studio have no document-level horizontal overflow;
- keyboard navigation, focus, pressed state, live-region semantics, and serious/critical axe checks pass;
- unit, typecheck, lint, build, and focused end-to-end flows pass;
- visual checks cover 1536 by 960, 1440 by 900, 390 by 844, and reduced motion in the in-app Codex browser.

## Acceptance criteria

The iteration is accepted when a first-time business owner can:

1. open Studio and recognize their existing widgets;
2. start or resume a widget without seeing internal generation terminology;
3. understand the current main stage and read a truthful live activity update;
4. refresh without losing progress or result;
5. preview the widget on desktop and mobile, then open and close it;
6. request a change and understand that the previous version is preserved;
7. inspect, preview, and restore versions without truncated user text;
8. understand the subscription and publication path before seeing developer details;
9. recover from session or generation errors through an obvious next action;
10. complete the same path on a 390 px viewport and with reduced motion enabled.
