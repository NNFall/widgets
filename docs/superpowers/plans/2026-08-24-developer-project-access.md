# Developer Project Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Let the verified developer OAuth session inspect and debug every Studio project while preserving ordinary owner isolation, CSRF/idempotency checks, and billing safety.

**Architecture:** Add a shared browser-only developer-scope helper backed by the existing verified operator email allowlist. Project, run, version, preview, and publication handlers accept that explicit scope; billing mutations remain owner-scoped. Return a small capability snapshot from the auth-session endpoint and pass it through Studio so the UI visibly identifies developer mode and foreign project ownership.

**Tech Stack:** Python 3, aiohttp, SQLAlchemy async sessions, pytest/pytest-asyncio, React/TypeScript, Vitest, ESLint, Vite.

---

### Task 1: Add the explicit developer OAuth scope helper

**Files:**
- Modify: \`app/admin/operator_auth.py\`
- Modify: \`app/auth/routes.py\`
- Modify: \`app/projects/routes.py\`
- Test: \`tests/saas_cases/test_operator_read_token.py\`
- Test: \`tests/saas_cases/test_auth_routes.py\`

- [ ] **Step 1: Write failing helper tests**

Add tests for a verified Google/Yandex identity whose normalized email is in
the configured allowlist, plus negative cases for an unverified identity, VK,
and an authenticated non-allowlisted user. The successful assertion must be an
explicit developer-scope object/flag; the bearer-token principal must remain
\`service_token\` and must not satisfy the browser helper.

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run:

\`\`\`powershell
python -m pytest tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_auth_routes.py -q
\`\`\`

Expected: the new helper imports or assertions fail because the developer
scope is not implemented yet; existing tests should still collect normally.

- [ ] **Step 3: Implement the minimal helper**

In \`app/admin/operator_auth.py\`, add a \`DeveloperPrincipal\` dataclass and an
\`optional_developer_principal(request)\` coroutine. It must:

\`\`\`python
config = _forensics_config(request)
session = await get_session(request)
user_id = session.get("user_id")
if not isinstance(user_id, int):
    return None
identity = await database.scalar(
    select(UserIdentity).where(
        UserIdentity.user_id == user_id,
        UserIdentity.provider.in_(("google", "yandex")),
        UserIdentity.email_verified.is_(True),
        func.lower(UserIdentity.email).in_(config.admin_emails),
    ).limit(1)
)
return DeveloperPrincipal(user_id=user_id, email=identity.email) if identity else None
\`\`\`

Keep the existing raising \`require_verified_operator\` behavior and
\`require_read_operator\` service-token behavior unchanged. Add a small
\`developer_session_snapshot(request)\` helper returning only
\`{"enabled": bool, "scope": "all_projects" | None}\`.

- [ ] **Step 4: Expose the non-secret capability snapshot**

In \`app/auth/routes.py\`, call the helper in \`auth_session()\` and include the
\`developer\` object. Do not return allowlist contents, bearer tokens, provider
subjects, or internal IDs beyond the existing authenticated user ID.

- [ ] **Step 5: Run the focused tests and commit**

Run:

\`\`\`powershell
python -m pytest tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_auth_routes.py -q
\`\`\`

Expected: all focused tests pass. Commit:

\`\`\`powershell
git add app/admin/operator_auth.py app/auth/routes.py tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_auth_routes.py
git commit -m "feat: expose verified developer scope"
\`\`\`

### Task 2: Apply developer scope to SaaS project and run access

**Files:**
- Modify: \`app/projects/routes.py\`
- Modify: \`app/projects/serializers.py\`
- Modify: \`frontend/src/studio/types.ts\`
- Test: \`tests/saas_cases/test_project_routes.py\`

- [ ] **Step 1: Add failing owner-isolation and developer-access tests**

Extend the project fixture with a verified allowlisted operator session. Test
that the operator can list a project owned by the other test user and fetch its
details, while the ordinary foreign user still receives the current \`404\`.
Also test a developer can access a foreign run, versions, artifact, preview,
and events, but unauthenticated requests remain \`401\`.

- [ ] **Step 2: Run the new tests to verify RED**

Run:

\`\`\`powershell
python -m pytest tests/saas_cases/test_project_routes.py -q
\`\`\`

Expected: the developer cases fail with the current owner-only \`404\`.

- [ ] **Step 3: Add a single project-scope access value**

Add a \`ProjectScope\` value in \`app/projects/routes.py\` or a focused
\`app/projects/access.py\` module:

\`\`\`python
@dataclass(frozen=True, slots=True)
class ProjectScope:
    user_id: int
    tenant_id: int
    developer: bool = False
\`\`\`

Keep \`_scope()\` returning the existing two-tuple for create/billing callers.
Add \`_project_scope()\` that calls \`_scope()\` and the optional developer helper.
Update \`_owned_project()\` and \`_owned_run()\` to accept \`developer=False\`; when
true, constrain by the requested project/run ID and tenant-independent project
membership, never by a bearer token. Use \`_project_scope()\` only in project,
run, version, artifact, preview, chat, and event handlers. Project creation
must remain owned by the authenticated user's own tenant.

- [ ] **Step 4: Make global listing and owner labels explicit**

For developer listing, select all projects ordered by updated/created time and
join the owner user email. For normal listing, retain the existing owner and
tenant predicate. Extend \`serialize_project(project, owner_email=None)\` with a
nullable \`owner_email\` field and add the matching TypeScript property.

- [ ] **Step 5: Preserve mutation gates**

Pass the developer flag through all project mutation handlers while leaving
\`_require_csrf()\`, idempotency keys, project locks, subscription checks, and
version conflict checks intact. Do not add a route that accepts a key from a
query string or Authorization bearer token for mutations.

- [ ] **Step 6: Run the project suite and commit**

Run:

\`\`\`powershell
python -m pytest tests/saas_cases/test_project_routes.py -q
\`\`\`

Expected: the original owner tests and new developer tests pass. Commit:

\`\`\`powershell
git add app/projects/routes.py app/projects/serializers.py frontend/src/studio/types.ts tests/saas_cases/test_project_routes.py
git commit -m "feat: allow developer access to all studio projects"
\`\`\`

### Task 3: Permit developer publication debugging without billing side effects

**Files:**
- Modify: \`app/publication/service.py\`
- Modify: \`app/publication/routes.py\`
- Modify: \`app/billing/routes.py\`
- Test: \`tests/saas_cases/test_publication_routes.py\`
- Test: \`tests/saas_cases/test_billing_routes.py\`

- [ ] **Step 1: Add failing publication access tests**

Test that a developer can read a foreign publication state and publish/rollback
an already-entitled owner project when the request includes normal CSRF. Test
that an ordinary foreign account remains \`404\`, and that a developer cannot
create checkout, capture payment, alter auto-renewal, or claim a founder offer
for another owner.

- [ ] **Step 2: Run focused publication/billing tests and verify RED**

Run:

\`\`\`powershell
python -m pytest tests/saas_cases/test_publication_routes.py tests/saas_cases/test_billing_routes.py -q
\`\`\`

Expected: foreign publication access fails under the current owner predicate;
existing billing tests remain the baseline.

- [ ] **Step 3: Thread an explicit developer flag through publication service**

Add \`developer: bool = False\` to \`publish\`, \`publish_version\`,
\`get_project_state\`, and \`rollback\`. For developer access, resolve the project
by ID and use its owner user for entitlement checks; retain the authenticated
developer ID as the audit actor. Keep all artifact validation, allowed-domain
normalization, release locking, and expected-pointer conflict checks unchanged.

- [ ] **Step 4: Keep billing mutations owner-scoped**

Use \`_scope()\` (not \`_project_scope()\`) for checkout, founder claim, contact,
auto-renewal, and subscription mutation routes. If a developer session submits
one of these routes for a foreign project, return the existing safe not-found
or a dedicated \`developer_billing_forbidden\` response without touching payment
tables or provider calls.

- [ ] **Step 5: Run the focused tests and commit**

Run:

\`\`\`powershell
python -m pytest tests/saas_cases/test_publication_routes.py tests/saas_cases/test_billing_routes.py -q
\`\`\`

Expected: publication debugging tests pass and no billing mutation is created.
Commit:

\`\`\`powershell
git add app/publication/service.py app/publication/routes.py app/billing/routes.py tests/saas_cases/test_publication_routes.py tests/saas_cases/test_billing_routes.py
git commit -m "feat: support developer publication debugging"
\`\`\`

### Task 4: Add visible developer mode and owner context in Studio

**Files:**
- Modify: \`frontend/src/studio/types.ts\`
- Modify: \`frontend/src/studio/api.ts\`
- Modify: \`frontend/src/studio/StudioPage.tsx\`
- Modify: \`frontend/src/studio/StudioProjectWorkbench.tsx\`
- Modify: \`frontend/src/studio/StudioLibrary.tsx\`
- Modify: \`frontend/src/styles.css\`
- Test: \`frontend/src/studio/StudioLibrary.test.tsx\`
- Test: \`frontend/src/studio/StudioPage.test.tsx\`
- Test: \`frontend/src/studio/StudioProjectWorkbench.test.tsx\`

- [ ] **Step 1: Write failing UI tests**

Add a library fixture with \`owner_email\` and assert that developer mode renders
“Режим разработчика” plus the owner email, while normal mode keeps the regular
“Мои виджеты” copy. Add a workbench assertion that the developer badge is
visible without exposing any provider or token fields.

- [ ] **Step 2: Run the frontend tests and verify RED**

Run:

\`\`\`powershell
Set-Location frontend
npm.cmd test -- --run src/studio/StudioLibrary.test.tsx src/studio/StudioPage.test.tsx src/studio/StudioProjectWorkbench.test.tsx
\`\`\`

Expected: the new badge/owner assertions fail because the props and session
capability are not present.

- [ ] **Step 3: Add capability types and pass them through the UI**

Extend \`AuthSessionSnapshot\` with \`developer: { enabled: boolean; scope: string | null }\`,
extend \`SaasProject\` with \`owner_email: string | null\`, and load the capability
once in \`StudioPage\`. Pass \`developerMode\` to \`StudioLibrary\` and
\`StudioProjectWorkbench\`; render an accessible badge in the Studio header and
the owner email in each global-library card.

- [ ] **Step 4: Add narrow styles**

Add a small badge style in \`frontend/src/styles.css\` that works at desktop and
mobile widths, uses existing Studio tokens, and does not change the workbench
grid or preview geometry.

- [ ] **Step 5: Run focused frontend checks and commit**

Run:

\`\`\`powershell
Set-Location frontend
npm.cmd test -- --run src/studio/StudioLibrary.test.tsx src/studio/StudioPage.test.tsx src/studio/StudioProjectWorkbench.test.tsx
npx.cmd tsc --noEmit
npm.cmd run lint
\`\`\`

Expected: all focused tests, TypeScript, and lint pass. Commit:

\`\`\`powershell
git add frontend/src/studio frontend/src/styles.css
git commit -m "feat: label developer project access in studio"
\`\`\`

### Task 5: Add the protected developer entry route and release documentation

**Files:**
- Create: \`app/admin/developer.py\`
- Modify: \`app/admin/routes.py\`
- Test: \`tests/saas_cases/test_developer_entry.py\`
- Modify: \`docs/product-journal/2026-08.md\`
- Create: \`docs/telegram/release-packets/2026-08-24-developer-project-access.md\`
- Modify: \`docs/telegram/content-backlog.md\`

- [ ] **Step 1: Write the failing route tests**

Assert \`/admin/developer\` returns \`401\` without OAuth, \`403\` for a normal
authenticated user, and redirects to \`/studio?scope=all\` for a verified
allowlisted developer. Assert a query parameter named \`key\` is ignored and
never grants access.

- [ ] **Step 2: Run the route tests and verify RED**

Run:

\`\`\`powershell
python -m pytest tests/saas_cases/test_developer_entry.py -q
\`\`\`

Expected: route-not-found or authorization assertions fail before the route is
registered.

- [ ] **Step 3: Implement the protected entry route**

Create \`developer_entry()\` that calls the verified browser-only developer
helper, sets \`Cache-Control: no-store\` and \`Referrer-Policy: no-referrer\`, and
returns \`HTTPFound('/studio?scope=all')\`. Register it from
\`setup_admin_routes\`. Do not create a permanent token, password, or URL-key
lookup.

- [ ] **Step 4: Add the required editor records**

Record the verified behavior and the production-deployment boundary in the
August product journal, add a release packet without secrets or personal
identifiers, and add one backlog topic for the developer-access story.

- [ ] **Step 5: Run route tests and commit**

Run:

\`\`\`powershell
python -m pytest tests/saas_cases/test_developer_entry.py -q
\`\`\`

Expected: all route tests pass. Commit:

\`\`\`powershell
git add app/admin/developer.py app/admin/routes.py tests/saas_cases/test_developer_entry.py docs/product-journal/2026-08.md docs/telegram/release-packets/2026-08-24-developer-project-access.md docs/telegram/content-backlog.md
git commit -m "feat: add protected developer studio entry"
\`\`\`

### Task 6: Full verification and browser evidence

**Files:**
- Modify only if a verification failure identifies a real regression.

- [ ] **Step 1: Run focused backend regression suites**

\`\`\`powershell
python -m pytest tests/saas_cases/test_operator_read_token.py tests/saas_cases/test_auth_routes.py tests/saas_cases/test_project_routes.py tests/saas_cases/test_publication_routes.py tests/saas_cases/test_billing_routes.py tests/saas_cases/test_developer_entry.py -q
\`\`\`

- [ ] **Step 2: Run frontend unit, type, lint, and production build gates**

\`\`\`powershell
Set-Location frontend
npm.cmd test -- --run
npx.cmd tsc --noEmit
npm.cmd run lint
npm.cmd run build
\`\`\`

- [ ] **Step 3: Run the browser check**

With the local app and test OAuth session, open \`/admin/developer\`, confirm the
redirect to \`/studio?scope=all\`, open a foreign project, verify the developer
badge, owner email, version history, preview, and a no-charge billing refusal.
Capture desktop and narrow mobile screenshots and check console/network errors.

- [ ] **Step 4: Inspect the final diff and status**

\`\`\`powershell
git diff --check HEAD~6 HEAD
git status --short
git log --oneline -8
\`\`\`

Expected: no whitespace errors; only the intended commits are tracked; known
pre-existing untracked user files remain untouched. Production deployment is
not claimed until the standard SaaS release procedure is explicitly run.

