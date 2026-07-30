# Funnel Journeys and Aggregate Admin Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one server-owned, opaque journey identifier with immutable first-touch campaign attribution across landing, OAuth, project, run, payment, and publication boundaries, then expose only unique-journey aggregates to authenticated admins.

**Architecture:** A new `funnel_journeys` row owns the server-generated UUID and sanitized first-touch campaign. Nullable additive foreign keys carry that UUID through existing durable entities and `funnel_events`; legacy rows remain valid but are excluded from unique-journey counts. A server-rendered admin page and JSON endpoint query cohort aggregates only, while a bounded cleanup runtime removes raw journey/event rows after 90 days.

**Tech Stack:** Python 3.12, aiohttp, aiohttp-session, SQLAlchemy 2 async ORM, Alembic, PostgreSQL/SQLite tests, pytest, React 19, TypeScript, Vitest.

---

## Execution preconditions and fixed contracts

Do not start production edits until the migrations from the approved design have landed in order:

```text
0014_generation_forensics
0015_yookassa_recurring_foundation
0016_project_versions
0017_funnel_journeys
```

Before Task 1, run:

```powershell
python -m alembic heads
```

Expected before this plan: exactly `0016_project_versions (head)`. If the repository is still at `0013_pattern_registry`, stop; do not invent placeholder `0014`-`0016` migrations. Rebase this plan's test expectations onto the landed implementations without changing the contracts below.

The implementation must preserve these decisions:

- `journey_id` is a server-generated UUIDv4. It is not derived from email, IP, URL, user agent, session token, or another stable user identifier.
- The browser never chooses or persists `journey_id`; the existing HTTP-only,
  database-backed session stores it under `funnel_journey_id`. Journey lifetime
  is independent from the sliding 14-day auth session: rotate it after a
  bounded attribution TTL or inactivity window (initial contract: 24 hours)
  while preserving immutable first touch inside one journey.
- `funnel_journeys` contains only `id`, five allowlisted campaign dimensions, and `started_at`. It has no user, tenant, URL, prompt, email, IP, profile, payload, or generic metadata column.
- The first accepted campaign is immutable. Later requests in the same journey may not update, merge, or fill blank campaign dimensions.
- Existing `funnel_events` campaign columns remain during the additive rollout. New attributed events use `journey_id` as the source of truth; no destructive backfill or column removal belongs in this plan.
- The primary funnel order is exactly
  `landing_entered -> authenticated_project -> run_queued -> free_result ->
  payment_completed -> published`.
- `upgrade_started` remains a secondary commercial aggregate; verified
  `payment_completed` is both a commercial metric and the required primary
  stage before publication.
- Admin reporting counts `COUNT(DISTINCT journey_id)` for a `funnel_journeys.started_at` cohort. It never returns event rows, journey IDs, user IDs, entity IDs, email, IP, source URL, prompt, or payment-provider data.
- Raw funnel journeys and events have a default 90-day retention, configurable only from 7 through 365 days. Reports may not request a window longer than configured retention.
- Legacy events whose `journey_id` is null are excluded from journey conversion counts. Do not guess an attribution from user or entity IDs.
- Interactive checkout is linked to the owner-scoped project shown in Studio. Provider metadata keeps its current minimal allowlist and does not receive `journey_id` or `project_id`.
- `KAIGO_FUNNEL_JOURNEYS_ENABLED=false` is the default. Schema may land first;
  journey writes, aggregate report and cleanup are enabled in separate
  fail-closed rollout steps.

## Target file map

Create:

- `migrations/versions/0017_funnel_journeys.py` — additive table, linkage columns, foreign keys, and indexes.
- `app/analytics/routes.py` — public server-owned landing-entry endpoint.
- `app/analytics/reporting.py` — cohort aggregation and allowlisted serialization.
- `app/analytics/retention.py` — transactional raw-data purge.
- `app/analytics/runtime.py` — startup/hourly retention loop.
- `app/admin/funnel.py` — authenticated aggregate JSON and server-rendered HTML routes.
- `frontend/src/shared/journey.ts` — coalesced landing-entry request; never stores the journey ID.
- `frontend/src/shared/journey.test.ts` — browser request/privacy contract.
- `tests/saas_cases/test_funnel_journey_migration.py` — migration and model contract.
- `tests/saas_cases/test_funnel_journeys.py` — first-touch and event attribution service contract.
- `tests/saas_cases/test_funnel_journey_routes.py` — anonymous entry/session contract.
- `tests/saas_cases/test_funnel_reporting.py` — distinct cohort aggregation contract.
- `tests/saas_cases/test_funnel_admin.py` — admin auth, JSON allowlist, and HTML contract.
- `tests/saas_cases/test_funnel_retention.py` — retention and runtime lifecycle contract.

Modify:

- `app/saas/models.py` — `FunnelJourney` plus nullable attribution/linkage fields.
- `app/analytics/service.py` — journey creation, stage registry, and attributed event writes.
- `app/auth/routes.py` — draft/OAuth propagation, session rotation, and authenticated-project hook.
- `app/auth/service.py` — copy draft journey to the claimed project.
- `app/projects/routes.py` — direct-project attribution and run inheritance.
- `builder_lab/worker.py` — first-artifact/free-result journey propagation.
- `app/billing/routes.py` — owner-scoped `project_id` checkout input.
- `app/billing/payments.py` — immutable payment/project/journey linkage across replay and fulfillment.
- `app/publication/service.py` — publication linkage and published event attribution.
- `app/admin/routes.py` — register funnel admin routes.
- `app/admin/layout.py` — add the aggregate report navigation link.
- `app/config.py`, `.env.example`, `app/server.py` — bounded retention settings and route/runtime registration.
- `frontend/src/landing/LandingPage.tsx` — start the landing entry request once.
- `frontend/src/auth/AuthGate.tsx` — await the same entry boundary on direct Studio visits before reading or starting auth.
- `frontend/src/shared/UrlComposer.tsx` — await the coalesced entry request before draft creation.
- `frontend/src/studio/api.ts`, `frontend/src/studio/UpgradeGate.tsx` — send the current project with checkout.
- Relevant existing funnel, auth, project, worker, billing, publication, frontend, and migration-head tests named in the tasks below.

