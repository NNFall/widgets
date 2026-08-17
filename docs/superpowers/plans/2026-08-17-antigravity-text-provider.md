# AntiGravity Text Provider Integration Plan

> Execute in the isolated `codex/antigravity-provider-integration` worktree.
> Use TDD for every production-code change and keep the feature disabled until
> the production canary gate.

**Goal:** Add the existing AntiGravity Text API as the primary provider for the
three parallel direction candidates, with the Codex bridge as a transparent
fallback, then prove the complete URL-to-published-widget path.

**Architecture:** A dedicated `ModelProvider` adapter translates Kaigo's
structured `ModelRequest` to `/v1/respond`. `ModelRouter` remains the sole owner
of routing, fallback, accounting, and time budgets. Runtime wiring changes only
the `direction_candidate` policy when the feature flag is enabled.

---

## Task 1: Provider contract by tests

**Files:**

- Create: `tests/saas_cases/test_antigravity_text_provider.py`
- Create: `app/models/providers/antigravity_text.py`

1. Add failing tests for constructor validation, exact request payload,
   structured output, usage/identity normalization, text-only rejection,
   cancellation, timeout/transport handling, status mappings, malformed bodies,
   and schema mismatch.
2. Run:
   `python -m pytest tests/saas_cases/test_antigravity_text_provider.py -q`
   and confirm RED for the missing provider.
3. Implement the minimum async `httpx` adapter with local JSON Schema validation
   and secret-safe error messages.
4. Re-run the focused test file to GREEN.

## Task 2: Fail-closed builder configuration

**Files:**

- Modify: `builder_lab/config.py`
- Modify: `tests/builder_lab_cases/test_config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`

1. Add RED tests for disabled defaults and enabled validation: HTTPS URL,
   required secret/model/effort, positive timeout, and model/effort agreement.
2. Add the six settings from the design to `BuilderLabConfig.from_env()` and
   `__post_init__()`.
3. Pass the settings through the builder-worker Compose environment without
   placing a real key in Git.
4. Run:
   `python -m pytest tests/builder_lab_cases/test_config.py -q`.

## Task 3: Narrow runtime routing

**Files:**

- Modify: `scripts/run_builder_worker.py`
- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `tests/builder_lab_cases/test_directions.py` only if an integration
  assertion cannot be expressed in the worker tests.

1. Add RED tests proving that enabling AntiGravity:
   - registers one `antigravity_text` provider;
   - sets only `direction_candidate` to AntiGravity first and Codex second;
   - leaves `direction_judge`, artifact roles, and image-bearing roles unchanged;
   - preserves unknown cost and existing fallback behavior.
2. Instantiate `AntigravityTextProvider` only when enabled.
3. Replace the direction-candidate policy after the generic policy construction;
   do not change `with_codex()` globally.
4. Run:
   `python -m pytest tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_directions.py -q`.

## Task 4: Least-privilege production egress

**Files:**

- Modify: `scripts/apply_builder_egress_guard.sh`
- Modify: `deploy/systemd/kaigo-builder-worker.service`
- Modify: `tests/builder_lab_cases/test_egress_guard.py`
- Modify: `tests/deployment_cases/test_saas_production_contract.py`
- Modify: `docs/SAAS_PRODUCTION_RUNBOOK.md`

1. Add RED tests for a new immutable firewall generation that permits only the
   fixed builder-worker build-network address to reach the configured NL public
   `/32` on TCP 443 before the host-input reject.
2. Add fail-closed validation for the fixed worker address, destination `/32`,
   and port supplied by a root-owned systemd environment file. Do not permit
   builder-lab, the database bridge, or a broad HTTPS destination.
3. Verify atomic removal of the old generation and exact post-state.
4. Document installation and rollback without putting the bearer key in the
   egress file.

## Task 5: Regression and independent review

1. Run:
   `python -m pytest tests/saas_cases/test_antigravity_text_provider.py tests/saas_cases/test_model_router.py tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_directions.py -q`.
2. Run Ruff on touched Python files, `python -m compileall` on provider/config/
   worker wiring, and `git diff --check`.
3. Send the final diff to an AntiGravity Worker in read-only Reviewer mode.
4. Manually validate every reviewer finding before changing code.
5. Update `docs/product-journal/2026-08.md`, the release packet, and content
   backlog with verified facts only.

## Task 6: Secure production configuration

1. On NL, record current worker image/release identity and readiness without
   printing secrets.
2. Transfer the user-provided service key from its operator-owned secret file
   into the dedicated root-only builder-worker environment file. Do not place
   plaintext in the shared Kaigo environment; verify that the existing gateway
   verifier accepts it without printing either value.
3. Validate permissions and configuration shapes without echoing values.
4. Build/pull one pinned worker image, mindful of the NL host's limited disk.
5. Recreate only the gateway if its hash list changed and the builder worker for
   the new image/config; verify both services and identities.

## Task 7: Real URL-to-widget production acceptance

1. Perform one authenticated structured AntiGravity turn using the dedicated
   Kaigo key; assert returned model, parsed schema, usage, and
   `conversation_deleted=true`.
2. Submit one public, content-rich HTTPS business site to the controlled Kaigo
   canary account with an empty brief and start an Express run.
3. Wait for terminal state and assert:
   - run completed;
   - artifact quality is verified;
   - three direction-candidate `ModelCall` rows used `antigravity_text` (or show
     an explicitly audited fallback reason);
   - later visual/image stages did not use AntiGravity.
4. Publish the verified version to the existing external HTTPS canary origin.
5. Run `scripts/run_publication_https_canary.py` and verify launcher open/close,
   desktop/mobile rendering, chat response, release marker, stable key across
   update/rollback, and denied-origin failure.
6. Capture non-secret evidence and exact timings. If any release gate fails,
   disable the feature flag and restart only the builder worker.

## Task 8: Closeout

1. Compare AntiGravity candidate latency, structured-response success, fallback
   rate, and useful output against the existing path.
2. Document which local subagent tasks it handled well or poorly and recommend
   the next eligible role (`code_review` is the leading follow-up, not part of
   this rollout).
3. Commit and push the verified branch, then report the production result and
   public canary link without exposing credentials or personal data.
