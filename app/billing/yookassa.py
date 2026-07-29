from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Mapping

import httpx

from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentStatus,
    ProviderCheckout,
    ProviderNotification,
    ProviderPayment,
)


class YooKassaError(RuntimeError):
    pass


class YooKassaVerificationError(YooKassaError):
    pass


_PAYMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,255}$")


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
    if value == "canceled":
        return PaymentStatus.CANCELLED
    try:
        return PaymentStatus(str(value))
    except ValueError as error:
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
        try:
            response = await self._client.post(
                "/payments",
                headers={"Idempotence-Key": command.idempotency_key},
                json={
                    "amount": {
                        "value": f"{Decimal(command.amount.amount_minor) / Decimal(100):.2f}",
                        "currency": command.amount.currency,
                    },
                    "capture": True,
                    "confirmation": {
                        "type": "redirect",
                        "return_url": self._return_url,
                    },
                    "description": command.description,
                    "metadata": dict(command.metadata),
                },
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
        )

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
        try:
            response = await self._client.get(
                f"/payments/{notification.provider_payment_id}"
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise YooKassaError("YooKassa payment verification failed") from error
        payment = self._parse_payment(payload)
        if payment.provider_payment_id != notification.provider_payment_id:
            raise YooKassaVerificationError("provider payment id mismatch")
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
        if payment.test_mode is not self._test_mode:
            raise YooKassaVerificationError("provider test mode mismatch")
        if expected_amount is not None and payment.amount != expected_amount:
            raise YooKassaVerificationError("provider payment amount mismatch")
        if expected_metadata is not None and payment.metadata != dict(expected_metadata):
            raise YooKassaVerificationError("provider payment metadata mismatch")
        return payment

    @staticmethod
    def _parse_payment(payload: object) -> ProviderPayment:
        if not isinstance(payload, dict):
            raise YooKassaVerificationError("provider response is invalid")
        payment_id = payload.get("id")
        if not isinstance(payment_id, str) or not payment_id:
            raise YooKassaVerificationError("provider payment id is invalid")
        paid, test_mode = payload.get("paid"), payload.get("test")
        if not isinstance(paid, bool) or not isinstance(test_mode, bool):
            raise YooKassaVerificationError("provider payment flags are invalid")
        return ProviderPayment(
            provider_payment_id=payment_id,
            status=_status(payload.get("status")),
            amount=_money(payload.get("amount")),
            paid=paid,
            metadata=_metadata(payload.get("metadata")),
            test_mode=test_mode,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
