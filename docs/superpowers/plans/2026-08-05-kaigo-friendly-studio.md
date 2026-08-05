# Kaigo Friendly Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a friendly Studio where a business owner can find existing widgets, understand truthful generation progress, preview and refine the result, manage versions, and prepare publication without seeing internal engineering terminology.

**Architecture:** Keep all existing SaaS APIs, authentication, SSE, polling, preview sandbox, billing, and publication transport. Add a pure presentation module, focused library/progress/activity components, and compose them from `StudioPage`; retain technical details as a collapsed secondary disclosure. Implement behavior test-first and keep backend files untouched.

**Tech Stack:** React 19, TypeScript 6, Vite 8, Vitest, Testing Library, Motion, Phosphor Icons, Playwright, existing plain CSS.

---

## File map

- Create `frontend/src/studio/studioPresentation.ts` and its unit test for Russian labels and safe event presentation.
- Create `frontend/src/studio/StudioActivity.tsx` and its test for truthful two-second activity timing.
- Create `frontend/src/studio/StudioProgress.tsx` and its test for seven-stage progress.
- Create `frontend/src/studio/StudioLibrary.tsx` and its test for project library states.
- Modify `frontend/src/studio/types.ts`, `api.ts`, and `useBuilderRun.ts` for list/stage contracts and terminal monotonicity.
- Modify `frontend/src/auth/AuthGate.tsx` for retryable session errors.
- Modify `StudioPage.tsx`, `StudioComposer.tsx`, `StudioTimeline.tsx`, `StudioPreview.tsx`, and `ProjectVersionHistory.tsx` for the friendly workspace.
- Modify `UpgradeGate.tsx` for subscription and publication disclosure.
- Modify `frontend/src/styles.css` and focused e2e tests for responsive, accessible visual behavior.

### Task 1: Presentation contracts and stage preservation

**Files:**
- Create: `frontend/src/studio/studioPresentation.ts`
- Create: `frontend/src/studio/studioPresentation.test.ts`
- Modify: `frontend/src/studio/types.ts`
- Modify: `frontend/src/studio/useBuilderRun.ts`
- Test: `frontend/src/studio/SaasStudioFlow.test.tsx`

- [x] **Step 1: Write the failing presentation tests**

```ts
import { describe, expect, it } from 'vitest';
import { STUDIO_STAGES, projectStatusLabel, runStatusPresentation, safeActivityForEvent } from './studioPresentation';

describe('Studio presentation', () => {
  it('maps every primary stage in backend order', () => {
    expect(STUDIO_STAGES.map(({ id }) => id)).toEqual([
      'art_direction', 'foundation', 'identity', 'conversation',
      'motion_polish', 'validation', 'agent_build',
    ]);
    expect(STUDIO_STAGES.map(({ label }) => label)).toEqual([
      'Образ и характер', 'Основа виджета', 'Стиль и бренд', 'Диалог',
      'Анимация и детали', 'Проверка качества', 'Подготовка AI-консультанта',
    ]);
  });

  it('never exposes unknown status or event strings', () => {
    expect(projectStatusLabel('internal_pending')).toBe('Состояние уточняется');
    expect(runStatusPresentation('failed').title).toBe('Нужен повторный запуск');
    expect(safeActivityForEvent({
      type: 'provider.internal', stage: 'identity', message: 'Gemini bytes=991 teal',
    })).toBe('Настраиваем стиль под ваш бренд');
  });
});
```

- [x] **Step 2: Run the test and verify RED**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/studioPresentation.test.ts --maxWorkers=1`

Expected: FAIL because `studioPresentation.ts` does not exist.

- [x] **Step 3: Implement the presentation module**

```ts
import type { BuilderRunStatus, BuilderStage } from './types';

export const STUDIO_STAGES: ReadonlyArray<{ id: BuilderStage; label: string; activity: string }> = [
  { id: 'art_direction', label: 'Образ и характер', activity: 'Изучаем структуру и содержание сайта' },
  { id: 'foundation', label: 'Основа виджета', activity: 'Собираем основу будущего виджета' },
  { id: 'identity', label: 'Стиль и бренд', activity: 'Настраиваем стиль под ваш бренд' },
  { id: 'conversation', label: 'Диалог', activity: 'Настраиваем полезный диалог с посетителем' },
  { id: 'motion_polish', label: 'Анимация и детали', activity: 'Дорабатываем движения и детали' },
  { id: 'validation', label: 'Проверка качества', activity: 'Проверяем результат перед показом' },
  { id: 'agent_build', label: 'Подготовка AI-консультанта', activity: 'Подготавливаем AI-консультанта к работе' },
];

