# Kaigo Expressive Landing Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the approved Kaigo landing into an expressive, cyclic product demonstration while preserving accessibility, performance, responsive layout, and the existing Studio product.

**Architecture:** A deterministic hero timeline hook owns semantic phases, visible-card count, program (`cinematic` or `loop`) and cycle number. Presentation components consume that state through Motion transforms and CSS decorative layers. Lower sections get small local motion primitives so their loops are isolated and can stop outside the viewport; the real Studio route is not modified.

**Tech Stack:** React 19, TypeScript 6, Motion 12, Vitest/Testing Library, Playwright, CSS, Vite.

---

### Task 1: Deterministic hero cycle

**Files:**
- Create: `frontend/src/landing/useHeroMotionCycle.ts`
- Create: `frontend/src/landing/useHeroMotionCycle.test.tsx`
- Modify: `frontend/src/landing/HeroOrbitScene.tsx`
- Modify: `frontend/src/landing/HeroOrbitScene.test.tsx`

- [ ] **Step 1: Write failing tests for the approved state machine**

Test the exported constants and hook through `HeroOrbitScene`: source at mount, scanning at 1.2 s, sequential `data-visible-cards` values at 2.4/4.0/5.6 s, widget at 7.5 s, complete after the first run, reset after the 12 s hold, and `data-motion-program="loop"` for subsequent cycles. Assert the visible `Сканирование…` label and that reduced motion goes directly to complete with no repeat timers.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `npm test -- --run src/landing/HeroOrbitScene.test.tsx src/landing/useHeroMotionCycle.test.tsx`

Expected: failures for missing hook, data attributes, scan label and repeat cycle.

- [ ] **Step 3: Implement the state machine**

Use one effect with exactly one pending timeout. Each event schedules only the next event. The schedule shape is:

```ts
type HeroMotionProgram = 'cinematic' | 'loop';
type HeroMotionPhase = 'source' | 'scanning' | 'widget' | 'complete' | 'resetting';
type HeroMotionState = {
  program: HeroMotionProgram;
  phase: HeroMotionPhase;
  cycle: number;
  visibleCards: 0 | 1 | 2 | 3;
};
```

The cinematic run is 1.2 s source + 6.0 s scan/widget build + 12 s hold. Later runs use a 5.4 s build + 11 s hold. Cancel every timer on unmount or reduced-motion changes.

- [ ] **Step 4: Bind semantic state to the hero scene**

Expose the diagnostic attributes on `.hero-scene`; key scan/card/widget entrances by `cycle`; keep the decorative browser hidden from assistive technology; render the scan label only as visual text with an accessible overall scene description.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run the same focused command. Expected: all hero tests pass and fake-timer cleanup leaves no pending cycle mutations after unmount.

### Task 2: Expressive hero presentation

**Files:**
- Modify: `frontend/src/landing/HeroOrbitScene.tsx`
- Modify: `frontend/src/shared/BrowserMockup.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/landing/HeroOrbitScene.test.tsx`
- Test: `frontend/src/landing/MotionContracts.test.ts`

- [ ] **Step 1: Add failing presentation contracts**

Assert presence of the scan-label element, scanner core/band/trail layers, per-card direction modifiers, widget-arrival burst, a larger hero widget modifier, and reduced-motion rules that disable all new infinite keyframes. Assert keyframes use transform/opacity rather than layout properties or animated box shadow.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `npm test -- --run src/landing/HeroOrbitScene.test.tsx src/landing/MotionContracts.test.ts`

- [ ] **Step 3: Implement the scanner and synchronized entrances**

Build the scanner from absolute pseudo-elements and semantic spans: luminous band, sharp line, grid trail, particles, and label. Drive its long/short duration from `data-motion-program`. Give cards distinct x/y/rotate starts and spring overshoot; keep complete-state drift isolated per card.

- [ ] **Step 4: Enlarge and strengthen the widget payoff**

Add a hero-only BrowserMockup variant so other usages do not change. Increase the message panel and launcher, add a transform/opacity halo burst, and use a higher-energy spring on each cycle. Keep the existing 13 s launcher pulse contract or replace it with an equally legible 12–15 s complete-state cycle.

- [ ] **Step 5: Harden responsive and reduced-motion CSS**

At max-width 980 px reduce travel and rotations; at max-width 640 px keep the scan label and widget fully inside the scene. Under reduced motion disable every new animation and transition while leaving the complete state visible.

- [ ] **Step 6: Run tests and build**

Run: `npm test -- --run src/landing/HeroOrbitScene.test.tsx src/landing/MotionContracts.test.ts && npm run typecheck`

Expected: green.

### Task 3: Animated public sections

**Files:**
- Create: `frontend/src/shared/AmbientMotion.tsx`
- Modify: `frontend/src/landing/HowItWorksSection.tsx`
- Modify: `frontend/src/landing/AnalysisSection.tsx`
- Modify: `frontend/src/landing/CaseStudySection.tsx`
- Modify: `frontend/src/landing/CapabilitiesSection.tsx`
- Modify: `frontend/src/landing/StudioSection.tsx`
- Modify: `frontend/src/landing/FaqSection.tsx`
- Modify: `frontend/src/landing/FinalCtaSection.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/landing/MotionContracts.test.ts`
- Test: `frontend/src/landing/AccessibilityContracts.test.tsx`

