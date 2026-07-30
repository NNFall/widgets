# Project Versions, Durable Refinement, and Verifiable Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add owner-scoped project version history, durable and restart-safe refinement, version-selected publication with compare-and-swap protection, and a real external HTTPS canary that proves publish/update/rollback and origin isolation.

**Architecture:** `project_versions` is the immutable user-visible lineage over exact `generation_runs` and `generation_artifacts`; intermediate stage artifacts remain implementation details. SaaS refinement is enqueued as an ordinary durable PostgreSQL worker run seeded from an active version and finalized atomically into a new version, while the existing Builder Lab `BuilderOrchestrator.refine()` path remains private and disconnected from SaaS. Publication keeps its stable embed URL, immutable release manifests, trusted nested runtime, exact-origin allowlist, sandboxing, and rollback chain, adding version selection plus optimistic compare-and-swap at the active-release pointer.

**Tech Stack:** Python 3.12, aiohttp, SQLAlchemy async ORM, Alembic/PostgreSQL, existing durable builder worker, React 19/TypeScript/Vitest, Playwright, nginx, pytest.

---

## Delivery boundaries and invariants

- This plan is implemented only in `D:\papka for all\work\kaigo.widgets\work\saas-foundation`. Preserve every unrelated dirty file and hunk.
- This plan depends on migrations `0014_generation_forensics` and `0015_yookassa_recurring_foundation` landing first. Its only migration ID is exactly `0016_project_versions` with `down_revision = "0015_yookassa_recurring_foundation"`. If `python -m alembic heads` does not report `0015_yookassa_recurring_foundation` before implementation starts, stop and rebase; do not renumber this migration.
- Do not import or call `builder_lab.web.refine_run`, `ORCHESTRATOR_KEY`, or `BuilderOrchestrator.refine()` from `app/`. Keep `refineBuilderRun()` only for the private legacy `/builder` route.
- A user version is created only from the exact accepted/verified artifact selected at a terminal user boundary. Never create one version per stage artifact.
- A failed, cancelled, stale, or CAS-losing refinement never changes `projects.active_version_id` and never changes `publications.active_release_id`. A successful but CAS-losing refinement remains recoverable as a detached history entry.
- Project restore changes the active project version only. It never silently republishes or rolls back a public release.
- Preserve these public security contracts: stable `/embed/{stable_key}.js`, immutable release snapshots/checksums, exact origins rather than wildcard domains, `sandbox="allow-scripts"` without `allow-same-origin`, fixed trusted runtime ownership of open/close/chat/network, no prompt/provenance in manifests, and fail-closed corrupt-release behavior.
- `KAIGO_PROJECT_VERSIONS_ENABLED` defaults to `false` for schema-first rollout. While false, existing artifact-based publication remains available; project-version/refinement/restore routes return the existing privacy-safe not-found shape. When true, Studio uses version IDs and publication CAS.
- Local loopback tests are regression evidence only. Readiness criterion 6 is not complete until the separate externally hosted HTTPS canary passes in a clean browser context.

## File map

**Create**

- `migrations/versions/0016_project_versions.py` — additive schema and deterministic one-version-per-project backfill; legacy v1 releases remain unlinked and byte-for-byte serviceable.
- `app/projects/versions.py` — owner-scoped version repository/service, restore, durable refinement enqueue, and terminal version materialization.
- `tests/saas_cases/test_project_versions_migration.py` — Alembic shape/backfill/downgrade tests.
- `tests/saas_cases/test_project_versions.py` — version service, plan clone, idempotency, restore, and CAS tests.
- `tests/saas_cases/test_project_version_routes.py` — HTTP auth/CSRF/owner/body/feature-flag contracts.
- `tests/builder_lab_cases/test_project_version_worker.py` — restart-safe refinement seed and terminal activation tests.
- `frontend/src/studio/ProjectVersionHistory.tsx` — accessible user version list and restore controls.
- `frontend/src/studio/ProjectVersionHistory.test.tsx` — version history component behavior.
- `deploy/publication-canary/index.html` — static, credential-free canary shell.
- `deploy/publication-canary/canary.js` — strict stable-key loader with no storage or credentials.
- `deploy/nginx/kaigo-publication-canary.conf` — HTTPS-only allowed and denied canary origins.
- `scripts/run_publication_https_canary.py` — external browser/API acceptance runner that reads the owner cookie only from a private file.
- `tests/saas_cases/test_publication_https_canary.py` — runner validation, secret-handling, and evidence-contract tests.
- `tests/deployment_cases/test_publication_canary_contract.py` — static/nginx security contract.

**Modify**

- `app/saas/models.py` — `ProjectVersion`, active/source pointers, membership constraints, and optional release linkage.
- `app/patterns/repository.py` — exact persisted composition-plan clone for refinement runs.
- `app/projects/serializers.py` — version payloads and public event allowlist additions.
- `app/projects/routes.py` — list/refine/restore routes and feature gate; existing run preview/chat routes remain authoritative.
- `builder_lab/worker.py` — load a refinement seed from the version, clone-safe stage context, and atomic terminal version creation/CAS.
- `app/publication/service.py` — version selection, manifest v2 compatibility, release CAS, and idempotent replay.
- `app/publication/routes.py` — bounded strict bodies, version/CAS fields, conflict mapping, and legacy flag-off compatibility.
- `app/widgets/loader.py` — non-secret release/version markers used by external acceptance; sandbox behavior is unchanged.
- `app/config.py`, `.env.example`, `docker-compose.yml` — rollout flag only; no canary credential enters application configuration.
- `scripts/preflight_saas_schema.py` and `tests/deployment_cases/test_saas_production_contract.py` — migration-head/fingerprint coverage.
- `tests/saas_cases/test_schema.py` and `tests/saas_cases/test_publication.py` — ORM, release, CAS, legacy-v1, and route regressions.
- `frontend/src/studio/types.ts`, `frontend/src/studio/api.ts`, `frontend/src/studio/api.test.ts` — version/refinement/restore/publication wire contracts.
- `frontend/src/studio/useBuilderRun.ts`, `frontend/src/studio/StudioPage.tsx`, `frontend/src/studio/StudioPage.test.tsx`, `frontend/src/studio/SaasStudioFlow.test.tsx` — durable SaaS version state without using legacy refinement.
- `frontend/src/studio/UpgradeGate.tsx` and `frontend/src/studio/UpgradeGate.test.tsx` — publish selected version and CAS-safe rollback/recovery.
- `frontend/src/styles.css` — version-history layout using existing Studio tokens.
- `frontend/e2e/fixtures/builder.ts` and `frontend/e2e/studio.spec.ts` — deterministic browser history/refinement/publication fixture.
- `scripts/run_saas_browser_acceptance.py`, `tests/saas_cases/test_browser_acceptance_harness.py`, `scripts/smoke_saas_foundation.py`, `tests/saas_cases/test_saas_acceptance.py`, and `tests/saas_cases/test_saas_smoke_script.py` — local durable-flow regression evidence.
- `docs/SAAS_PRODUCTION_RUNBOOK.md` and `frontend/e2e/README.md` — schema-first flag sequence and explicit external-canary gate.