const PROJECT_STATUS: Record<string, string> = {
  created: 'Можно начинать', queued: 'Ожидает запуска', running: 'Создаётся сейчас',
  completed: 'Готов к работе', failed: 'Нужен повторный запуск', cancelled: 'Создание остановлено',
};

export const projectStatusLabel = (status: string) => PROJECT_STATUS[status] ?? 'Состояние уточняется';

export function runStatusPresentation(status: BuilderRunStatus | null) {
  if (status === 'completed') return { title: 'Виджет готов', detail: 'Можно проверить результат и внести изменения.' };
  if (status === 'failed') return { title: 'Нужен повторный запуск', detail: 'Сохранённые данные и доступный черновик не потеряны.' };
  if (status === 'cancelled') return { title: 'Создание остановлено', detail: 'Работу можно запустить снова.' };
  if (status === 'running') return { title: 'Создаём ваш виджет', detail: 'Можно оставить страницу открытой или вернуться позже.' };
  if (status === 'queued' || status === 'created') return { title: 'Готовим запуск', detail: 'Kaigo начнёт работу автоматически.' };
  return { title: 'Проект готов к запуску', detail: 'Проверьте сайт и пожелание перед началом.' };
}

export function safeActivityForEvent(event: { type: string; stage: BuilderStage | null; message?: string | null }) {
  const byType: Record<string, string> = {
    'reference.started': 'Изучаем структуру и содержание сайта',
    'reference.completed': 'Сайт изучен, переходим к виджету',
    'visual_audit.started': 'Проверяем виджет на разных экранах',
    'visual_audit.completed': 'Визуальная проверка завершена',
    'artifact.committed': 'Сохраняем готовую версию',
  };
  return byType[event.type]
    ?? STUDIO_STAGES.find(({ id }) => id === event.stage)?.activity
    ?? 'Продолжаем создавать ваш виджет';
}
```

Add `current_stage?: BuilderStage | null` and `last_completed_stage?: BuilderStage | null` to `BuilderRunSnapshot`; copy both fields in `adaptSaasRun`.

- [x] **Step 4: Write a failing equal-sequence terminal-regression test**

In `SaasStudioFlow.test.tsx`, hydrate a completed run, then return a running snapshot for the same run and `latest_sequence`. Assert the completed state remains visible.

- [x] **Step 5: Implement the monotonic guard**

```ts
const previous = runRef.current;
const previousStatus = previous ? saasStatus(previous) : null;
const nextStatus = saasStatus(run);
if (
  previous?.id === run.id
  && previousStatus
  && TERMINAL_STATUSES.has(previousStatus)
  && !TERMINAL_STATUSES.has(nextStatus)
) return;
```

- [x] **Step 6: Run focused tests and verify GREEN**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/studioPresentation.test.ts src/studio/SaasStudioFlow.test.tsx --maxWorkers=1`

Expected: both files pass.

- [x] **Step 7: Commit**

```bash
git add frontend/src/studio/studioPresentation.ts frontend/src/studio/studioPresentation.test.ts frontend/src/studio/types.ts frontend/src/studio/useBuilderRun.ts frontend/src/studio/SaasStudioFlow.test.tsx
git commit -m "feat: add friendly studio presentation contracts"
```

### Task 2: Truthful two-second activity line

**Files:**
- Create: `frontend/src/studio/StudioActivity.tsx`
- Create: `frontend/src/studio/StudioActivity.test.tsx`

- [x] **Step 1: Write the failing timer test**

