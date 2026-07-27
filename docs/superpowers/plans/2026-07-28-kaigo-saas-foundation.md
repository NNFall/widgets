# Kaigo SaaS Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить Builder Lab в durable SaaS-контур с OAuth, безопасными сессиями, одним бесплатным express-build, учётом моделей/стоимости, восстановлением Studio, публикацией и базовым billing.

**Architecture:** Существующий aiohttp/SQLAlchemy/PostgreSQL backend остаётся основой. Длительные build-задачи переходят на PostgreSQL queue/checkpoints, модели вызываются через role-based router, а React Studio получает единый project/run API и SSE. Каждый этап вводится вертикальным TDD-срезом и сохраняет обратную совместимость старых routes.

**Tech Stack:** Python 3.11+, aiohttp, SQLAlchemy async, PostgreSQL 15, Alembic, aiohttp-session-compatible server storage, OAuth 2.0/OIDC + PKCE, React 19, TypeScript 6, SSE, pytest, Vitest, Playwright, Docker Compose, nginx.

---

## Карта файлов

- `app/saas/models.py` — SaaS ORM-сущности без разрастания legacy `app/db/models.py`.
- `app/saas/repositories.py` — транзакционные repositories для identity, projects, runs, ledger и publication.
- `app/auth/session_storage.py` — opaque server-side cookie storage.
- `app/auth/oauth.py` — provider-neutral OAuth contracts и Google/Yandex adapters.
- `app/auth/routes.py` — login/callback/logout/session API и claim draft.
- `app/projects/routes.py` — project/run/event endpoints.
- `app/models/router.py` — role policies, adapters, fallback и model-call audit.
- `app/billing/service.py` — trial/credit/subscription rules.
- `app/billing/providers/yookassa.py` — YooKassa checkout/webhook adapter.
- `app/publication/service.py` — immutable releases, stable embed и rollback.
- `builder_lab/store_protocol.py` — интерфейс run storage.
- `builder_lab/postgres_store.py` — durable adapter существующего orchestrator.
- `builder_lab/worker.py` — lease/heartbeat/checkpoint worker.
- `frontend/src/auth/` — session и OAuth gate.
- `frontend/src/studio/` — composer→workspace, durable restore, SSE и publish.
- `migrations/versions/0003_saas_foundation.py` — единая начальная SaaS-миграция.

### Task 1: Конфигурация и криптографические примитивы

**Files:**
- Modify: `app/config.py`
- Create: `app/auth/tokens.py`
- Test: `tests/saas_cases/test_config.py`
- Test: `tests/saas_cases/test_tokens.py`

- [ ] **Step 1: написать падающие тесты**

```python
def test_session_token_is_random_and_only_hash_is_persisted():
    raw, digest = issue_token()
    assert raw != digest
    assert token_digest(raw) == digest
    assert len(raw) >= 43

def test_oauth_requires_public_base_url_in_public_mode(monkeypatch):
    monkeypatch.setenv("KAIGO_PUBLIC_AUTH_ENABLED", "true")
    monkeypatch.delenv("KAIGO_PUBLIC_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="KAIGO_PUBLIC_BASE_URL"):
        load_config()
```

- [ ] **Step 2: запустить RED**

Run: `python -m pytest tests/saas_cases/test_config.py tests/saas_cases/test_tokens.py -q`  
Expected: FAIL because `app.auth.tokens` and new config fields do not exist.

- [ ] **Step 3: реализовать минимальный contract**

```python
def issue_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, token_digest(raw)

def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

`AppConfig` получает public base URL, cookie name/TTL, OAuth client ids/secrets,
auth enable flag и environment. Секреты никогда не выводятся в repr/log.

- [ ] **Step 4: запустить GREEN и commit**

Run: `python -m pytest tests/saas_cases/test_config.py tests/saas_cases/test_tokens.py -q`  
Expected: PASS.  
Commit: `feat: add SaaS security configuration`

### Task 2: SaaS schema и Alembic migration

**Files:**
- Create: `app/saas/__init__.py`
- Create: `app/saas/models.py`
- Modify: `migrations/env.py`
- Create: `migrations/versions/0003_saas_foundation.py`
- Test: `tests/saas_cases/test_schema.py`

- [ ] **Step 1: написать schema contract**

```python
EXPECTED = {
    "user_identities", "auth_sessions", "oauth_states", "anonymous_drafts",
    "projects", "generation_runs", "generation_events",
    "generation_artifacts", "artifact_evidence", "model_calls",
    "usage_ledger", "trial_entitlements", "subscriptions",
    "payment_attempts", "payment_webhook_events",
    "publications", "publication_releases",
}

