from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.catalog import PLAN_CATALOG
from app.billing.contracts import PaymentStatus, ProviderCheckout
from app.billing.payments import BillingService
from app.billing.routes import BILLING_SERVICE_KEY, setup_billing_routes
from app.billing.yookassa import YooKassaError
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import PaymentAttempt, Subscription
from tests.saas_cases.test_billing_service import FakeProvider


async def _billing_app(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'billing-routes.db'}")
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
        assert payload["payment"]["amount_minor"] == 199_000
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
async def test_payment_status_is_owner_scoped_and_webhook_activates(tmp_path) -> None:
    engine, factory, _provider, client = await _billing_app(tmp_path)
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
        status = await client.get(f"/api/billing/payments/{payment_uuid}")
        assert status.status == 200
        assert (await status.json())["payment"]["status"] == "succeeded"
        subscription = await client.get("/api/billing/subscription")
        data = await subscription.json()
        assert data["subscription"]["plan_code"] == "starter_monthly"
        assert data["subscription"]["status"] == "active"
        async with factory() as database:
            assert await database.scalar(select(func.count()).select_from(Subscription)) == 1
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
            assert await database.scalar(select(func.count()).select_from(Subscription)) == 0
            assert await database.scalar(select(func.count()).select_from(PaymentAttempt)) == 0
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_subscription_hides_expired_and_pending_payment_recovers(tmp_path) -> None:
    engine, factory, _provider, client = await _billing_app(tmp_path)
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

        plan = PLAN_CATALOG["starter_monthly"]
        async with factory() as database, database.begin():
            stuck = PaymentAttempt(
                user_id=10,
                provider="fakepay",
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
        assert recoverable["payment"]["status"] == "failed"
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
