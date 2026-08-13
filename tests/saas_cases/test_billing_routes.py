from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.catalog import PLAN_CATALOG
from app.billing.contracts import PaymentStatus, ProviderCheckout
from app.billing.payments import BillingService
from app.billing.routes import BILLING_SERVICE_KEY, _subscription, setup_billing_routes
from app.billing.yookassa import YooKassaError
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import (
    BillingPaymentMethod,
    CustomerContactRequest,
    FounderAccessGrant,
    FunnelEvent,
    PaymentAttempt,
    Project,
    Subscription,
    UserIdentity,
)
from tests.saas_cases.test_billing_service import FakeProvider


async def _billing_app(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'billing-routes.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add_all(
            [
                User(id=10, tenant_id=1, email="owner@example.com"),
                User(id=11, tenant_id=1, email="other@example.com"),
            ]
        )
        database.add_all(
            [
                UserIdentity(
                    user_id=10,
                    provider="yandex",
                    provider_subject="verified-owner-10",
                    email="owner@example.com",
                    email_verified=True,
                ),
                UserIdentity(
                    user_id=11,
                    provider="yandex",
                    provider_subject="verified-owner-11",
                    email="other@example.com",
                    email_verified=True,
                ),
            ]
        )
    provider = FakeProvider()
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(public_base_url="https://kaigo.space")
    app[BILLING_SERVICE_KEY] = BillingService(factory, provider)
    setup_session(app, SimpleCookieStorage(cookie_name="kaigo_billing_test"))

    async def login(request: web.Request) -> web.Response:
        session = await get_session(request)
        session["user_id"] = int(request.match_info["user_id"])
        session["tenant_id"] = 1
        session["csrf_token"] = "csrf"
        return web.json_response({"csrf_token": "csrf"})

    app.router.add_post("/test/login/{user_id}", login)
    setup_billing_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, factory, provider, client


async def _ready_project(factory, *, user_id: int = 10, host: str = "brand.example"):
    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=user_id,
            source_url=f"https://{host}/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        return project.id