### Task 1: Add migration `0017_funnel_journeys` and ORM linkage

**Files:**

- Create: `migrations/versions/0017_funnel_journeys.py`
- Create: `tests/saas_cases/test_funnel_journey_migration.py`
- Modify: `app/saas/models.py`
- Modify: `tests/deployment_cases/test_saas_production_contract.py`
- Modify: `tests/saas_cases/test_billing_migration.py`
- Modify: `tests/saas_cases/test_project_recovery_migration.py`
- Modify: `tests/saas_cases/test_publication_migration.py`

- [ ] **Step 1: RED — write the migration and model contract tests first**

Add tests with these exact names:

```python
def test_funnel_journey_migration_is_additive_after_project_versions() -> None:
    migration = importlib.import_module("migrations.versions.0017_funnel_journeys")
    source = Path(migration.__file__).read_text(encoding="utf-8")

    assert migration.revision == "0017_funnel_journeys"
    assert migration.down_revision == "0016_project_versions"
    assert 'create_table(\n        "funnel_journeys"' in source
    for table in (
        "anonymous_drafts",
        "oauth_states",
        "projects",
        "generation_runs",
        "payment_attempts",
        "publications",
        "funnel_events",
    ):
        assert f'"{table}"' in source
    assert '"project_id"' in source
    assert "ondelete=\"SET NULL\"" in source
    assert "op.drop_table(\"funnel_journeys\")" in source


def test_funnel_journey_model_is_privacy_minimal() -> None:
    columns = {column.name for column in inspect(FunnelJourney).columns}
    assert columns == {
        "id",
        "campaign_source",
        "campaign_medium",
        "campaign_name",
        "campaign_term",
        "campaign_content",
        "started_at",
    }
    assert columns.isdisjoint({
        "user_id", "tenant_id", "session_id", "source_url", "url",
        "prompt", "brief", "email", "ip", "ip_address", "profile", "payload",
    })
```

Also assert through SQLAlchemy inspection that:

- `AnonymousDraft`, `OAuthState`, `Project`, `GenerationRun`, `PaymentAttempt`, `Publication`, and `FunnelEvent` each expose nullable `journey_id`;
- `PaymentAttempt` exposes nullable `project_id`;
- every new foreign key uses `ON DELETE SET NULL`;
- every new journey-link column and `PaymentAttempt.project_id` has an index;
- `funnel_events` also has composite index
  `ix_funnel_events_journey_type_occurred` on
  `(journey_id, event_type, occurred_at)` for the report query;
- no upgrade statement updates or deletes existing data.

Update every test that invokes `command.upgrade` with target `"head"` and then asserts the Alembic head to expect `0017_funnel_journeys`. Preserve its downgrade-specific assertions.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_journey_migration.py tests/deployment_cases/test_saas_production_contract.py -q
```

Expected: FAIL because `migrations.versions.0017_funnel_journeys` and `FunnelJourney` do not exist and the current head is not `0017_funnel_journeys`.

- [ ] **Step 3: GREEN — add the minimal additive schema**

Add this ORM shape to `app/saas/models.py`:

```python
class FunnelJourney(Base):
    __tablename__ = "funnel_journeys"

    id: Mapped[UUID] = _uuid_pk()
    campaign_source: Mapped[str | None] = mapped_column(String(255))
    campaign_medium: Mapped[str | None] = mapped_column(String(255))
    campaign_name: Mapped[str | None] = mapped_column(String(255))
    campaign_term: Mapped[str | None] = mapped_column(String(255))
    campaign_content: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
```

Add nullable indexed `journey_id` UUID foreign keys to all seven boundary models. Add nullable indexed `project_id` to `PaymentAttempt`. Use explicit named foreign keys in Alembic, all pointing to `funnel_journeys.id` or `projects.id` with `ondelete="SET NULL"`.

`upgrade()` order must be:

1. create `funnel_journeys` and `ix_funnel_journeys_started_at`;
2. add nullable columns;
3. create foreign keys;
4. create indexes, including
   `ix_funnel_events_journey_type_occurred(journey_id, event_type,
   occurred_at)`.

`downgrade()` must reverse indexes, foreign keys, columns, then the table. It must not attempt to reconstruct or overwrite pre-0017 attribution.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_journey_migration.py tests/deployment_cases/test_saas_production_contract.py tests/saas_cases/test_billing_migration.py tests/saas_cases/test_project_recovery_migration.py tests/saas_cases/test_publication_migration.py -q
```

Expected: PASS, with PostgreSQL-only tests skipped only when `KAIGO_TEST_POSTGRES_URL` is absent.

When disposable PostgreSQL is configured, also run:

```powershell
python -m pytest tests/saas_cases/test_funnel_journey_migration.py -m postgres -q
```

Expected: PASS after upgrading from `0016_project_versions` to `0017_funnel_journeys` and downgrading back without losing existing projects, payments, publications, or funnel events.

- [ ] **Step 5: Commit**

```powershell
git add migrations/versions/0017_funnel_journeys.py app/saas/models.py tests/saas_cases/test_funnel_journey_migration.py tests/deployment_cases/test_saas_production_contract.py tests/saas_cases/test_billing_migration.py tests/saas_cases/test_project_recovery_migration.py tests/saas_cases/test_publication_migration.py
git commit -m "feat: add funnel journey schema"
```

### Task 2: Create immutable first-touch journeys and attributed events

**Files:**

- Create: `tests/saas_cases/test_funnel_journeys.py`
- Modify: `tests/saas_cases/test_funnel_analytics.py`
- Modify: `app/analytics/service.py`

- [ ] **Step 1: RED — define the journey service behavior**

Add tests for these behaviors:

```python
@pytest.mark.asyncio
async def test_ensure_funnel_journey_generates_uuid_and_freezes_first_touch(
    funnel_database,
) -> None:
    _, factory = funnel_database
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    async with factory() as database, database.begin():
        first = await ensure_funnel_journey(
            database,
            journey_id=None,
            campaign={"utm_source": "telegram", "utm_campaign": "launch"},
            now=now,
        )
        replay = await ensure_funnel_journey(
            database,
            journey_id=first.journey.id,
            campaign={"utm_source": "google", "utm_campaign": "summer"},
            now=now + timedelta(minutes=1),
        )

    assert first.created is True
    assert first.journey.id.version == 4
    assert replay.created is False
    assert replay.journey.id == first.journey.id
    assert replay.journey.campaign_source == "telegram"
    assert replay.journey.campaign_name == "launch"
    assert replay.journey.started_at == now
```

