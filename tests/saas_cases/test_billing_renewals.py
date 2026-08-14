from __future__ import annotations

import asyncio
import ast
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import inspect
import os
import textwrap
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.billing.catalog import PLAN_CATALOG
from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentCancellationReason,
    PaymentStatus,
    ProviderPayment,
    RecurringPaymentCommand,
)
from app.billing.payments import BillingError, BillingService
from app.billing.receipts import BillingReceiptSettings
from app.billing.renewals import RenewalScheduler, renewal_idempotency_key
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    BillingPaymentMethod,
    PaymentAttempt,
    Subscription,
    UsageLedger,
    UserIdentity,
)


@dataclass
class FakeClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeRecurringProvider:
    name = "fakepay"
    merchant_account_fingerprint = "a" * 64

    def __init__(
        self,
        *,
        status: PaymentStatus = PaymentStatus.SUCCEEDED,
        cancellation_reason: PaymentCancellationReason | None = None,
    ) -> None:
        self.status = status
        self.cancellation_reason = cancellation_reason
        self.recurring_calls: list[RecurringPaymentCommand] = []
        self._gate = asyncio.Event()
        self._gate.set()

    async def create_checkout(self, command: CheckoutCommand):  # pragma: no cover
        raise AssertionError("checkout is outside renewal tests")

    async def create_recurring_payment(
        self, command: RecurringPaymentCommand
    ) -> ProviderPayment:
        self.recurring_calls.append(command)
        await self._gate.wait()
        return ProviderPayment(
            provider_payment_id=f"renewal-{command.idempotency_key}",
            status=self.status,
            amount=command.amount,
            paid=self.status is PaymentStatus.SUCCEEDED,
            metadata=command.metadata,
            test_mode=True,
            cancellation_reason=self.cancellation_reason,
        )

    async def get_payment(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("reconciliation is outside renewal tests")

    def parse_notification(self, payload):  # pragma: no cover
        raise AssertionError("webhook is outside renewal tests")

    async def verify_notification(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("webhook is outside renewal tests")

    async def aclose(self) -> None:
        return None


class AmbiguousRecurringProvider(FakeRecurringProvider):
    async def create_recurring_payment(
        self, command: RecurringPaymentCommand
    ) -> ProviderPayment:
        self.recurring_calls.append(command)
        raise RuntimeError("lost provider response")


@pytest_asyncio.fixture
async def recurring_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'renewals.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 31, 12, tzinfo=UTC)
    plan = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        database.add(
            UserIdentity(
                user_id=10,
                provider="yandex",
                provider_subject="owner",
                email="owner@example.com",
                email_verified=True,
            )
        )
        method = BillingPaymentMethod(
            user_id=10,
            provider="fakepay",
            merchant_account_fingerprint="a" * 64,
            provider_payment_method_id="saved-renewal-method",
            status="active",
            consent_version="yookassa-auto-renew-v1",
            consented_at=now - timedelta(days=31),
            saved_at=now - timedelta(days=31),
        )
        database.add(method)
        await database.flush()
        subscription = Subscription(
            user_id=10,
            provider="fakepay",
            merchant_account_fingerprint="a" * 64,
            payment_method_id=method.id,
            plan_code=plan.code,
            plan_snapshot=plan.snapshot(),
            plan_fingerprint=plan.fingerprint(),
            status="active",
            current_period_start=now - timedelta(days=30),
            current_period_end=now,
            auto_renew=True,
            next_renewal_at=now,
            auto_renew_enabled_at=now - timedelta(days=31),
        )
        database.add(subscription)
        await database.flush()
        subscription_id = subscription.id
    try:
        yield factory, FakeClock(now), subscription_id
    finally:
        await engine.dispose()


def scheduler(factory, clock, provider, *, receipts=BillingReceiptSettings()):
    service = BillingService(factory, provider, receipt_settings=receipts, clock=clock)
    return RenewalScheduler(
        factory,
        provider,
        payment_service=service,
        receipt_settings=receipts,
        clock=clock,
    )


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _locked_select_entities(method_name: str) -> list[str]:
    owner = getattr(RenewalScheduler, method_name, None) or getattr(
        BillingService, method_name
    )
    source = textwrap.dedent(inspect.getsource(owner))
    tree = ast.parse(source)
    entities: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Attribute) and node.func.attr == "with_for_update":
                # The select(...) call is the root of the SQLAlchemy query chain.
                query = node.func.value
                while isinstance(query, ast.Call) and isinstance(query.func, ast.Attribute):
                    query = query.func.value
                if (
                    isinstance(query, ast.Call)
                    and isinstance(query.func, ast.Name)
                    and query.func.id == "select"
                    and query.args
                    and isinstance(query.args[0], ast.Name)
                ):
                    entities.append(query.args[0].id)
            self.generic_visit(node)

    Visitor().visit(tree)
    return entities


def test_renewal_lock_order_is_user_attempt_subscription() -> None:
    expected_prefix = ["User", "PaymentAttempt", "Subscription"]
    for method_name in (
        "_ensure_primary",
        "_claim",
        "_schedule_retry_or_disable",
        "disable_auto_renew",
    ):
        assert _locked_select_entities(method_name)[:3] == expected_prefix


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not os.getenv("KAIGO_TEST_POSTGRES_URL"),
    reason="KAIGO_TEST_POSTGRES_URL is not configured",
)
async def test_postgres_concurrent_renewal_claim_and_disable_has_one_atomic_winner() -> None:
    postgres_url = os.environ["KAIGO_TEST_POSTGRES_URL"]
    source_url = make_url(postgres_url)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_renewal_lock_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered_target_url = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT"
    )
    target_engine = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        target_engine = create_async_engine(rendered_target_url)
        factory = async_sessionmaker(target_engine, expire_on_commit=False)
        async with target_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        plan = PLAN_CATALOG["starter_monthly"]
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Renewal lock test", slug=database_name))
            database.add(User(id=10, tenant_id=1, email="renewal-lock@example.com"))
            await database.flush()
            method = BillingPaymentMethod(
                user_id=10,
                provider="fakepay",
                merchant_account_fingerprint="a" * 64,
                provider_payment_method_id="renewal-lock-method",
                status="active",
                consent_version="yookassa-auto-renew-v1",
                consented_at=now - timedelta(days=31),
                saved_at=now - timedelta(days=31),
            )
            database.add(method)
            await database.flush()
            subscription = Subscription(
                user_id=10,
                provider="fakepay",
                merchant_account_fingerprint="a" * 64,
                payment_method_id=method.id,
                plan_code=plan.code,
                plan_snapshot=plan.snapshot(),
                plan_fingerprint=plan.fingerprint(),
                status="active",
                current_period_start=now - timedelta(days=30),
                current_period_end=now,
                auto_renew=True,
                next_renewal_at=now,
                auto_renew_enabled_at=now - timedelta(days=31),
            )
            database.add(subscription)
            await database.flush()
            attempt = PaymentAttempt(
                user_id=10,
                provider="fakepay",
                merchant_account_fingerprint="a" * 64,
                purpose="renewal",
                subscription_id=subscription.id,
                payment_method_id=method.id,
                billing_period_start=now,
                billing_period_end=now + timedelta(days=30),
                renewal_attempt_number=1,
                retry_of_payment_attempt_id=None,
                retry_of_renewal_attempt_number=None,
                next_dispatch_at=None,
                auto_renew_requested=True,
                save_payment_method_requested=False,
                consent_version=method.consent_version,
                consented_at=method.consented_at,
                idempotency_key=renewal_idempotency_key(subscription.id, now, 1),
                plan_code=plan.code,
                plan_snapshot=plan.snapshot(),
                plan_fingerprint=plan.fingerprint(),
                amount_minor=plan.amount.amount_minor,
                currency=plan.amount.currency,
                status="creating",
                payload={},
            )
            database.add(attempt)
            await database.flush()
            subscription_id = subscription.id
            attempt_id = attempt.id

        attempt_locked = asyncio.Event()
        release_attempt = asyncio.Event()
        disable_user_started = asyncio.Event()
        disable_subscription_locked = asyncio.Event()

        class GatedSession(AsyncSession):
            def __init__(self, *, role: str):
                super().__init__(bind=target_engine, expire_on_commit=False)
                self.role = role
                self._attempt_gate_used = False

            @staticmethod
            def _locked_entity(statement):
                if getattr(statement, "_for_update_arg", None) is None:
                    return None
                descriptions = statement.column_descriptions
                return descriptions[0].get("entity") if descriptions else None

            async def scalar(self, statement, *args, **kwargs):
                entity = self._locked_entity(statement)
                if self.role == "disable" and entity is User:
                    disable_user_started.set()
                result = await super().scalar(statement, *args, **kwargs)
                if (
                    self.role == "claim"
                    and entity is PaymentAttempt
                    and not self._attempt_gate_used
                ):
                    self._attempt_gate_used = True
                    attempt_locked.set()
                    await release_attempt.wait()
                if self.role == "disable" and entity is Subscription:
                    disable_subscription_locked.set()
                return result

        class SessionContext:
            def __init__(self, role: str):
                self.session = GatedSession(role=role)

            async def __aenter__(self):
                return await self.session.__aenter__()

            async def __aexit__(self, *args):
                return await self.session.__aexit__(*args)

        class SessionFactory:
            def __init__(self, role: str):
                self.role = role

            def __call__(self):
                return SessionContext(self.role)

        claim_factory = SessionFactory("claim")
        disable_factory = SessionFactory("disable")
        clock = FakeClock(now)
        provider = FakeRecurringProvider(status=PaymentStatus.PENDING)
        payments = BillingService(
            disable_factory,
            provider,
            receipt_settings=BillingReceiptSettings(),
            clock=clock,
        )
        scheduler = RenewalScheduler(
            claim_factory,
            provider,
            payment_service=payments,
            receipt_settings=BillingReceiptSettings(),
            clock=clock,
        )

        claim_task = asyncio.create_task(scheduler._claim(attempt_id, now=now))
        await asyncio.wait_for(attempt_locked.wait(), timeout=3)
        disable_task = asyncio.create_task(
            payments.disable_auto_renew(10, subscription_id)
        )
        await asyncio.wait_for(disable_user_started.wait(), timeout=3)
        try:
            await asyncio.wait_for(disable_subscription_locked.wait(), timeout=1)
        except TimeoutError:
            # With the canonical order, disable waits on User while claim owns it.
            pass
        release_attempt.set()
        claim_result, disable_result = await asyncio.wait_for(
            asyncio.gather(claim_task, disable_task, return_exceptions=True),
            timeout=5,
        )

        assert not isinstance(claim_result, Exception)
        assert claim_result is not None
        assert isinstance(disable_result, BillingError)
        assert str(disable_result) == "renewal_in_progress"
        async with factory() as database:
            final_attempt = await database.get(PaymentAttempt, attempt_id)
            final_subscription = await database.get(Subscription, subscription_id)
        assert final_attempt.status == "dispatching"
        assert final_subscription.auto_renew is True
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        await admin_engine.dispose()
        if created:
            cleanup_engine = create_async_engine(
                admin_url.render_as_string(hide_password=False),
                isolation_level="AUTOCOMMIT",
            )
            try:
                async with cleanup_engine.connect() as connection:
                    await connection.execute(
                        text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
                    )
            finally:
                await cleanup_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not os.getenv("KAIGO_TEST_POSTGRES_URL"),
    reason="KAIGO_TEST_POSTGRES_URL is not configured",
)
async def test_postgres_compensated_retry_preserves_immutable_fk_period() -> None:
    """A shifted in-flight primary creates a retry with the same FK period."""
    postgres_url = os.environ["KAIGO_TEST_POSTGRES_URL"]
    source_url = make_url(postgres_url)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_renewal_retry_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT"
    )
    target_engine = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        target_engine = create_async_engine(
            target_url.render_as_string(hide_password=False)
        )
        factory = async_sessionmaker(target_engine, expire_on_commit=False)
        async with target_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        now = datetime(2026, 8, 14, 12, tzinfo=UTC)
        old_start = now
        old_end = old_start + timedelta(days=30)
        effective_start = old_start + timedelta(days=15)
        effective_end = effective_start + timedelta(days=30)
        plan = PLAN_CATALOG["starter_monthly"]
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Renewal retry test", slug=database_name))
            database.add(User(id=10, tenant_id=1, email="renewal-retry@example.com"))
            await database.flush()
            method = BillingPaymentMethod(
                user_id=10,
                provider="fakepay",
                merchant_account_fingerprint="a" * 64,
                provider_payment_method_id="renewal-retry-method",
                status="active",
                consent_version="yookassa-auto-renew-v1",
                consented_at=now - timedelta(days=31),
                saved_at=now - timedelta(days=31),
            )
            database.add(method)
            await database.flush()
            subscription = Subscription(
                user_id=10,
                provider="fakepay",
                merchant_account_fingerprint="a" * 64,
                payment_method_id=method.id,
                plan_code=plan.code,
                plan_snapshot=plan.snapshot(),
                plan_fingerprint=plan.fingerprint(),
                status="active",
                current_period_start=now - timedelta(days=30),
                current_period_end=old_start,
                auto_renew=True,
                next_renewal_at=old_start,
                auto_renew_enabled_at=now - timedelta(days=31),
            )
            database.add(subscription)
            await database.flush()
            attempt = PaymentAttempt(
                user_id=10,
                provider="fakepay",
                merchant_account_fingerprint="a" * 64,
                purpose="renewal",
                subscription_id=subscription.id,
                payment_method_id=method.id,
                billing_period_start=old_start,
                billing_period_end=old_end,
                renewal_attempt_number=1,
                retry_of_payment_attempt_id=None,
                retry_of_renewal_attempt_number=None,
                next_dispatch_at=None,
                auto_renew_requested=True,
                save_payment_method_requested=False,
                consent_version=method.consent_version,
                consented_at=method.consented_at,
                idempotency_key=renewal_idempotency_key(subscription.id, old_start, 1),
                plan_code=plan.code,
                plan_snapshot=plan.snapshot(),
                plan_fingerprint=plan.fingerprint(),
                amount_minor=plan.amount.amount_minor,
                currency=plan.amount.currency,
                status="cancelled",
                provider_payment_id="provider-before-intro",
                first_dispatched_at=now - timedelta(minutes=1),
                provider_idempotency_expires_at=now + timedelta(days=1),
                payload={
                    "cancellation_reason": "insufficient_funds",
                    "effective_entitlement_start": effective_start.isoformat(),
                    "effective_entitlement_end": effective_end.isoformat(),
                    "billing_period_shift": {
                        "reason": "intro_compensation",
                        "original_billing_period_start": old_start.isoformat(),
                        "original_billing_period_end": old_end.isoformat(),
                        "new_billing_period_start": effective_start.isoformat(),
                        "new_billing_period_end": effective_end.isoformat(),
                    },
                },
            )
            database.add(attempt)
            await database.flush()
            subscription_id = subscription.id
            attempt_id = attempt.id

        provider = FakeRecurringProvider()
        clock = FakeClock(now)
        payments = BillingService(factory, provider, clock=clock)
        runner = RenewalScheduler(
            factory,
            provider,
            payment_service=payments,
            receipt_settings=BillingReceiptSettings(),
            clock=clock,
        )
        await runner._schedule_retry_or_disable(attempt_id, now=now)
        await runner._schedule_retry_or_disable(attempt_id, now=now)
        async with factory() as database:
            subscription = await database.get(Subscription, subscription_id)
            attempts = list(
                await database.scalars(
                    select(PaymentAttempt).order_by(PaymentAttempt.renewal_attempt_number)
                )
            )
        assert subscription is not None
        assert len(attempts) == 2
        retry = attempts[1]
        assert retry.retry_of_payment_attempt_id == attempt_id
        assert retry.billing_period_start == old_start
        assert retry.billing_period_end == old_end
        assert retry.payload["effective_entitlement_start"] == effective_start.isoformat()
        assert aware(retry.next_dispatch_at) >= effective_start
        assert aware(subscription.next_renewal_at) >= effective_start
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        await admin_engine.dispose()
        if created:
            cleanup_engine = create_async_engine(
                admin_url.render_as_string(hide_password=False),
                isolation_level="AUTOCOMMIT",
            )
            try:
                async with cleanup_engine.connect() as connection:
                    await connection.execute(
                        text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
                    )
            finally:
                await cleanup_engine.dispose()