def test_saas_tables_are_registered():
    assert EXPECTED <= set(Base.metadata.tables)
```

- [ ] **Step 2: запустить RED**

Run: `python -m pytest tests/saas_cases/test_schema.py -q`  
Expected: FAIL listing missing tables.

- [ ] **Step 3: добавить focused ORM-модели и ограничения**

Ключевые ограничения:

```python
UniqueConstraint("provider", "provider_subject", name="uq_identity_subject")
UniqueConstraint("run_id", "sequence", name="uq_run_event_sequence")
UniqueConstraint("provider", "provider_event_id", name="uq_payment_event")
CheckConstraint("amount <> 0", name="ck_usage_ledger_nonzero")
```

`users.password_hash` становится nullable для OAuth-only accounts. Все public ids
используют UUID, money хранится целым числом minor units, payload — JSONB.

- [ ] **Step 4: проверить upgrade/downgrade**

Run: `python -m pytest tests/saas_cases/test_schema.py -q`  
Run: `alembic upgrade head` на disposable PostgreSQL, затем `alembic downgrade 0002_widget_settings`.  
Expected: обе операции успешны, повторный upgrade создаёт все таблицы.

- [ ] **Step 5: commit**

Commit: `feat: add SaaS persistence schema`

### Task 3: Opaque server-side sessions

**Files:**
- Create: `app/auth/session_storage.py`
- Create: `app/auth/middleware.py`
- Modify: `app/server.py`
- Modify: `app/db/session.py`
- Test: `tests/saas_cases/test_session_storage.py`

- [ ] **Step 1: написать lifecycle-тесты**

```python
async def test_login_rotation_replaces_cookie_and_revokes_old_session(client):
    anonymous = await client.get("/api/auth/session")
    old_cookie = anonymous.cookies["kaigo_session"].value
    authenticated = await complete_fake_oauth(client)
    new_cookie = authenticated.cookies["kaigo_session"].value
    assert new_cookie != old_cookie
    assert await session_is_revoked(old_cookie)

async def test_database_never_contains_raw_cookie(client):
    response = await client.get("/api/auth/session")
    raw = response.cookies["kaigo_session"].value
    assert raw not in await persisted_session_values()
```

- [ ] **Step 2: запустить RED**

Run: `python -m pytest tests/saas_cases/test_session_storage.py -q`  
Expected: FAIL because app still uses `SimpleCookieStorage`.

- [ ] **Step 3: реализовать storage**

```python
class DatabaseSessionStorage(AbstractStorage):
    async def load_session(self, request): ...
    async def save_session(self, request, response, session): ...

SESSION_COOKIE = CookiePolicy(
    secure=True, httponly=True, samesite="Lax", path="/", max_age=1209600
)
```

Storage сохраняет только SHA-256 token hash, JSON payload, expiry/revocation и
вращает token при privilege change. `metadata.create_all` выключается вне tests;
startup проверяет актуальность Alembic revision.

- [ ] **Step 4: GREEN + legacy regression + commit**

Run: `python -m pytest tests/saas_cases/test_session_storage.py tests/deployment_cases -q`  
Expected: PASS.  
Commit: `feat: persist secure browser sessions`

### Task 4: Google и Яндекс OAuth adapters

**Files:**
- Create: `app/auth/oauth.py`
- Test: `tests/saas_cases/test_oauth.py`

- [ ] **Step 1: написать provider-neutral contract tests**

```python
@pytest.mark.parametrize("provider", ["google", "yandex"])
async def test_exchange_verifies_state_pkce_redirect_and_verified_identity(provider):
    profile = await fake_adapter(provider).exchange(valid_callback(provider))
    assert profile.provider == provider
    assert profile.subject
    assert profile.email_verified is True

async def test_unverified_email_is_not_linked_to_existing_user():
    with pytest.raises(UnverifiedIdentity):
        await fake_adapter("google", email_verified=False).exchange(callback())
