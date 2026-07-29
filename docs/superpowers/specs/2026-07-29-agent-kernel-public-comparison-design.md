# Public Agent Kernel Model Comparison

**Status:** approved for implementation  
**Date:** 2026-07-29

## Purpose

Publish the already completed frozen-input GLM-5.2 versus GPT-5.5 benchmark as a
safe, public and interactive Kaigo page. The page must let a nontechnical viewer
open both generated widgets, compare desktop and mobile evidence, understand the
cost and quality difference, and see why neither result was accepted for
publication.

## Public routes

- `/builder-comparison/agent-kernel-v1/` — comparison hub.
- `/builder-comparison/agent-kernel-v1/glm-5.2/` — isolated GLM preview.
- `/builder-comparison/agent-kernel-v1/gpt-5.5/` — isolated GPT preview.
- `/builder-comparison/agent-kernel-v1/assets/**` — copied, explicitly selected
  benchmark screenshots only.

The existing comparison pages and their URLs remain unchanged.

## Page structure

The comparison hub contains four compact views:

1. **Live comparison** — two sandboxed preview frames with model labels and an
   explicit warning that these are rejected benchmark artifacts, not published
   customer widgets.
2. **Visual evidence** — desktop and mobile screenshots for closed, initial-open
   and after-two-turn states. A simple state switch keeps the page usable on a
   laptop and phone.
3. **Metrics** — elapsed time, input/output/thinking tokens, ruble cost, retries,
   repair cycles, browser repairs, visual score, validator result, browser gate
   result and publishability.
4. **What happened** — a short Russian explanation of the frozen input,
   Composition Plan, repair loop, final horizontal-overflow failure and the
   practical conclusion.

The visual direction follows the current Kaigo landing: warm off-white canvas,
navy text, coral and mint accents, restrained motion and clear typography. The
comparison itself, not decorative dashboard chrome, remains the first viewport.

## Interaction and isolation

- Generated previews run only inside iframes with `sandbox="allow-scripts"`.
- The parent comparison page does not grant same-origin, navigation, popup,
  download, form-submission or storage privileges to generated code.
- Desktop/mobile controls resize only the preview shell; they do not rewrite the
  generated artifacts.
- The screenshot gallery is usable by keyboard and touch.
- Each public preview route serves a frozen copy and never reads from the private
  benchmark directory at request time.

## Public data boundary

Only these inputs may be copied into the public package:

- the two final `preview.html` files;
- the twelve selected browser screenshots;
- the redacted metrics already present in
  `docs/model-benchmarks/2026-07-29-agent-kernel-frozen-v1.json`.

The package must not contain provider keys, request IDs, raw responses,
technical JSON, Windows cache files, database files, private paths or hidden
reasoning. Request IDs are omitted even though the redacted report currently
contains them.

## Honest interpretation

The page must not name a winner without qualification:

- GPT-5.5 used fewer tokens, cost less and received the higher visual score.
- GLM-5.2 completed faster.
- Both passed the deterministic validator.
- Both failed the browser gate because horizontal overflow remained.
- Therefore both are useful research artifacts, but neither is publishable.

## Publication

Build the package reproducibly from explicit source paths, validate every public
file against an allowlist, copy it to the existing production comparison root,
and verify the three routes through the in-app Codex Browser on desktop and
mobile. Publication must not rebuild or restart the main Kaigo application.

## Acceptance criteria

- Both live previews open and remain interactive inside sandboxed frames.
- Switching model, evidence state and desktop/mobile mode works.
- Metrics exactly match the frozen JSON report.
- No request ID, secret, private absolute path or unrelated benchmark file is
  present in the public output.
- The page has no horizontal overflow at 1440x900 or 390x844.
- Existing `/builder-comparison/` pages still respond normally.
- The new public URLs are verified in the in-app Browser.