```tsx
it('keeps a visible activity for two seconds and then shows the newest safe event', () => {
  vi.useFakeTimers();
  const { rerender } = render(<StudioActivity running events={[]} stage="foundation" fallback="Начинаем работу" />);
  const nextEvent: BuilderEvent = {
    run_id: 'run-1', sequence: 2, timestamp: '', type: 'stage.started', stage: 'identity',
    status: 'running', message: 'raw model bytes', revision: null,
    usage: { prompt_tokens: 0, output_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
    issues: [], changes: [], error_code: null,
  };
  rerender(<StudioActivity running events={[nextEvent]} stage="identity" fallback="Продолжаем" />);
  act(() => vi.advanceTimersByTime(1_999));
  expect(screen.getByRole('status')).toHaveTextContent('Начинаем работу');
  act(() => vi.advanceTimersByTime(1));
  expect(screen.getByRole('status')).toHaveTextContent('Настраиваем стиль под ваш бренд');
  expect(screen.queryByText('raw model bytes')).not.toBeInTheDocument();
});
```

- [x] **Step 2: Run and verify RED**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/StudioActivity.test.tsx --maxWorkers=1`

Expected: FAIL because `StudioActivity` does not exist.

- [x] **Step 3: Implement the activity queue**

Use `MIN_VISIBLE_MS = 2_000`, `visibleAtRef`, and one cleanup-safe timeout. Store `{ key, text }`. For bursts, replace the pending value with the newest event; when no event arrives, keep the current text. Render one atomic polite status. Set `data-motion="reduced"` from `useReducedMotion` and leave animation to CSS.

- [x] **Step 4: Add cleanup and reduced-motion assertions**

Unmount with a pending update and assert the timer is cleared. Mock reduced motion and assert `data-motion="reduced"` while the text remains truthful.

- [x] **Step 5: Run and verify GREEN**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/StudioActivity.test.tsx --maxWorkers=1`

Expected: all tests pass and fake timers are restored.

- [x] **Step 6: Commit**

```bash
git add frontend/src/studio/StudioActivity.tsx frontend/src/studio/StudioActivity.test.tsx
git commit -m "feat: add truthful studio activity updates"
```

### Task 3: Seven-stage progress and technical disclosure

**Files:**
- Create: `frontend/src/studio/StudioProgress.tsx`
- Create: `frontend/src/studio/StudioProgress.test.tsx`
- Modify: `frontend/src/studio/StudioTimeline.tsx`
- Modify: `frontend/src/studio/StudioAccessibilityContracts.test.tsx`

- [x] **Step 1: Write failing progress semantics tests**

Render current stage `conversation`, last completed `identity`, and progress `52`. Assert `Диалог` has `aria-current="step"`, `Стиль и бренд` has `data-state="completed"`, `Проверка качества` has `data-state="upcoming"`, and the progressbar has `aria-valuenow="52"`.

- [x] **Step 2: Run and verify RED**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/StudioProgress.test.tsx --maxWorkers=1`

Expected: FAIL because `StudioProgress` does not exist.

- [x] **Step 3: Implement `StudioProgress`**

Compose `runStatusPresentation`, `STUDIO_STAGES`, and `StudioActivity`. Use an ordered list, a semantic progressbar, and only completed/current/upcoming states. Use backend progress directly; do not derive it from event count.

- [x] **Step 4: Convert `StudioTimeline` into a secondary disclosure**

```tsx
<details className="studio-technical">
  <summary>Технические детали</summary>
  <ol>{events.slice(-8).map((event) => (
    <li key={`${event.run_id}-${event.sequence}`}>
      {safeActivityForEvent(event)} · шаг {event.sequence}
    </li>
  ))}</ol>
</details>
```

Render safe type/stage labels, sequence, and timestamp only. Do not render arbitrary messages, model names, byte counts, file paths, raw issues, or raw changes.

- [x] **Step 5: Update accessibility contracts**

Assert an ordered stage list, polite live region, minimum 44 px controls, and removal of `.studio-activity__message` animation under reduced motion.

- [x] **Step 6: Run and verify GREEN**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/StudioProgress.test.tsx src/studio/StudioAccessibilityContracts.test.tsx --maxWorkers=1`

Expected: progress and accessibility tests pass.

- [x] **Step 7: Commit**

```bash
git add frontend/src/studio/StudioProgress.tsx frontend/src/studio/StudioProgress.test.tsx frontend/src/studio/StudioTimeline.tsx frontend/src/studio/StudioAccessibilityContracts.test.tsx
git commit -m "feat: replace event log with friendly progress"
```

