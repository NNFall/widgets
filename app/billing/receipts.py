from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.catalog import BillingPlan
from app.billing.contracts import Money, PaymentReceipt, ReceiptItem
from app.db.models import User
from app.saas.models import UserIdentity


class ReceiptCustomerUnavailable(RuntimeError):
    """Raised before dispatch when fiscalization has no verified customer email."""


@dataclass(frozen=True, slots=True)
class BillingReceiptSettings:
    enabled: bool = False
    vat_code: int | None = None
    payment_mode: str = "full_payment"
    payment_subject: str = "service"
    tax_system_code: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("receipt enabled must be boolean")
        if self.enabled and self.vat_code is None:
            raise ValueError("receipt vat_code is required when receipts are enabled")
        validation_vat = 1 if self.vat_code is None else self.vat_code
        # ReceiptItem is the provider-neutral source of truth for the accepted
        # YooKassa fiscal enums and bounds. Validate configuration at startup,
        # rather than waiting for the first payment attempt.
        item = ReceiptItem(
            description="Kaigo receipt settings validation",
            quantity=Decimal("1"),
            amount=Money(1, "RUB"),
            vat_code=validation_vat,
            measure="piece",
            payment_mode=self.payment_mode,
            payment_subject=self.payment_subject,
        )
        PaymentReceipt(
            customer_email="validation@kaigo.invalid",
            items=(item,),
            tax_system_code=self.tax_system_code,
        )


def _normalized_email(value: str) -> str:
    return value.strip().lower()


async def verified_receipt_email(
    database: AsyncSession,
    *,
    user_id: int,
) -> str | None:
    email = await database.scalar(
        select(User.email)
        .join(
            UserIdentity,
            and_(
                UserIdentity.user_id == User.id,
                UserIdentity.email_verified.is_(True),
                func.lower(func.trim(UserIdentity.email))
                == func.lower(func.trim(User.email)),
            ),
        )
        .where(User.id == user_id)
        .limit(1)
    )
    if not isinstance(email, str):
        return None
    normalized = _normalized_email(email)
    return normalized or None


def build_payment_receipt(
    *,
    customer_email: str,
    plan: BillingPlan,
    settings: BillingReceiptSettings,
) -> PaymentReceipt:
    if not settings.enabled or settings.vat_code is None:
        raise ValueError("receipt settings are disabled")
    return PaymentReceipt(
        customer_email=_normalized_email(customer_email),
        tax_system_code=settings.tax_system_code,
        items=(
            ReceiptItem(
                description=plan.title,
                quantity=Decimal("1.00"),
                amount=plan.amount,
                vat_code=settings.vat_code,
                measure="piece",
                payment_mode=settings.payment_mode,
                payment_subject=settings.payment_subject,
            ),
        ),
    )


async def resolve_payment_receipt(
    database: AsyncSession,
    *,
    user_id: int,
    plan: BillingPlan,
    settings: BillingReceiptSettings,
) -> PaymentReceipt | None:
    if not settings.enabled:
        return None
    customer_email = await verified_receipt_email(database, user_id=user_id)
    if customer_email is None:
        raise ReceiptCustomerUnavailable(
            "a verified account email is required for payment receipt"
        )
    return build_payment_receipt(
        customer_email=customer_email,
        plan=plan,
        settings=settings,
    )
