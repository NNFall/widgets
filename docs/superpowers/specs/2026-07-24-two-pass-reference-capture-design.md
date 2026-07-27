# Two-pass reference capture

Date: 2026-07-24

## Problem

The reference crawler waits for every laid-out image before it starts scrolling.
On long pages such as `https://about.flowwow.com/`, off-screen images use
`loading="lazy"` and intentionally stay incomplete until the viewport reaches
them. The initial global image wait therefore times out before the first
screenshot or any Gemini request.

The existing capture pass already uses real wheel events and observes document,
nested, and transform-driven scrolling, but it starts too late and combines
content discovery with evidence capture.

## Approved behavior

Reference capture becomes a bounded two-pass process.

### Pass 1: visual warm-up

1. Wait for document load, fonts, and only images intersecting the current
   viewport plus a small preload margin.
2. Traverse downward with the existing wheel-first scrolling strategy.
3. At each step, wait for local viewport images and a short visual-quiet period
   so lazy assets and scroll-triggered animations can render.
4. Stop when the end is proven stable, the scroll-step limit is reached, or the
   height limit is reached.
5. Return to the beginning with reverse wheel events and the existing
   script-based fallback when wheel scrolling cannot move the active scroller.
6. Verify that the active scroll state is back at its initial position before
   evidence capture starts.

### Pass 2: evidence capture

From the restored initial position, run the existing wheel-first traversal
again. Capture the top, middle, and bottom (or last observed) viewport images
and collect semantic/style samples during this second pass only.

The capture remains viewport-based. It must not replace the process with one
stitched full-page screenshot because that would not faithfully exercise lazy
loading, sticky elements, nested scrollers, or transform-driven animation.

## Waiting and failure semantics

- Image readiness is scoped to the current viewport plus a bounded preload
  margin. Off-screen lazy images do not block the current step.
- A local image-settle timeout is recorded as a warning and capture continues.
- Navigation failure, redirect-policy violation, byte-budget violation, an
  undecodable screenshot, or inability to restore the starting scroll state
  remain fatal.
- Warm-up exhaustion at the existing step or height cap records partial
  coverage but still permits the second pass from the restored start.
- No unbounded retry or infinite-scroll behavior is introduced.

Existing limits remain authoritative:

- maximum 40 scroll steps per pass by default;
- maximum observed scroll height of 50,000 pixels by default;
- wheel-first movement with script fallback;
- document, nested-element, and virtual/transform scroll detection.

## Observability

The result records a reset strategy that distinguishes the new two-pass flow
from the previous `not-required-top-first` behavior. Non-fatal warm-up or local
image timeouts appear in `skipped_reasons`.

The user-facing run should still fail with `reference_capture_failed` only for
fatal capture errors. A lazy off-screen image by itself is not fatal.

## Tests

Regression coverage must prove:

1. Off-screen lazy images no longer block the initial stage.
2. Wheel events occur before the first screenshot.
3. The first screenshot is taken at the restored top position.
4. A scroll-triggered/lazy element revealed during warm-up remains available
   during the capture pass.
5. Top, middle, and bottom evidence is still produced for a normal document.
6. Nested and transform-driven scrollers retain their existing behavior.
7. Step and height caps remain bounded and report partial coverage rather than
   looping indefinitely.

The Flowwow reproduction is used as a live deployment check after automated
tests pass; it is not a replacement for deterministic local regression tests.
