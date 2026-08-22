# Operator Read-Only Analytics Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Kaigo automation read aggregate funnel and sanitized generation-run JSON through a rotatable bearer secret, without weakening OAuth or exposing mutation/raw-data routes.

**Architecture:** Add one optional secret to `AppConfig`, a separate read-only operator principal helper, and use that helper only in the three JSON operator routes. Keep HTML and all non-read routes on verified OAuth. Add a standard-library CLI that reads the secret from environment or a secret file and combines funnel/runs data for analysis.

**Tech Stack:** Python 3.13, aiohttp, aiohttp-session, pytest/pytest-asyncio, SQLAlchemy, `urllib.request`, PowerShell/Linux runbook documentation.

---

### Task 1: Add failing service-token authorization tests

**Files:**
- Modify: `tests/saas_cases/test_operator_funnel_analytics.py`
- Modify: `tests/saas_cases/test_operator_generation_forensics.py`
- Create: `tests/saas_cases/test_operator_read_token.py`

- [ ] **Step 1: Extend the funnel route fixture with a 32-byte configured token and bearer assertions.**

Set `operator_read_token="t" * 32` in the test app config. Before OAuth login, assert that `GET /api/operator/funnel` with `Authorization: Bearer ` plus the configured value returns `200`, while no header remains `401`. Assert the response remains `Cache-Control: no-store` and contains only the existing aggregate fields.

- [ ] **Step 2: Extend generation-route tests with bearer list/detail assertions.**

Use the same configured token in the generation-forensics test app. Assert the bearer token returns `200` for `/api/operator/generation-runs` and the known run detail, and that the sanitized payload still excludes the existing private markers.

- [ ] **Step 3: Add a mutation-boundary regression test.**

Create a test-only `GET /test/mutation-boundary` handler that calls the unchanged `require_verified_operator`. With only the service bearer header, assert the handler returns `401` or `403`; with the verified OAuth test session, assert it returns `200`. This proves the service token cannot authorize a route that still uses the OAuth-only helper.

- [ ] **Step 4: Add malformed/incorrect/disabled-token cases.**

Exercise missing `Bearer` text, an incorrect token, an empty configured token, and a configured token shorter than 32 bytes. Assert each request fails closed and the response body is the same safe authentication error without echoing the supplied value.

- [ ] **Step 5: Run the new tests and verify RED.**

Run:

```text
python -m pytest tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_operator_funnel_analytics.py tests/saas_cases/test_operator_generation_forensics.py -q
```

Expected before implementation: the new bearer assertions fail because the routes only recognize OAuth sessions.

### Task 2: Add secret configuration and read-only principal helper

**Files:**
- Modify: `app/config.py:30-90, 150-190, 540-550`
- Modify: `app/admin/operator_auth.py`
- Modify: `tests/saas_cases/test_config.py`
- Modify: `tests/saas_cases/test_operator_read_token.py`

- [ ] **Step 1: Add the redacted config field and environment loader.**

Add `operator_read_token: str | None = field(default=None, repr=False)` to `AppConfig`, load it from `_first_nonblank("KAIGO_OPERATOR_READ_TOKEN")`, strip it once, and reject a configured value shorter than 32 UTF-8 bytes. An unset value remains `None` and does not affect OAuth.

- [ ] **Step 2: Add a typed read-only principal.**

In `app/admin/operator_auth.py`, add an immutable principal with `user_id: int | None` and `auth_method: Literal["oauth", "service_token"]`. Add `require_read_operator(request)` that first checks the exact `Authorization: Bearer <configured-token>` form using `secrets.compare_digest`; if it matches, return a `service_token` principal without opening a database session. Otherwise delegate to `require_verified_operator` and return an `oauth` principal. Keep `require_verified_operator` unchanged so mutation/admin routes cannot inherit service-token access.

- [ ] **Step 3: Make service-auth failures indistinguishable and secret-safe.**

Return the existing JSON `authentication_required` error for missing/malformed/incorrect service credentials when no valid OAuth session is present. Never include the header, token length, or token fingerprint in the response or log record. Add only an `auth_method` field (`oauth` or `service_token`) to successful operator log entries.

- [ ] **Step 4: Run config/helper tests and verify GREEN.**

Run:

```text
python -m pytest tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_config.py -q
```

Expected: all new token/config assertions pass and existing secret-redaction assertions remain green.

### Task 3: Attach the helper only to JSON operator reads

**Files:**
- Modify: `app/admin/funnel_analytics.py:65-75`
- Modify: `app/admin/generation_forensics.py:778-806`
- Modify: `tests/saas_cases/test_operator_funnel_analytics.py`
- Modify: `tests/saas_cases/test_operator_generation_forensics.py`

- [ ] **Step 1: Switch only JSON report/list/detail handlers to `require_read_operator`.**

Use the principal in `operator_funnel_json`, `operator_runs_json`, and `operator_run_json`. Preserve the current aggregate serializers, no-store headers, date/source validation, and forensic redaction. Keep `operator_funnel_page`, `operator_runs_page`, and `operator_run_page` on `require_verified_operator`.

