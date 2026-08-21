# Refinement Chat History and Mobile Preview Fit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each durable widget refinement as a real user/assistant exchange in the Studio chat and keep the 390×844 mobile preview entirely inside every supported workbench viewport.

**Architecture:** Build refinement chat entries only from owner-visible project versions, owner-scoped run snapshots, and their already projected public events. Keep the just-submitted request in a bounded session cache until the server materializes its version, then replace it with the durable server history. Extract mobile fit math into a pure helper and apply the minimum of width and height ratios from `ResizeObserver` without an inline canvas minimum height.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, Playwright, CSS Grid, ResizeObserver.

---

### Task 1: RED tests for durable refinement chat

**Files:**
- Create: `frontend/src/studio/refinementConversation.test.ts`
- Modify: `frontend/src/studio/SaasStudioFlow.test.tsx`

- [ ] **Step 1: Define the expected public conversation contract**

Add fixtures containing an initial version, refinement versions with `change_request`, and owner-scoped runs whose events include public `stage.completed` messages. Assert the resulting entries contain only:

```ts
{
  runId: 'run-refinement-2',
  changeRequest: 'Переименуй виджет в RFN Assistant',
  status: 'completed',
  versionNumber: 2,
  assistantTitle: 'Готово — версия 2',
  assistantMessage: 'В RFN Assistant обновлена реакция закрытого launcher…',
}
```

Add separate assertions for queued/running progress, failed/cancelled terminal copy, the completed-summary fallback, ordering by version ordinal, and rejection of non-`stage.completed` provider/internal text.

- [ ] **Step 2: Add reload/deep-link UI coverage**

Render `StudioPage` at `/studio?project=<id>` with two server versions. Mock `GET /api/runs/{run_id}` for each refinement run. Assert the left region named `Чат с Kaigo` shows each exact user request, `Готово — версия N`, and its final public summary without opening the Versions drawer.

- [ ] **Step 3: Add active and failed request coverage**

Submit a refinement through the existing composer and assert its user bubble appears immediately with a visible queued/running assistant status. Then return a failed run and assert the failure message remains inside the same refinement exchange. Reload the same project and assert the bounded session cache restores the active request while run status/events still come from the server.

- [ ] **Step 4: Verify RED**

Run from `frontend/`:

```powershell
npx.cmd vitest run src/studio/refinementConversation.test.ts src/studio/SaasStudioFlow.test.tsx --maxWorkers=1 --pool=forks
```

Expected: FAIL because no conversation builder, history controller field, or user/assistant refinement bubbles exist.

- [ ] **Step 5: Commit RED tests**

```powershell
git add frontend/src/studio/refinementConversation.test.ts frontend/src/studio/SaasStudioFlow.test.tsx
git commit -m "test: define durable refinement chat history"
```

### Task 2: Implement the safe refinement conversation model

**Files:**
- Create: `frontend/src/studio/refinementConversation.ts`
- Modify: `frontend/src/studio/types.ts`
- Modify: `frontend/src/studio/useBuilderRun.ts`

- [ ] **Step 1: Add a public chat entry type**

Define `RefinementConversationEntry` with `runId`, exact owner-visible `changeRequest`, `status`, optional `versionNumber`, and two already-safe assistant strings. Do not include raw prompt, request payload, provider, `error_message`, usage, or operational payload.

- [ ] **Step 2: Build entries from durable sources**

Implement a pure builder that:

```ts
const summary = [...run.events]
  .reverse()
  .find((event) => event.type === 'stage.completed' && event.message?.trim())
  ?.message?.trim();
```

uses that message only for completed refinements, uses `safeActivityForEvent` for in-progress status, and uses fixed friendly terminal copy for failed/cancelled runs. When a completed public summary is absent, return `Доработка завершена. Откройте версию справа и проверьте результат.`

- [ ] **Step 3: Hydrate historical refinement runs**

When the versions list changes, fetch owner-scoped `/api/runs/{run_id}` for every `kind="refinement"` entry not already cached. Merge successful public snapshots without making one failed historical read block the workbench. Add the active run to the same map in `applyRun`.

- [ ] **Step 4: Preserve an in-flight request across reload**

Create a transient chat entry before awaiting the refinement POST so the user bubble appears immediately. If the POST itself fails, keep that exact request in the same entry with friendly failure copy. After a successful POST returns its run id, persist only `{projectId, runId, changeRequest}` in `sessionStorage`. Read it only for the matching current owner project/run. Remove it when the same run appears as a durable refinement version. This cache supplements, but never replaces, the version/run/event history.

- [ ] **Step 5: Verify GREEN**

Run the Task 1 command. Expected: all refinement conversation tests PASS.

- [ ] **Step 6: Commit the model/controller**

```powershell
git add frontend/src/studio/refinementConversation.ts frontend/src/studio/types.ts frontend/src/studio/useBuilderRun.ts frontend/src/studio/refinementConversation.test.ts frontend/src/studio/SaasStudioFlow.test.tsx
git commit -m "feat: restore refinement chat history"
```

### Task 3: Render real refinement exchanges in the left chat

