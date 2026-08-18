# Publication modal and source-domain publication

## Goal

Make publication understandable and usable at desktop and mobile sizes, and make the first publication succeed when the project has a valid HTTPS source URL but the owner has not entered an extra domain.

## Design

### Publication surface

The publication flow uses the existing accessible `StudioDrawer` behavior with a `modal` presentation variant. Projects, versions, and account remain right-side drawers. Publication opens in a centered near-fullscreen panel with a bounded readable width, a scrollable body, Escape/backdrop close behavior, and the existing dialog semantics. The offer remains server-priced; founder and paid plans are shown in a three-column desktop grid and stack on narrow screens.

### Access explanation

The launch-step label `Доступ` explicitly describes the decision: the owner chooses the founder pilot when eligible or a paid publication plan. The offer header and founder card state that founder access is a no-card, no-auto-charge pilot; paid plan prices and renewal details continue to come from the billing offer response.

### Domain policy

`UpgradeGate` receives the project source URL and displays its normalized HTTPS origin as the initial allowed-domain value. The owner may add more exact origins, one per line. Clearing the field means “use the project source origin” and does not create an empty deny-all policy. The versioned publication endpoint treats `allowed_domains` as optional and derives the source origin when the key is omitted. Explicit arrays remain server-validated exact-origin policies.

### Error handling

Publication errors preserve the server’s actionable message where safe. Conflict handling continues to reload authoritative publication state; validation errors identify that an HTTPS origin is required instead of hiding every failure behind a generic retry message.

## Verification

- Frontend component tests cover modal presentation, access copy, server-priced founder/paid cards, source-origin initialization, and actionable publication errors.
- Backend tests cover versioned publication with the `allowed_domains` key omitted and source-origin derivation.
- Playwright covers publication at 1920×1080 and a mobile viewport, including no horizontal overflow and readable plan cards.
- Typecheck, lint, focused Vitest/Pytest, publication E2E, and production diff checks run before completion. No production deployment is included in this change.