### Task 1: Lock the additive `0016_project_versions` schema

**Files:**
- Create: `tests/saas_cases/test_project_versions_migration.py`
- Modify: `tests/saas_cases/test_schema.py`
- Create: `migrations/versions/0016_project_versions.py`
- Modify: `app/saas/models.py`
- Modify: `scripts/preflight_saas_schema.py`
- Modify: `tests/deployment_cases/test_saas_production_contract.py`

- [ ] **Step 1: Write the failing ORM and migration-shape tests**

Assert that `Base.metadata` registers `project_versions` with these exact columns:

```python
{
    "id",
    "project_id",
    "ordinal",
    "run_id",
    "artifact_id",
    "parent_version_id",
    "kind",
    "change_request",
    "idempotency_key",
    "created_at",
}
```

Assert named constraints `uq_project_version_ordinal`, `uq_project_version_membership`, `uq_project_version_restore_idempotency`, `ck_project_version_ordinal_positive`, `ck_project_version_kind`, `ck_project_version_shape`, `fk_project_versions_run_membership`, `fk_project_versions_artifact_membership`, and `fk_project_versions_parent_membership`. Also assert:

- `projects.active_version_id` has a deferred same-project membership FK;
- `generation_runs.source_version_id` has a deferred same-project membership FK and `change_request` is `VARCHAR(2000)`;
- `generation_runs` and `generation_artifacts` expose the composite membership unique constraints needed by those FKs;
- `publication_releases.project_version_id` stays `NULL` for every pre-0016
  manifest-v1 release, including the active release. Only newly created
  manifest-v2 releases may carry it;
- the first rollout slice stops at schema, terminal materialization and
  owner-scoped read-only history under the default-off flag. Refinement and
  versioned publication remain disabled until release uniqueness and
  same-project membership are proven at DB level;
- Alembic has one head, `0016_project_versions`, whose parent is `0015_yookassa_recurring_foundation`.

The migration fake-op test must assert additive operations and the backfill SQL; it must also assert that no existing artifact, run, release, or publication row is deleted or rewritten.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_project_versions_migration.py tests/saas_cases/test_schema.py tests/deployment_cases/test_saas_production_contract.py -k "project_version or alembic_has_one_production_head or exact_fingerprint" -q
```

Expected: FAIL because `ProjectVersion` and `0016_project_versions` do not exist and the current recorded head is earlier.

- [ ] **Step 3: Add the ORM model and exact database constraints**

Define `ProjectVersion` in `app/saas/models.py` with:

```python
class ProjectVersion(Base):
    __tablename__ = "project_versions"

    id: Mapped[UUID]
    project_id: Mapped[UUID]
    ordinal: Mapped[int]
    run_id: Mapped[UUID]
    artifact_id: Mapped[UUID]
    parent_version_id: Mapped[UUID | None]
    kind: Mapped[str]
    change_request: Mapped[str | None]
    idempotency_key: Mapped[str | None]
    created_at: Mapped[datetime]
```

The `ck_project_version_shape` truth table is exact:

- `initial`: no parent and no change request;
- `refinement`: parent required and a nonblank bounded change request required;
- `restore`: parent required and no change request.

Add `Project.active_version_id`, `GenerationRun.source_version_id`,
`GenerationRun.change_request`, and nullable
`PublicationRelease.project_version_id`. Use composite membership FKs so a
version cannot point to a run/artifact/parent from another project and an
active/source pointer cannot cross projects. Before versioned publication can
be enabled, add and test an equivalent release-level same-project membership
constraint; an application-only check is insufficient.

- [ ] **Step 4: Implement migration `0016_project_versions`**

Set:

```python
revision = "0016_project_versions"
down_revision = "0015_yookassa_recurring_foundation"
```

The upgrade order is:

1. add membership unique constraints to `generation_runs(project_id, id)` and `generation_artifacts(run_id, id)`;
2. create `project_versions` and indexes on `project_id`, `run_id`, `artifact_id`, and `parent_version_id`;
3. add nullable `projects.active_version_id`, `generation_runs.source_version_id`, `generation_runs.change_request`, and `publication_releases.project_version_id`;
4. backfill at most one `initial` version per existing project, choosing the exact current artifact by this priority: matching `projects.active_run_id + active_revision`; otherwise the latest accepted/verified artifact of the active run; otherwise the active publication release artifact;
5. set `projects.active_version_id` to that row;
6. leave every existing publication release with
   `project_version_id IS NULL`; do not mutate or relink manifest-v1 history;
7. add deferred membership FKs after the backfill.

The downgrade removes only the 0016 columns, FKs, indexes, constraints, and table in dependency order. It does not delete artifacts or releases before dropping the new nullable linkage.

- [ ] **Step 5: Prove the PostgreSQL backfill ignores intermediate artifacts**

In the PostgreSQL integration test, upgrade a disposable database to
`0015_yookassa_recurring_foundation`; seed one project with three stage
artifacts, an active revision, one active manifest-v1 release and one historical
manifest-v1 release; upgrade to head; assert exactly one project version exists
and it points to the active exact artifact. Assert both immutable releases
remain intact with `project_version_id IS NULL`, and resolve the active stable
embed through the real verifier after upgrade to prove it did not become
`ReleaseCorrupt`.

Run:

```powershell
python -m pytest tests/saas_cases/test_project_versions_migration.py -q
```

Expected: PASS; when `KAIGO_TEST_POSTGRES_URL` is configured, the real PostgreSQL upgrade/backfill/downgrade case also passes.

- [ ] **Step 6: Update the production schema fingerprint**

Upgrade the repository’s disposable PostgreSQL schema to head, run the existing `inspect_schema` plus `_schema_fingerprint` helper, and add the emitted literal under `"0016_project_versions"` in `EXPECTED_VERSIONED_SCHEMA_FINGERPRINTS`. Update head assertions to `0016_project_versions` without deleting the fingerprints for `0014` or `0015`.

Run:

```powershell
python -m pytest tests/deployment_cases/test_saas_production_contract.py -k "alembic_has_one_production_head or exact_fingerprint or postgres_preflight" -q
```

Expected: PASS with a single production head and exact fingerprint coverage for every revision.

- [ ] **Step 7: Commit the schema slice**

```powershell
git add app/saas/models.py migrations/versions/0016_project_versions.py tests/saas_cases/test_project_versions_migration.py tests/saas_cases/test_schema.py scripts/preflight_saas_schema.py tests/deployment_cases/test_saas_production_contract.py
git commit -m "feat: add immutable project version schema"
```

### Task 2: Build owner-scoped version history and exact plan cloning

**Files:**
- Create: `app/projects/versions.py`
- Create: `tests/saas_cases/test_project_versions.py`
- Modify: `app/patterns/repository.py`
- Modify: `tests/saas_cases/test_pattern_repository.py`

- [ ] **Step 1: Write failing version-service tests**

Seed two tenants, completed runs, exact final artifacts, and persisted composition plans. Test these contracts:

```python
versions = await service.list_owned(
    project_id,
    actor_user_id=owner_id,
    tenant_id=tenant_id,
)
assert [item.ordinal for item in versions] == [2, 1]
assert all(item.project_id == project_id for item in versions)
```

- cross-owner and cross-tenant lookups raise `ProjectVersionNotFound` rather than revealing existence;
- list order is project ordinal descending and contains metadata only, not HTML/CSS/JS/provenance;
- `restore()` creates a new immutable `restore` row that points to the target’s exact run/artifact, uses the target as `parent_version_id`, and advances the project compatibility pointers;
- stale `expected_active_version_id` raises `ProjectVersionConflict` without inserting a row;
- repeating the same restore `idempotency_key` returns the same row, while reusing the key for a different target raises `ProjectVersionConflict`.

- [ ] **Step 2: Write the failing exact-plan clone test**

Call:

```python
cloned = await PatternRepository(database).clone_plan(
    source_run_id=source_run_id,
    target_run_id=refinement_run_id,
)
```

Assert a new plan ID and new item IDs, but the same `pattern_version_id`, slot, parameters, reason, schema version, registry digest, and implementation hashes. Deactivate or change the in-memory builtin registry before loading the clone and prove cloning uses persisted rows, not current registry resolution.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_project_versions.py tests/saas_cases/test_pattern_repository.py -k "version or clone_plan" -q
```

