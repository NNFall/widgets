from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import re
from typing import Mapping, Protocol


_OPAQUE_PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_PROVIDER_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[0-9A-Za-z+_.-]{1,64}$")
_PAYMENT_MODES = frozenset(
    {
        "full_prepayment",
        "full_payment",
    }
)
_RECEIPT_MEASURES = frozenset(
    {
        "piece",
        "gram",
        "kilogram",
        "ton",
        "centimeter",
        "decimeter",
        "meter",
        "square_centimeter",
        "square_decimeter",
        "square_meter",
        "milliliter",
        "liter",
        "cubic_meter",
        "kilowatt_hour",
        "gigacalorie",
        "day",
        "hour",
        "minute",
        "second",
        "kilobyte",
        "megabyte",
        "gigabyte",
        "terabyte",
        "another",
    }
)
_PAYMENT_SUBJECTS = frozenset(
    {
        "commodity",
        "excise",
        "job",
        "service",
        "casino",
        "gambling_bet",
        "gambling_prize",
        "lottery",
        "lottery_prize",
        "intellectual_activity",
        "payment",
        "agent_commission",
        "property_right",
        "non_operating_gain",
        "sales_tax",
        "resort_fee",
        "another",
        "marked",
        "non_marked",
        "marked_excise",
        "non_marked_excise",
        "fine",
        "tax",
        "lien",
        "cost",
        "agent_withdrawals",
        "pension_insurance_without_payouts",
        "pension_insurance_with_payouts",
        "health_insurance_without_payouts",
        "health_insurance_with_payouts",
        "health_insurance",
    }
)


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        currency = self.currency.strip().upper()
        if (
            isinstance(self.amount_minor, bool)
            or not isinstance(self.amount_minor, int)
            or self.amount_minor <= 0
        ):
            raise ValueError("amount_minor must be a positive integer")
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("currency must be an ISO 4217 code")
        object.__setattr__(self, "currency", currency)


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PaymentCancellationReason(str, Enum):
    THREE_D_SECURE_FAILED = "3d_secure_failed"
    CALL_ISSUER = "call_issuer"
    CARD_EXPIRED = "card_expired"
    PAYMENT_METHOD_LIMIT_EXCEEDED = "payment_method_limit_exceeded"
    PAYMENT_METHOD_RESTRICTED = "payment_method_restricted"
    COUNTRY_FORBIDDEN = "country_forbidden"
    GENERAL_DECLINE = "general_decline"
    FRAUD_SUSPECTED = "fraud_suspected"
    IDENTIFICATION_REQUIRED = "identification_required"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    INVALID_CARD_NUMBER = "invalid_card_number"
    INVALID_CSC = "invalid_csc"
    ISSUER_UNAVAILABLE = "issuer_unavailable"
    CANCELED_BY_MERCHANT = "canceled_by_merchant"
    PERMISSION_REVOKED = "permission_revoked"
    INTERNAL_TIMEOUT = "internal_timeout"
    EXPIRED_ON_CONFIRMATION = "expired_on_confirmation"
    EXPIRED_ON_CAPTURE = "expired_on_capture"
    UNSUPPORTED_MOBILE_OPERATOR = "unsupported_mobile_operator"
    DEAL_EXPIRED = "deal_expired"
    LOAN_DECLINED = "loan_declined"
    LOAN_DECLINED_BY_PAYER = "loan_declined_by_payer"
    LOAN_APPLICATION_EXPIRED = "loan_application_expired"
    UNKNOWN = "unknown"


def validate_provider_idempotency_key(value: object) -> str:
    """Return a YooKassa-compatible idempotency key without normalizing it."""
    if not isinstance(value, str) or not _PROVIDER_IDEMPOTENCY_KEY_PATTERN.fullmatch(
        value
    ):
        raise ValueError("idempotency_key is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ReceiptItem:
    description: str
    quantity: Decimal
    amount: Money
    vat_code: int
    measure: str
    payment_mode: str
    payment_subject: str

    def __post_init__(self) -> None:
        description = self.description.strip()
        if not description or len(description) > 128:
            raise ValueError("receipt item description must be 1..128 characters")
        if not isinstance(self.quantity, Decimal) or not self.quantity.is_finite():
            raise ValueError("receipt item quantity must be a finite Decimal")
        quantity = self.quantity.normalize()
        decimal_places = max(0, -quantity.as_tuple().exponent)
        if quantity <= 0 or quantity > Decimal("99999.999"):
            raise ValueError("receipt item quantity is outside YooKassa bounds")
        if decimal_places > 3:
            raise ValueError("receipt item quantity supports at most 3 decimal places")
        if (
            isinstance(self.vat_code, bool)
            or not isinstance(self.vat_code, int)
            or not 1 <= self.vat_code <= 12
        ):
            raise ValueError("receipt item vat_code must be between 1 and 12")
        if self.measure not in _RECEIPT_MEASURES:
            raise ValueError("receipt item measure is invalid")
        if self.payment_mode not in _PAYMENT_MODES:
            raise ValueError("receipt item payment_mode is invalid")
        if self.payment_subject not in _PAYMENT_SUBJECTS:
            raise ValueError("receipt item payment_subject is invalid")
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "quantity", quantity)