```

- [ ] **Step 2: RED**

Run: `python -m pytest tests/saas_cases/test_oauth.py -q`  
Expected: import failure.

- [ ] **Step 3: реализовать adapters**

```python
class OAuthProvider(Protocol):
    def authorization_url(self, transaction: OAuthTransaction) -> str: ...
    async def exchange(self, callback: OAuthCallback) -> OAuthIdentity: ...
```

Google проверяет issuer/audience/nonce и verified email. Яндекс получает profile
через официальный user-info endpoint. Оба используют одноразовый state, PKCE,
точный redirect URI, timeouts и redacted errors.

- [ ] **Step 4: GREEN + commit**

Run: `python -m pytest tests/saas_cases/test_oauth.py -q`  
Expected: PASS with fake HTTP transport and no live credentials.  
Commit: `feat: add Google and Yandex OAuth adapters`

### Task 5: Auth routes и anonymous draft claim

**Files:**
- Create: `app/auth/routes.py`
- Create: `app/auth/service.py`
- Modify: `app/server.py`
- Modify: `frontend/src/shared/UrlComposer.tsx`
- Create: `frontend/src/auth/AuthGate.tsx`
- Test: `tests/saas_cases/test_auth_routes.py`
- Test: `frontend/src/auth/AuthGate.test.tsx`

- [ ] **Step 1: написать end-to-end fake OAuth tests**

```python
async def test_draft_survives_oauth_round_trip(client):
    draft = await client.post("/api/drafts", json={"url": "https://example.com", "brief": "Чат продаж"})
    callback = await complete_fake_oauth(client, return_to=f"/studio?draft={draft.id}")
    project = await client.get("/api/projects/current")
    assert project.json()["source_url"] == "https://example.com"

async def test_generation_endpoint_rejects_anonymous_spend(client):
    response = await client.post("/api/projects/x/runs", json={"mode": "express"})
    assert response.status == 401
```

- [ ] **Step 2: RED, implementation, GREEN**

Run: `python -m pytest tests/saas_cases/test_auth_routes.py -q`  
Implement `/api/drafts`, `/api/auth/{provider}/start`, callback, `/api/auth/session`,
`POST /api/auth/logout`; OAuth callback transactionally links identity, creates a
personal tenant/user when needed, claims draft and rotates session.  
Run: `python -m pytest tests/saas_cases/test_auth_routes.py -q`  
Expected: PASS.

- [ ] **Step 3: frontend gate и commit**

Run: `npm test -- --run src/auth/AuthGate.test.tsx src/landing/LandingPage.test.tsx`  
Expected: URL/brief persist, Studio offers Google/Yandex, no model run starts anonymously.  
Commit: `feat: add public OAuth entry flow`

### Task 6: Durable project/run/event repositories

**Files:**
- Create: `app/saas/repositories.py`
- Create: `builder_lab/store_protocol.py`
- Create: `builder_lab/postgres_store.py`
- Modify: `builder_lab/orchestrator.py`
- Test: `tests/saas_cases/test_project_repository.py`
- Test: `tests/builder_lab_cases/test_postgres_store.py`

- [ ] **Step 1: написать concurrency и restore tests**

```python
async def test_event_sequences_are_monotonic_under_concurrency(store):
    await asyncio.gather(*(store.append_event(run_id, event(i)) for i in range(20)))
    assert [e.sequence for e in await store.events_after(run_id, 0)] == list(range(1, 21))

async def test_new_store_instance_restores_run_and_artifact(factory):
    first = PostgresRunStore(factory)
    run = await first.create(request())
    await first.commit_artifact(run.run_id, artifact(revision=1))
    restored = await PostgresRunStore(factory).snapshot(run.run_id)
    assert restored.artifact.revision == 1