Add `test_ensure_funnel_journey_replaces_missing_candidate_without_deriving_identity`,
`test_record_funnel_event_persists_journey_id_and_copies_only_its_first_touch`,
`test_record_funnel_event_rejects_a_missing_journey`,
`test_record_funnel_event_rejects_event_key_reused_by_another_journey`, and
`test_core_funnel_stage_order_is_fixed`. For the attributed-event test, create a
Telegram journey, pass a conflicting Google campaign to `record_funnel_event`,
and assert the stored event has the Telegram values copied from the journey.
The stage assertion is exact:

```python
assert CORE_FUNNEL_STAGES == (
    "landing_entered",
    "authenticated_project",
    "run_queued",
    "free_result",
    "payment_completed",
    "published",
)
assert COMMERCIAL_FUNNEL_STAGES == ("upgrade_started", "payment_completed")
```

Extend the privacy schema assertion in `test_funnel_analytics.py` to include `journey_id` while retaining the existing forbidden-column set. Keep all legacy campaign sanitization and event-key idempotency tests.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_journeys.py tests/saas_cases/test_funnel_analytics.py -q
```

Expected: FAIL because the service has no `ensure_funnel_journey`, stage constants, or `journey_id` event argument.

- [ ] **Step 3: GREEN — implement the single-writer first-touch contract**

In `app/analytics/service.py`:

- add `FUNNEL_JOURNEY_SESSION_KEY = "funnel_journey_id"` as the shared
  auth/analytics session-key contract, keeping it out of `analytics.routes` so
  that reusing the auth entry limiter cannot introduce a circular import;
- add `CORE_FUNNEL_STAGES` and `COMMERCIAL_FUNNEL_STAGES` exactly as tested;
- add `landing_entered` and `authenticated_project` to `FUNNEL_EVENT_TYPES` without removing existing auxiliary event types;
- add `FunnelJourneyResult(journey: FunnelJourney, created: bool)`;
- add `ensure_funnel_journey(database, *, journey_id, campaign, now=None)`;
- if `journey_id` resolves to an existing row, return it without assigning any campaign or timestamp field;
- otherwise create a UUIDv4-backed `FunnelJourney` using only `_campaign_values(campaign)` and the supplied UTC `now` when present;
- add optional `journey_id` to `record_funnel_event`; when present, load that
  journey, reject a missing row, persist its ID, and copy its stored campaign
  columns to the compatibility event columns while ignoring the call's
  campaign argument;
- retain `event_key` uniqueness and the nested savepoint race handling; on
  normal or integrity-race replay, reject only a conflict where both the stored
  and requested journey IDs are non-null and different, never rewrite a legacy
  null journey in place.

Do not add a journey update method. Do not infer a journey from `user_id`, `project_id`, or another entity.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_journeys.py tests/saas_cases/test_funnel_analytics.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/analytics/service.py tests/saas_cases/test_funnel_journeys.py tests/saas_cases/test_funnel_analytics.py
git commit -m "feat: add immutable first-touch journeys"
```

### Task 3: Record a landing entry without exposing the journey ID

**Files:**

- Create: `app/analytics/routes.py`
- Create: `tests/saas_cases/test_funnel_journey_routes.py`
- Create: `frontend/src/shared/journey.ts`
- Create: `frontend/src/shared/journey.test.ts`
- Modify: `app/server.py`
- Modify: `frontend/src/landing/LandingPage.tsx`
- Modify: `frontend/src/landing/LandingPage.test.tsx`
- Modify: `frontend/src/auth/AuthGate.tsx`
- Modify: `frontend/src/auth/AuthGate.test.tsx`
- Modify: `frontend/src/shared/UrlComposer.tsx`
- Modify: `frontend/src/App.test.tsx`

- [ ] **Step 1: RED — write the server entry-route tests**

Build an aiohttp test app with the existing session middleware and entry limiter. Add:

- `test_landing_entry_creates_one_server_owned_journey_per_session`;
- `test_landing_entry_replay_keeps_first_campaign_and_one_event`;
- `test_landing_entry_rejects_client_ids_urls_and_pii_fields`;
- `test_landing_entry_rate_limit_runs_before_database_write`.

For the replay test, POST twice in one cookie session:

```python
first = await client.post(
    "/api/analytics/entry",
    json={"campaign": {"utm_source": "telegram", "utm_campaign": "launch"}},
)
second = await client.post(
    "/api/analytics/entry",
    json={"campaign": {"utm_source": "google", "utm_campaign": "summer"}},
)

assert first.status == second.status == 204
assert await first.read() == await second.read() == b""
assert journey_count == 1
assert event_count == 1
assert journey.campaign_source == "telegram"
assert journey.campaign_name == "launch"
assert event.event_type == "landing_entered"
assert event.event_key == f"landing_entered:journey:{journey.id}"
assert event.journey_id == journey.id
```

A request containing any top-level key other than optional `campaign`, including `journey_id`, `url`, `email`, or `ip`, must return 400 and create no row.

- [ ] **Step 2: Verify server RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_journey_routes.py -q
```

Expected: FAIL because `/api/analytics/entry` is not registered.

- [ ] **Step 3: GREEN — implement the server-owned entry boundary**

Import `FUNNEL_JOURNEY_SESSION_KEY` from `app.analytics.service`. In
`app/analytics/routes.py`, define only the route registration boundary:

```python
def setup_analytics_routes(app: web.Application) -> None:
    app.router.add_post("/api/analytics/entry", record_landing_entry)
```

`record_landing_entry` must:

1. use the existing entry limiter with scope `funnel_entry` and the existing maximum request size;
2. accept only an object containing optional object `campaign`;
3. parse the session candidate as a UUID, treating invalid/stale values as absent;
4. call `ensure_funnel_journey` in the same database transaction as `record_funnel_event`;
5. store only `str(journey.id)` in the server-backed session;
6. record `landing_entered:journey:{journey.id}`;
7. return 204 with no JSON body and no `journey_id` header.

Register analytics routes in `app/server.py` after `setup_auth_routes`, because the route intentionally reuses the configured entry limiter.

- [ ] **Step 4: Verify server GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_journey_routes.py -q
```