Expected: FAIL on the missing module and `clone_plan` method.

- [ ] **Step 4: Implement the service boundaries**

In `app/projects/versions.py` define:

```python
class ProjectVersionError(RuntimeError): ...
class ProjectVersionNotFound(ProjectVersionError): ...
class ProjectVersionConflict(ProjectVersionError): ...
class ProjectVersionNotRefinable(ProjectVersionError): ...
class ProjectBusy(ProjectVersionError): ...

@dataclass(frozen=True, slots=True)
class ProjectVersionSnapshot:
    id: UUID
    project_id: UUID
    ordinal: int
    run_id: UUID
    artifact_id: UUID
    artifact_revision: int
    parent_version_id: UUID | None
    kind: str
    change_request: str | None
    refinable: bool
    created_at: datetime
```

`ProjectVersionService` owns session/transaction boundaries for list, restore, and enqueue operations. A small `ProjectVersionRepository` accepts an existing `AsyncSession` for worker finalization. Allocate ordinals only while the project row is locked and return database constraint collisions as domain conflicts, not 500s.

Implement `PatternRepository.clone_plan()` as row cloning inside the caller transaction. Do not call `load_builtin_registry()` to reinterpret an old plan.

- [ ] **Step 5: Run the service tests and verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_project_versions.py tests/saas_cases/test_pattern_repository.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the repository slice**

```powershell
git add app/projects/versions.py app/patterns/repository.py tests/saas_cases/test_project_versions.py tests/saas_cases/test_pattern_repository.py
git commit -m "feat: persist project version lineage"
```

### Task 3: Enqueue refinement as a durable, idempotent worker run

**Files:**
- Modify: `tests/saas_cases/test_project_versions.py`
- Modify: `app/projects/versions.py`

- [ ] **Step 1: Write failing durable-enqueue tests**

Exercise:

```python
run = await service.enqueue_refinement(
    project_id,
    source_version_id=active_version_id,
    expected_active_version_id=active_version_id,
    change_request="Сделай приветствие короче",
    idempotency_key="refine-short-greeting",
    actor_user_id=owner_id,
    tenant_id=tenant_id,
)
```

Assert:

- the source version must be the active version and must have an accepted/verified artifact plus a persisted composition plan;
- change request is stripped, nonblank, NUL-free, and at most 2,000 characters;
- another queued/running project run returns `ProjectBusy`;
- the new `GenerationRun` is `mode="express"`, `state="queued"`, `last_completed_stage="conversation"`, `source_version_id` is exact, and `change_request` is durable;
- `run.created` contains the complete `BuilderRequest` with the source request/reference context and the bounded `ПОЖЕЛАНИЕ ПОЛЬЗОВАТЕЛЯ` suffix;
- the source composition plan is cloned to the new run in the same transaction;
- no `trial.reserve` ledger row is created, so terminal settlement becomes `not_applicable`;
- same key plus same source/request returns the same run; same key plus changed source/request raises `ProjectVersionConflict`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_project_versions.py -k "enqueue_refinement" -q
```

Expected: FAIL because no durable refinement enqueue exists.

- [ ] **Step 3: Implement minimal enqueue behavior**

Build the refinement request from the source run’s durable `run.created.payload["request"]`, not from mutable frontend state. Use the legacy 12,000-character total brief bound but implement it in `app/projects/versions.py`; never call the legacy orchestrator. Insert the run, `run.created` event, and cloned composition plan atomically, then update only `projects.active_run_id` and `projects.status="queued"`. Preserve `active_version_id` and `active_revision` until terminal CAS succeeds.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2; expect all selected tests to pass.

- [ ] **Step 5: Commit**

```powershell
git add app/projects/versions.py tests/saas_cases/test_project_versions.py
git commit -m "feat: enqueue durable project refinements"
```

### Task 4: Seed the worker from the exact version and materialize terminal versions atomically

**Files:**
- Create: `tests/builder_lab_cases/test_project_version_worker.py`
- Modify: `builder_lab/worker.py`
- Modify: `app/projects/versions.py`
- Modify: `app/projects/serializers.py`

- [ ] **Step 1: Write failing restart-safe seed tests**

Create a refinement run, dispose the first engine/session, construct a new `PostgresWorkerQueue`, claim the run, and call `stage_input()`. Assert:

```python
assert claim.next_stage == "motion_polish"
assert stage_input.previous_artifact.to_dict() == source_artifact.to_dict()
assert stage_input.context["composition_plan"] == source_plan.to_dict()
assert stage_input.context["selected_direction"]["art_direction"] == source_artifact.art_direction
```

Also assert the exact source artifact is loaded through `ProjectVersion.artifact_id`, not by “latest artifact in run”.

- [ ] **Step 2: Write failing terminal/CAS tests**

Cover four independent cases:

1. Initial durable run completion creates exactly one `initial` version and activates it; its intermediate artifacts create no versions.
2. Refinement completion creates one `refinement` version with the source parent and change request, then atomically updates `active_version_id`, `active_run_id`, `active_revision`, and `status`.
3. If a restore changes `active_version_id` before refinement completion, the refinement version is still inserted but the project pointers and publication pointer remain unchanged; a `project.version_activation_conflict` event is recorded.
4. Worker failure/cancellation leaves the old active version and active publication release untouched.

Replay a staged final result after worker restart and assert one version row and one `project.version_created` event.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_project_version_worker.py -q
```