async def _seed_paid_renewal_target(
    factory,
    subscription_id,
    *,
    provider: FakeRecurringProvider,
    status: str,
) -> tuple[UUID, datetime, datetime, dict[str, str]]:
    monthly = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        assert subscription is not None
        method = await database.get(BillingPaymentMethod, subscription.payment_method_id)
        assert method is not None
        period_start = aware(subscription.current_period_end)
        period_end = period_start + timedelta(days=monthly.period_days)
        entitlement = PaymentAttempt(
            user_id=subscription.user_id,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            purpose="initial",
            subscription_id=None,
            payment_method_id=None,
            billing_period_start=None,
            billing_period_end=None,
            renewal_attempt_number=None,
            retry_of_payment_attempt_id=None,
            retry_of_renewal_attempt_number=None,
            next_dispatch_at=None,
            auto_renew_requested=False,
            save_payment_method_requested=False,
            consent_version=None,
            consented_at=None,
            idempotency_key=f"monthly-entitlement-{uuid4().hex}",
            plan_code=monthly.code,
            plan_snapshot=monthly.snapshot(),
            plan_fingerprint=monthly.fingerprint(),
            amount_minor=monthly.amount.amount_minor,
            currency=monthly.amount.currency,
            status="succeeded",
            provider_payment_id=f"monthly-entitlement-{uuid4().hex}",
            payload={"test_mode": True},
        )
        database.add(entitlement)
        await database.flush()
        subscription.payment_attempt_id = entitlement.id
        renewal = PaymentAttempt(
            user_id=subscription.user_id,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            purpose="renewal",
            subscription_id=subscription.id,
            payment_method_id=method.id,
            billing_period_start=period_start,
            billing_period_end=period_end,
            renewal_attempt_number=1,
            retry_of_payment_attempt_id=None,
            retry_of_renewal_attempt_number=None,
            next_dispatch_at=None,
            auto_renew_requested=True,
            save_payment_method_requested=False,
            consent_version=method.consent_version,
            consented_at=method.consented_at,
            idempotency_key=renewal_idempotency_key(subscription.id, period_start, 1),
            plan_code=monthly.code,
            plan_snapshot=monthly.snapshot(),
            plan_fingerprint=monthly.fingerprint(),
            amount_minor=monthly.amount.amount_minor,
            currency=monthly.amount.currency,
            status=status,
            provider_payment_id=(
                None if status == "creating" else f"renewal-before-intro-{uuid4().hex}"
            ),
            first_dispatched_at=(
                None
                if status == "creating"
                else period_start - timedelta(minutes=1)
            ),
            provider_idempotency_expires_at=(
                None if status == "creating" else period_start + timedelta(days=1)
            ),
            payload={} if status == "creating" else {"test_mode": True},
        )
        database.add(renewal)
        await database.flush()
        original_metadata = BillingService._metadata(renewal)
        return renewal.id, period_start, period_end, original_metadata


