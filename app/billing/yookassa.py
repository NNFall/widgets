from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Mapping

import httpx

from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentCancellationReason,
    PaymentReceipt,
    PaymentStatus,
    ProviderCheckout,
    ProviderNotification,
    ProviderPayment,
    ProviderPaymentMethod,
    RecurringPaymentCommand,
    validate_provider_idempotency_key,
)


class YooKassaError(RuntimeError):
    pass


class YooKassaVerificationError(YooKassaError):
    pass


_PAYMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_CANCELLATION_PARTIES = frozenset({"yoo_money", "payment_network", "merchant"})


def _validated_provider_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _PAYMENT_ID_PATTERN.fullmatch(value):
        raise YooKassaVerificationError(f"provider {field_name} is invalid")
    return value


def _validated_idempotency_key(value: object) -> str:
    try:
        return validate_provider_idempotency_key(value)
    except ValueError as error:
        raise YooKassaVerificationError(
            "provider idempotency key is invalid"
        ) from error


def _provider_amount(money: Money) -> dict[str, str]:
    return {
        "value": f"{Decimal(money.amount_minor) / Decimal(100):.2f}",
        "currency": money.currency,
    }


def _provider_quantity(quantity: Decimal) -> int | float:
    if quantity == quantity.to_integral_value():
        return int(quantity)
    return float(quantity)


def _receipt_payload(receipt: PaymentReceipt, *, expected_amount: Money) -> dict[str, object]:
    if not isinstance(receipt, PaymentReceipt):
        raise ValueError("receipt must be a validated PaymentReceipt")
    total_minor = Decimal(0)
    items: list[dict[str, object]] = []
    for item in receipt.items:
        if item.amount.currency != expected_amount.currency:
            raise ValueError("receipt total currency does not match payment amount")
        total_minor += Decimal(item.amount.amount_minor) * Decimal(item.quantity)
        items.append(
            {
                "description": item.description,
                "quantity": _provider_quantity(item.quantity),
                "amount": _provider_amount(item.amount),
                "vat_code": item.vat_code,
                "measure": item.measure,
                "payment_mode": item.payment_mode,
                "payment_subject": item.payment_subject,
            }
        )
    if total_minor != Decimal(expected_amount.amount_minor):
        raise ValueError("receipt total does not match payment amount")
    payload: dict[str, object] = {
        "customer": {"email": receipt.customer_email},
        "items": items,
    }
    if receipt.tax_system_code is not None:
        payload["tax_system_code"] = receipt.tax_system_code
    return payload


def _payment_method(payload: object) -> ProviderPaymentMethod | None:
    if not isinstance(payload, dict) or payload.get("saved") is not True:
        return None
    method_id = _validated_provider_id(
        payload.get("id"), field_name="payment method id"
    )
    method_type = payload.get("type")
    if method_type is not None and (
        not isinstance(method_type, str) or not method_type.strip()
    ):
        raise YooKassaVerificationError("provider payment method type is invalid")
    return ProviderPaymentMethod(
        provider_payment_method_id=method_id,
        saved=True,
        method_type=method_type,
    )


def _cancellation_reason(
    payload: object, *, status: PaymentStatus
) -> PaymentCancellationReason | None:
    if status is not PaymentStatus.CANCELLED or payload is None:
        return None
    if not isinstance(payload, dict):
        raise YooKassaVerificationError("provider cancellation details are invalid")
    party = payload.get("party")
    reason = payload.get("reason")
    if party not in _CANCELLATION_PARTIES or not isinstance(reason, str):
        raise YooKassaVerificationError("provider cancellation details are invalid")
    try:
        return PaymentCancellationReason(reason)
    except ValueError:
        return PaymentCancellationReason.UNKNOWN


def _money(payload: object) -> Money:
    if not isinstance(payload, dict):
        raise YooKassaVerificationError("provider amount is invalid")
    value, currency = payload.get("value"), payload.get("currency")
    if not isinstance(value, str) or not isinstance(currency, str):
        raise YooKassaVerificationError("provider amount is invalid")
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise YooKassaVerificationError("provider amount is invalid") from error
    minor = decimal * 100
    if minor != minor.to_integral_value():
        raise YooKassaVerificationError("provider amount precision is invalid")
    return Money(int(minor), currency)