Expected: FAIL because `stage_input()` knows only same-run artifacts and final checkpoints do not create versions.

- [ ] **Step 4: Add the durable refinement seed**

When `GenerationRun.source_version_id` is present, `stage_input()` must:

- join the source version to its exact artifact and verify same-project membership;
- load the cloned composition plan for the refinement run;
- create the deterministic “preserve accepted direction” `DirectionProposal` from the source artifact;
- return that artifact and context to the existing `DurableStageHandler`.

The existing handler then executes only `motion_polish` because `last_completed_stage="conversation"`. It continues through the existing model router, visual gate, staged result, lease fence, and checkpoint code.

- [ ] **Step 5: Add atomic terminal version materialization**

At the final checkpoint, after the final artifact and pattern outcomes exist but before the transaction commits, call:

```python
activation = await ProjectVersionRepository(database).materialize_completed_run(
    run=run,
    artifact=final_artifact,
    now=now,
)
```

For refinement, always persist the history row, then activate with a guarded update requiring both the expected source version and the current run. For initial runs, activate only when `active_version_id IS NULL` and `active_run_id` is the completing run. Emit public-safe fields only: `version_id`, `ordinal`, `kind`, and `activated`. Extend the existing event allowlist or the typed registry delivered by migration 0014; do not expose the change request or builder request through SSE.

- [ ] **Step 6: Run focused and adjacent worker tests and verify GREEN**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_project_version_worker.py tests/builder_lab_cases/test_worker.py tests/saas_cases/test_pattern_worker_persistence.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add builder_lab/worker.py app/projects/versions.py app/projects/serializers.py tests/builder_lab_cases/test_project_version_worker.py
git commit -m "feat: finalize durable refinements with version CAS"
```

### Task 5: Expose owner-scoped history, refinement, and restore HTTP contracts

**Files:**
- Create: `tests/saas_cases/test_project_version_routes.py`
- Modify: `app/projects/routes.py`
- Modify: `app/projects/serializers.py`
- Modify: `app/config.py`
- Modify: `tests/saas_cases/test_config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Write failing feature-flag and route tests**

Register and test:

```text
GET  /api/projects/{project_id}/versions
POST /api/projects/{project_id}/versions/{version_id}/refine
POST /api/projects/{project_id}/versions/{version_id}/restore
```

The list payload is:

```json
{
  "active_version_id": "uuid-or-null",
  "versions": [
    {
      "id": "uuid",
      "project_id": "uuid",
      "ordinal": 2,
      "run_id": "uuid",
      "artifact_id": "uuid",
      "artifact_revision": 6,
      "parent_version_id": "uuid-or-null",
      "kind": "refinement",
      "change_request": "bounded owner-visible text",
      "refinable": true,
      "created_at": "ISO-8601"
    }
  ]
}
```

Refine requires `Idempotency-Key`, CSRF, verified OAuth, exact JSON keys `change_request` and `expected_active_version_id`, and returns the serialized run with `202`. Restore requires the same protections, exact `expected_active_version_id`, and returns the serialized new version with `201` or `200` on replay.

Test `401` unauthenticated, `403` CSRF/unverified, owner-safe `404`, `400` invalid/oversized/unknown fields, `409` stale source/busy/idempotency conflict, and flag-off `404`. GET is authenticated but needs no CSRF.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_project_version_routes.py tests/saas_cases/test_config.py -q
```

Expected: FAIL because the flag and routes are absent.

- [ ] **Step 3: Add the rollout flag**

Add `project_versions_enabled: bool = False` to `AppConfig` and load `KAIGO_PROJECT_VERSIONS_ENABLED` with the existing strict flag parser. Add it to `.env.example` and the app service in `docker-compose.yml` with default `false`. Do not add any canary cookie, project ID, version ID, stable key, or TLS secret to application environment.

- [ ] **Step 4: Implement thin route handlers**

Keep parsing/auth/CSRF/status mapping in `app/projects/routes.py` and all transaction logic in `ProjectVersionService`. Reuse `_idempotency_key()`. Reject unknown JSON keys and bodies above 4 KiB before mutation. Do not proxy to `/builder/api/runs/{id}/refine` and do not read `ORCHESTRATOR_KEY`.

Add `active_version_id` to `serialize_project()` without removing `active_run` or `active_revision` compatibility fields.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_project_version_routes.py tests/saas_cases/test_project_routes.py tests/saas_cases/test_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/projects/routes.py app/projects/serializers.py app/config.py .env.example docker-compose.yml tests/saas_cases/test_project_version_routes.py tests/saas_cases/test_config.py
git commit -m "feat: expose owner project version APIs"
```

### Task 6: Publish exact project versions with release compare-and-swap

**Phase gate:** do not start or enable this task in the first rollout slice.
Before RED tests, amend the schema contract so a versioned release has a
database-enforced same-project membership and the uniqueness rule distinguishes
legacy artifact releases from versioned releases. A restore version may point
to an artifact already used by a legacy/original release; this must either be
supported by partial unique indexes such as one legacy uniqueness key and one
`(publication_id, project_version_id)` versioned key, or restore publication
must remain explicitly rejected. Do not remove the current legacy uniqueness
constraint until its replacement is proven on PostgreSQL.

**Files:**
- Modify: `tests/saas_cases/test_publication.py`
- Modify: `tests/saas_cases/test_schema.py`
- Modify: `app/publication/service.py`
- Modify: `app/widgets/loader.py`

- [ ] **Step 1: Convert the publication fixture to real project versions**

Seed an `initial` and `refinement` version for the two same-revision artifacts. Keep draft and foreign versions for negative cases. Preserve all existing checks for immutable snapshots, private prompt/provenance exclusion, exact-origin normalization, stable key reuse, corruption rejection, public chat capability, rate limits, and trusted runtime behavior.

- [ ] **Step 2: Write failing version-selection and manifest-v2 tests**

Call:

```python
first = await service.publish_version(
    project_id,
    actor_user_id=owner_id,
    tenant_id=tenant_id,
    project_version_id=version_one_id,
    expected_active_release_id=None,
)
second = await service.publish_version(
    project_id,
    actor_user_id=owner_id,
    tenant_id=tenant_id,
    project_version_id=version_two_id,
    expected_active_release_id=first.release_id,
)
```

Assert the release stores `project_version_id`, the manifest has `"version": 2` plus the matching non-secret `project_version_id`, and the runtime fields/checksum remain immutable. Reject a foreign version, a version/artifact/run mismatch, an unpublishable artifact, or a version outside the owned project.

Create a legacy manifest-v1 release with `project_version_id=None` and prove `resolve()` and rollback still validate and serve it.

Add PostgreSQL tests proving:

- a release cannot reference a project version from another project/tenant;
- an original and a restore version over the same artifact cannot collide or
  silently reuse the wrong version linkage;
- every pre-0016 v1 release remains nullable and serviceable.

- [ ] **Step 3: Write failing CAS and replay tests**

Assert:

- two updates based on the same old active release produce exactly one winner and one `PublicationConflict`;
- stale publish cannot change `allowed_domains` or `active_release_id`;
- stale rollback cannot change the active release;
- replaying the already-active same target with the same normalized domains succeeds idempotently even if its original expected pointer is stale;
- a replay that attempts to change domains with a stale expected pointer conflicts;
- first-publish races preserve one stable key and valid release membership.

Use the optional PostgreSQL fixture for the true concurrent race; keep a deterministic sequential stale-pointer test for every environment.

- [ ] **Step 4: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_publication.py -k "version or compare_and_swap or stale or manifest_v2 or legacy_manifest_v1" -q
```

Expected: FAIL because publication selects artifacts directly and does not compare the active pointer.

- [ ] **Step 5: Implement version-selected publication**

Add `PublicationConflict` and `publish_version()`. Preserve `publish()` as the flag-off compatibility method, but share one private manifest/release mutation path. The versioned path locks in the established order `Project -> Publication`, resolves the exact version/artifact, validates entitlement and artifact consistency, normalizes domains, performs the idempotent replay check, then compares `expected_active_release_id` before changing any publication field.

New releases use manifest v2. `_verify_release()` accepts:

- v1 only when the stored release has no version linkage;
- v2 only when manifest `project_version_id` matches the stored linkage;
- the same strict runtime-only artifact keys as today.

Add hidden `data-kaigo-release-id` and `data-kaigo-project-version-id` markers to the outer runtime document for canary observation. Do not change the loader iframe sandbox, generated-widget sandbox, geometry bridge, chat capability, or network ownership.

- [ ] **Step 6: Implement rollback CAS**

Extend rollback with an explicit `expected_active_release_id` in versioned mode. Lock and validate the expected pointer before changing `active_release_id`. Treat “target is already active” as an idempotent replay only after verifying that target’s immutable checksum and membership.

- [ ] **Step 7: Run publication tests and verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_publication.py tests/saas_cases/test_schema.py -q
```

Expected: PASS, including all pre-existing origin/sandbox/chat/corruption tests.

- [ ] **Step 8: Commit**

```powershell
git add app/publication/service.py app/widgets/loader.py tests/saas_cases/test_publication.py tests/saas_cases/test_schema.py
git commit -m "feat: publish project versions with release CAS"
```

### Task 7: Harden publication request bodies and conflict responses

**Files:**
- Modify: `tests/saas_cases/test_publication.py`
- Modify: `app/publication/routes.py`

- [ ] **Step 1: Write failing route-contract tests**

With `KAIGO_PROJECT_VERSIONS_ENABLED=true`, the publish body is exactly:

```json
{
  "project_version_id": "uuid",
  "expected_active_release_id": "uuid-or-null",
  "allowed_domains": ["https://canary.example"]
}
```

The rollback body is exactly:

```json
{
  "target_release_id": "uuid",
  "expected_active_release_id": "uuid"
}
```

Test duplicate keys/unknown keys, malformed UUIDs, booleans, body over 32 KiB, more than 32 origins, stale `409 publication_conflict`, owner-safe `404`, and unchanged `402/403/422` entitlement/validation behavior. Assert every successful response preserves existing fields and adds nullable `project_version_id`.

With the flag false, assert the current artifact/revision body continues to work and version/refinement routes remain hidden. This is the schema-first rollback path, not a second refinement implementation.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_publication.py -k "publish_route or rollback_route or request_body or publication_conflict or feature_flag" -q
```

Expected: FAIL because bodies are unbounded and have no CAS/version fields.

- [ ] **Step 3: Implement strict parsing and safe status mapping**

Replace permissive `request.json()` publication parsing with a bounded raw read, UTF-8 JSON decode, exact-key validation, explicit nullable UUID parsing, and bounded origin-array validation. Map `PublicationConflict` to:

```json
{"error":{"code":"publication_conflict","message":"Publication changed; reload before retrying"}}
```

Do not include current foreign IDs, SQL diagnostics, prompts, domains from another owner, or stack traces. Keep `_public_base_url()` validation before mutation.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_publication.py tests/saas_cases/test_pattern_outcome_publication.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/publication/routes.py tests/saas_cases/test_publication.py
git commit -m "fix: harden publication mutation contracts"
```

### Task 8: Add version/refinement/publication contracts to the frontend API

**Files:**
- Modify: `frontend/src/studio/types.ts`
- Modify: `frontend/src/studio/api.ts`
- Modify: `frontend/src/studio/api.test.ts`

- [ ] **Step 1: Write failing API tests**

Add tests for:

```typescript
await getProjectVersions('project/123');
await refineProjectVersion(
  'project/123',
  'version/2',
  'Сделай приветствие короче',
  'version/2',
  'csrf',
  'refine-key',
);
await restoreProjectVersion(
  'project/123',
  'version/1',
  'version/2',
  'csrf',
  'restore-key',
);
```

Assert URL encoding, `credentials: 'include'`, CSRF, `Idempotency-Key`, and exact JSON bodies. Update publish/rollback tests to assert `project_version_id` and `expected_active_release_id`. Keep all legacy Builder API tests unchanged.

- [ ] **Step 2: Run tests and verify RED**

Run from `frontend`:

```powershell
cmd /c node_modules\.bin\vitest.cmd run src\studio\api.test.ts --pool=forks --maxWorkers=1 --reporter=verbose
```

Expected: FAIL on missing types/functions and old publication payloads.

- [ ] **Step 3: Add exact TypeScript contracts**

Define `SaasProjectVersion`, `ProjectVersionList`, and nullable `project_version_id` on publication release/history. Add `active_version_id` to `SaasProject`. Implement the three version functions with `saasRequestJson`. Keep `refineBuilderRun()` and `requestJson()` isolated to legacy mode.