```

- [ ] **Step 2: RED, protocol extraction, PostgreSQL adapter, GREEN**

`RunStoreProtocol` повторяет только реально используемые orchestrator methods.
Sequence резервируется row lock-ом run; artifact JSON сохраняется immutable.
In-memory implementation остаётся для быстрых unit tests.

Run: `python -m pytest tests/builder_lab_cases/test_store.py tests/builder_lab_cases/test_postgres_store.py tests/saas_cases/test_project_repository.py -q`  
Expected: PASS.

- [ ] **Step 3: commit**

Commit: `feat: persist builder projects and runs`

### Task 7: PostgreSQL worker queue и checkpoints

**Files:**
- Create: `builder_lab/worker.py`
- Create: `scripts/run_builder_worker.py`
- Modify: `docker-compose.yml`
- Test: `tests/builder_lab_cases/test_worker.py`

- [ ] **Step 1: написать lease tests**

```python
async def test_only_one_worker_claims_a_run(queue):
    claims = await asyncio.gather(queue.claim("w1"), queue.claim("w2"))
    assert sum(claim is not None for claim in claims) == 1

async def test_expired_lease_resumes_from_last_completed_stage(queue):
    await queue.complete_stage(run_id, "reference_analysis")
    await expire_lease(run_id)
    claim = await queue.claim("replacement")
    assert claim.next_stage == "art_direction"
```

- [ ] **Step 2: RED, implementation, GREEN**

Claim query использует `FOR UPDATE SKIP LOCKED`; lease имеет owner, expires_at и
heartbeat. Stage completion и следующий state записываются транзакционно.

Run: `python -m pytest tests/builder_lab_cases/test_worker.py -q`  
Expected: PASS.

- [ ] **Step 3: Compose smoke и commit**

Run: `docker compose config --quiet`  
Expected: app, builder-worker and postgres configuration valid.  
Commit: `feat: run builder jobs with durable leases`

### Task 8: Provider-agnostic model router и audit

**Files:**
- Create: `app/models/__init__.py`
- Create: `app/models/contracts.py`
- Create: `app/models/router.py`
- Create: `app/models/providers/gemini.py`
- Modify: `builder_lab/engines/gemini_direct.py`
- Test: `tests/saas_cases/test_model_router.py`

- [ ] **Step 1: написать routing/fallback tests**

```python
async def test_role_policy_records_exact_attempt_and_usage(router, repo):
    result = await router.generate(role="visual_critic", request=req())
    call = await repo.last_model_call()
    assert (call.provider, call.model, call.role) == ("gemini", "configured-model", "visual_critic")
    assert call.prompt_version and call.input_tokens >= 0 and call.latency_ms >= 0

async def test_fallback_is_a_second_visible_attempt(router, repo):
    primary.fail_with(ProviderUnavailable())
    await router.generate(role="code_review", request=req())
    assert [c.attempt for c in await repo.calls()] == [1, 2]
```

- [ ] **Step 2: RED, adapters and policies, GREEN**

Router normalizes text/images/structured output/tool calls/usage/errors. Policy
is loaded by role and mode; no request receives a global `max_output_tokens`.

Run: `python -m pytest tests/saas_cases/test_model_router.py tests/builder_lab_cases/test_no_output_token_limit.py tests/builder_lab_cases/test_gemini_direct.py -q`  
Expected: PASS.

- [ ] **Step 3: commit**

Commit: `feat: route builder roles across model providers`

### Task 9: Trial entitlement, ledger и express mode

**Files:**
- Create: `app/billing/service.py`
- Create: `builder_lab/modes.py`
- Modify: `builder_lab/orchestrator.py`
- Test: `tests/saas_cases/test_trial_service.py`
- Test: `tests/builder_lab_cases/test_express_mode.py`

- [ ] **Step 1: написать atomic trial tests**

```python
async def test_parallel_requests_consume_only_one_trial(service):
    results = await asyncio.gather(
        service.reserve_trial(user_id, "a"),
        service.reserve_trial(user_id, "b"),
        return_exceptions=True,
    )
    assert sum(isinstance(x, TrialReservation) for x in results) == 1

async def test_infrastructure_failure_before_artifact_compensates_trial(service):
    reservation = await service.reserve_trial(user_id, run_id)
    await service.compensate_if_eligible(reservation, valid_artifact_exists=False)
    assert await service.can_start_trial(user_id)
