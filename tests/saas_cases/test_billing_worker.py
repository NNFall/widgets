from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.billing import worker as worker_module
from app.billing.worker import BillingWorker
from scripts.run_billing_worker import _database_url


def test_billing_worker_normalizes_postgresql_dsn_for_asyncpg() -> None:
    assert _database_url("postgresql://user:pass@db/kaigo") == (
        "postgresql+asyncpg://user:pass@db/kaigo"
    )
    assert _database_url("postgresql+asyncpg://user:pass@db/kaigo") == (
        "postgresql+asyncpg://user:pass@db/kaigo"
    )

    with pytest.raises(RuntimeError, match="PostgreSQL with asyncpg"):
        _database_url("sqlite+aiosqlite:///billing.db")


@pytest.mark.asyncio
async def test_worker_reconciles_when_renewals_are_disabled() -> None:
    reconciler = AsyncMock()
    renewals = AsyncMock()
    worker = BillingWorker(
        reconciler=reconciler,
        renewals=renewals,
        renewals_enabled=False,
        poll_seconds=5,
    )

    await worker.run_once()

    reconciler.run_once.assert_awaited_once()
    renewals.run_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_dispatches_renewals_only_with_explicit_flag() -> None:
    reconciler = AsyncMock()
    renewals = AsyncMock()
    worker = BillingWorker(
        reconciler=reconciler,
        renewals=renewals,
        renewals_enabled=True,
        poll_seconds=5,
    )

    await worker.run_once()

    reconciler.run_once.assert_awaited_once()
    renewals.run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_stops_without_waiting_full_poll_and_redacts_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[str] = []
    monkeypatch.setattr(
        worker_module.logger,
        "error",
        lambda message, *args: logged.append(message % args),
    )
    reconciler = AsyncMock()
    entered = asyncio.Event()

    async def fail_reconciliation() -> None:
        entered.set()
        raise RuntimeError("sentinel-provider-body")

    reconciler.run_once.side_effect = fail_reconciliation
    worker = BillingWorker(
        reconciler=reconciler,
        renewals=AsyncMock(),
        renewals_enabled=False,
        poll_seconds=60,
    )

    task = asyncio.create_task(worker.run_forever())
    await asyncio.wait_for(entered.wait(), timeout=1)
    await asyncio.sleep(0)
    worker.stop()
    await asyncio.wait_for(task, timeout=1)

    assert "sentinel-provider-body" not in "\n".join(logged)
    assert "RuntimeError" in "\n".join(logged)