- [ ] **Step 1: Add failing section-motion contracts**

Test stable data markers for active route drawing, analysis scanner/nodes, larger case widget, capability icon drift, presentation-only Studio showcase motion and final CTA sequence. Test that every infinite class is disabled inside the existing reduced-motion media query.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `npm test -- --run src/landing/MotionContracts.test.ts src/landing/AccessibilityContracts.test.tsx`

- [ ] **Step 3: Implement a viewport-aware decorative primitive**

`AmbientMotion` uses Motion `useInView`, `useReducedMotion` and one `visibilitychange` listener to expose `data-motion-active`; active means `inViewport && documentVisible && !reducedMotion`. It must not register raw scroll listeners or update page-level state.

- [ ] **Step 4: Animate How it works and Analysis**

Draw the route on entry; alternate card directions; add URL/checklist/chat micro-loops. In Analysis, move four focus nodes and one scan ring between zones with transform/opacity only. Keep labels still and readable.

- [ ] **Step 5: Animate Case, Capabilities and landing Studio showcase**

Make before visibly desaturated and after vivid; increase the after widget; add a spring/halo on mode change. Float capability icons only. Limit Studio changes to its marketing mockup: cursor/version/preview presentation; do not modify any `frontend/src/studio/*` file or route/auth behavior.

- [ ] **Step 6: Animate FAQ entrance and Final CTA**

Keep FAQ expansion user-controlled. Choreograph final before/after separation, composer lift and guarantee reveal, then leave the footer static.

- [ ] **Step 7: Run section tests and full unit suite**

Run: `npm test -- --run`

Expected: 49 existing tests plus new motion tests pass.

### Task 4: Independent review and corrections

**Files:**
- Modify only files identified by reviewers.

- [ ] **Step 1: Spec compliance review**

Provide the approved design spec, plan and diff to a fresh reviewer. Require a requirement-by-requirement verdict, with special attention to cycle timing, scan/card synchronization, widget scale, reduced motion, mobile overflow and the untouched Studio route.

- [ ] **Step 2: Fix all spec gaps and re-review**

No “close enough” items remain.

- [ ] **Step 3: Code-quality review**

Require checks for timer cleanup, stale closures, offscreen work, transform-only loops, React re-render frequency, CSS containment and test brittleness.

- [ ] **Step 4: Fix all quality findings and re-review**

Run focused tests after every repair.

### Task 5: Browser verification and evidence

**Files:**
- Modify if visual testing reveals defects.
- Create screenshots under `docs/evidence/2026-07-27-expressive-motion/`.

- [ ] **Step 1: Run the app and inspect in the in-app browser**

Use the browser skill at 1920×1080 and 390×844. Capture source, mid-scan, complete, lower-section active states and mobile complete.

- [ ] **Step 2: Verify behaviour**

Confirm first cinematic timing, automatic faster replay, scan label, sequential cards, larger widget, section loops, no horizontal overflow, no console errors and reduced-motion final state.

- [ ] **Step 3: Run automated verification**

Run:

```powershell
npm test -- --run
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

Expected: all commands exit 0.

### Task 6: Documentation, delivery and cleanup

**Files:**
- Modify: `docs/PROJECT_JOURNAL.md`
- Modify: `docs/PRODUCT_BACKLOG.md`
- Modify the existing Telegram/editor handoff files used by this project.

- [x] **Step 1: Record the result**

Document the motion architecture, timings, browser evidence, commands, residual backlog and one concise publication angle.

- [x] **Step 2: Commit intentionally**

Stage only task-owned files; leave `frontend/public/assets/house-cutout-checker-preview.png` untouched. Commit with a scoped message such as `feat: amplify Kaigo landing motion`.

- [x] **Step 3: Push, deploy and smoke-test**

Push `codex/gemini-technical-foundation`, deploy through `scripts/deploy_marketing_site.sh`, verify `https://kaigo.space/`, and confirm `/studio` remains protected.

- [x] **Step 4: Clean task-created subagents**

Delete only completed agents created for this motion task using the approved cleanup workflow and report deleted count and reclaimed bytes.

## Completion update — 2026-07-27

Tasks 1–6 are complete, including documentation, commit, GitHub push, production deployment and verified cleanup of task-created agent storage.

Delivered contracts:

- deterministic cinematic and repeat hero timelines;
- activity-gated motion for hero and every animated landing section;
- reduced-motion final states without infinite animations;
- responsive desktop/mobile composition without horizontal overflow;
- independent visual, accessibility and technical review;
- 92/92 Vitest tests, 11/11 Playwright scenarios, TypeScript, ESLint and production build passing;
- evidence catalogued under `docs/evidence/2026-07-27-expressive-motion/`.
- commit `b7afacb` deployed atomically as `/var/www/kaigo-marketing/releases/b7afacb`;
- production smoke passed at `https://kaigo.space/`, while `/studio` and `/builder/` retained their expected `401` protection.
- 23 completed motion-task subagents were deleted through native `codex delete`; 573,439,092 bytes (546.87 MiB) of rollout storage were reclaimed and the parent task remained intact.