```

- [ ] **Step 2: RED, implementation, GREEN**

Express policy задаёт стадии/критиков/retry budget, но сохраняет browser chat
gate и последний рабочий artifact. Ledger reservation/debit/compensation
immutable и идемпотентны.

Run: `python -m pytest tests/saas_cases/test_trial_service.py tests/builder_lab_cases/test_express_mode.py -q`  
Expected: PASS.

- [ ] **Step 3: commit**

Commit: `feat: add one complete express trial build`

### Task 10: Project API, SSE и refresh restore

**Files:**
- Create: `app/projects/routes.py`
- Create: `app/projects/serializers.py`
- Modify: `app/server.py`
- Test: `tests/saas_cases/test_project_routes.py`

- [ ] **Step 1: написать API contract tests**

```python
async def test_create_run_returns_202_and_does_not_execute_inline(client):
    response = await client.post(f"/api/projects/{project_id}/runs", json={"mode": "express"})
    assert response.status == 202
    assert response.json()["status"] == "queued"

async def test_sse_resumes_after_last_event_id(client):
    response = await client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "4"})
    assert [event.id for event in await read_sse(response)] == ["5", "6"]
```

- [ ] **Step 2: RED, routes, ownership policy, GREEN**

Все project/run/artifact endpoints проверяют user/tenant ownership. SSE отправляет
heartbeat comments и закрывается на terminal state; polling snapshot остаётся.

Run: `python -m pytest tests/saas_cases/test_project_routes.py -q`  
Expected: PASS.

- [ ] **Step 3: commit**

Commit: `feat: expose durable project generation API`

### Task 11: Studio composer → live workspace

**Files:**
- Modify: `frontend/src/studio/types.ts`
- Modify: `frontend/src/studio/api.ts`
- Modify: `frontend/src/studio/useBuilderRun.ts`
- Modify: `frontend/src/studio/StudioPage.tsx`
- Create: `frontend/src/studio/StudioComposer.tsx`
- Create: `frontend/src/studio/UpgradeGate.tsx`
- Test: `frontend/src/studio/SaasStudioFlow.test.tsx`

- [ ] **Step 1: написать UI flow tests**

```tsx
it("restores an authenticated queued run after reload", async () => {
  mockSession(authenticatedSession)
  mockProject(projectWithQueuedRun)
  render(<StudioPage />)
  expect(await screen.findByText("Запуск в очереди")).toBeVisible()
  expect(screen.getByDisplayValue("https://example.com")).toBeVisible()
})

it("shows the usable free result before upgrade", async () => {
  mockProject(projectWithFreeResult)
  render(<StudioPage />)
  expect(await screen.findByTitle("Предпросмотр виджета")).toBeVisible()
  expect(screen.getByRole("button", {name: "Доработать и опубликовать"})).toBeVisible()
})
```

- [ ] **Step 2: RED, state reducer/SSE integration, GREEN**

Run: `npm test -- --run src/studio/SaasStudioFlow.test.tsx src/studio/StudioPage.test.tsx src/studio/api.test.ts`  
Expected: PASS; duplicate SSE events ignored by sequence, refresh restores server state.

- [ ] **Step 3: accessibility/build and commit**

Run: `npm run typecheck && npm run lint && npm run build`  
Expected: PASS.  
Commit: `feat: connect Studio to durable SaaS projects`

### Task 12: Publication, stable embed и rollback

**Files:**
- Create: `app/publication/service.py`
- Create: `app/publication/routes.py`
- Create: `app/widgets/loader.py`
- Modify: `app/server.py`
- Test: `tests/saas_cases/test_publication.py`

- [ ] **Step 1: написать publish/rollback tests**

```python
async def test_publish_keeps_stable_key_across_revisions(service):
    first = await service.publish(project, revision=3)
    second = await service.publish(project, revision=5)
    assert first.public_key == second.public_key
    assert (await service.resolve(first.public_key)).revision == 5

async def test_rollback_atomically_restores_previous_release(service):
    await service.rollback(publication_id, target_release_id=first.id)
    assert (await service.resolve(public_key)).release_id == first.id