**Files:**
- Create: `frontend/src/studio/StudioRefinementHistory.tsx`
- Create: `frontend/src/studio/StudioRefinementHistory.test.tsx`
- Modify: `frontend/src/studio/StudioProjectWorkbench.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: RED component semantics**

Assert each entry renders one right-aligned user bubble followed by one assistant bubble. Completed entries expose `Готово — версия N`; running entries have `role="status"`; failed entries use an inline terminal state tied to the same `<li>`.

- [ ] **Step 2: Implement the history component**

Render an ordered list inside `studio-conversation__feed`. Keep the general introductory assistant message only when no refinement history exists. Preserve `StudioProgress`, `StudioTimeline`, retry/cancel controls, and the Versions drawer.

- [ ] **Step 3: Add scoped product UI styles**

Use the existing white/orange/metal palette. User bubbles align right and use a quiet warm surface; assistant entries keep the existing Kaigo mark. Do not introduce decorative motion, gradients in text, nested cards, or changes outside the workbench.

- [ ] **Step 4: Verify and commit**

Run focused component and flow tests, then:

```powershell
git add frontend/src/studio/StudioRefinementHistory.tsx frontend/src/studio/StudioRefinementHistory.test.tsx frontend/src/studio/StudioProjectWorkbench.tsx frontend/src/styles.css
git commit -m "feat: show refinement exchanges in Studio chat"
```

### Task 4: RED tests for height-aware mobile preview

**Files:**
- Modify: `frontend/src/studio/StudioPreview.test.tsx`

- [ ] **Step 1: Test pure fit geometry**

For a 390×844 source viewport, assert the helper uses:

```ts
scale = Math.min(
  1,
  availableWidth / 390,
  availableHeight / 844,
);
```

Cover available canvases derived from 390×844, 390×720, 360×640, and 320×568 workbenches. Assert scaled width and height never exceed available dimensions.

- [ ] **Step 2: Test ResizeObserver integration**

Mock `clientWidth`, `clientHeight`, padding, and `ResizeObserver`. Trigger the observer after changing the canvas height. Assert `data-preview-scale`, slot width/height, and device transform update, while `canvas.style.minHeight` stays empty.

- [ ] **Step 3: Verify RED**

```powershell
npx.cmd vitest run src/studio/StudioPreview.test.tsx --maxWorkers=1 --pool=forks
```

Expected: FAIL because the implementation ignores `clientHeight` and writes an inline minimum height.

- [ ] **Step 4: Commit RED tests**

```powershell
git add frontend/src/studio/StudioPreview.test.tsx
git commit -m "test: define height-aware mobile preview fit"
```

### Task 5: Implement height-aware preview fit

**Files:**
- Modify: `frontend/src/studio/StudioPreview.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add bounded fit math**

Parse canvas padding defensively, compute positive available width and height, and constrain scale by both axes. If one dimension is temporarily zero, constrain by the measured dimension and let `ResizeObserver` correct it after layout.

- [ ] **Step 2: Remove the inline minimum height**

Never set `canvas.style.minHeight` in mobile mode. Continue to remove legacy sizing when switching to desktop or the preview disappears. Keep the iframe’s intrinsic 390×844 contract and transform only the outer device.

- [ ] **Step 3: Verify GREEN and desktop isolation**

Run `StudioPreview.test.tsx` and the Studio accessibility/flow suites. Assert desktop preview has no transform or mobile slot sizing.

- [ ] **Step 4: Commit the geometry fix**

```powershell
git add frontend/src/studio/StudioPreview.tsx frontend/src/studio/StudioPreview.test.tsx frontend/src/styles.css
git commit -m "fix: fit mobile Studio preview by height"
```

### Task 6: Browser and release verification

**Files:**
- Modify: `frontend/e2e/studio.spec.ts`
- Modify: `docs/product-journal/2026-08.md`
- Create: `docs/telegram/release-packets/2026-08-21-refinement-chat-mobile-preview.md`
- Modify: `docs/telegram/content-backlog.md`
- Create: `docs/release-evidence/2026-08-21-refinement-chat-mobile-preview.md`
- Create: screenshots under `docs/release-evidence/assets/2026-08-21-refinement-chat-mobile-preview/`

- [ ] **Step 1: Add browser contracts**

Extend the production-like Studio fixture with the RFN version 2 request and final public summary. At 1280×720 and every mobile viewport, assert chat text is visible without opening Versions, the mobile device rectangle stays within the preview canvas, and `documentElement.scrollWidth === documentElement.clientWidth`.

- [ ] **Step 2: Run local gates**

From `frontend/`:

```powershell
npm run typecheck
npm run lint
npm run test -- --run
npm run build
npx.cmd playwright test e2e/studio.spec.ts --project=desktop-1920 --project=mobile-390
```

Then run `git diff --check` from the checkout root.

- [ ] **Step 3: Inspect with the in-app browser**

Open the production-like local build and capture 1280×720, 390×844, 390×720, 360×640, and 320×568. Record canvas/device/iframe bounding rectangles before and after. Verify no lower-edge clipping, no horizontal overflow, restored refinement chat after reload, and no private payload in visible text.

- [ ] **Step 4: Update the editorial contour**

Record only verified results in the August product journal, release packet, and content backlog. Link screenshots according to `docs/telegram/assets/README.md`; do not publish Telegram content.

- [ ] **Step 5: Final review and handoff**

Confirm billing, publication, Yandex Direct, production checkout, and server services are unchanged. Report release readiness without deploying production.
