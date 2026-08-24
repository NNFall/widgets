# Developer project access for Kaigo Studio

## Goal

Allow the verified developer account to inspect and debug any SaaS Studio
project, including projects owned by another tenant, without weakening the
normal owner boundary or introducing a permanent secret in a URL.

The capability is for internal development and support. It is not a public
share link, an impersonation mechanism for ordinary users, or a replacement
for OAuth.

## Existing boundary

The SaaS project API currently scopes every project, run, version, artifact,
preview, refinement, restore, retry, and publication request to the OAuth
session's `user_id` and `tenant_id`. The existing operator configuration is an
email allowlist used by forensic and aggregate read-only routes. A configured
bearer token must remain read-only and must never become a general project
credential.

## Proposed design

### 1. Developer authorization

- Reuse the verified OAuth identity allowlist already represented by
  `KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS`.
- Add an explicit developer scope check for browser sessions only. It must
  require a verified Google or Yandex identity whose normalized email is in
  that allowlist.
- Do not hard-code a personal email in Python, TypeScript, tests, or docs.
  Production configuration remains the source of truth.
- Keep the service bearer token restricted to the existing read-only operator
  JSON endpoints.

### 2. Project access behavior

For a developer OAuth session, project API handlers may read or mutate the
project lifecycle across tenants:

- list and open all projects;
- inspect runs, events, versions, artifacts, and preview documents;
- retry or cancel a run;
- refine or restore a version;
- open and update publication configuration and release state.

The developer scope does not authorize payment creation, payment capture,
subscription changes, auto-renewal changes, or account deletion. Those remain
owner-scoped and require the ordinary billing flow. This prevents a debugging
session from causing a financial side effect.

The implementation must use a single access helper rather than scattering
email checks through individual handlers. The helper returns the authenticated
user identity plus an explicit `is_developer` flag. Owner requests continue to
use the existing strict predicate; developer requests use the same project ID
predicate with the scope flag, never a service-token fallback.

### 3. Session and UI contract

Extend `/api/auth/session` with a safe, non-secret capability snapshot:

```json
{
  "developer": {
    "enabled": true,
    "scope": "all_projects"
  }
}
```

When the session is a developer session, Studio will:

- show a clear “Режим разработчика” badge;
- keep the signed-in developer email visible;
- label a project owner when that information is available;
- allow the normal preview, version history, refinement, and publication UI
  to operate against the selected project.

The API must not expose provider prompts, bearer tokens, internal diagnostics,
or unrelated user secrets as part of this badge or owner label.

### 4. Admin entry and temporary links

`/admin` remains a protected developer entry point and may link to the global
project library. A permanent `/admin?key=...` bypass will not be implemented:
query-string secrets leak through browser history, reverse-proxy logs, and
referrer propagation.

If automation later needs a link without a pre-existing browser session, add a
short-lived, single-use handoff token issued only after a verified developer
session. Store only a digest, expire it quickly, consume it once, and exchange
it for the normal HttpOnly session cookie. That handoff is deliberately outside
the first implementation unless a concrete automation caller requires it.

## Data flow

1. OAuth callback establishes the existing authenticated session.
2. A shared access helper checks the session user and verified allowlisted
   identity, then returns the developer scope when applicable.
3. Project and preview handlers use that scope for cross-tenant project access.
4. Mutation handlers still require the existing CSRF and idempotency checks.
5. Billing mutation handlers reject developer-only cross-tenant access rather
   than silently charging or changing the owner's account.
6. The frontend reads the capability snapshot and renders the developer badge;
   no secret is placed in the URL or local storage.

## Error handling

- Unauthenticated requests remain `401 authentication_required`.
- Non-allowlisted authenticated users opening another project continue to
  receive the existing not-found response, avoiding project enumeration.
- A developer request to a billing mutation receives an explicit forbidden
  response with a safe public code and no payment-provider details.
- Missing or disabled operator configuration leaves the existing owner-only
  behavior unchanged.

## Testing strategy

Write tests before implementation for:

1. allowlisted verified Google/Yandex browser sessions receive developer scope;
2. unverified, VK-only, and non-allowlisted sessions do not;
3. a developer can list and open a foreign project;
4. an ordinary user still cannot open the same foreign project;
5. developer preview/refine/restore/retry keep CSRF and idempotency requirements;
6. developer billing mutations are rejected and cannot create a payment or
   change a subscription;
7. `/api/auth/session` exposes only the boolean/scope snapshot;
8. Studio renders the developer badge and owner label without secrets;
9. no route accepts a permanent query-string admin key.

Verification will include the focused SaaS/backend tests, frontend unit tests,
TypeScript/lint/build checks, and a browser check of the developer account
opening the The International 2026 project link.

## Acceptance criteria

- The configured developer OAuth account can open the previously inaccessible
  project link and see its verified revision and preview.
- The same account can inspect and debug any project from the global library.
- Ordinary accounts still see only their own projects.
- No permanent URL key, service-token mutation path, or payment side effect is
  introduced.
- Production deployment remains a separate release action governed by the SaaS
  production runbook.
