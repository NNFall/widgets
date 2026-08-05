# Friendly Studio release evidence

Date: 2026-08-05

Branch: `codex/product-ui`

Committed feature baseline: `af2d234` (`feat: clarify studio publication and responsive layout`) plus the final review fixes captured with this evidence.

## Result

The local frontend now presents Studio as a business-owner workflow: widget library, plain-language progress, preview, refinement, version history, subscription state, and publication. Raw provider messages, run IDs, token balances, and developer-only publication identifiers are not part of the default customer view.

## Automated verification

Run sequentially from `frontend/` after the final UI fixes:

| Command | Result |
| --- | --- |
| `npm test` | 24 files, 217 tests passed |
| `npm run lint` | passed with zero warnings |
| `npm run typecheck` | passed |
| `npm run build` | production Vite build passed |
| `npm run test:e2e` | 14 of 14 passed |

The e2e run covers the landing page, owned-project creation and resume, safe cancel/retry, active-subscription publication, 390 px mobile layout, reduced motion, and serious/critical axe checks. Approved desktop and mobile Studio snapshots were inspected before regeneration.

The final review pass also proves that changing the project in browser history clears the previous run before hydration, cancel/retry actions cannot target a run from another project, raw SSE diagnostics cannot enter the visible header, and the live activity region exists only while a run is queued or running.

## In-app Codex browser verification

The local app was checked against the isolated API fixture in [`manual-studio-api.mjs`](./manual-studio-api.mjs). No production endpoint was mutated.

- At 1536 × 960, the library, ready project, seven-stage progress, desktop/mobile preview controls, version history, and publication card were readable without nested rail scrolling.
- At 390 × 844, `window.innerWidth` was 390 and document `scrollWidth` was 375, so no horizontal overflow was present.
- The final mobile evidence was recaptured after reordering the page as title and primary status, progress, preview, current action, refinement and versions, publication, then optional technical details. Read-only source fields are omitted from this compact view.
- The long refinement request expanded to its complete text and collapsed again; the current version had no restore action.
- Technical event details and the developer embed details were closed by default.
- After local publication, the customer-facing state changed to `Виджет опубликован`; the embed URL and release/artifact identifiers appeared only after opening `Код для разработчика`.
- Under `prefers-reduced-motion: reduce`, the media query matched, the activity component reported `data-motion="reduced"`, CSS activity animation was disabled, and the Motion rail/workspace transitions use zero duration instead of springs.

Automated visual coverage additionally ran at 1536 × 830 and 1920 × 1080, plus the 390 × 844 mobile and reduced-motion projects.

## Screenshots

- [`studio-library-desktop.png`](./studio-library-desktop.png) — widget library and new-widget form.
- [`studio-project-desktop.png`](./studio-project-desktop.png) — ready project at desktop width.
- [`studio-project-mobile-top.png`](./studio-project-mobile-top.png) — mobile header, project title, and primary progress.
- [`studio-project-mobile.png`](./studio-project-mobile.png) — mobile preview and refinement flow.
- [`studio-publication-desktop.png`](./studio-publication-desktop.png) — published state with the developer disclosure opened intentionally for inspection.

## Safety and limitations

- The browser data, subscription, and publication were local fixtures only.
- [`capture-evidence.mjs`](./capture-evidence.mjs) starts and stops the isolated local fixture and Vite server used to reproduce these screenshots.
- No payment window, paid action, AI generation, email, or external publication was triggered.
- Production checkout, nginx, services, migrations, and backend files were not changed.
- Production deployment remains a separate backend-chat action after review and explicit approval.