def _metadata(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in payload.items()
    ):
        raise YooKassaVerificationError("provider metadata is invalid")
    return dict(payload)


def _status(value: object) -> PaymentStatus:
    statuses = {
        "pending": PaymentStatus.PENDING,
        "succeeded": PaymentStatus.SUCCEEDED,
        "canceled": PaymentStatus.CANCELLED,
    }
    try:
        return statuses[value]
    except (KeyError, TypeError) as error:
        raise YooKassaVerificationError("provider status is invalid") from error


class YooKassaProvider:
    name = "yookassa"
    _BASE_URL = "https://api.yookassa.ru/v3"

    def __init__(
        self,
        *,
        shop_id: str,
        secret_key: str,
        return_url: str,
        test_mode: bool,
        timeout_seconds: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not shop_id.strip() or not secret_key.strip():
            raise ValueError("YooKassa credentials must not be blank")
        merchant_identity = (
            f"kaigo-payment-merchant-v1\x00{self.name}\x00{shop_id.strip()}"
        )
        self.merchant_account_fingerprint = sha256(
            merchant_identity.encode("utf-8")
        ).hexdigest()
        self._return_url = return_url
        self._test_mode = test_mode
        self._client = httpx.AsyncClient(
            base_url=self._BASE_URL,
            auth=(shop_id, secret_key),
            timeout=timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
        idempotency_key = _validated_idempotency_key(command.idempotency_key)
        body: dict[str, object] = {
            "amount": _provider_amount(command.amount),
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": self._return_url,
            },
            "description": command.description,
            "metadata": dict(command.metadata),
        }
        if command.save_payment_method is not None:
            body["save_payment_method"] = command.save_payment_method
        if command.receipt is not None:
            body["receipt"] = _receipt_payload(
                command.receipt, expected_amount=command.amount
            )
        try:
            response = await self._client.post(
                "/payments",
                headers={"Idempotence-Key": idempotency_key},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise YooKassaError("YooKassa checkout request failed") from error
        payment = self._parse_payment(payload)
        if payment.test_mode is not self._test_mode:
            raise YooKassaVerificationError("provider test mode mismatch")
        confirmation = payload.get("confirmation") if isinstance(payload, dict) else None
        checkout_url = confirmation.get("confirmation_url") if isinstance(confirmation, dict) else None
        if not isinstance(checkout_url, str) or not checkout_url.startswith("https://"):
            raise YooKassaVerificationError("provider checkout URL is invalid")
        return ProviderCheckout(
            provider_payment_id=payment.provider_payment_id,
            checkout_url=checkout_url,
            status=payment.status,
            amount=payment.amount,
            paid=payment.paid,
            metadata=payment.metadata,
            test_mode=payment.test_mode,
            payment_method=payment.payment_method,
        )

    async def create_recurring_payment(
        self, command: RecurringPaymentCommand
    ) -> ProviderPayment:
        idempotency_key = _validated_idempotency_key(command.idempotency_key)
        payment_method_id = _validated_provider_id(
            command.payment_method_id, field_name="payment method id"
        )
        body: dict[str, object] = {
            "amount": _provider_amount(command.amount),
            "capture": True,
            "payment_method_id": payment_method_id,
            "description": command.description,
            "metadata": dict(command.metadata),
        }
        if command.receipt is not None:
            body["receipt"] = _receipt_payload(
                command.receipt, expected_amount=command.amount
            )
        try:
            response = await self._client.post(
                "/payments",
                headers={"Idempotence-Key": idempotency_key},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise YooKassaError("YooKassa recurring payment request failed") from error
        payment = self._parse_payment(payload)
        self._verify_payment(
            payment,
            expected_id=payment.provider_payment_id,
            expected_amount=command.amount,
            expected_metadata=command.metadata,
        )
        return payment

    def parse_notification(self, payload: object) -> ProviderNotification:
        if not isinstance(payload, dict) or payload.get("event") not in {
            "payment.succeeded",
            "payment.canceled",
        }:
            raise YooKassaVerificationError("notification event is invalid")
        object_payload = payload.get("object")
        payment_id = object_payload.get("id") if isinstance(object_payload, dict) else None
        if not isinstance(payment_id, str) or not _PAYMENT_ID_PATTERN.fullmatch(payment_id):
            raise YooKassaVerificationError("notification payment id is invalid")
        return ProviderNotification(payment_id, str(payload["event"]))

    async def verify_notification(
        self,
        notification: ProviderNotification,
        *,
        expected_amount: Money | None = None,
        expected_metadata: Mapping[str, str] | None = None,
    ) -> ProviderPayment:
        payment = await self.get_payment(
            notification.provider_payment_id,
            expected_amount=expected_amount,
            expected_metadata=expected_metadata,
        )
        if notification.event == "payment.succeeded":
            if payment.status is not PaymentStatus.SUCCEEDED or not payment.paid:
                raise YooKassaVerificationError(
                    "provider payment is not succeeded and paid"
                )
        elif notification.event == "payment.canceled":
            if payment.status is not PaymentStatus.CANCELLED or payment.paid:
                raise YooKassaVerificationError("provider payment is not canceled")
        else:
            raise YooKassaVerificationError("notification event is invalid")
        return payment

    async def get_payment(
        self,
        provider_payment_id: str,
        *,
        expected_amount: Money | None = None,
        expected_metadata: Mapping[str, str] | None = None,
    ) -> ProviderPayment:
        safe_payment_id = _validated_provider_id(
            provider_payment_id, field_name="payment id"
        )
        try:
            response = await self._client.get(f"/payments/{safe_payment_id}")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise YooKassaError("YooKassa payment verification failed") from error
        payment = self._parse_payment(payload)
        self._verify_payment(
            payment,
            expected_id=safe_payment_id,
            expected_amount=expected_amount,
            expected_metadata=expected_metadata,
        )
        return payment

    def _verify_payment(
        self,
        payment: ProviderPayment,
        *,
        expected_id: str,
        expected_amount: Money | None,
        expected_metadata: Mapping[str, str] | None,
    ) -> None:
        if payment.provider_payment_id != expected_id:
            raise YooKassaVerificationError("provider payment id mismatch")
        if payment.test_mode is not self._test_mode:
            raise YooKassaVerificationError("provider test mode mismatch")
        if expected_amount is not None and payment.amount != expected_amount:
            raise YooKassaVerificationError("provider payment amount mismatch")
        if expected_metadata is not None and payment.metadata != dict(expected_metadata):
            raise YooKassaVerificationError("provider payment metadata mismatch")

    @staticmethod
    def _parse_payment(payload: object) -> ProviderPayment:
        if not isinstance(payload, dict):
            raise YooKassaVerificationError("provider response is invalid")
        payment_id = _validated_provider_id(payload.get("id"), field_name="payment id")
        paid, test_mode = payload.get("paid"), payload.get("test")
        if not isinstance(paid, bool) or not isinstance(test_mode, bool):
            raise YooKassaVerificationError("provider payment flags are invalid")
        status = _status(payload.get("status"))
        if (
            (status is PaymentStatus.PENDING and paid)
            or (status is PaymentStatus.SUCCEEDED and not paid)
            or (status is PaymentStatus.CANCELLED and paid)
        ):
            raise YooKassaVerificationError("provider payment flags are inconsistent")
        payment_method = None
        if status is PaymentStatus.SUCCEEDED and paid:
            payment_method = _payment_method(payload.get("payment_method"))
        return ProviderPayment(
            provider_payment_id=payment_id,
            status=status,
            amount=_money(payload.get("amount")),
            paid=paid,
            metadata=_metadata(payload.get("metadata")),
            test_mode=test_mode,
            payment_method=payment_method,
            cancellation_reason=_cancellation_reason(
                payload.get("cancellation_details"), status=status
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