Change versioned publish and rollback signatures so callers must supply the currently observed active release ID, including explicit `null` for first publish.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2; expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/studio/types.ts frontend/src/studio/api.ts frontend/src/studio/api.test.ts
git commit -m "feat: add project version frontend contracts"
```

### Task 9: Show version history and use durable SaaS refinement

**Files:**
- Create: `frontend/src/studio/ProjectVersionHistory.tsx`
- Create: `frontend/src/studio/ProjectVersionHistory.test.tsx`
- Modify: `frontend/src/studio/useBuilderRun.ts`
- Modify: `frontend/src/studio/StudioPage.tsx`
- Modify: `frontend/src/studio/StudioPage.test.tsx`
- Modify: `frontend/src/studio/SaasStudioFlow.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing component/controller tests**

Test a project with active version 2, historical version 1, and a failed refinement run. Assert:

- history renders `Версия 2` and `Версия 1` with kind/date/change summary;
- the active version is identified, and selecting version 1 changes preview to its exact `run_id + artifact_revision`;
- clicking restore sends the observed active version ID and refreshes project/history;
- submitting a project-mode refinement calls `/api/projects/{project}/versions/{active}/refine`, never `/builder/api/runs/{id}/refine`;
- after a failed/cancelled refinement, the active version’s preview and publication target remain selected;
- after successful terminal SSE, history refreshes and selects the newly active version;
- a `project_version_conflict` refreshes state and shows a retryable Russian message instead of blindly resubmitting;
- legacy `/builder` still calls `refineBuilderRun()` and retains its existing UI behavior.

- [ ] **Step 2: Run tests and verify RED**

Run from `frontend`:

```powershell
cmd /c node_modules\.bin\vitest.cmd run src\studio\ProjectVersionHistory.test.tsx src\studio\StudioPage.test.tsx src\studio\SaasStudioFlow.test.tsx --pool=forks --maxWorkers=1 --reporter=verbose
```

Expected: FAIL because project mode has no version state and deliberately shows only the dialogue-check notice.

- [ ] **Step 3: Separate active operation state from selected version state**

Extend `BuilderRunController` with:

```typescript
versionsAvailable: boolean;
versions: SaasProjectVersion[];
activeVersionId: string | null;
selectedVersion: SaasProjectVersion | null;
previewRunId: string | null;
selectedArtifact: WidgetArtifact | null;
selectVersion: (versionId: string) => Promise<void>;
restoreVersion: (versionId: string) => Promise<void>;
```

The current `snapshot/runId/events` continue to describe the active operation for progress and SSE. `previewRunId/selectedArtifact` describe the user-selected immutable result. Fetch the exact artifact through the existing owner-scoped `GET /api/artifacts/{artifact_id}` route.

Probe the version-list endpoint on hydration. A flag-off `404 feature_disabled` sets `versionsAvailable=false` without turning a recoverable project into an error.

- [ ] **Step 4: Implement the accessible history UI**

Render `ProjectVersionHistory` only in project mode when the feature is available. Use buttons with `aria-current` for the selected/active state, human labels for `initial/refinement/restore`, bounded change text, and an explicit restore action for inactive versions. Disable restore/refine while another mutation is pending.

Replace the project-mode dialogue-only block with the real refinement form only when the selected version is active and `refinable`. Keep the dialogue explanation next to the form so users understand preview chat does not itself edit the artifact.

Pass `previewRunId` and the selected artifact revision to `StudioPreview`. Do not change its sandbox or chat bridge.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the command from Step 2; expect PASS.

- [ ] **Step 6: Run accessibility contracts**

Run:

```powershell
cmd /c node_modules\.bin\vitest.cmd run src\studio\StudioAccessibilityContracts.test.tsx src\studio\ProjectVersionHistory.test.tsx --pool=forks --maxWorkers=1 --reporter=verbose
```

Expected: PASS with keyboard-accessible selection/restore and no duplicate labels.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/studio/ProjectVersionHistory.tsx frontend/src/studio/ProjectVersionHistory.test.tsx frontend/src/studio/useBuilderRun.ts frontend/src/studio/StudioPage.tsx frontend/src/studio/StudioPage.test.tsx frontend/src/studio/SaasStudioFlow.test.tsx frontend/src/styles.css
git commit -m "feat: add Studio project version history"
```

### Task 10: Publish the selected version and recover safely from CAS conflicts

**Files:**
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/studio/UpgradeGate.test.tsx`
- Modify: `frontend/src/studio/StudioPage.tsx`

- [ ] **Step 1: Write failing publication UI tests**

Assert first publish sends:

```typescript
{
  project_version_id: selectedVersionId,
  expected_active_release_id: null,
  allowed_domains: ['https://example.com'],
}
```

Assert update sends the currently loaded release ID, rollback sends both target and current release IDs, and selecting an old project version publishes that exact version without restoring it first.

Simulate `409 publication_conflict`. Assert the component immediately reloads `GET /api/projects/{id}/publication`, does not mutate its local release stack optimistically, and tells the user the publication changed in another session.

- [ ] **Step 2: Run tests and verify RED**

Run from `frontend`:

```powershell
cmd /c node_modules\.bin\vitest.cmd run src\studio\UpgradeGate.test.tsx src\studio\StudioPage.test.tsx --pool=forks --maxWorkers=1 --reporter=verbose
```

Expected: FAIL because `UpgradeGate` takes artifact IDs and has no expected pointer.

- [ ] **Step 3: Implement selected-version publication**

Replace `artifactId` with `projectVersionId` in `UpgradeGateProps` when versions are enabled. Keep artifact/revision fallback only for flag-off compatibility. Derive every expected pointer from the last authoritative publication GET/POST response. On any conflict, reload; never auto-retry a mutation with a new pointer.

Display project version ordinal separately from artifact revision so same-revision artifacts from different runs are not conflated.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2; expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/studio/UpgradeGate.tsx frontend/src/studio/UpgradeGate.test.tsx frontend/src/studio/StudioPage.tsx
git commit -m "feat: publish selected Studio versions safely"
```

### Task 11: Extend local durable acceptance without treating it as external proof

**Files:**
- Modify: `scripts/run_saas_browser_acceptance.py`
- Modify: `tests/saas_cases/test_browser_acceptance_harness.py`
- Modify: `scripts/smoke_saas_foundation.py`
- Modify: `tests/saas_cases/test_saas_acceptance.py`
- Modify: `tests/saas_cases/test_saas_smoke_script.py`
- Modify: `frontend/e2e/fixtures/builder.ts`
- Modify: `frontend/e2e/studio.spec.ts`

- [ ] **Step 1: Write failing composed-journey assertions**

Update deterministic fixtures to prove:

```text
initial run -> version 1 -> durable refinement run -> version 2
-> publish version 1 -> publish version 2 with CAS -> rollback release 1 with CAS
-> restore version 1 -> active project version 3 (kind restore)
```

Assert the stable embed key never changes, the runtime release marker changes on update and returns on rollback, and a forced failed refinement keeps the prior active project version and public release.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_browser_acceptance_harness.py tests/saas_cases/test_saas_acceptance.py tests/saas_cases/test_saas_smoke_script.py -q
```

