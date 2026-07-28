from __future__ import annotations

import json

import httpx
import pytest

from app.billing.contracts import CheckoutCommand, Money, PaymentStatus
from app.billing.yookassa import YooKassaError, YooKassaProvider, YooKassaVerificationError


@pytest.mark.asyncio
async def test_yookassa_checkout_uses_server_amount_and_safe_return_url() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "pay_1",
                "status": "pending",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "confirmation": {"confirmation_url": "https://yoomoney.ru/checkout/pay_1"},
                "metadata": {
                    "payment_attempt_id": "attempt-1",
                    "user_id": "10",
                    "plan_code": "starter",
                    "plan_fingerprint": "fingerprint",
                },
                "test": True,
            },
            request=request,
        )

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.create_checkout(
        CheckoutCommand(
            idempotency_key="checkout-key",
            amount=Money(199_000, "RUB"),
            description="Kaigo Starter",
            metadata={
                "payment_attempt_id": "attempt-1",
                "user_id": "10",
                "plan_code": "starter",
                "plan_fingerprint": "fingerprint",
            },
        )
    )

    assert result.provider_payment_id == "pay_1"
    assert result.checkout_url == "https://yoomoney.ru/checkout/pay_1"
    assert result.status is PaymentStatus.PENDING
    assert len(requests) == 1
    assert requests[0].url == httpx.URL("https://api.yookassa.ru/v3/payments")
    assert requests[0].headers["Idempotence-Key"] == "checkout-key"
    body = json.loads(requests[0].content)
    assert body["amount"] == {"value": "1990.00", "currency": "RUB"}
    assert body["confirmation"] == {
        "type": "redirect",
        "return_url": "https://kaigo.space/billing/success",
    }
    assert "secret" not in repr(provider)
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_notification_is_verified_by_provider_get() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.yookassa.ru/v3/payments/pay_1")
        return httpx.Response(
            200,
            json={
                "id": "pay_1",
                "status": "succeeded",
                "paid": True,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {
                    "payment_attempt_id": "attempt-1",
                    "user_id": "10",
                    "plan_code": "starter",
                    "plan_fingerprint": "fingerprint",
                },
                "test": True,
            },
            request=request,
        )

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    notification = provider.parse_notification(
        {"event": "payment.succeeded", "object": {"id": "pay_1"}}
    )
    verified = await provider.verify_notification(notification)

    assert verified.provider_payment_id == "pay_1"
    assert verified.status is PaymentStatus.SUCCEEDED
    assert verified.paid is True
    assert verified.amount == Money(199_000, "RUB")
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_cancellation_is_verified_and_normalized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pay_1",
                "status": "canceled",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {"payment_attempt_id": "attempt-1"},
                "test": True,
            },
            request=request,
        )

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    notification = provider.parse_notification(
        {"event": "payment.canceled", "object": {"id": "pay_1"}}
    )
    verified = await provider.verify_notification(
        notification,
        expected_amount=Money(199_000, "RUB"),
        expected_metadata={"payment_attempt_id": "attempt-1"},
    )

    assert notification.event == "payment.canceled"
    assert verified.status is PaymentStatus.CANCELLED
    assert verified.paid is False
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "pending"),
        ("paid", False),
        ("amount", {"value": "1.00", "currency": "RUB"}),
        ("metadata", {"payment_attempt_id": "other"}),
        ("test", False),
    ],
)
async def test_yookassa_verification_rejects_mismatch(field: str, value: object) -> None:
    payload = {
        "id": "pay_1",
        "status": "succeeded",
        "paid": True,
        "amount": {"value": "1990.00", "currency": "RUB"},
        "metadata": {
            "payment_attempt_id": "attempt-1",
            "user_id": "10",
            "plan_code": "starter",
            "plan_fingerprint": "fingerprint",
        },
        "test": True,
    }
    payload[field] = value

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    notification = provider.parse_notification(
        {"event": "payment.succeeded", "object": {"id": "pay_1"}}
    )
    with pytest.raises(YooKassaVerificationError):
        await provider.verify_notification(
            notification,
            expected_amount=Money(199_000, "RUB"),
            expected_metadata={
                "payment_attempt_id": "attempt-1",
                "user_id": "10",
                "plan_code": "starter",
                "plan_fingerprint": "fingerprint",
            },
        )
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_rejects_unsafe_notification_payment_id() -> None:
    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request)),
    )
    with pytest.raises(YooKassaVerificationError):
        provider.parse_notification(
            {"event": "payment.succeeded", "object": {"id": "../payments/other"}}
        )
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_checkout_rejects_test_mode_mismatch() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pay_1",
                "status": "pending",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "confirmation": {"confirmation_url": "https://yoomoney.ru/pay/1"},
                "metadata": {"payment_attempt_id": "attempt-1"},
                "test": False,
            },
            request=request,
        )

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(YooKassaVerificationError):
        await provider.create_checkout(
            CheckoutCommand(
                idempotency_key="checkout-key",
                amount=Money(199_000, "RUB"),
                description="Kaigo",
                metadata={"payment_attempt_id": "attempt-1"},
            )
        )
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_wraps_transport_failure_without_leaking_credentials() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dial failed", request=request)

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    notification = provider.parse_notification(
        {"event": "payment.succeeded", "object": {"id": "pay_1"}}
    )
    with pytest.raises(YooKassaError, match="verification") as caught:
        await provider.verify_notification(notification)
    assert "secret" not in str(caught.value)
    await provider.aclose()
