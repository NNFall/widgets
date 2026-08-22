# Operator Read-Only Analytics Access

## Goal

Allow Kaigo's automated analysis tooling to read the existing aggregate funnel
report and sanitized generation-run summaries without a browser OAuth session,
while keeping all mutation, billing, user, and raw forensic data behind the
existing verified operator OAuth gate.

## Scope

The service credential is accepted only by the JSON read routes that already
serve aggregate/operator data:

- `GET /api/operator/funnel`;
- `GET /api/operator/generation-runs`;
- `GET /api/operator/generation-runs/{run_id}`.

The HTML pages continue to require verified OAuth. Existing OAuth access keeps
working unchanged. No database user, password login, public auth bypass, or
new endpoint exposing raw IPs, emails, URLs, prompts, provider payloads, or
snapshots is added.

## Authentication contract

Production receives an optional `KAIGO_OPERATOR_READ_TOKEN` from its root-only
secret/environment store. The value is never committed, logged, returned, or
accepted in a URL/query parameter. A request uses:

```text
Authorization: Bearer <token>
```

The configured token must be at least 32 characters. Missing configuration
disables service-token access without changing OAuth behavior. Token comparison
uses constant-time comparison. Missing, malformed, and incorrect credentials
return the same `401 authentication_required` JSON response and do not open a
database session. Service-token requests are logged only with a stable
`service_token` auth-method label; the credential itself is never included.

The existing `require_verified_operator` function remains the authorization
boundary for mutation/admin routes. Read-only routes use a small separate
principal helper so a service token cannot accidentally authorize a future
write route.

## CLI contract

`scripts/operator_metrics.py` is a read-only client using the standard library
HTTP stack. It accepts `funnel`, `runs`, and `snapshot` subcommands. The base
URL defaults to `https://kaigo.space`; `--from`, `--to`, `--source`, and
`--limit` are optional filters. The token is loaded from
`KAIGO_OPERATOR_READ_TOKEN` or an explicitly supplied secret-file environment
variable. The CLI never prints the token, request headers, cookies, or raw
response bodies on errors. Successful output is sanitized JSON; `snapshot`
combines the funnel report and recent run list for one analysis call.

Examples:

```text
python scripts/operator_metrics.py funnel --from 2026-08-22 --to 2026-08-23 --source yandex
python scripts/operator_metrics.py runs --limit 20
python scripts/operator_metrics.py snapshot --from 2026-08-22 --to 2026-08-23 --source yandex
```

## Failure and privacy behavior

- No token: CLI exits non-zero with an actionable configuration message;
  server returns `401`.
- Expired/rotated token: same `401`; no retry loop and no token echo.
- Non-2xx API response: CLI prints status and a short safe error code only.
- Existing report retention and aggregation rules remain unchanged.
- No raw IP, email, full URL, prompt, provider payload, or browser cookie is
  persisted by this feature.

## Verification

Tests cover config loading/redaction and minimum length, service-token success
and rejection on each JSON route, OAuth fallback, mutation-route rejection,
secret-safe logging, and CLI request/response handling. Existing funnel and
generation-forensics tests must remain green. The runbook documents generating,
installing, rotating, and revoking the secret without placing it in Git or the
shell command line.
