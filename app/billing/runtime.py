from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.payments import BillingService
from app.billing.contracts import PaymentProvider
from app.billing.receipts import BillingReceiptSettings
from app.billing.routes import BILLING_SERVICE_KEY
from app.billing.yookassa import YooKassaProvider
from app.config import AppConfig
from app.db.session import get_session_factory

ProviderFactory = Callable[..., YooKassaProvider]


@dataclass(slots=True, repr=False)
class BillingRuntime:
    provider: PaymentProvider
    payments: BillingService
    receipt_settings: BillingReceiptSettings


def create_billing_runtime(
    config: AppConfig,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_factory: ProviderFactory = YooKassaProvider,
) -> BillingRuntime | None:
    if not config.yookassa_shop_id or not config.yookassa_secret_key:
        return None
    if not config.public_base_url:
        raise RuntimeError("public_base_url is required when billing is enabled")
    receipt_settings = BillingReceiptSettings(
        enabled=config.yookassa_receipts_enabled,
        vat_code=config.yookassa_receipt_vat_code,
        tax_system_code=config.yookassa_receipt_tax_system_code,
        payment_subject=config.yookassa_receipt_payment_subject,
        payment_mode=config.yookassa_receipt_payment_mode,
    )
    provider = provider_factory(
        shop_id=config.yookassa_shop_id,
        secret_key=config.yookassa_secret_key,
        return_url=f"{config.public_base_url.rstrip('/')}/billing/success",
        test_mode=config.yookassa_test_mode,
        timeout_seconds=config.yookassa_timeout_seconds,
    )
    payments = BillingService(
        session_factory,
        provider,
        receipt_settings=receipt_settings,
    )
    return BillingRuntime(provider, payments, receipt_settings)


def create_billing_service(
    config: AppConfig,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_factory: ProviderFactory = YooKassaProvider,
) -> BillingService | None:
    runtime = create_billing_runtime(
        config,
        session_factory,
        provider_factory=provider_factory,
    )
    return runtime.payments if runtime is not None else None


def setup_billing_runtime(app: web.Application) -> None:
    async def billing_runtime_context(application: web.Application):
        service = create_billing_service(
            application["config"], get_session_factory(application)
        )
        if service is not None:
            application[BILLING_SERVICE_KEY] = service
        try:
            yield
        finally:
            if service is not None:
                await service.close()

    app.cleanup_ctx.append(billing_runtime_context)


__all__ = [
    "BillingRuntime",
    "create_billing_runtime",
    "create_billing_service",
    "setup_billing_runtime",
]