Expected: FAIL because acceptance still publishes artifacts and has no version/refinement evidence.

- [ ] **Step 3: Update the local harnesses**

Enable the feature flag in test-only app config. Extend diagnostics with version IDs/ordinals but never CSRF, session, cookie, prompt, or secret. Keep the fake stage handler/provider network-free. Update smoke evidence with:

```json
{
  "versions": {
    "initial_created": true,
    "refinement_durable": true,
    "failed_refinement_preserved_active": true,
    "restore_created_new_ordinal": true
  },
  "publication": {
    "stable_key_preserved": true,
    "update_used_cas": true,
    "rollback_used_cas": true,
    "rollback_restored_first_release": true
  }
}
```

- [ ] **Step 4: Update browser fixture and Studio E2E**

Mock the version endpoints and assert history selection, durable refine request, failed-refinement preview preservation, selected-version publication, and stale publication refresh. Keep the existing SaaS sandbox preview/chat assertions and mobile overflow checks.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_browser_acceptance_harness.py tests/saas_cases/test_saas_acceptance.py tests/saas_cases/test_saas_smoke_script.py -q
cmd /c npm run test:e2e -- --grep "Studio"
```

Run the Playwright command from `frontend`. Expected: PASS. This remains loopback/mocked-origin evidence and does not satisfy the external canary criterion.

- [ ] **Step 6: Commit**

```powershell
git add scripts/run_saas_browser_acceptance.py tests/saas_cases/test_browser_acceptance_harness.py scripts/smoke_saas_foundation.py tests/saas_cases/test_saas_acceptance.py tests/saas_cases/test_saas_smoke_script.py frontend/e2e/fixtures/builder.ts frontend/e2e/studio.spec.ts
git commit -m "test: cover durable version publication journey"
```

### Task 12: Package a credential-free external HTTPS canary origin

**Files:**
- Create: `deploy/publication-canary/index.html`
- Create: `deploy/publication-canary/canary.js`
- Create: `deploy/nginx/kaigo-publication-canary.conf`
- Create: `tests/deployment_cases/test_publication_canary_contract.py`

- [ ] **Step 1: Write failing static/nginx contract tests**

Assert the package defines two real HTTPS virtual hosts:

- `canary.kaigo.space` — the origin supplied to the publication allowlist;
- `denied-canary.kaigo.space` — identical static shell used to prove exact-origin denial.

Assert:

- HTTP redirects to HTTPS;
- TLS paths are explicit operator-provisioned files, not committed keys;
- static responses set no cookie and use `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, restrictive `Permissions-Policy`, and a CSP that allows scripts/frames only from `https://kaigo.space`;
- `canary.js` accepts only `^[A-Za-z0-9_-]{20,128}$` as the public stable key and constructs only `https://kaigo.space/embed/{key}.js`;
- static files contain no `Authorization`, cookie value, CSRF token, API key, OAuth secret, YooKassa secret, local/session storage, or generated customer data.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/deployment_cases/test_publication_canary_contract.py -q
```

Expected: FAIL because the canary package is absent.

- [ ] **Step 3: Implement the minimal static canary**

`index.html` contains only a heading/status container and `<script src="/canary.js" defer></script>`. `canary.js` reads the public `key` query parameter, validates it, creates one classic script element for the fixed Kaigo embed origin, and reports only load/error status. It does not call owner APIs, persist values, read cookies, or accept a configurable script origin.

The nginx config serves the same immutable files for allowed and denied hosts, has no proxy locations, and never receives application secrets. Preserve certificate acquisition/renewal outside Git.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2; expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add deploy/publication-canary/index.html deploy/publication-canary/canary.js deploy/nginx/kaigo-publication-canary.conf tests/deployment_cases/test_publication_canary_contract.py
git commit -m "feat: package external publication canary"
```

### Task 13: Add a fail-closed real external HTTPS acceptance runner

**Files:**
- Create: `scripts/run_publication_https_canary.py`
- Create: `tests/saas_cases/test_publication_https_canary.py`
- Modify: `docs/SAAS_PRODUCTION_RUNBOOK.md`
- Modify: `frontend/e2e/README.md`

- [ ] **Step 1: Write failing runner contract tests**

Test pure configuration/secret/evidence helpers before browser code:

- all three origins (`app`, allowed canary, denied canary) must be absolute HTTPS origins with no credentials/path/query/fragment;
- allowed and denied origins must differ exactly and both must differ from the app origin;
- project and two distinct version IDs must be UUIDs;
- owner cookie is accepted only from `KAIGO_CANARY_COOKIE_FILE`, never from argv or a literal environment value;
- on POSIX the cookie file must be regular, non-symlink, owned by the current user, and have no group/other permission bits;
- redirects from owner API requests are rejected rather than forwarding the Cookie header;
- evidence serialization contains no cookie, CSRF, capability, Authorization header, raw HTML, prompt, or response text;
- missing inputs exit nonzero with machine-readable `status="blocked"` rather than skip/pass.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_publication_https_canary.py -q
```

Expected: FAIL because the runner is absent.

- [ ] **Step 3: Implement owner API CAS flow**

The runner reads:

```text
KAIGO_CANARY_APP_ORIGIN
KAIGO_CANARY_ALLOWED_ORIGIN
KAIGO_CANARY_DENIED_ORIGIN
KAIGO_CANARY_PROJECT_ID
KAIGO_CANARY_BASELINE_VERSION_ID
KAIGO_CANARY_CANDIDATE_VERSION_ID
KAIGO_CANARY_COOKIE_FILE
KAIGO_CANARY_EVIDENCE_FILE
```

Use one owner-only aiohttp session for `/api/auth/session`, version/publication GETs, publish baseline, publish candidate with the returned active release ID, and rollback with the candidate release ID. Set `allow_redirects=False` on every authenticated request and reject a Location header.

Use a separate clean Playwright Chromium context with no storage state and service workers blocked. Never copy the owner cookie/CSRF into that context.

Before any publish/update/rollback mutation, fail closed unless the dedicated
non-customer canary user has a currently active, unexpired test subscription.
The runner must expose this as an explicit entitlement preflight result. It
must never bypass or weaken `PublicationService`'s subscription gate.

Lazy-import Playwright inside the external runner so production imports remain valid without browser tooling. Execute the runner from the existing builder-lab/browser-tools environment installed from `requirements.builder-lab.txt`; do not add Playwright or Chromium to `requirements.txt` or the production application image.

- [ ] **Step 4: Implement browser assertions against the real origins**

For the allowed origin:

1. open `https://canary.kaigo.space/?key={stable_key}`;
2. assert `document.cookie === ""` and both Web Storage objects are empty;
3. assert no browser request contains `Cookie` or `Authorization`;
4. locate `iframe[data-kaigo-widget-key]`, open launcher, close it, reopen it, send one bounded canary chat message, and wait for an assistant reply;
5. assert the outer runtime’s `data-kaigo-release-id` is baseline;
6. publish candidate with CAS, reload the same stable URL, and assert the release marker is candidate;
7. rollback with CAS, reload, and assert the marker is baseline again.