Expected: PASS.

- [ ] **Step 5: RED — write the frontend coalescing/privacy tests**

In `frontend/src/shared/journey.test.ts`, reset modules between tests and assert:

```typescript
await Promise.all([
  ensureLandingJourney('?utm_source=telegram&utm_campaign=launch&email=private%40example.com'),
  ensureLandingJourney('?utm_source=telegram&utm_campaign=launch&email=private%40example.com'),
]);

expect(fetchMock).toHaveBeenCalledTimes(1);
expect(fetchMock).toHaveBeenCalledWith('/api/analytics/entry', {
  method: 'POST',
  credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  keepalive: true,
  body: JSON.stringify({
    campaign: { utm_source: 'telegram', utm_campaign: 'launch' },
  }),
});
expect(JSON.stringify(fetchMock.mock.calls)).not.toContain('private@example.com');
```

Add a failure test proving a rejected/non-2xx analytics request resolves without
blocking the user's later draft submission. Add a `LandingPage` test proving the
effect calls `ensureLandingJourney`, an `App`/`UrlComposer` test proving draft
POST happens after the entry promise settles, and an `AuthGate` test for a direct
`/studio?utm_source=telegram` visit proving the entry request settles before
`/api/auth/session` is read. That direct visit must send only the sanitized
campaign, not the full Studio URL.

- [ ] **Step 6: Verify frontend RED**

From `frontend/`, run:

```powershell
npm test -- --run src/shared/journey.test.ts src/landing/LandingPage.test.tsx src/auth/AuthGate.test.tsx src/App.test.tsx
```

Expected: FAIL because `journey.ts` and the landing hook do not exist.

- [ ] **Step 7: GREEN — add one coalesced best-effort browser request**

Implement `ensureLandingJourney(search = window.location.search): Promise<void>` in `frontend/src/shared/journey.ts`. It must:

- derive only the existing allowlisted `Campaign` from `campaignFromSearch`;
- memoize one in-flight/completed promise per page load;
- POST only `{campaign}` with credentials and `keepalive: true`;
- use an `AbortController` with a short bounded deadline (initially 750 ms);
- swallow analytics transport/status failures so analytics never blocks the product flow;
- never read a response ID and never use localStorage, sessionStorage, a JS cookie, fingerprint, URL, email, or IP.

Call it from a `LandingPage` mount effect. At the start of `AuthGate.hydrate`,
await the same promise before reading `/api/auth/session`; this gives direct
Studio/OAuth entry the same server session journey. In `UrlComposer`, await the
same promise before POSTing `/api/drafts`; because failures resolve, draft
creation still proceeds. Update existing fetch mocks to explicitly handle
`/api/analytics/entry` with a 204 response.

Add a test whose analytics `fetch` never settles and prove auth hydration and
draft creation proceed after the bounded deadline.

- [ ] **Step 8: Verify frontend GREEN**

From `frontend/`, run:

```powershell
npm test -- --run src/shared/journey.test.ts src/landing/LandingPage.test.tsx src/auth/AuthGate.test.tsx src/App.test.tsx
npm run typecheck
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add app/analytics/routes.py app/server.py tests/saas_cases/test_funnel_journey_routes.py frontend/src/shared/journey.ts frontend/src/shared/journey.test.ts frontend/src/landing/LandingPage.tsx frontend/src/landing/LandingPage.test.tsx frontend/src/auth/AuthGate.tsx frontend/src/auth/AuthGate.test.tsx frontend/src/shared/UrlComposer.tsx frontend/src/App.test.tsx
git commit -m "feat: capture server-owned landing journeys"
```

### Task 4: Carry the journey through draft, OAuth, and project creation

**Files:**

- Modify: `app/auth/routes.py`
- Modify: `app/auth/service.py`
- Modify: `app/projects/routes.py`
- Modify: `tests/saas_cases/test_funnel_entry_hooks.py`
- Modify: `tests/saas_cases/test_auth_routes.py`
- Modify: `tests/saas_cases/test_project_routes.py`

- [ ] **Step 1: RED — extend the boundary tests before changing routes**

Extend `test_draft_and_oauth_boundaries_emit_only_server_owned_funnel_events` to first POST `/api/analytics/entry`, then execute draft, OAuth start, and callback. Assert:

```python
assert draft_row.journey_id == journey.id
assert state.journey_id == journey.id
assert project.journey_id == journey.id
assert authenticated_session["funnel_journey_id"] == str(journey.id)
assert by_type["authenticated_project"].event_key == (
    f"authenticated_project:project:{project.id}"
)
assert {
    event.journey_id
    for event in events
    if event.event_type in {
        "landing_entered",
        "composer_submitted",
        "auth_started",
        "auth_completed",
        "authenticated_project",
    }
} == {journey.id}
```

Add separate tests for:

- authenticated `POST /api/drafts/{draft_id}/claim` emits the same idempotent `authenticated_project` stage on replay;
- authenticated direct `POST /api/projects` creates/uses the session journey and emits `authenticated_project` once;
- OAuth without a draft keeps the journey across `new_session` but does not emit `authenticated_project`;
- a later campaign on direct project creation cannot replace the journey's first-touch campaign.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_entry_hooks.py tests/saas_cases/test_auth_routes.py tests/saas_cases/test_project_routes.py -k "journey or authenticated_project or draft_and_oauth_boundaries" -q
```

Expected: FAIL because drafts, OAuth states, projects, and the rotated authenticated session do not carry `journey_id`, and `authenticated_project` is not emitted.

- [ ] **Step 3: GREEN — propagate one immutable journey**

Make these exact changes:

- `create_draft`: resolve/create the journey from the session and request campaign inside the draft transaction; store `draft.journey_id`; pass it to `composer_submitted`.
- `auth_start`: choose `draft.journey_id` when a valid draft exists, otherwise resolve/create from the session; store it on `OAuthState`; pass it to `auth_started`.
- `auth_callback`: include `OAuthState.journey_id` in the atomic update statement's returning columns; pass it to `auth_completed`; after `new_session`, copy it to `funnel_journey_id`.
- `link_identity_and_claim_draft`: set `Project.journey_id = draft.journey_id` in the same transaction as claim.
- `claim_draft`: set the deterministic project's journey from the draft and record `authenticated_project:project:{project.id}` on both create and safe replay.
- OAuth callback: when the claim returns a project, record the same authenticated-project key; event-key idempotency prevents duplication.
- authenticated `create_project`: resolve/create from the session campaign, set `Project.journey_id`, and record both the existing composer event and `authenticated_project` with that journey.

Do not replace the current draft claim token, OAuth state/session binding, CSRF, owner scope, or deterministic replay logic. Do not copy a journey from another project owned by the same user.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_entry_hooks.py tests/saas_cases/test_auth_routes.py tests/saas_cases/test_project_routes.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/auth/routes.py app/auth/service.py app/projects/routes.py tests/saas_cases/test_funnel_entry_hooks.py tests/saas_cases/test_auth_routes.py tests/saas_cases/test_project_routes.py
git commit -m "feat: propagate journeys through auth and projects"
```