@dataclass(frozen=True, slots=True)
class PaymentReceipt:
    customer_email: str
    items: tuple[ReceiptItem, ...]
    tax_system_code: int | None = None

    def __post_init__(self) -> None:
        email = self.customer_email.strip()
        if (
            not email
            or len(email) > 254
            or email.count("@") != 1
            or any(character.isspace() for character in email)
        ):
            raise ValueError("receipt customer email is invalid")
        try:
            items = tuple(self.items)
        except TypeError as error:
            raise ValueError("receipt must contain validated items") from error
        if not items or any(not isinstance(item, ReceiptItem) for item in items):
            raise ValueError("receipt must contain validated items")
        if len(items) > 80:
            raise ValueError("YooKassa receipt cannot contain more than 80 items")
        if self.tax_system_code is not None and (
            isinstance(self.tax_system_code, bool)
            or not isinstance(self.tax_system_code, int)
            or not 1 <= self.tax_system_code <= 6
        ):
            raise ValueError("receipt tax_system_code must be between 1 and 6")
        object.__setattr__(self, "customer_email", email)
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class CheckoutCommand:
    idempotency_key: str
    amount: Money
    description: str
    metadata: Mapping[str, str]
    save_payment_method: bool | None = None
    receipt: PaymentReceipt | None = None

    def __post_init__(self) -> None:
        validate_provider_idempotency_key(self.idempotency_key)
        if self.save_payment_method is not None and not isinstance(
            self.save_payment_method, bool
        ):
            raise ValueError("save_payment_method must be boolean or omitted")


@dataclass(frozen=True, slots=True)
class RecurringPaymentCommand:
    idempotency_key: str
    payment_method_id: str = field(repr=False)
    amount: Money
    description: str
    metadata: Mapping[str, str]
    receipt: PaymentReceipt | None = None

    def __post_init__(self) -> None:
        validate_provider_idempotency_key(self.idempotency_key)
        if not isinstance(self.payment_method_id, str) or not (
            _OPAQUE_PROVIDER_ID_PATTERN.fullmatch(self.payment_method_id)
        ):
            raise ValueError("payment_method_id is invalid")


@dataclass(frozen=True, slots=True)
class ProviderPaymentMethod:
    provider_payment_method_id: str = field(repr=False)
    saved: bool
    method_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_payment_method_id, str) or not (
            _OPAQUE_PROVIDER_ID_PATTERN.fullmatch(self.provider_payment_method_id)
        ):
            raise ValueError("provider payment method id is invalid")
        if not isinstance(self.saved, bool):
            raise ValueError("provider payment method saved flag must be boolean")
        if self.method_type is not None and (
            not isinstance(self.method_type, str) or not self.method_type.strip()
        ):
            raise ValueError("provider payment method type is invalid")


@dataclass(frozen=True, slots=True)
class ProviderCheckout:
    provider_payment_id: str
    checkout_url: str
    status: PaymentStatus
    amount: Money
    paid: bool
    metadata: Mapping[str, str]
    test_mode: bool
    payment_method: ProviderPaymentMethod | None = None


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    provider_payment_id: str
    status: PaymentStatus
    amount: Money
    paid: bool
    metadata: Mapping[str, str]
    test_mode: bool
    payment_method: ProviderPaymentMethod | None = None
    cancellation_reason: PaymentCancellationReason | None = None


@dataclass(frozen=True, slots=True)
class ProviderNotification:
    provider_payment_id: str
    event: str


class PaymentProvider(Protocol):
    name: str
    merchant_account_fingerprint: str

    async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout: ...

    async def create_recurring_payment(
        self, command: RecurringPaymentCommand
    ) -> ProviderPayment: ...

    async def get_payment(
        self,
        provider_payment_id: str,
        *,
        expected_amount: Money | None = None,
        expected_metadata: Mapping[str, str] | None = None,
    ) -> ProviderPayment: ...

    def parse_notification(self, payload: object) -> ProviderNotification: ...

    async def verify_notification(
        self,
        notification: ProviderNotification,
        *,
        expected_amount: Money | None = None,
        expected_metadata: Mapping[str, str] | None = None,
    ) -> ProviderPayment: ...

    async def aclose(self) -> None: ...