For the denied origin, load the same key and assert no usable launcher appears; capture the runtime response CSP and prove `frame-ancestors` contains the allowed origin and not the denied origin.

Record booleans, public IDs/checksums, origins, timestamps, and response statuses only. Do not record chat text or capability values.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_publication_https_canary.py tests/deployment_cases/test_publication_canary_contract.py -q
```

Expected: PASS without network access; these are runner/package contract tests, not the external acceptance itself.

- [ ] **Step 6: Document the schema-first external gate**

In `docs/SAAS_PRODUCTION_RUNBOOK.md` document this exact order:

1. backup;
2. apply migrations through `0016_project_versions` while `KAIGO_PROJECT_VERSIONS_ENABLED=false`;
3. deploy app/worker and run readiness plus existing production smoke;
4. provision both static HTTPS canary hosts and verify certificate/headers;
5. enable `KAIGO_PROJECT_VERSIONS_ENABLED=true`;
6. create/use a dedicated non-customer canary project with two reviewed versions;
7. place the real browser Cookie header in a temporary owner-only file without echoing it;
8. run the external runner;
9. remove the cookie file and retain only sanitized JSON evidence;
10. disable the flag or roll back app code using the existing schema-forward procedure if the canary fails.

State explicitly: a skipped, blocked, loopback, HTTP, self-signed, or mocked run is not acceptance.

- [ ] **Step 7: Commit**

```powershell
git add scripts/run_publication_https_canary.py tests/saas_cases/test_publication_https_canary.py docs/SAAS_PRODUCTION_RUNBOOK.md frontend/e2e/README.md
git commit -m "test: add external HTTPS publication canary"
```

### Task 14: Verify locally, then run the real external acceptance as a separate authorized operation

**Files:**
- Verify all files above
- Do not alter unrelated worktree files

- [ ] **Step 1: Verify migration and schema**

Run:

```powershell
python -m alembic heads
python -m pytest tests/saas_cases/test_project_versions_migration.py tests/saas_cases/test_schema.py tests/deployment_cases/test_saas_production_contract.py -q
```

Expected: one head, `0016_project_versions`, and all tests pass. Run the PostgreSQL-marked migration/concurrency tests with the repository’s configured disposable `KAIGO_TEST_POSTGRES_URL` before release.

- [ ] **Step 2: Verify backend project/worker/publication behavior**

Run:

```powershell
python -m pytest tests/saas_cases/test_project_versions.py tests/saas_cases/test_project_version_routes.py tests/builder_lab_cases/test_project_version_worker.py tests/saas_cases/test_project_routes.py tests/saas_cases/test_publication.py tests/saas_cases/test_publication_migration.py tests/saas_cases/test_pattern_outcome_publication.py -q
```

Expected: PASS.

- [ ] **Step 3: Verify composed/local acceptance**

Run:

```powershell
python -m pytest tests/saas_cases/test_browser_acceptance_harness.py tests/saas_cases/test_saas_acceptance.py tests/saas_cases/test_saas_smoke_script.py tests/saas_cases/test_publication_https_canary.py tests/deployment_cases/test_publication_canary_contract.py -q
```

Expected: PASS, while still explicitly reporting that external acceptance has not run.

- [ ] **Step 4: Verify all backend regressions**

Run:

```powershell
python -m pytest tests/saas_cases tests/builder_lab_cases tests/deployment_cases -q
```

Expected: PASS.

- [ ] **Step 5: Verify frontend**

Run from `frontend`:

```powershell
cmd /c npm test -- --run
cmd /c npm run typecheck
cmd /c npm run lint
cmd /c npm run build
cmd /c npm run test:e2e -- --grep "Studio"
```

Expected: exit code 0 for every command; the known Vite chunk-size warning remains non-blocking.

- [ ] **Step 6: Audit the no-reconnection and security invariants**

Run:

```powershell
rg -n "BuilderOrchestrator\.refine|ORCHESTRATOR_KEY|/builder/api/runs/.*/refine" app frontend/src/studio/useBuilderRun.ts
rg -n "allow-same-origin|document\.cookie|localStorage|sessionStorage|Authorization|YOOKASSA_SECRET_KEY|OAUTH_CLIENT_SECRET|API_KEY" deploy/publication-canary scripts/run_publication_https_canary.py
git diff --check
```

Expected:

- no SaaS app/controller match reconnects legacy refinement;
- no `allow-same-origin` appears in publication loader/runtime;
- canary static assets contain no storage/cookie/secret behavior;
- any secret-name occurrences in the runner are limited to rejection/redaction tests, never values;
- `git diff --check` is clean.

- [ ] **Step 7: Run the real external canary only after explicit rollout authorization**

On the operations host, create the owner-only cookie file interactively, set the eight `KAIGO_CANARY_*` variables listed in Task 13, then run:

```powershell
python scripts/run_publication_https_canary.py
```

Expected sanitized evidence:

```json
{
  "status": "passed",
  "https": true,
  "allowed_origin": {
    "launcher": true,
    "open_close": true,
    "chat": true,
    "no_credentials": true
  },
  "publication": {
    "stable_url": true,
    "candidate_after_reload": true,
    "rollback_after_reload": true,
    "cas": true
  },
  "denied_origin": {
    "blocked": true
  }
}
```

Delete the cookie file in a `finally`/shell cleanup path. Do not mark the feature complete if this command is blocked, skipped, run on loopback, or returns nonzero.

- [ ] **Step 8: Final scoped review**

Run:

```powershell
git status --short
git diff --stat
git log --oneline --max-count=14
```

Confirm only the files listed in this plan are part of these commits, unrelated pre-existing changes remain untouched, no deployment was performed during plan creation, and the final handoff distinguishes local verification from the separately authorized real HTTPS result.