- [ ] **Step 2: Update successful logs without persisting a fake user id.**

For OAuth principals, retain the existing `operator_user_id`. For service-token principals, log `operator_auth_method="service_token"` and omit `operator_user_id`; never log the bearer value.

- [ ] **Step 3: Run the route suite.**

Run:

```text
python -m pytest tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_operator_funnel_analytics.py tests/saas_cases/test_operator_generation_forensics.py -q
```

Expected: bearer JSON reads pass, bearer HTML pages still fail closed, OAuth behavior is unchanged, and private payload markers remain absent.

### Task 4: Build the analysis CLI

**Files:**
- Create: `scripts/operator_metrics.py`
- Create: `tests/saas_cases/test_operator_metrics_cli.py`

- [ ] **Step 1: Write CLI tests for request construction and safe errors.**

Patch the CLI's standard-library opener with a fake response. Assert `funnel` sends `GET /api/operator/funnel` with URL-encoded `from`, `to`, and `source` query parameters plus `Authorization: Bearer <token>`. Assert `runs` sends the limit. Assert `snapshot` calls both JSON routes and emits one object with `funnel` and `runs`. Assert missing token, HTTP 401, malformed JSON, and connection errors exit non-zero without printing the token or raw response body.

- [ ] **Step 2: Run CLI tests and verify RED.**

Run:

```text
python -m pytest tests/saas_cases/test_operator_metrics_cli.py -q
```

Expected before implementation: import/entry-point failures because the script does not exist.

- [ ] **Step 3: Implement the minimal argparse CLI.**

Implement `funnel`, `runs`, and `snapshot` subcommands using `urllib.request.Request` and `urlopen`. Read `KAIGO_OPERATOR_READ_TOKEN` first, otherwise read a single trimmed line from `KAIGO_OPERATOR_READ_TOKEN_FILE`; reject an empty value. Default the base URL to `https://kaigo.space`, normalize a trailing slash, and print only successful sanitized JSON. Convert HTTP and transport errors to short stderr messages and exit code `1`.

- [ ] **Step 4: Run CLI tests and a help smoke check.**

Run:

```text
python -m pytest tests/saas_cases/test_operator_metrics_cli.py -q
python scripts/operator_metrics.py --help
```

Expected: all CLI tests pass and help lists `funnel`, `runs`, and `snapshot` without requiring a token.

### Task 5: Document secret installation and rotation

**Files:**
- Modify: `.env.example`
- Modify: `docs/SAAS_PRODUCTION_RUNBOOK.md`
- Modify: `docs/YANDEX_DIRECT_LAUNCH.md`

- [ ] **Step 1: Add an empty example setting.**

Add `KAIGO_OPERATOR_READ_TOKEN=` next to the existing operational secrets, with a comment that it is optional, root-only, and never committed.

- [ ] **Step 2: Add runbook procedures.**

Document generating a random value directly into the root-only secret store, setting `KAIGO_OPERATOR_READ_TOKEN` for the app service, checking the CLI with `--source yandex`, rotating by replacing the secret and restarting the app, and revoking by removing the variable. Explicitly prohibit query-string tokens, shell tracing, Git, logs, and copying the value into chat.

- [ ] **Step 3: Link the CLI and report filters from the Yandex guide.**

Add the exact `snapshot` command and explain that `landing_entered` is an anonymous browser journey, while Direct's click count is a separate metric.

- [ ] **Step 4: Review documentation for secret leakage.**

Run a repository search over the changed files for literal token values, `Bearer` values, and accidental command-line assignments; only placeholder variable names and redacted examples may remain.

### Task 6: Full verification and handoff

**Files:**
- No additional source files; inspect the complete diff and working tree.

- [ ] **Step 1: Run focused backend and CLI tests.**

```text
python -m pytest tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_operator_funnel_analytics.py tests/saas_cases/test_operator_generation_forensics.py tests/saas_cases/test_operator_metrics_cli.py tests/saas_cases/test_config.py -q
```

- [ ] **Step 2: Run the existing funnel/forensics regression suites.**

```text
python -m pytest tests/saas_cases/test_funnel_journey_routes.py tests/saas_cases/test_operator_funnel_analytics.py tests/saas_cases/test_operator_generation_forensics.py -q
```

- [ ] **Step 3: Run formatting/static checks for changed Python.**

```text
python -m compileall -q app scripts tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_operator_metrics_cli.py
python -m ruff check app/admin/operator_auth.py app/admin/funnel_analytics.py app/admin/generation_forensics.py app/config.py scripts/operator_metrics.py tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_operator_metrics_cli.py
```

- [ ] **Step 4: Inspect the diff and confirm no production deployment occurred.**

Run `git diff --check`, `git diff --stat`, and `git status --short`. Report the implementation commit, tests, exact CLI command, and the remaining deployment requirement: provision the secret in the production environment before the service-token path can be used against `kaigo.space`.
