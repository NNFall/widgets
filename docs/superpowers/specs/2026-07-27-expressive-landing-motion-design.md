# Kaigo Expressive Landing Motion Design

## Status

Approved directly by the product owner in the 2026-07-27 task. This document translates that approved direction into deterministic UI states, timings, accessibility rules, and visual acceptance criteria. The Studio product and its authentication are outside this change.

## Product intent

The public landing page must visibly demonstrate transformation: Kaigo scans an ordinary website, understands it, and adds a polished AI widget. Motion is not decoration here; it explains the product. The page should feel energetic, deliberate, and crafted, while remaining readable and performant.

## Hero narrative

The hero has two motion programs.

### First visit: cinematic program

1. **Source hold — 1.2 s.** The neutral website mockup is large and dominant. Cards and widget are absent.
2. **Scanning — 6.0 s.** A bright coral scan band moves slowly from top to bottom. It includes the visible label `Сканирование…`, a sharp core line, a soft glow, a trailing grid, and small moving particles. It must read unmistakably as scanning.
3. **Evidence cards.** The three cards appear while the band passes their vertical zones, not after the scan finishes:
   - card 1 at 2.4 s;
   - card 2 at 4.0 s;
   - card 3 at 5.6 s.
   Each card uses a different entrance direction plus spring overshoot and a short icon accent.
4. **Transformation.** During the scan the browser moves right, scales down, gains colour, and makes room for the evidence trail.
5. **Widget arrival — 7.5 s.** The final widget is materially larger than the old preview. It arrives with a strong scale/vertical spring, a short halo burst, and the `Готовый AI-виджет` callout.
6. **Complete hold — 12 s.** Cards, connector and widget keep restrained isolated micro-motion. The composition itself remains stable and readable.

### Repeat program

After the complete hold, the scene resets without a hard cut. The browser returns to the larger neutral source state, cards and widget leave, and the same story repeats in a faster 5.4 s cycle. Completed state then rests for 11 s before the next repeat. The first cinematic run must never replay at full duration during the same mount.

The root scene exposes `data-motion-program`, `data-motion-phase`, `data-motion-cycle`, and `data-visible-cards` for deterministic testing and diagnostics. The sequence is advanced by exactly one pending timeout; every transition cancels or replaces that timeout, so repeated cycles cannot multiply callbacks.

## Hero visual language

- The scan band uses coral and warm-white light against the cream background; it must remain legible at desktop and mobile sizes.
- Animation intensity comes from distance, scale, rotation, stagger, light and secondary particles, not layout thrashing.
- The widget is the visual payoff. Its open message panel is larger, its launcher remains recognizable, and the arrival is the strongest single spring on the page.
- Perpetual motion is isolated to decorative layers: small card drift, scanner-ready shimmer, widget breath, orbit dash, and tiny particles. Copy, controls and the overall layout do not continuously move.
- At `prefers-reduced-motion: reduce`, the final complete state is rendered immediately: no timers, no infinite motion, no scan trail, no delayed content.

## Lower-section choreography

### How it works

- The route draws as the section enters.
- Cards enter with alternating horizontal direction and stronger spring overshoot.
- Each artifact has an isolated loop: URL confirmation pulse, scan checklist progress, and chat typing/send response.
- Loops only run while the section is near the viewport and the document is visible.

### Visual analysis

- Orange observation nodes orbit/zoom toward the browser and return, suggesting active inspection.
- A scan focus ring travels between content, visual language, structure, and customer-question zones.
- Notes use staggered spring entrances; their text remains stationary after arrival.

### Before / after case

- The before state is visibly desaturated and quiet.
- Switching to after restores colour, brightens the site, and introduces a larger widget with a distinct spring/halo reveal.
- The section keeps its existing accessible user-controlled before/after switch.

### Capabilities, Studio showcase, FAQ and final CTA

- Capability satellites enter with alternating direction and keep subtle icon-only float.
- The landing-page Studio showcase may gain presentation-only cursor/version/preview motion; the real `/studio` route and authentication are untouched.
- FAQ keeps user-controlled spring expansion and adds only entrance rhythm.
- Final CTA makes the before and after cards separate, then lifts the URL composer and reveals guarantees. The footer remains calm.

## Performance and accessibility contracts

- Prefer `transform`, `opacity`, SVG path progress and pseudo-element gradients.
- No perpetual `box-shadow` animation, layout-property animation, scroll-event render loops or page-wide re-render timer.
- Infinite decorative loops are guarded by one local motion-activity rule: `inViewport && documentVisible && !reducedMotion`.
- Activity boundaries use `IntersectionObserver`/Motion visibility and `visibilitychange`; they never install raw scroll listeners.
- Mobile uses the same narrative with smaller travel distance and no horizontal overflow.
- Reduced-motion renders all information and controls in their final state.
- Existing keyboard, contrast, focus, route and Studio tests remain green.

## Visual acceptance

At 1920×1080 the first scan is impossible to miss, the three cards clearly synchronize with scan progress, the browser transformation reads as one coherent story, and the larger widget is the payoff. At 390×844 the same order is preserved without clipped labels, horizontal scrolling, or controls below 44 px. Screenshots must cover source, mid-scan, complete, lower-section active states, and mobile complete.
