from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp import web
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analytics.retention import PurgeResult, purge_expired_funnel_data
from app.analytics.routes import setup_analytics_routes
from app.analytics.runtime import ANALYTICS_CLEANUP_TASK_KEY, setup_analytics_runtime
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import FunnelEvent, FunnelJourney, PaymentAttempt, Project


@pytest.mark.asyncio
async def test_purge_removes_expired_raw_attribution_and_preserves_product_rows(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=90)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Tenant", slug="tenant"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        old = FunnelJourney(started_at=cutoff - timedelta(microseconds=1))
        exact = FunnelJourney(started_at=cutoff)
        database.add_all([old, exact])
        await database.flush()
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            journey_id=old.id,
            source_url="https://example.com/",
        )
        database.add(project)
        await database.flush()
        payment = PaymentAttempt(
            user_id=10,
            project_id=project.id,
            journey_id=old.id,
            provider="yookassa",
            merchant_account_fingerprint="a" * 64,
            purpose="initial",
            auto_renew_requested=False,
            save_payment_method_requested=False,
            idempotency_key="retention-payment",
            plan_code="starter_monthly",
            plan_snapshot={},
            plan_fingerprint="b" * 64,
            amount_minor=100,
            currency="RUB",
            status="pending",
            payload={},
        )
        database.add(payment)
        database.add_all(
            [
                FunnelEvent(
                    event_key="old-journey-recent-event",
                    event_type="free_result",
                    journey_id=old.id,
                    occurred_at=now - timedelta(days=1),
                ),
                FunnelEvent(
                    event_key="legacy-old-event",
                    event_type="composer_submitted",
                    journey_id=None,
                    occurred_at=cutoff - timedelta(microseconds=1),
                ),
                FunnelEvent(
                    event_key="legacy-exact-cutoff",
                    event_type="composer_submitted",
                    journey_id=None,
                    occurred_at=cutoff,
                ),
                FunnelEvent(
                    event_key="exact-cutoff-journey-event",
                    event_type="landing_entered",
                    journey_id=exact.id,
                    occurred_at=cutoff,
                ),
            ]
        )
        await database.flush()
        old_id = old.id
        exact_id = exact.id
        project_id = project.id
        payment_id = payment.id

    async with factory() as database:
        result = await purge_expired_funnel_data(
            database,
            now=now,
            retention_days=90,
            batch_size=500,
            time_budget_seconds=5,
        )

    assert result == PurgeResult(
        lock_acquired=True,
        deleted_journeys=1,
        deleted_events=2,
    )
    async with factory() as database:
        assert await database.get(FunnelJourney, old_id) is None
        assert await database.get(FunnelJourney, exact_id) is not None
        assert await database.get(Project, project_id) is not None
        assert await database.get(PaymentAttempt, payment_id) is not None
        assert (await database.get(Project, project_id)).journey_id is None
        assert (await database.get(PaymentAttempt, payment_id)).journey_id is None
        remaining_keys = set(
            (await database.execute(select(FunnelEvent.event_key))).scalars()
        )
        assert remaining_keys == {
            "legacy-exact-cutoff",
            "exact-cutoff-journey-event",
        }
    await engine.dispose()


@pytest.mark.asyncio
async def test_analytics_runtime_runs_before_ready_and_cancels_hourly_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_purge(_database, **_kwargs) -> PurgeResult:
        calls.append("purged")
        return PurgeResult(True, 0, 0)

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr("app.analytics.runtime.purge_expired_funnel_data", fake_purge)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = lambda: Session()
    app["config"] = SimpleNamespace(
        funnel_journeys_enabled=True,
        funnel_retention_days=90,
        funnel_cleanup_interval_seconds=3600,
        funnel_cleanup_batch_size=500,
        funnel_cleanup_time_budget_seconds=5,
    )
    setup_analytics_runtime(app)
    assert len(app.cleanup_ctx) == 1
    context = app.cleanup_ctx[0](app)
    await anext(context)
    assert calls == ["purged"]
    task = app[ANALYTICS_CLEANUP_TASK_KEY]
    assert not task.done()
    with pytest.raises(StopAsyncIteration):
        await anext(context)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_analytics_runtime_aborts_startup_when_first_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_purge(_database, **_kwargs) -> PurgeResult:
        raise RuntimeError("retention unavailable")

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr("app.analytics.runtime.purge_expired_funnel_data", broken_purge)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = lambda: Session()
    app["config"] = SimpleNamespace(
        funnel_journeys_enabled=True,
        funnel_retention_days=90,
        funnel_cleanup_interval_seconds=3600,
        funnel_cleanup_batch_size=500,
        funnel_cleanup_time_budget_seconds=5,
    )
    setup_analytics_runtime(app)
    context = app.cleanup_ctx[0](app)
    with pytest.raises(RuntimeError, match="retention unavailable"):
        await anext(context)
    assert ANALYTICS_CLEANUP_TASK_KEY not in app
    await asyncio.sleep(0)


def test_analytics_runtime_is_absent_when_rollout_is_disabled() -> None:
    app = web.Application()
    app["config"] = SimpleNamespace(funnel_journeys_enabled=False)
    setup_analytics_runtime(app)
    assert not app.cleanup_ctx


def test_landing_entry_route_is_absent_when_rollout_is_disabled() -> None:
    app = web.Application()
    setup_analytics_routes(app, enabled=False)
    assert not app.router.resources()
