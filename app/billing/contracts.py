from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        currency = self.currency.strip().upper()
        if isinstance(self.amount_minor, bool) or self.amount_minor <= 0:
            raise ValueError("amount_minor must be positive")
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValueError("currency must be an ISO 4217 code")
        object.__setattr__(self, "currency", currency)


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CheckoutCommand:
    idempotency_key: str
    amount: Money
    description: str
    metadata: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ProviderCheckout:
    provider_payment_id: str
    checkout_url: str
    status: PaymentStatus
    amount: Money
    paid: bool
    metadata: Mapping[str, str]
    test_mode: bool


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    provider_payment_id: str
    status: PaymentStatus
    amount: Money
    paid: bool
    metadata: Mapping[str, str]
    test_mode: bool


@dataclass(frozen=True, slots=True)
class ProviderNotification:
    provider_payment_id: str
    event: str


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout: ...

    def parse_notification(self, payload: object) -> ProviderNotification: ...

    async def verify_notification(
        self,
        notification: ProviderNotification,
        *,
        expected_amount: Money | None = None,
        expected_metadata: Mapping[str, str] | None = None,
    ) -> ProviderPayment: ...

    async def aclose(self) -> None: ...
