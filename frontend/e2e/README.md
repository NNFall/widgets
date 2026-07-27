# Kaigo browser evidence contract

`npm run test:e2e` verifies the public landing and Studio without calling Gemini.

- `desktop-1920`: 1920×1080 hero phases, full landing, navigation, FAQ, case switch and Studio create/resume/preview.
- `mobile-390`: 390×844 content order, menu, target sizes, overflow, case switch and Studio preview.
- `reduced-motion`: immediate final hero and absence of recurring browser animations.
- `accessibility`: WCAG A/AA scan with serious and critical axe findings treated as failures.

The Builder fixture intercepts `/builder/api/runs*`, injects deterministic run events and serves a local sandboxed preview. Golden images live beside their specs in `*-snapshots/`. Failure screenshots and first-retry CI traces are written to the ignored `test-results/` directory.

Update goldens only after an intentional visual change:

```powershell
npm run test:e2e -- --update-snapshots
```