@pytest.mark.asyncio
async def test_publication_offer_and_founder_claim_are_server_priced(tmp_path) -> None:
    engine, factory, _provider, client = await _billing_app(tmp_path)
    try:
        project_id = await _ready_project(factory)
        await client.post("/test/login/10")

        offer = await client.get(f"/api/billing/offer?project_id={project_id}")
        assert offer.status == 200
        payload = await offer.json()
        assert payload["founder"] == {
            "eligible": True,
            "reason": None,
            "remaining": 20,
            "capacity": 20,
            "period_days": 14,
            "generation_tokens": 1_500_000,
        }
        assert [plan["code"] for plan in payload["plans"]] == [
            "starter_intro_15d",
            "starter_monthly",
            "starter_quarterly",
        ]
        assert payload["plans"][0]["renewal"] == {
            "plan_code": "starter_intro_balance_15d",
            "amount_minor": 150_000,
            "currency": "RUB",
            "period_days": 15,
            "following": {
                "plan_code": "starter_monthly",
                "amount_minor": 200_000,
                "currency": "RUB",
                "period_days": 30,
            },
        }

        no_csrf = await client.post(
            "/api/billing/founder/claim", json={"project_id": str(project_id)}
        )
        assert no_csrf.status == 403
        claimed = await client.post(
            "/api/billing/founder/claim",
            json={"project_id": str(project_id)},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert claimed.status == 201
        result = await claimed.json()
        assert result["created"] is True
        assert result["subscription"]["plan_code"] == "founder_14d"
        assert result["subscription"]["auto_renew"] is False
        assert result["subscription"]["access_kind"] == "founder"
        assert result["subscription"]["plan_title"] == "Kaigo Founder, 14 дней"
        assert result["subscription"]["next_charge"] is None

        replay = await client.post(
            "/api/billing/founder/claim",
            json={"project_id": str(project_id)},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert replay.status == 200
        async with factory() as database:
            assert await database.scalar(select(func.count()).select_from(FounderAccessGrant)) == 1
            event = await database.scalar(
                select(FunnelEvent).where(FunnelEvent.event_type == "founder_claimed")
            )
            assert event is not None
            assert event.project_id == project_id
    finally:
        await client.close()
        await engine.dispose()


def test_subscription_exposes_exact_intro_balance_renewal_terms() -> None:
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    renewal_at = now + timedelta(days=15)
    intro = PLAN_CATALOG["starter_intro_15d"]
    row = Subscription(
        user_id=10,
        provider="fakepay",
        plan_code=intro.code,
        plan_snapshot=intro.snapshot(),
        plan_fingerprint=intro.fingerprint(),
        status="active",
        current_period_start=now,
        current_period_end=renewal_at,
        auto_renew=True,
        next_renewal_at=renewal_at,
    )

    payload = _subscription(row, generation_tokens_remaining=400_000)

    assert payload["access_kind"] == "paid"
    assert payload["plan_title"] == "Kaigo Starter, первые 15 дней"
    assert payload["next_charge"] == {
        "plan_code": "starter_intro_balance_15d",
        "amount_minor": 150_000,
        "currency": "RUB",
        "period_days": 15,
        "at": "2026-08-28T12:00:00Z",
    }


@pytest.mark.asyncio
async def test_customer_contact_uses_authenticated_lineage(tmp_path) -> None:
    engine, factory, _provider, client = await _billing_app(tmp_path)
    try:
        project_id = await _ready_project(factory)
        await client.post("/test/login/10")
        invalid = await client.post(
            "/api/billing/contact",
            json={"project_id": str(project_id), "kind": "support", "message": "x"},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert invalid.status == 400

        response = await client.post(
            "/api/billing/contact",
            json={
                "project_id": str(project_id),
                "kind": "support",
                "message": "Помогите установить код на сайт и проверить домен.",
                "testimonial_allowed": False,
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert response.status == 201
        async with factory() as database:
            request = await database.scalar(select(CustomerContactRequest))
        assert request.user_id == 10
        assert request.project_id == project_id
        assert request.message.startswith("Помогите установить")
        async with factory() as database:
            event = await database.scalar(
                select(FunnelEvent).where(FunnelEvent.event_type == "support_requested")
            )
        assert event is not None
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkout_requires_auth_csrf_and_server_plan(tmp_path) -> None:
    engine, _factory, provider, client = await _billing_app(tmp_path)
    try:
        unauthenticated = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={"Idempotency-Key": "checkout-123"},
        )
        assert unauthenticated.status == 401
        await client.post("/test/login/10")
        no_csrf = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={"Idempotency-Key": "checkout-123"},
        )
        assert no_csrf.status == 403
        response = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly", "amount_minor": 1},
            headers={"Idempotency-Key": "checkout-123", "X-CSRF-Token": "csrf"},
        )
        assert response.status == 400
        response = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={"Idempotency-Key": "checkout-123", "X-CSRF-Token": "csrf"},
        )
        assert response.status == 201
        payload = await response.json()
        assert payload["created"] is True
        assert payload["payment"]["amount_minor"] == 200_000
        assert payload["checkout_url"].startswith("https://pay.example/")
        replay = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={"Idempotency-Key": "checkout-123", "X-CSRF-Token": "csrf"},
        )
        assert replay.status == 200
        assert (await replay.json())["payment"]["id"] == payload["payment"]["id"]
        assert len(provider.checkout_calls) == 1
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkout_accepts_only_explicit_boolean_auto_renew_consent(
    tmp_path,
) -> None:
    engine, factory, provider, client = await _billing_app(tmp_path)
    try:
        await client.post("/test/login/10")
        invalid = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly", "auto_renew": "yes"},
            headers={
                "Idempotency-Key": "invalid-auto-renew-consent",
                "X-CSRF-Token": "csrf",
            },
        )
        assert invalid.status == 400

        response = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly", "auto_renew": True},
            headers={
                "Idempotency-Key": "explicit-auto-renew-consent",
                "X-CSRF-Token": "csrf",
            },
        )
        assert response.status == 201
        payload = await response.json()
        assert provider.checkout_calls[-1].save_payment_method is True
        async with factory() as database:
            attempt = await database.get(
                PaymentAttempt,
                UUID(payload["payment"]["id"]),
            )
            assert attempt is not None
            assert attempt.auto_renew_requested is True
            assert attempt.save_payment_method_requested is True
        assert "provider_payment_method_id" not in json.dumps(payload)
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_intro_checkout_requires_auto_renew_consent_for_monthly_transition(
    tmp_path,
) -> None:
    engine, _factory, provider, client = await _billing_app(tmp_path)
    try:
        await client.post("/test/login/10")
        rejected = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_intro_15d", "auto_renew": False},
            headers={"Idempotency-Key": "intro-without-consent", "X-CSRF-Token": "csrf"},
        )
        assert rejected.status == 400

        accepted = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_intro_15d", "auto_renew": True},
            headers={"Idempotency-Key": "intro-with-consent-ok", "X-CSRF-Token": "csrf"},
        )
        assert accepted.status == 201
        assert (await accepted.json())["payment"]["amount_minor"] == 50_000
        assert provider.checkout_calls[-1].save_payment_method is True
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_disable_auto_renew_is_owner_scoped_csrf_protected_and_idempotent(
    tmp_path,
) -> None:
    engine, factory, _provider, client = await _billing_app(tmp_path)
    try:
        await client.post("/test/login/10")
        checkout = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={
                "Idempotency-Key": "auto-renew-off-seed",
                "X-CSRF-Token": "csrf",
            },
        )
        checkout_payload = await checkout.json()
        attempt_id = UUID(checkout_payload["payment"]["id"])
        now = datetime.now(UTC)
        period_end = now + timedelta(days=30)
        async with factory() as database, database.begin():
            attempt = await database.get(PaymentAttempt, attempt_id)
            assert attempt is not None
            method = BillingPaymentMethod(
                user_id=10,
                provider=attempt.provider,
                merchant_account_fingerprint=attempt.merchant_account_fingerprint,
                provider_payment_method_id="private-saved-method",
                source_payment_attempt_id=attempt.id,
                status="active",
                consent_version="yookassa-auto-renew-v1",
                consented_at=now,
                saved_at=now,
            )
            database.add(method)
            await database.flush()
            subscription = Subscription(
                user_id=10,
                provider=attempt.provider,
                merchant_account_fingerprint=attempt.merchant_account_fingerprint,
                payment_method_id=method.id,
                payment_attempt_id=attempt.id,
                plan_code=attempt.plan_code,
                plan_snapshot=attempt.plan_snapshot,
                plan_fingerprint=attempt.plan_fingerprint,
                status="active",
                current_period_start=now,
                current_period_end=period_end,
                auto_renew=True,
                next_renewal_at=period_end,
                auto_renew_enabled_at=now,
            )
            database.add(subscription)
            await database.flush()
            subscription_id = subscription.id

        serialized_responses = [
            await client.get("/api/billing/subscription"),
            await client.get(f"/api/billing/payments/{attempt_id}"),
            await client.get("/api/billing/payments/pending"),
        ]
        for response in serialized_responses:
            assert response.status == 200
            assert "provider_payment_method_id" not in json.dumps(await response.json())

        missing_csrf = await client.post(
            f"/api/billing/subscriptions/{subscription_id}/auto-renew/off"
        )
        assert missing_csrf.status == 403

        await client.post("/test/login/11")
        other_owner = await client.post(
            f"/api/billing/subscriptions/{subscription_id}/auto-renew/off",
            headers={"X-CSRF-Token": "csrf"},
        )
        assert other_owner.status == 404

        await client.post("/test/login/10")
        first = await client.post(
            f"/api/billing/subscriptions/{subscription_id}/auto-renew/off",
            headers={"X-CSRF-Token": "csrf"},
        )
        assert first.status == 200
        first_payload = await first.json()
        assert first_payload["subscription"]["auto_renew"] is False
        assert first_payload["subscription"]["current_period_end"] == (
            period_end.isoformat().replace("+00:00", "Z")
        )
        assert "provider_payment_method_id" not in json.dumps(first_payload)

        second = await client.post(
            f"/api/billing/subscriptions/{subscription_id}/auto-renew/off",
            headers={"X-CSRF-Token": "csrf"},
        )
        assert second.status == 200
        assert await second.json() == first_payload

        async with factory() as database:
            stored = await database.get(Subscription, subscription_id)
            assert stored is not None
            assert stored.status == "active"
            assert stored.current_period_end is not None
            stored_period_end = stored.current_period_end
            if stored_period_end.tzinfo is None:
                stored_period_end = stored_period_end.replace(tzinfo=UTC)
            assert stored_period_end == period_end
            assert stored.auto_renew is False
            assert stored.next_renewal_at is None
            stored_method = await database.get(BillingPaymentMethod, method.id)
            assert stored_method.status == "disabled"
            assert stored_method.disabled_at is not None
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_payment_status_is_owner_scoped_and_webhook_activates(tmp_path) -> None:
    engine, factory, provider, client = await _billing_app(tmp_path)
    try:
        await client.post("/test/login/10")
        checkout = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={"Idempotency-Key": "checkout-owner", "X-CSRF-Token": "csrf"},
        )
        created = await checkout.json()
        payment_uuid = created["payment"]["id"]
        await client.post("/test/login/11")
        hidden = await client.get(f"/api/billing/payments/{payment_uuid}")
        assert hidden.status == 404

        webhook = await client.post(
            "/api/billing/webhooks/yookassa",
            json={"payment_id": f"pay-{payment_uuid}"},
        )
        assert webhook.status == 200
        assert (await webhook.json()) == {"accepted": True, "processed": True}

        await client.post("/test/login/10")
        provider_calls_before_poll = len(provider.verify_calls) + len(
            provider.get_calls
        )
        status = await client.get(f"/api/billing/payments/{payment_uuid}")
        assert status.status == 200
        assert (await status.json())["payment"]["status"] == "succeeded"
        assert len(provider.verify_calls) + len(provider.get_calls) == (
            provider_calls_before_poll
        )
        subscription = await client.get("/api/billing/subscription")
        data = await subscription.json()
        assert data["subscription"]["plan_code"] == "starter_monthly"
        assert data["subscription"]["status"] == "active"
        assert data["subscription"]["generation_tokens_remaining"] == 1_000_000
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(Subscription))
                == 1
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_webhook_and_success_redirect_never_fulfill(tmp_path) -> None:
    engine, factory, _provider, client = await _billing_app(tmp_path)
    try:
        unknown = await client.post(
            "/api/billing/webhooks/yookassa", json={"payment_id": "forged"}
        )
        assert unknown.status == 400
        redirect = await client.get("/billing/success?payment=forged")
        assert redirect.status == 200
        assert 'href="/studio"' in await redirect.text()
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(Subscription))
                == 0
            )
            assert (
                await database.scalar(select(func.count()).select_from(PaymentAttempt))
                == 0
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_subscription_hides_expired_and_pending_payment_recovers(
    tmp_path,
) -> None:
    engine, factory, provider, client = await _billing_app(tmp_path)
    try:
        async with factory() as database, database.begin():
            database.add(
                Subscription(
                    user_id=10,
                    provider="fakepay",
                    plan_code="starter_monthly",
                    plan_snapshot={},
                    plan_fingerprint="x" * 64,
                    status="active",
                    current_period_start=datetime.now(UTC) - timedelta(days=31),
                    current_period_end=datetime.now(UTC) - timedelta(days=1),
                )
            )
        await client.post("/test/login/10")
        expired = await client.get("/api/billing/subscription")
        assert (await expired.json()) == {"subscription": None}

        checkout = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={"Idempotency-Key": "recover-pending", "X-CSRF-Token": "csrf"},
        )
        created = await checkout.json()
        pending = await client.get("/api/billing/payments/pending")
        payload = await pending.json()
        assert payload["payment"]["id"] == created["payment"]["id"]
        assert payload["checkout_url"] == created["checkout_url"]
        assert payload["recovery"]["status"] == "ready"

        plan = PLAN_CATALOG["starter_monthly"]
        async with factory() as database, database.begin():
            prior = await database.get(PaymentAttempt, UUID(created["payment"]["id"]))
            assert prior is not None
            prior.status = "cancelled"
            stuck = PaymentAttempt(
                user_id=10,
                provider="fakepay",
                merchant_account_fingerprint=provider.merchant_account_fingerprint,
                idempotency_key="route-stale-recovery",
                plan_code=plan.code,
                plan_snapshot=plan.snapshot(),
                plan_fingerprint=plan.fingerprint(),
                amount_minor=plan.amount.amount_minor,
                currency=plan.amount.currency,
                status="creating",
                payload={},
                updated_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            database.add(stuck)
            await database.flush()
            stuck_id = stuck.id
            first_dispatched_at = datetime.now(UTC) - timedelta(minutes=1)
            stuck.payload = {
                "provider_idempotency_key": f"kaigo-{stuck_id}",
                "first_dispatched_at": first_dispatched_at.isoformat(),
                "provider_idempotency_expires_at": (
                    first_dispatched_at + timedelta(hours=24)
                ).isoformat(),
            }
            stuck.updated_at = datetime.now(UTC) - timedelta(minutes=1)
        resumed = await client.post(
            f"/api/billing/payments/{stuck_id}/resume",
            headers={"X-CSRF-Token": "csrf"},
        )
        assert resumed.status == 200
        recovered = await resumed.json()
        assert recovered["payment"]["id"] == str(stuck_id)
        assert recovered["created"] is False
        assert recovered["checkout_url"].endswith(str(stuck_id))
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_merchant_cutover_hides_old_checkout_and_blocks_new_payment(
    tmp_path,
) -> None:
    engine, factory, merchant_a, client = await _billing_app(tmp_path)
    try:
        await client.post("/test/login/10")
        created_response = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={
                "Idempotency-Key": "merchant-a-checkout",
                "X-CSRF-Token": "csrf",
            },
        )
        assert created_response.status == 201
        created = await created_response.json()
        payment_id = created["payment"]["id"]
        assert created["checkout_url"].startswith("https://pay.example/")

        merchant_b = FakeProvider("b" * 64)
        client.server.app[BILLING_SERVICE_KEY] = BillingService(factory, merchant_b)

        # Simulate a pre-fix deployment that already created a newer checkout
        # under merchant B.  The older merchant-A attempt must still force the
        # whole account into drain mode; neither checkout URL is safe to expose.
        plan = PLAN_CATALOG["starter_monthly"]
        async with factory() as database, database.begin():
            database.add(
                PaymentAttempt(
                    user_id=10,
                    provider="fakepay",
                    merchant_account_fingerprint=(
                        merchant_b.merchant_account_fingerprint
                    ),
                    idempotency_key="merchant-b-pre-fix-checkout",
                    provider_payment_id="pay-merchant-b-pre-fix",
                    plan_code=plan.code,
                    plan_snapshot=plan.snapshot(),
                    plan_fingerprint=plan.fingerprint(),
                    amount_minor=plan.amount.amount_minor,
                    currency=plan.amount.currency,
                    status="pending",
                    checkout_url="https://pay.example/merchant-b-pre-fix",
                    payload={},
                    updated_at=datetime.now(UTC) + timedelta(minutes=1),
                )
            )

        pending_response = await client.get("/api/billing/payments/pending")
        assert pending_response.status == 200
        pending = await pending_response.json()
        assert pending["payment"]["id"] == payment_id
        assert pending["checkout_url"] is None
        assert pending["recovery"] == {
            "status": "merchant_cutover_required",
            "recoverable": True,
            "action": "restore_previous_merchant",
            "message": (
                "Оплата начата в другом аккаунте магазина. "
                "Верните прежнюю платёжную конфигурацию для завершения."
            ),
        }

        blocked_checkout = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={
                "Idempotency-Key": "merchant-b-new-checkout",
                "X-CSRF-Token": "csrf",
            },
        )
        assert blocked_checkout.status == 409
        assert (await blocked_checkout.json())["error"] == {
            "code": "merchant_cutover_required",
            "message": (
                "Оплата начата в другом аккаунте магазина. "
                "Верните прежнюю платёжную конфигурацию для завершения."
            ),
            "retryable": False,
        }

        webhook = await client.post(
            "/api/billing/webhooks/yookassa",
            json={"payment_id": f"pay-{payment_id}"},
        )
        assert webhook.status == 503
        assert webhook.headers["Retry-After"] == "300"
        assert (await webhook.json())["error"] == {
            "code": "merchant_cutover_required",
            "message": (
                "Оплата начата в другом аккаунте магазина. "
                "Верните прежнюю платёжную конфигурацию для завершения."
            ),
            "retryable": True,
        }
        assert merchant_b.checkout_calls == []
        assert merchant_b.verify_calls == []
        async with factory() as database:
            attempt = await database.get(PaymentAttempt, UUID(payment_id))
            assert attempt.checkout_url == created["checkout_url"]
            assert attempt.merchant_account_fingerprint == (
                merchant_a.merchant_account_fingerprint
            )
            assert (
                await database.scalar(select(func.count()).select_from(Subscription))
                == 0
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkout_sanitizes_temporary_provider_failure(tmp_path) -> None:
    engine, _factory, provider, client = await _billing_app(tmp_path)

    async def unavailable(_command):
        raise YooKassaError("upstream detail must not leak")

    provider.create_checkout = unavailable
    try:
        await client.post("/test/login/10")
        response = await client.post(
            "/api/billing/checkout",
            json={"plan_code": "starter_monthly"},
            headers={"Idempotency-Key": "provider-down", "X-CSRF-Token": "csrf"},
        )

        assert response.status == 503
        assert response.headers["Retry-After"] == "30"
        payload = await response.json()
        assert payload == {
            "error": {
                "code": "provider_unavailable",
                "message": "Платёжная система временно недоступна",
                "retryable": True,
            }
        }
        assert "upstream" not in str(payload)

        pending = await client.get("/api/billing/payments/pending")
        recoverable = await pending.json()
        assert recoverable["payment"]["status"] == "dispatch_unknown"
        payment_id = recoverable["payment"]["id"]

        async def restored(command):
            return ProviderCheckout(
                provider_payment_id=f"pay-{command.metadata['payment_attempt_id']}",
                checkout_url=f"https://pay.example/{command.metadata['payment_attempt_id']}",
                status=PaymentStatus.PENDING,
                amount=command.amount,
                paid=False,
                metadata=command.metadata,
                test_mode=True,
            )

        provider.create_checkout = restored
        resumed = await client.post(
            f"/api/billing/payments/{payment_id}/resume",
            headers={"X-CSRF-Token": "csrf"},
        )
        assert resumed.status == 200
        resumed_payload = await resumed.json()
        assert resumed_payload["payment"]["id"] == payment_id
        assert resumed_payload["created"] is False
    finally:
        await client.close()
        await engine.dispose()