### Task 5: Carry the journey through runs, free results, and publication

**Files:**

- Modify: `app/projects/routes.py`
- Modify: `builder_lab/worker.py`
- Modify: `app/publication/service.py`
- Modify: `tests/saas_cases/test_funnel_entry_hooks.py`
- Modify: `tests/builder_lab_cases/test_funnel_hooks.py`
- Modify: `tests/saas_cases/test_funnel_hooks.py`
- Modify: `tests/saas_cases/test_publication.py`

- [ ] **Step 1: RED — assert durable inheritance at every existing hook**

Update the run queue test to assert:

```python
assert stored_run.journey_id == project.journey_id
assert events[0].journey_id == project.journey_id
```

Seed a `FunnelJourney` in both worker funnel tests, attach it to the project and run, then assert `first_artifact` and `free_result` events keep that ID on successful completion, failure-with-verified-artifact, and replay.

Extend publication tests so a new publication copies `project.journey_id`, and the idempotent `published` event has that same journey. Add a legacy-publication test: a pre-0017 publication with null journey may be safely attached to its own project's journey on the next owner-authorized publish, but a non-null conflicting journey fails closed as corrupt state.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_entry_hooks.py tests/builder_lab_cases/test_funnel_hooks.py tests/saas_cases/test_funnel_hooks.py tests/saas_cases/test_publication.py -k "journey or run_queue_replay or final_artifact_boundary or terminal_failure or publish_replay" -q
```

Expected: FAIL because runs, worker events, publications, and published events do not copy `journey_id`.

- [ ] **Step 3: GREEN — inherit attribution, never recalculate it**

Make these changes:

- `_enqueue_express_run` sets `GenerationRun.journey_id = project.journey_id`.
- Both initial and retry/refinement queue paths pass `run.journey_id` to `run_queued`.
- After `0016_project_versions` lands, audit all additional `GenerationRun(` creation sites with `rg -n "GenerationRun\\(" app builder_lab`; every project-owned initial/refinement/restore run must copy the project's journey.
- Worker `first_artifact` and `_record_free_result_if_available` calls pass `run.journey_id`.
- `PublicationService.publish` sets a new publication's journey from the locked project. For a legacy null value it fills once from that project; a different non-null value raises the existing corruption-domain error before mutation.
- Both published-event branches pass `publication.journey_id`.

Retries, refinements, restores, releases, and rollbacks do not create a new journey. Rollback does not emit another `published` acquisition stage.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_entry_hooks.py tests/builder_lab_cases/test_funnel_hooks.py tests/saas_cases/test_funnel_hooks.py tests/saas_cases/test_publication.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/projects/routes.py builder_lab/worker.py app/publication/service.py tests/saas_cases/test_funnel_entry_hooks.py tests/builder_lab_cases/test_funnel_hooks.py tests/saas_cases/test_funnel_hooks.py tests/saas_cases/test_publication.py
git commit -m "feat: attribute runs and publications to journeys"
```

### Task 6: Link interactive payment to the owner-scoped project and journey

**Files:**

- Modify: `app/billing/routes.py`
- Modify: `app/billing/payments.py`
- Modify: `frontend/src/studio/api.ts`
- Modify: `frontend/src/studio/api.test.ts`
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/studio/UpgradeGate.test.tsx`
- Modify: `tests/saas_cases/test_billing_routes.py`
- Modify: `tests/saas_cases/test_billing_service.py`
- Modify: `tests/saas_cases/test_funnel_hooks.py`

- [ ] **Step 1: RED — write backend ownership and replay tests**

Seed two owner-scoped projects with different journeys. Add tests that prove:

- checkout body must be exactly `{plan_code, project_id}`;
- a missing, malformed, foreign-user, or foreign-tenant project returns 400/404 before provider dispatch;
- `PaymentAttempt.project_id` and `.journey_id` are copied from the validated project;
- the same idempotency key and project replays one attempt;
- the same idempotency key with another project raises `CheckoutIdempotencyConflict` even when the plan is unchanged;
- `upgrade_started` and verified `payment_completed` carry stored payment project/journey linkage;
- webhook/reconciliation cannot accept attribution from provider payload;
- provider metadata remains exactly `payment_attempt_id`, `user_id`, `plan_code`, and `plan_fingerprint`.

The key assertion is:

```python
assert attempt.project_id == project.id
assert attempt.journey_id == project.journey_id
assert {
    (event.event_type, event.project_id, event.journey_id)
    for event in events
} == {
    ("upgrade_started", project.id, project.journey_id),
    ("payment_completed", project.id, project.journey_id),
}
assert set(provider.checkout_calls[0].metadata) == {
    "payment_attempt_id", "user_id", "plan_code", "plan_fingerprint",
}
```

If `0015_yookassa_recurring_foundation` added stored-method renewals, add a focused test that a renewal copies attribution only from the server-owned founding/subscription payment. It must not accept a new browser journey or count the renewal as a new distinct journey.

- [ ] **Step 2: Verify backend RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_routes.py tests/saas_cases/test_billing_service.py tests/saas_cases/test_funnel_hooks.py -k "project or journey or checkout_and_webhook_replays" -q
```

Expected: FAIL because checkout accepts only `plan_code` and payment events have no project/journey attribution.

- [ ] **Step 3: GREEN — make payment attribution server-owned and immutable**

Change the authenticated checkout route to retain both values returned by
`_scope`, parse UUID `project_id`, and call
`BillingService.create_checkout(user_id, plan_code, idempotency_key,
project_id=project_id, tenant_id=tenant_id)`. Keep `project_id` and `tenant_id`
optional only on the internal service boundary if the landed `0015` renewal
path needs an account-level invocation. Inside the existing user/payment
locking transaction:

1. when a project is supplied, require both attribution arguments and load the
   project by `id`, `owner_user_id`, and `tenant_id`;
2. reject it before creating or dispatching a payment if ownership fails;
3. set a new attempt's `project_id` and `journey_id` once;
4. compare stored `project_id` on idempotent replay and conflict on mismatch;
5. use only stored attempt linkage for `upgrade_started`, webhook fulfillment, reconciliation, and renewal;
6. keep `project_id` and `journey_id` out of provider metadata and browser payment serializers.

The public Studio checkout must always supply a project. A server-scheduled
renewal may omit both project arguments only if `0015` already defines that
account-level path; never permit only one of the two arguments.

- [ ] **Step 4: Verify backend GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_routes.py tests/saas_cases/test_billing_service.py tests/saas_cases/test_funnel_hooks.py -q
```

Expected: PASS.

- [ ] **Step 5: RED — update the browser checkout contract test**

Change the API test expectation to:

```typescript
await createBillingCheckout(
  'starter_monthly',
  'project-123',
  'csrf-billing',
  'checkout-stable-key',
);

expect(fetchMock).toHaveBeenCalledWith('/api/billing/checkout', expect.objectContaining({
  method: 'POST',
  body: JSON.stringify({
    plan_code: 'starter_monthly',
    project_id: 'project-123',
  }),
}));
```

Add an `UpgradeGate` assertion that its existing `projectId` prop is passed to checkout and that no journey ID is present in request or component state.

- [ ] **Step 6: Verify frontend RED**

From `frontend/`, run:

```powershell
npm test -- --run src/studio/api.test.ts src/studio/UpgradeGate.test.tsx
```

Expected: FAIL because `createBillingCheckout` does not accept/send `projectId`.

- [ ] **Step 7: GREEN — pass the already owner-scoped Studio project**

Update `createBillingCheckout(planCode, projectId, csrfToken, idempotencyKey)` and pass `UpgradeGate`'s existing non-empty `projectId`. Do not add a browser journey prop, query parameter, storage key, or payment response field.

- [ ] **Step 8: Verify frontend GREEN**

From `frontend/`, run:

```powershell
npm test -- --run src/studio/api.test.ts src/studio/UpgradeGate.test.tsx
npm run typecheck
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add app/billing/routes.py app/billing/payments.py frontend/src/studio/api.ts frontend/src/studio/api.test.ts frontend/src/studio/UpgradeGate.tsx frontend/src/studio/UpgradeGate.test.tsx tests/saas_cases/test_billing_routes.py tests/saas_cases/test_billing_service.py tests/saas_cases/test_funnel_hooks.py
git commit -m "feat: link payments to project journeys"
```

### Task 7: Build the unique-journey aggregate report service

**Files:**

- Create: `app/analytics/reporting.py`
- Create: `tests/saas_cases/test_funnel_reporting.py`

- [ ] **Step 1: RED — specify cohort and deduplication semantics**

Seed these rows with fixed UTC timestamps:

- journey A, Telegram/launch: all six core stages plus the commercial
  `upgrade_started` stage;
- journey B, Telegram/launch: landing, authenticated project, run queued, with two distinct `run_queued` event keys;
- journey C, Google/summer: landing only;
- journey D, direct/no campaign: landing and authenticated project;
- one journey before the requested cohort;
- one legacy event with null journey;
- one future event for an in-cohort journey.

Call `build_funnel_report` with `start=datetime(2026, 7, 1, tzinfo=UTC)` and `end=datetime(2026, 7, 31, tzinfo=UTC)`, then assert:

```python
assert [(row.stage, row.journeys) for row in report.core] == [
    ("landing_entered", 4),
    ("authenticated_project", 3),
    ("run_queued", 2),
    ("free_result", 1),
    ("payment_completed", 1),
    ("published", 1),
]
assert [(row.stage, row.journeys) for row in report.commercial] == [
    ("upgrade_started", 1),
    ("payment_completed", 1),
]
assert report.core[1].from_entry_bps == 7500
assert report.core[2].from_entry_bps == 5000
```

The duplicate run event must still count journey B once. The old cohort, legacy event, and future event must not count. Campaign rows must group on the five first-touch columns from `FunnelJourney`, never event campaign columns.

Add a serializer allowlist test with this exact top-level shape:

```python
assert set(payload) == {"window", "core", "commercial", "campaigns"}
assert set(payload["window"]) == {"from", "to", "days", "retention_days"}
assert all(set(row) == {"stage", "journeys", "from_entry_bps"} for row in payload["core"])
```

Campaign rows may contain only `source`, `medium`, `campaign`, `term`,
`content`, `landing_entered`, `authenticated_project`, `run_queued`,
`free_result`, `published`, `upgrade_started`, and `payment_completed`.
Recursively assert that serialized JSON contains no key ending in `_id` and
none of the seeded UUID, email, source URL, IP, prompt, or provider payment
values.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_reporting.py -q
```

Expected: FAIL because `app.analytics.reporting` does not exist.

- [ ] **Step 3: GREEN — implement one aggregate-only query boundary**

In `app/analytics/reporting.py`, add immutable `FunnelStageAggregate`,
`FunnelCampaignAggregate`, and `FunnelReport` dataclasses. Implement
`build_funnel_report(database: AsyncSession, *, start: datetime, end: datetime)
-> FunnelReport` and `serialize_funnel_report(report: FunnelReport, *, days: int,
retention_days: int) -> dict[str, object]` with those exact signatures. Use SQL
conditional aggregates equivalent to
`COUNT(DISTINCT CASE WHEN event_type = :stage THEN journey_id END)`, joined from
journeys in `[start, end)` to events with `occurred_at >= started_at` and
`occurred_at < end`. Do not load raw event/entity rows into the response layer.

Compute `from_entry_bps` as rounded integer basis points, `None` when the landing count is zero. Sort campaign rows deterministically by descending `landing_entered`, then normalized dimension values. Represent all-null campaign dimensions as JSON null; the UI supplies the label `Прямой / не определён`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_reporting.py -q
```

Expected: PASS on SQLite. When PostgreSQL is available, run the same seeded contract through the disposable PostgreSQL fixture and expect identical counts/order.

- [ ] **Step 5: Commit**

```powershell
git add app/analytics/reporting.py tests/saas_cases/test_funnel_reporting.py
git commit -m "feat: aggregate unique funnel journeys"
```

### Task 8: Expose aggregate-only admin JSON and HTML

**Files:**

- Create: `app/admin/funnel.py`
- Create: `tests/saas_cases/test_funnel_admin.py`
- Modify: `app/admin/routes.py`
- Modify: `app/admin/layout.py`

- [ ] **Step 1: RED — write auth, range, and non-disclosure tests**

Add tests with these exact behaviors:

- unauthenticated `GET /admin/api/funnel` and `/admin/funnel` redirect to `/admin/login` before opening a database session;
- authenticated `GET /admin/api/funnel` defaults to 30 days;
- `days=1`, non-integer, zero, negative, and greater than retention return 400; valid `days=7`, `30`, and `90` return 200 when retention is 90;
- JSON matches the serializer allowlist from Task 7;
- HTML contains the primary funnel, commercial totals, campaign table, UTC window, retention note, and no drill-down/entity links;
- empty data renders a truthful empty state instead of synthetic zero-conversion claims;
- seeded journey UUID, project UUID, run UUID, payment UUID, publication UUID, email, URL, prompt, IP, and provider reference appear in neither JSON nor HTML.

Use the existing admin session keys in a test-only login route. Do not weaken `_require_session` or public auth.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_admin.py -q
```

Expected: FAIL because the admin funnel routes and navigation do not exist.

- [ ] **Step 3: GREEN — add a separate aggregate admin module**

Create `app/admin/funnel.py` with:

```python
def setup_funnel_admin_routes(app: web.Application) -> None:
    app.router.add_get("/admin/api/funnel", funnel_report_api)
    app.router.add_get("/admin/funnel", funnel_report_page)
```

Both handlers must:

1. require a separate global operator capability using the existing fail-closed
   admin capability/access-log mechanism; an ordinary tenant-admin session is
   insufficient for an unscoped report;
2. validate `days` against `app["config"].funnel_retention_days`;
3. calculate `[now - days, now)` in UTC;
4. call only `build_funnel_report` and its allowlisted serializer;
5. never expose a raw-search, detail, export, or entity endpoint.

The HTML handler may render the returned aggregates directly with `render_layout`. Escape every campaign label. Show counts and `from_entry_bps / 100` as a percentage, with `—` for a missing denominator. Add one global `Воронка` link in `app/admin/layout.py`, and register the new routes from `setup_admin_routes`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_admin.py tests/saas_cases/test_funnel_reporting.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/admin/funnel.py app/admin/routes.py app/admin/layout.py tests/saas_cases/test_funnel_admin.py
git commit -m "feat: add aggregate funnel admin report"
```

### Task 9: Enforce raw funnel retention

**Files:**

- Create: `app/analytics/retention.py`
- Create: `app/analytics/runtime.py`
- Create: `tests/saas_cases/test_funnel_retention.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `app/server.py`
- Modify: `tests/saas_cases/test_config.py`
- Modify: `tests/saas_cases/test_app_runtime_dependency_isolation.py`

- [ ] **Step 1: RED — define deletion, boundary, and lifecycle behavior**

Add tests for:

- default `funnel_retention_days == 90` and `funnel_cleanup_interval_seconds == 3600`;
- retention outside 7-365 days and interval outside 60-86400 seconds is rejected;
- an exact-cutoff journey is retained while a journey one microsecond older is purged;
- purging an old journey deletes all its attributed funnel events, including a recent event, then nulls entity attribution through `ON DELETE SET NULL` without deleting project/run/payment/publication rows;
- legacy events older than the cutoff are deleted and newer legacy events remain;
- first startup cleanup completes before the runtime yields readiness;
- shutdown cancels and awaits the hourly loop without leaving a pending task;
- a cleanup failure before first yield aborts startup instead of serving with an unenforced retention policy.

Use a fixed UTC clock. The core count assertion is:

```python
assert result.deleted_journeys == 1
assert result.deleted_events == 2
assert await database.get(Project, old_project_id) is not None
assert await database.get(PaymentAttempt, old_payment_id) is not None
assert (await database.get(Project, old_project_id)).journey_id is None
assert (await database.get(PaymentAttempt, old_payment_id)).journey_id is None
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_retention.py tests/saas_cases/test_config.py tests/saas_cases/test_app_runtime_dependency_isolation.py -k "funnel or analytics_runtime" -q
```

Expected: FAIL because retention configuration, purge service, and runtime do not exist.

- [ ] **Step 3: GREEN — implement bounded transactional cleanup**

Add configuration:

```python
funnel_journeys_enabled: bool = False
funnel_retention_days: int = 90
funnel_cleanup_interval_seconds: int = 3_600
funnel_cleanup_batch_size: int = 500
funnel_cleanup_time_budget_seconds: int = 5
```

Load `KAIGO_FUNNEL_JOURNEYS_ENABLED`,
`KAIGO_FUNNEL_RETENTION_DAYS`,
`KAIGO_FUNNEL_CLEANUP_INTERVAL_SECONDS`,
`KAIGO_FUNNEL_CLEANUP_BATCH_SIZE`, and
`KAIGO_FUNNEL_CLEANUP_TIME_BUDGET_SECONDS`; validate bounded values and document
the default-off rollout in `.env.example`.

`purge_expired_funnel_data(...)` must acquire a PostgreSQL advisory lock (or
equivalent single-leader lease) and process at most one bounded batch per
transaction/time budget in this order:

1. identify journeys with `started_at < now - retention_days`;
2. delete every event linked to those journeys, regardless of event timestamp;
3. delete legacy null-journey events whose `occurred_at` is before the cutoff;
4. delete the old journeys, allowing `SET NULL` to detach retained product rows;
5. return aggregate deletion counts only.

`setup_analytics_runtime` must register a cleanup context after the database
context only when the feature is enabled. Startup runs at most one short batch;
it never drains an unbounded backlog before readiness. Only the lock holder
continues hourly batches; other web processes return without duplicate work.
Log counts only, never journey/entity IDs. Cancel and await the loop during
cleanup.

Register the runtime immediately after `init_db_signals(app)` and before billing/chat runtimes so all later contexts see the schema/session factory.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_funnel_retention.py tests/saas_cases/test_config.py tests/saas_cases/test_app_runtime_dependency_isolation.py -q
```

Expected: PASS with no pending-task warnings.

- [ ] **Step 5: Commit**

```powershell
git add app/analytics/retention.py app/analytics/runtime.py app/config.py app/server.py .env.example tests/saas_cases/test_funnel_retention.py tests/saas_cases/test_config.py tests/saas_cases/test_app_runtime_dependency_isolation.py
git commit -m "feat: enforce funnel data retention"
```

### Task 10: Cross-boundary regression, privacy review, and delivery evidence

**Files:**

- Modify: `docs/product-journal/2026-07.md` only after implementation is verified
- Create or modify: `docs/telegram/release-packets/2026-07-30-funnel-journeys.md` only if the verified result is independently publishable

- [ ] **Step 1: Run the complete funnel-focused backend suite**

```powershell
python -m pytest tests/saas_cases/test_funnel_analytics.py tests/saas_cases/test_funnel_journeys.py tests/saas_cases/test_funnel_journey_routes.py tests/saas_cases/test_funnel_entry_hooks.py tests/builder_lab_cases/test_funnel_hooks.py tests/saas_cases/test_funnel_hooks.py tests/saas_cases/test_funnel_reporting.py tests/saas_cases/test_funnel_admin.py tests/saas_cases/test_funnel_retention.py tests/saas_cases/test_auth_routes.py tests/saas_cases/test_project_routes.py tests/saas_cases/test_billing_routes.py tests/saas_cases/test_billing_service.py tests/saas_cases/test_publication.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend funnel/payment regression**

From `frontend/`, run:

```powershell
npm test -- --run src/shared/campaign.test.ts src/shared/journey.test.ts src/landing/LandingPage.test.tsx src/auth/AuthGate.test.tsx src/App.test.tsx src/studio/api.test.ts src/studio/UpgradeGate.test.tsx src/studio/SaasStudioFlow.test.tsx
npm run typecheck
npm run lint
npm run build
```

Expected: PASS with zero TypeScript or ESLint errors.

- [ ] **Step 3: Run broader bounded regression**

```powershell
python -m pytest tests/saas_cases tests/builder_lab_cases tests/deployment_cases -q
python -m alembic heads
```

Expected: all tests PASS except explicitly documented environment skips, and Alembic reports exactly `0017_funnel_journeys (head)`.

- [ ] **Step 4: Perform the privacy and attribution audit**

Run:

```powershell
rg -n "journey_id|funnel_journey_id" app frontend/src builder_lab
rg -n "FunnelEvent\(|record_funnel_event\(|GenerationRun\(|PaymentAttempt\(|Publication\(" app builder_lab
```

Verify from the diff and tests:

- every current project-owned run creation inherits `Project.journey_id`;
- every current interactive payment receives an owner-scoped `project_id`;
- every funnel hook passes the durable entity's journey rather than request data;
- no admin serializer/page includes a raw ID or PII field;
- frontend code never stores or sends a journey ID;
- provider metadata never gains project/journey attribution;
- all writes remain additive and legacy null linkage is handled explicitly.

- [ ] **Step 5: Review the patch**

```powershell
git diff --check
git status --short
git diff -- migrations/versions/0017_funnel_journeys.py app/analytics app/auth app/projects app/billing app/publication app/admin app/config.py app/server.py builder_lab/worker.py frontend/src tests/saas_cases tests/builder_lab_cases tests/deployment_cases
```

Expected: no whitespace errors and no unrelated dirty files included.

- [ ] **Step 6: Update the editorial contour only with verified facts**

Add a short Russian entry to `docs/product-journal/2026-07.md` describing the proven unique-journey funnel, aggregate-only admin view, and 90-day raw retention. If a release packet is warranted, state aggregate/privacy behavior without including real campaign values, customer identifiers, private URLs, or screenshots containing admin data. Do not publish to Telegram without explicit user permission.

- [ ] **Step 7: Final scoped commit**

```powershell
git add docs/product-journal/2026-07.md
git add docs/telegram/release-packets/2026-07-30-funnel-journeys.md
git commit -m "docs: record verified funnel journey rollout"
```

If no release packet was created, omit that path from `git add`. Before committing, inspect `git diff --cached --name-only` and remove every unrelated pre-existing dirty file from the index.

## Self-review checklist

- Spec coverage: opaque journey, immutable first touch, landing/auth/project/run/payment/publication propagation, primary funnel stages, aggregate-only admin API/UI, PII exclusion, retention, and migration order each have a dedicated RED/GREEN task.
- Existing-foundation coverage: current `composer_submitted`, `auth_started`, `auth_completed`, `run_queued`, `first_artifact`, `free_result`, `upgrade_started`, `payment_completed`, and `published` hooks remain; only `landing_entered` and `authenticated_project` are new primary-stage hooks.
- Type consistency: every layer uses `journey_id: UUID | None`; session storage alone uses its string form. `project_id` remains UUID internally and a string only at the HTTP/TypeScript boundary.
- Attribution consistency: first-touch values live on `FunnelJourney`; entity/event links inherit them and never accept a client-supplied journey ID.
- Report consistency: primary and commercial stage orders are fixed once in `app.analytics.service`; reporting imports them instead of duplicating literals.
- Migration safety: `0017_funnel_journeys` revises `0016_project_versions`, uses only additive nullable linkage, and performs no speculative legacy backfill.
- Privacy consistency: raw identifiers are available only to server internals/tests; admin output is recursively allowlisted and cleanup removes raw journey/event data after the bounded retention window.
- Placeholder scan: implementation interfaces, route paths, field names, event keys, test names, commands, expected failures, and expected passes are specified; no implementation decision remains deferred.
