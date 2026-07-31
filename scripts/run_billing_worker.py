from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.reconciliation import PaymentReconciler
from app.billing.renewals import RenewalScheduler
from app.billing.runtime import create_billing_runtime
from app.billing.worker import BillingWorker
from app.config import load_config


logger = logging.getLogger(__name__)


def install_signal_handlers(worker: BillingWorker) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, worker.stop)
        except NotImplementedError:  # Windows event loop
            signal.signal(
                signum,
                lambda _signum, _frame: loop.call_soon_threadsafe(worker.stop),
            )


async def run() -> None:
    config = load_config()
    engine = create_async_engine(config.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = create_billing_runtime(config, sessions)
    if runtime is None:
        await engine.dispose()
        raise RuntimeError("billing provider is not configured")
    reconciler = PaymentReconciler(
        sessions,
        runtime.provider,
        payment_service=runtime.payments,
    )
    renewals = RenewalScheduler(
        sessions,
        runtime.provider,
        payment_service=runtime.payments,
        receipt_settings=runtime.receipt_settings,
    )
    worker = BillingWorker(
        reconciler=reconciler,
        renewals=renewals,
        renewals_enabled=config.billing_renewals_enabled,
        poll_seconds=config.billing_worker_poll_seconds,
    )
    install_signal_handlers(worker)
    try:
        await worker.run_forever()
    finally:
        await runtime.payments.close()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