### Task 4: Owner project library and Studio home

**Files:**
- Create: `frontend/src/studio/StudioLibrary.tsx`
- Create: `frontend/src/studio/StudioLibrary.test.tsx`
- Modify: `frontend/src/studio/types.ts`
- Modify: `frontend/src/studio/api.ts`
- Modify: `frontend/src/studio/api.test.ts`
- Modify: `frontend/src/studio/StudioComposer.tsx`
- Modify: `frontend/src/studio/StudioPage.tsx`

- [x] **Step 1: Write the failing API test**

```ts
it('lists the authenticated owner projects', async () => {
  const payload = { projects: [{ id: 'project-1', source_url: 'https://atelier.ru/', status: 'completed' }] };
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchMock);
  await expect(getProjects()).resolves.toEqual(payload);
  expect(fetchMock).toHaveBeenCalledWith('/api/projects', expect.objectContaining({ credentials: 'include' }));
});
```

- [x] **Step 2: Run and verify RED**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/api.test.ts --maxWorkers=1`

Expected: FAIL because `getProjects` is not exported.

- [x] **Step 3: Add the typed API**

```ts
export interface SaasProjectList { projects: SaasProject[] }

export function getProjects(signal?: AbortSignal) {
  return saasRequestJson<SaasProjectList>('/api/projects', { signal });
}
```

- [x] **Step 4: Write failing library tests**

Cover completed and running rows with Russian labels, local date and domain, `Открыть`, the empty-state `Создать первый виджет`, retry after failed fetch, and an unknown status that never leaks its raw value.

- [x] **Step 5: Implement `StudioLibrary`**

Load with `AbortController`, retry with a request counter, and render divider-separated rows. Derive the domain with `new URL(source_url).hostname`, falling back to `Сайт проекта`. Provide one heading `Мои виджеты` and one primary `Новый виджет` action.

- [x] **Step 6: Compose the Studio home**

When there is no project query, render `StudioLibrary` followed by `StudioComposer` configured as:

```tsx
<StudioComposer
  title="Создайте новый виджет"
  description="Добавьте сайт и коротко опишите, чем виджет должен помогать посетителям."
  submitLabel="Создать проект"
  sourceUrl={sourceUrl}
  brief={brief}
  pending={projectPending}
  error={formError}
  onSourceUrlChange={setSourceUrl}
  onBriefChange={setBrief}
  onSubmit={submitProject}
/>
```

`onOpenProject` writes `/studio?project=<id>` and calls `setProjectId(id)`. Add a `popstate` effect that synchronizes `projectId` with the URL.

- [x] **Step 7: Run and verify GREEN**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/api.test.ts src/studio/StudioLibrary.test.tsx src/studio/StudioPage.test.tsx --maxWorkers=1`

Expected: library, project creation, and legacy Builder tests pass.

- [x] **Step 8: Commit**

```bash
git add frontend/src/studio/StudioLibrary.tsx frontend/src/studio/StudioLibrary.test.tsx frontend/src/studio/types.ts frontend/src/studio/api.ts frontend/src/studio/api.test.ts frontend/src/studio/StudioComposer.tsx frontend/src/studio/StudioPage.tsx frontend/src/studio/StudioPage.test.tsx
git commit -m "feat: add studio widget library"
```

### Task 5: Friendly authentication recovery

**Files:**
- Modify: `frontend/src/auth/AuthGate.tsx`
- Modify: `frontend/src/auth/AuthGate.test.tsx`

- [x] **Step 1: Write the failing recovery test**

Mock `/api/auth/session` to return 502 once and an authenticated session on retry. Assert the friendly heading, hidden `session:502`, `Повторить`, `Вернуться на главную`, and successful child render after the retry click.

```tsx
expect(await screen.findByRole('heading', { name: 'Студия сейчас не открылась' })).toBeVisible();
expect(screen.queryByText('session:502')).not.toBeInTheDocument();
await user.click(screen.getByRole('button', { name: 'Повторить' }));
expect(await screen.findByRole('heading', { name: 'Студия доступна' })).toBeVisible();
```