async def _seed_intro_success(
    factory,
    *,
    provider: FakeRecurringProvider,
    idempotency_key: str,
) -> tuple[UUID, ProviderPayment]:
    intro = PLAN_CATALOG["starter_intro_15d"]
    async with factory() as database, database.begin():
        attempt = PaymentAttempt(
            user_id=10,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            purpose="initial",
            subscription_id=None,
            payment_method_id=None,
            billing_period_start=None,
            billing_period_end=None,
            renewal_attempt_number=None,
            retry_of_payment_attempt_id=None,
            retry_of_renewal_attempt_number=None,
            next_dispatch_at=None,
            auto_renew_requested=False,
            save_payment_method_requested=False,
            consent_version=None,
            consented_at=None,
            idempotency_key=idempotency_key,
            plan_code=intro.code,
            plan_snapshot=intro.snapshot(),
            plan_fingerprint=intro.fingerprint(),
            amount_minor=intro.amount.amount_minor,
            currency=intro.amount.currency,
            status="pending",
            provider_payment_id=f"late-intro-{uuid4().hex}",
            payload={"test_mode": True},
        )
        database.add(attempt)
        await database.flush()
        payment = ProviderPayment(
            provider_payment_id=str(attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(attempt.amount_minor, attempt.currency),
            paid=True,
            metadata=BillingService._metadata(attempt),
            test_mode=True,
        )
        return attempt.id, payment


@pytest.mark.asyncio
async def test_late_intro_compensation_reschedules_creating_renewal(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    payments = BillingService(factory, provider, clock=clock)
    renewal_id, old_start, old_end, _ = await _seed_paid_renewal_target(
        factory,
        subscription_id,
        provider=provider,
        status="creating",
    )
    intro_id, intro_payment = await _seed_intro_success(
        factory,
        provider=provider,
        idempotency_key="late-intro-creating",
    )

    await payments.apply_verified_payment(intro_id, intro_payment, source="webhook")

    expected_start = old_start + timedelta(days=15)
    expected_end = expected_start + timedelta(days=30)
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        renewal = await database.get(PaymentAttempt, renewal_id)
    assert subscription is not None
    assert renewal is not None
    assert aware(subscription.current_period_end) == expected_start
    assert renewal.status == "canceled"
    # Provider/retry identity stays immutable; the compensated entitlement is
    # recorded in payload and a fresh primary is created at the new boundary.
    assert aware(renewal.billing_period_start) == old_start
    assert aware(renewal.billing_period_end) == old_end
    assert renewal.next_dispatch_at is None
    shift = renewal.payload["billing_period_shift"]
    assert shift["original_billing_period_start"] == old_start.isoformat()
    assert shift["original_billing_period_end"] == old_end.isoformat()
    assert shift["new_billing_period_start"] == expected_start.isoformat()
    assert shift["new_billing_period_end"] == expected_end.isoformat()
    assert shift["source_intro_payment_attempt_id"] == str(intro_id)
    assert renewal.payload["effective_entitlement_start"] == expected_start.isoformat()
    assert renewal.payload["effective_entitlement_end"] == expected_end.isoformat()
    assert renewal.payload["invalidated_by_intro"] is True
    runner = scheduler(factory, clock, provider)
    assert await runner._claim(renewal_id, now=expected_start - timedelta(seconds=1)) is None
    assert await runner._claim(renewal_id, now=expected_start) is None
    fresh_id = await runner._ensure_primary(subscription_id, now=expected_start)
    assert fresh_id is not None
    assert fresh_id != renewal_id
    async with factory() as database:
        fresh = await database.get(PaymentAttempt, fresh_id)
    assert fresh is not None
    assert aware(fresh.billing_period_start) == expected_start
    assert aware(fresh.billing_period_end) == expected_end


@pytest.mark.asyncio
async def test_late_intro_invalidates_scheduled_retry_without_breaking_period_constraint(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider(
        status=PaymentStatus.CANCELLED,
        cancellation_reason=PaymentCancellationReason.INSUFFICIENT_FUNDS,
    )
    runner = scheduler(factory, clock, provider)
    await runner.run_once()
    async with factory() as database:
        retry = await database.scalar(
            select(PaymentAttempt).where(PaymentAttempt.renewal_attempt_number == 2)
        )
    assert retry is not None
    assert retry.status == "scheduled"

    intro_id, intro_payment = await _seed_intro_success(
        factory,
        provider=provider,
        idempotency_key="late-intro-scheduled-retry",
    )
    await BillingService(factory, provider, clock=clock).apply_verified_payment(
        intro_id,
        intro_payment,
        source="webhook",
    )

    async with factory() as database:
        retry = await database.get(PaymentAttempt, retry.id)
    assert retry is not None
    assert retry.status == "canceled"
    assert retry.next_dispatch_at is not None
    assert aware(retry.next_dispatch_at) >= clock.now + timedelta(days=15)
    assert aware(retry.billing_period_start) == clock.now
    assert retry.payload["invalidated_by_intro"] is True


@pytest.mark.asyncio
async def test_late_intro_compensation_shifts_pending_renewal_then_succeeds(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    payments = BillingService(factory, provider, clock=clock)
    async with factory() as database:
        original_period_start = aware(
            (await database.get(Subscription, subscription_id)).current_period_start
        )
    renewal_id, old_start, _old_end, original_metadata = await _seed_paid_renewal_target(
        factory,
        subscription_id,
        provider=provider,
        status="pending",
    )
    intro_id, intro_payment = await _seed_intro_success(
        factory,
        provider=provider,
        idempotency_key="late-intro-pending",
    )

    await payments.apply_verified_payment(intro_id, intro_payment, source="webhook")

    # Consume part of the introductory balance before the provider settles the
    # already-dispatched renewal.  The 400k remainder must follow the new
    # primary, while the ordinary monthly credit is added on top.
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        assert subscription is not None
        assert subscription.payment_attempt_id is not None
        old_primary_id = subscription.payment_attempt_id
        database.add(
            UsageLedger(
                user_id=subscription.user_id,
                payment_attempt_id=subscription.payment_attempt_id,
                bucket="tokens",
                entry_type="generation.consume",
                amount=-100_000,
                idempotency_key=f"late-intro-consume:{renewal_id}",
                payload={"test": True},
            )
        )

    async with factory() as database:
        shifted = await database.get(PaymentAttempt, renewal_id)
    assert shifted is not None
    assert shifted.status == "pending"
    assert aware(shifted.billing_period_start) == old_start
    assert aware(shifted.billing_period_end) == old_start + timedelta(days=30)
    assert shifted.payload["billing_period_shift"]["original_billing_period_start"] == (
        old_start.isoformat()
    )
    assert shifted.payload["billing_period_shift"]["new_billing_period_start"] == (
        old_start + timedelta(days=15)
    ).isoformat()
    assert shifted.payload["effective_entitlement_start"] == (
        old_start + timedelta(days=15)
    ).isoformat()
    assert shifted.payload["effective_entitlement_end"] == (
        old_start + timedelta(days=45)
    ).isoformat()
    assert BillingService._metadata(shifted) == original_metadata
    renewal_payment = ProviderPayment(
        provider_payment_id=str(shifted.provider_payment_id),
        status=PaymentStatus.SUCCEEDED,
        amount=Money(shifted.amount_minor, shifted.currency),
        paid=True,
        # The provider was charged before compensation and therefore returns
        # the original request metadata, not the shifted entitlement period.
        metadata=original_metadata,
        test_mode=True,
    )
    result = await payments.apply_verified_payment(
        renewal_id,
        renewal_payment,
        source="webhook",
    )
    assert result.processed is True
    replay = await payments.apply_verified_payment(
        renewal_id,
        renewal_payment,
        source="webhook",
    )
    assert replay.processed is False

    expected_end = old_start + timedelta(days=15 + 30)
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        renewal = await database.get(PaymentAttempt, renewal_id)
        ledger = list(
            await database.scalars(
                select(UsageLedger).where(
                    UsageLedger.user_id == subscription.user_id
                )
            )
        )
    assert subscription is not None
    assert renewal is not None
    assert renewal.status == "succeeded"
    assert aware(renewal.billing_period_start) == old_start
    assert aware(renewal.billing_period_end) == old_start + timedelta(days=30)
    assert aware(subscription.current_period_end) == expected_end
    assert aware(subscription.current_period_start) == original_period_start
    carry = [
        entry
        for entry in ledger
        if entry.idempotency_key
        in {
            f"renewal-carry:{renewal_id}:debit",
            f"renewal-carry:{renewal_id}:credit",
        }
    ]
    assert sorted((entry.idempotency_key, entry.amount, entry.payment_attempt_id) for entry in carry) == sorted(
        [
            (f"renewal-carry:{renewal_id}:debit", -400_000, old_primary_id),
            (f"renewal-carry:{renewal_id}:credit", 400_000, renewal_id),
        ]
    )
    normal_credit = next(
        entry
        for entry in ledger
        if entry.idempotency_key
        == f"renewal:{subscription_id}:{(old_start + timedelta(days=15)).isoformat()}:tokens"
    )
    assert normal_credit.amount == PLAN_CATALOG["starter_monthly"].generation_tokens

    # The following ordinary cycle starts from the compensated end and resets
    # the lineage to a fresh monthly credit; the carried remainder is not
    # transferred a second time.
    clock.advance(timedelta(days=45))
    runner = scheduler(factory, clock, provider)
    await runner.run_once()
    await runner.run_once()
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        current_lineage = list(
            await database.scalars(
                select(UsageLedger).where(
                    UsageLedger.payment_attempt_id == subscription.payment_attempt_id,
                    UsageLedger.bucket == "tokens",
                )
            )
        )
    assert subscription is not None
    assert sum(entry.amount for entry in current_lineage) == PLAN_CATALOG[
        "starter_monthly"
    ].generation_tokens


@pytest.mark.asyncio
async def test_late_intro_recovers_null_entitlement_primary(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    payments = BillingService(factory, provider, clock=clock)
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        assert subscription is not None
        old_end = aware(subscription.current_period_end)
        subscription.payment_attempt_id = None
    intro_id, intro_payment = await _seed_intro_success(
        factory,
        provider=provider,
        idempotency_key="late-intro-null-entitlement",
    )

    result = await payments.apply_verified_payment(
        intro_id,
        intro_payment,
        source="webhook",
    )
    assert result.processed is True
    replay = await payments.apply_verified_payment(
        intro_id,
        intro_payment,
        source="webhook",
    )
    assert replay.processed is False
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        attempt = await database.get(PaymentAttempt, intro_id)
        credits = list(await database.scalars(select(UsageLedger)))
    assert subscription is not None
    assert attempt is not None
    assert subscription.payment_attempt_id == intro_id
    assert aware(subscription.current_period_end) == old_end + timedelta(days=15)
    assert attempt.payload["founder_access_transition"][
        "entitlement_primary_recovered_from_null"
    ] is True
    assert [(credit.amount, credit.payment_attempt_id) for credit in credits] == [
        (PLAN_CATALOG["starter_intro_15d"].generation_tokens, intro_id)
    ]


@pytest.mark.asyncio
async def test_persist_response_preserves_period_shift_audit(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider(status=PaymentStatus.PENDING)
    runner = scheduler(factory, clock, provider)
    attempt_id = await runner._ensure_primary(subscription_id, now=clock.now)
    assert attempt_id is not None
    claim = await runner._claim(attempt_id, now=clock.now)
    assert claim is not None
    async with factory() as database, database.begin():
        attempt = await database.get(PaymentAttempt, attempt_id)
        assert attempt is not None
        payload = dict(attempt.payload)
        payload["billing_period_shift"] = {
            "original_billing_period_start": "2026-07-31T12:00:00+00:00",
            "original_billing_period_end": "2026-08-30T12:00:00+00:00",
            "new_billing_period_start": "2026-08-15T12:00:00+00:00",
            "new_billing_period_end": "2026-09-14T12:00:00+00:00",
        }
        attempt.payload = payload
    payment = ProviderPayment(
        provider_payment_id="pending-renewal-provider",
        status=PaymentStatus.PENDING,
        amount=claim.command.amount,
        paid=False,
        metadata=claim.command.metadata,
        test_mode=True,
    )
    assert await runner._persist_response(claim, payment) is True
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, attempt_id)
    assert attempt is not None
    assert attempt.payload["billing_period_shift"]["new_billing_period_end"] == (
        "2026-09-14T12:00:00+00:00"
    )


@pytest.mark.asyncio
async def test_due_renewal_extends_exact_period_and_credits_once(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    runner = scheduler(factory, clock, provider)

    await runner.run_once()
    await runner.run_once()

    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        attempts = list(await database.scalars(select(PaymentAttempt)))
        credits = list(await database.scalars(select(UsageLedger)))
    assert len(provider.recurring_calls) == 1
    assert len(attempts) == 1
    assert aware(subscription.current_period_start) == clock.now
    assert aware(subscription.current_period_end) == clock.now + timedelta(days=30)
    assert subscription.next_renewal_at == subscription.current_period_end
    assert [(row.bucket, row.amount) for row in credits] == [("tokens", 1_000_000)]
    assert credits[0].idempotency_key == (
        f"renewal:{subscription_id}:{clock.now.isoformat()}:tokens"
    )


@pytest.mark.asyncio
async def test_intro_period_charges_only_first_month_balance(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    intro = PLAN_CATALOG["starter_intro_15d"]
    balance = PLAN_CATALOG["starter_intro_balance_15d"]
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        subscription.plan_code = intro.code
        subscription.plan_snapshot = intro.snapshot()
        subscription.plan_fingerprint = intro.fingerprint()
        subscription.current_period_start = clock.now - timedelta(days=15)
        subscription.current_period_end = clock.now
        subscription.next_renewal_at = clock.now

    provider = FakeRecurringProvider()
    await scheduler(factory, clock, provider).run_once()

    assert len(provider.recurring_calls) == 1
    command = provider.recurring_calls[0]
    assert command.amount.amount_minor == 150_000
    assert command.metadata["plan_code"] == balance.code
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        attempt = await database.scalar(select(PaymentAttempt))
        credit = await database.scalar(select(UsageLedger))
    assert attempt.plan_code == balance.code
    assert attempt.plan_snapshot == balance.snapshot()
    assert subscription.plan_code == balance.code
    assert aware(subscription.current_period_end) == clock.now + timedelta(days=15)
    assert credit.amount == 500_000


@pytest.mark.asyncio
async def test_intro_balance_then_renews_into_regular_monthly_starter(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    balance = PLAN_CATALOG["starter_intro_balance_15d"]
    monthly = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        subscription.plan_code = balance.code
        subscription.plan_snapshot = balance.snapshot()
        subscription.plan_fingerprint = balance.fingerprint()
        subscription.current_period_start = clock.now - timedelta(days=15)
        subscription.current_period_end = clock.now
        subscription.next_renewal_at = clock.now

    provider = FakeRecurringProvider()
    await scheduler(factory, clock, provider).run_once()

    assert len(provider.recurring_calls) == 1
    assert provider.recurring_calls[0].amount.amount_minor == 200_000
    assert provider.recurring_calls[0].metadata["plan_code"] == monthly.code
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
    assert subscription.plan_code == monthly.code
    assert aware(subscription.current_period_end) == clock.now + timedelta(days=30)


@pytest.mark.asyncio
async def test_due_renewal_is_claimed_once_across_workers(recurring_db) -> None:
    factory, clock, _subscription_id = recurring_db
    provider = FakeRecurringProvider(status=PaymentStatus.PENDING)
    first = scheduler(factory, clock, provider)
    second = scheduler(factory, clock, provider)

    await asyncio.gather(first.run_once(), second.run_once())

    async with factory() as database:
        count = await database.scalar(select(func.count()).select_from(PaymentAttempt))
    assert count == 1
    assert len(provider.recurring_calls) == 1


@pytest.mark.asyncio
async def test_temporary_cancellation_schedules_one_retry_next_day(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider(
        status=PaymentStatus.CANCELLED,
        cancellation_reason=PaymentCancellationReason.INSUFFICIENT_FUNDS,
    )
    runner = scheduler(factory, clock, provider)

    await runner.run_once()
    async with factory() as database:
        attempts = list(
            await database.scalars(
                select(PaymentAttempt).order_by(PaymentAttempt.renewal_attempt_number)
            )
        )
        subscription = await database.get(Subscription, subscription_id)
    assert [(row.renewal_attempt_number, row.status) for row in attempts] == [
        (1, "cancelled"),
        (2, "scheduled"),
    ]
    assert aware(attempts[1].next_dispatch_at) == clock.now + timedelta(days=1)
    assert aware(subscription.next_renewal_at) == aware(attempts[1].next_dispatch_at)

    await runner.run_once()
    assert len(provider.recurring_calls) == 1
    clock.advance(timedelta(days=1))
    provider.status = PaymentStatus.SUCCEEDED
    provider.cancellation_reason = None
    await runner.run_once()
    assert len(provider.recurring_calls) == 2


@pytest.mark.asyncio
async def test_disabled_subscription_and_missing_receipt_email_never_post(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        subscription.auto_renew = False
        subscription.next_renewal_at = None
    await scheduler(factory, clock, provider).run_once()
    assert provider.recurring_calls == []

    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        subscription.auto_renew = True
        subscription.next_renewal_at = clock.now
        identity = await database.scalar(select(UserIdentity))
        identity.email_verified = False
    settings = BillingReceiptSettings(enabled=True, vat_code=1)
    await scheduler(factory, clock, provider, receipts=settings).run_once()
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
    assert provider.recurring_calls == []
    assert subscription.auto_renew is False
    assert aware(subscription.current_period_end) == clock.now


@pytest.mark.asyncio
async def test_permanent_cancellation_disables_method_without_shortening_period(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider(
        status=PaymentStatus.CANCELLED,
        cancellation_reason=PaymentCancellationReason.PERMISSION_REVOKED,
    )

    await scheduler(factory, clock, provider).run_once()

    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        method = await database.get(
            BillingPaymentMethod, subscription.payment_method_id
        )
        attempts = list(await database.scalars(select(PaymentAttempt)))
    assert len(attempts) == 1
    assert subscription.auto_renew is False
    assert subscription.next_renewal_at is None
    assert aware(subscription.current_period_end) == clock.now
    assert method.status == "invalid"


@pytest.mark.asyncio
async def test_ambiguous_post_never_creates_retry_and_expires_same_key(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = AmbiguousRecurringProvider()
    runner = scheduler(factory, clock, provider)

    await runner.run_once()
    clock.advance(timedelta(days=1))
    await runner.run_once()

    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        attempts = list(await database.scalars(select(PaymentAttempt)))
    assert len(provider.recurring_calls) == 1
    assert len(attempts) == 1
    assert attempts[0].status == "dispatch_unknown"
    assert subscription.auto_renew is False


@pytest.mark.asyncio
async def test_cross_provider_method_binding_fails_closed_before_post(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        method = await database.get(
            BillingPaymentMethod, subscription.payment_method_id
        )
        method.provider = "other-provider"

    await scheduler(factory, clock, provider).run_once()

    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        count = await database.scalar(select(func.count()).select_from(PaymentAttempt))
    assert provider.recurring_calls == []
    assert count == 0
    assert subscription.auto_renew is False
