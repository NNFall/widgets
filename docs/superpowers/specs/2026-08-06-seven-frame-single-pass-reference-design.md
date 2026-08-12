# Seven-frame single-pass reference capture

## Goal

Make the technical website capture fast and observable without imposing a time
limit on the AI analysis that follows it.

## Capture contract

One browser traversal must produce exactly seven JPEG evidence states:

1. `desktop.top`
2. `desktop.after_top`
3. `desktop.middle`
4. `desktop.bottom`
5. `mobile.top`
6. `mobile.middle`
7. `mobile.bottom`

`desktop.after_top` is captured after the first desktop viewport has completely
left the screen (`scrollTop >= viewport height`, with a short-page fallback).
Mobile keeps three states.

## Traversal

- Load and settle the initial viewport once.
- Capture `top`.
- Scroll toward the end once, letting lazy images and reveal animations load.
- Settle only at evidence anchors, then capture them.
- Do not pre-scroll, return to the top, and scroll the page a second time.
- Preserve the existing URL, byte, redirect, timeout, and coverage guards.

## User-visible phases

The run timeline separates two different operations:

1. Technical loading and screenshot collection.
2. AI analysis of the seven screenshots and visual-direction ideation.

Only the first phase is a technical performance target. The AI phase has its
own provider timeout but no 15-second product target.

## Acceptance criteria

- Successful capture contains exactly the seven ordered states above.
- No warm-pass or reset-pass is executed by the evidence path.
- Capture metrics report the one evidence pass independently of AI usage.
- The timeline announces technical capture completion before AI analysis.
- The analyzer manifest and evidence-label validation accept seven states and
  reject incomplete or duplicated captures.
- A production crawler-only run on a public site other than Mindbox preserves
  the seven JPEGs and reports separate desktop, mobile, and total timings.