- [x] **Step 2: Run and verify RED**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/auth/AuthGate.test.tsx --maxWorkers=1`

Expected: FAIL because the error state has no recovery actions and exposes raw content.

- [x] **Step 3: Implement retryable hydration**

Add `attempt` state and include it in the hydration effect dependency. Use `Студия сейчас не открылась`, a sentence that the user's work is safe, retry button, home link, and raw error only inside a closed `Технические детали` disclosure.

- [x] **Step 4: Run and verify GREEN**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/auth/AuthGate.test.tsx --maxWorkers=1`

Expected: draft, OAuth, authenticated-session, and recovery tests pass.

- [x] **Step 5: Commit**

```bash
git add frontend/src/auth/AuthGate.tsx frontend/src/auth/AuthGate.test.tsx
git commit -m "fix: make studio session errors recoverable"
```

### Task 6: Friendly workspace, refinement, and versions

**Files:**
- Modify: `frontend/src/studio/StudioPage.tsx`
- Modify: `frontend/src/studio/StudioPage.test.tsx`
- Modify: `frontend/src/studio/ProjectVersionHistory.tsx`
- Modify: `frontend/src/studio/ProjectVersionHistory.test.tsx`
- Modify: `frontend/src/studio/StudioPreview.tsx`

- [ ] **Step 1: Write failing version expansion tests**

Use a 320-character request. Assert `Показать полностью` reveals all text, `Свернуть` restores the preview, and the active row renders `Текущая версия` without a restore button.

- [ ] **Step 2: Run and verify RED**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/ProjectVersionHistory.test.tsx --maxWorkers=1`

Expected: FAIL because long text has no disclosure and the active row uses a disabled restore button.

- [ ] **Step 3: Implement expandable versions**

Track expanded IDs with `useState<Set<string>>`. Normalize but retain the complete request. Render a 240-character preview only while collapsed. Replace artifact-revision copy with version language and use a status badge for the active row.

- [ ] **Step 4: Write failing friendly-workspace tests**

For a completed SaaS project, assert the project domain in the header and the presence of `Мои виджеты`, `Новый виджет`, `Что изменить в виджете?`, and `Текущая версия`. Assert default content contains none of `Сессия`, `Gemini staged`, `Antigravity agent`, `runtime`, `launcher`, or `sandbox`, and uses `Версия` instead of `Ревизия`.

- [ ] **Step 5: Recompose `StudioPage`**

Remove advanced engine controls from SaaS mode. Replace the rail timeline with `StudioProgress`. Put refinement and versions below the preview. Use the project hostname instead of the run UUID. Add Studio navigation actions that update local history without changing backend state. Keep `/builder` controls isolated.

- [ ] **Step 6: Clarify preview controls**

Use `На компьютере` and `На телефоне`, preserve `aria-pressed`, and show a safe concise preview description by default rather than the raw art-direction report.

- [ ] **Step 7: Run and verify GREEN**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/ProjectVersionHistory.test.tsx src/studio/StudioPage.test.tsx src/studio/SaasStudioFlow.test.tsx --maxWorkers=1`

Expected: legacy Builder and SaaS project flows pass with friendly default copy.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/studio/StudioPage.tsx frontend/src/studio/StudioPage.test.tsx frontend/src/studio/ProjectVersionHistory.tsx frontend/src/studio/ProjectVersionHistory.test.tsx frontend/src/studio/StudioPreview.tsx
git commit -m "feat: simplify studio workspace for business owners"
```

### Task 7: Subscription, publication, layout, and reduced motion

**Files:**
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/studio/UpgradeGate.test.tsx`
- Modify: `frontend/src/studio/StudioAccessibilityContracts.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing publication disclosure tests**

For an active published subscription, assert `Виджет опубликован` and `Код для разработчика`, and assert the script snippet is hidden until the disclosure opens. Assert `Stable embed URL`, `HTTPS origin`, and the raw plan code are absent from the default copy.

- [ ] **Step 2: Run and verify RED**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/UpgradeGate.test.tsx --maxWorkers=1`

Expected: FAIL because developer content is visible and English terminology remains.

- [ ] **Step 3: Implement friendly subscription and publication copy**

