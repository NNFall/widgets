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

## Real publication acceptance

The Playwright suite above uses deterministic fixtures and does not prove a
real cross-origin publication. The separate
`scripts/run_publication_https_canary.py` gate runs only against the real
`https://kaigo.space`, `https://canary.kaigo.space`, and
`https://denied-canary.kaigo.space` origins with a dedicated test account and
an owner-only Cookie file.

Its contract tests are network-free:

```powershell
python -m pytest tests/saas_cases/test_publication_https_canary.py tests/deployment_cases/test_publication_canary_contract.py -q
```

Passing those tests proves the package and runner contract, not the external
journey. A skipped, blocked, loopback, HTTP, self-signed, or mocked invocation
must never be reported as publication acceptance. See
`docs/SAAS_PRODUCTION_RUNBOOK.md` for the schema-first deployment and evidence
procedure.
