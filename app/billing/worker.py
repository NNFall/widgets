from __future__ import annotations

import asyncio
import logging

from app.billing.reconciliation import PaymentReconciler
from app.billing.renewals import RenewalScheduler


logger = logging.getLogger(__name__)


class BillingWorker:
    def __init__(
        self,
        *,
        reconciler: PaymentReconciler,
        renewals: RenewalScheduler,
        renewals_enabled: bool,
        poll_seconds: float,
    ) -> None:
        if not isinstance(renewals_enabled, bool):
            raise ValueError("renewals_enabled must be boolean")
        if (
            isinstance(poll_seconds, bool)
            or not isinstance(poll_seconds, (int, float))
            or not 1 <= poll_seconds <= 60
        ):
            raise ValueError("billing poll_seconds must be between 1 and 60")
        self._reconciler = reconciler
        self._renewals = renewals
        self._renewals_enabled = renewals_enabled
        self._poll_seconds = float(poll_seconds)
        self._stopped = asyncio.Event()

    async def run_once(self) -> None:
        await self._reconciler.run_once()
        if self._renewals_enabled:
            await self._renewals.run_once()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.run_once()
            except Exception as error:
                # Provider bodies, opaque IDs and exception messages must not
                # cross this outer process boundary.
                logger.error(
                    "billing worker iteration failed (%s)",
                    type(error).__name__,
                )
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self._poll_seconds
                )
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()


__all__ = ["BillingWorker"]