```ts
const heading = active
  ? publication ? 'Виджет опубликован' : 'Всё готово к публикации'
  : 'Подключите виджет к сайту';
```

Describe auto-renewal with the next renewal or access-end date. Rename the origin field to `На каких сайтах разрешить виджет`. Move the snippet, stable URL, release and artifact identities, and revision details into a closed `Код для разработчика` disclosure. Keep existing handlers unchanged.

- [ ] **Step 4: Write failing CSS contract assertions**

```ts
expect(stylesSource).toMatch(/\.studio-rail\s*\{[^}]*overflow-y:\s*visible/s);
expect(stylesSource).not.toMatch(/\.studio-timeline__list\s*\{[^}]*max-height:\s*330px/s);
expect(stylesSource).toMatch(/min-height:\s*44px/);
expect(stylesSource.slice(stylesSource.lastIndexOf('@media (prefers-reduced-motion: reduce)')))
  .toMatch(/\.studio-activity__message[\s\S]*animation:\s*none !important/);
```

- [ ] **Step 5: Implement responsive Studio CSS**

Use a maximum 1480 px shell and `minmax(300px, 340px) minmax(0, 1fr)` desktop grid. Remove rail and timeline max-height scrolling. Set ordinary text to at least 14 px, secondary text to at least 12 px, and controls to at least 44 px. At 860 px use one column with progress before preview. At 390 px keep 16 px gutters and full-width actions. Animate only opacity and transform for at most 160 ms; remove nonessential motion under reduced motion.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `.\frontend\node_modules\.bin\vitest.cmd run --root frontend src/studio/UpgradeGate.test.tsx src/studio/StudioAccessibilityContracts.test.tsx --maxWorkers=1`

Expected: publication and CSS contracts pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/studio/UpgradeGate.tsx frontend/src/studio/UpgradeGate.test.tsx frontend/src/studio/StudioAccessibilityContracts.test.tsx frontend/src/styles.css
git commit -m "feat: clarify studio publication and responsive layout"
```

### Task 8: Integration, browser verification, and evidence

**Files:**
- Modify: `frontend/e2e/landing.spec.ts`
- Modify: `frontend/e2e/visual.spec.ts`
- Create: `docs/release-evidence/2026-08-05-friendly-studio/README.md`
- Create: `docs/release-evidence/2026-08-05-friendly-studio/*.png`

- [ ] **Step 1: Align the stale landing meta test**

Set `EXACT_DESCRIPTION` to the approved `frontend/index.html` description mentioning the free first version and the usual 10 to 20 minute range. Do not change the approved landing solely for the stale assertion.

- [ ] **Step 2: Update focused e2e journeys**

Assert project library, friendly progress, Russian viewport labels, refinement/version placement, collapsed developer disclosure, reload/resume, cancel/retry, billing/publication, no document overflow, serious/critical axe checks, and no infinite reduced-motion animation.

- [ ] **Step 3: Run all automated verification sequentially**

```bash
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Expected: all commands exit 0. Inspect expected, actual, and diff before updating any approved visual snapshot.

- [ ] **Step 4: Verify in the in-app Codex browser**

Run local frontend and inspect 1536 by 960, 1440 by 900, 390 by 844, and reduced motion. Verify library, running progress fixture, completed result, desktop/mobile preview, widget open/close, refinement, version expansion, and developer disclosure. Use production only for read-only comparison.

- [ ] **Step 5: Capture release evidence**

Store representative local screenshots and a README under `docs/release-evidence/2026-08-05-friendly-studio/`. Record commit, verification commands, viewport results, known external limitations, and that no live paid or generation side effect was triggered.

- [ ] **Step 6: Run repository checks**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only planned frontend, plan/spec, and release-evidence changes.

- [ ] **Step 7: Commit verification evidence**

```bash
git add frontend/e2e/landing.spec.ts frontend/e2e/visual.spec.ts docs/release-evidence/2026-08-05-friendly-studio
git commit -m "test: verify friendly studio journey"
```

- [ ] **Step 8: Prepare handoff without deployment**

Report the final local commit SHA, tests, screenshots, and backend contract assumptions. Do not push, pull production, restart services, change nginx, run migrations, or deploy. Wait for explicit push instruction before `git push -u origin codex/product-ui`.