```

- [ ] **Step 2: RED, implementation, GREEN**

`POST /api/projects/{id}/publish` создаёт immutable release; `/embed/{key}.js`
отдаёт stable loader, `/runtime/{key}` — sandbox payload с domain policy.

Run: `python -m pytest tests/saas_cases/test_publication.py tests/deployment_cases -q`  
Expected: PASS.

- [ ] **Step 3: commit**

Commit: `feat: publish immutable widget releases`

### Task 13: Billing contracts и YooKassa test flow

**Files:**
- Create: `app/billing/contracts.py`
- Create: `app/billing/providers/yookassa.py`
- Create: `app/billing/routes.py`
- Test: `tests/saas_cases/test_billing.py`

- [ ] **Step 1: написать webhook idempotency tests**

```python
async def test_same_webhook_credits_account_once(client):
    payload, signature = paid_webhook("evt-1", amount=299000)
    assert (await client.post("/api/billing/yookassa/webhook", data=payload, headers=signature)).status == 200
    assert (await client.post("/api/billing/yookassa/webhook", data=payload, headers=signature)).status == 200
    assert await ledger_credit_count("evt-1") == 1

async def test_browser_redirect_cannot_activate_subscription(client):
    response = await client.get("/billing/success?payment=unverified")
    assert response.status == 202
    assert not await subscription_is_active()
```

- [ ] **Step 2: RED, adapter, webhook transaction, GREEN**

Checkout metadata содержит internal payment id; webhook проверяется provider API,
deduplicates provider event, меняет payment/subscription и ledger в одной
транзакции. Redirect только показывает pending/success server state.

Run: `python -m pytest tests/saas_cases/test_billing.py -q`  
Expected: PASS with fake YooKassa transport.

- [ ] **Step 3: commit**

Commit: `feat: add provider-neutral billing foundation`

### Task 14: Лендинг-воронка, аналитика и production migration

**Files:**
- Create: `frontend/src/landing/FreeResultSection.tsx`
- Modify: `frontend/src/landing/LandingPage.tsx`
- Modify: `frontend/src/shared/UrlComposer.tsx`
- Create: `app/analytics/routes.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `deploy/nginx/kaigo-marketing-site.conf`
- Create: `scripts/smoke_saas_foundation.py`
- Test: `frontend/src/landing/FreeResultSection.test.tsx`
- Test: `tests/deployment_cases/test_saas_routes.py`

- [ ] **Step 1: написать truthful funnel tests**

```tsx
it("explains free result before payment without promising a fixed deadline", () => {
  render(<FreeResultSection />)
  expect(screen.getByText(/Сначала результат — потом оплата/i)).toBeVisible()
  expect(screen.getByText(/обычно 10–20 минут/i)).toBeVisible()
  expect(screen.getByText(/первая версия бесплатно/i)).toBeVisible()
})
```

- [ ] **Step 2: RED, landing + funnel events, GREEN**

События: composer_submitted, auth_started/completed, run_queued,
first_artifact, free_result, upgrade_started, payment_completed, published.
Payload содержит только internal ids и campaign data, без prompt/URL/PII.

Run: `npm test -- --run src/landing/FreeResultSection.test.tsx src/landing/LandingPage.test.tsx`  
Run: `python -m pytest tests/deployment_cases/test_saas_routes.py -q`  
Expected: PASS.

- [ ] **Step 3: полный release gate**

Run: `python -m pytest -q`  
Run: `npm test -- --run && npm run typecheck && npm run lint && npm run build && npm run test:e2e`  
Run: `docker compose config --quiet`  
Run: `python scripts/smoke_saas_foundation.py --base-url http://127.0.0.1:8080`  
Expected: all green; smoke proves auth fake flow, durable refresh, trial race,
express artifact, publish/embed and billing webhook replay.

- [ ] **Step 4: documentation and production rollout**

Обновить `README.md`, `docs/product-journal/2026-07.md`, release packet,
operations/runbook и evidence. Применить миграции перед переключением app/worker,
провести canary с тестовыми OAuth apps и sandbox YooKassa, проверить rollback.

- [ ] **Step 5: final commit**

Commit: `feat: launch Kaigo SaaS foundation`

## Self-review результата плана

- Spec coverage: identity/session, drafts, durable execution, events/artifacts,
  model provenance, trial/ledger, Studio restore, publication, billing, funnel,
  security and production migration имеют отдельные задачи.
- Deliberately deferred: конкретные цены, окончательная model matrix,
  self-service password registration, white-label и CRM integrations — как в
  утверждённой спецификации.
- Type consistency: `project_id`, `run_id`, `revision`, `sequence`,
  `provider_event_id` и `public_key` сохраняют одно значение во всех задачах.
- Placeholder scan: каждая задача содержит точные файлы, RED/GREEN команды,
  минимальный contract и commit boundary.
