from __future__ import annotations

from decimal import Decimal
import json

import httpx
import pytest

from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentStatus,
    RecurringPaymentCommand,
)
from app.billing.yookassa import YooKassaError, YooKassaProvider, YooKassaVerificationError


@pytest.mark.parametrize("amount_minor", [1.0, 1.5, Decimal("1"), True])
def test_money_rejects_non_integer_minor_units(amount_minor: object) -> None:
    with pytest.raises(ValueError, match="amount_minor"):
        Money(amount_minor, "RUB")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "idempotency_key",
    ["", "has:colon", "has space", "non-ascii-ключ", "x" * 65],
)
def test_yookassa_commands_reject_invalid_idempotency_key(
    idempotency_key: str,
) -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        CheckoutCommand(
            idempotency_key=idempotency_key,
            amount=Money(100, "RUB"),
            description="Kaigo",
            metadata={"payment_attempt_id": "attempt-1"},
        )
    with pytest.raises(ValueError, match="idempotency_key"):
        RecurringPaymentCommand(
            idempotency_key=idempotency_key,
            payment_method_id="opaque-method-id",
            amount=Money(100, "RUB"),
            description="Kaigo renewal",
            metadata={"payment_attempt_id": "attempt-1"},
        )


@pytest.mark.parametrize(
    "idempotency_key",
    ["a", "Kaigo-._+09", "x" * 64],
)
def test_yookassa_commands_accept_documented_idempotency_key_boundary(
    idempotency_key: str,
) -> None:
    checkout = CheckoutCommand(
        idempotency_key=idempotency_key,
        amount=Money(100, "RUB"),
        description="Kaigo",
        metadata={"payment_attempt_id": "attempt-1"},
    )
    recurring = RecurringPaymentCommand(
        idempotency_key=idempotency_key,
        payment_method_id="opaque-method-id",
        amount=Money(100, "RUB"),
        description="Kaigo renewal",
        metadata={"payment_attempt_id": "attempt-1"},
    )

    assert checkout.idempotency_key == idempotency_key
    assert recurring.idempotency_key == idempotency_key


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
    assert "save_payment_method" not in body
    assert "receipt" not in body
    assert "secret" not in repr(provider)
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_checkout_sends_explicit_method_save_and_validated_receipt() -> None:
    from app.billing.contracts import PaymentReceipt, ReceiptItem

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "pay_receipt_1",
                "status": "pending",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "confirmation": {
                    "confirmation_url": "https://yoomoney.ru/checkout/pay_receipt_1"
                },
                "metadata": {"payment_attempt_id": "attempt-receipt-1"},
                "payment_method": {
                    "id": "opaque-checkout-method",
                    "saved": True,
                    "type": "bank_card",
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
            idempotency_key="checkout-receipt-key",
            amount=Money(199_000, "RUB"),
            description="Kaigo Starter",
            metadata={"payment_attempt_id": "attempt-receipt-1"},
            save_payment_method=True,
            receipt=PaymentReceipt(
                customer_email="buyer@example.com",
                items=(
                    ReceiptItem(
                        description="Kaigo Starter",
                        quantity=Decimal("1.00"),
                        amount=Money(199_000, "RUB"),
                        vat_code=1,
                        measure="piece",
                        payment_mode="full_payment",
                        payment_subject="service",
                    ),
                ),
                tax_system_code=1,
            ),
        )
    )

    assert result.provider_payment_id == "pay_receipt_1"
    assert result.payment_method is None
    body = json.loads(requests[0].content)
    assert body["save_payment_method"] is True
    assert body["receipt"] == {
        "customer": {"email": "buyer@example.com"},
        "tax_system_code": 1,
        "items": [
            {
                "description": "Kaigo Starter",
                "quantity": 1,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "vat_code": 1,
                "measure": "piece",
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }
        ],
    }
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_receipt_total_must_match_checkout_amount_before_request() -> None:
    from app.billing.contracts import PaymentReceipt, ReceiptItem

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, request=request)

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="receipt total"):
        await provider.create_checkout(
            CheckoutCommand(
                idempotency_key="checkout-receipt-mismatch",
                amount=Money(199_000, "RUB"),
                description="Kaigo Starter",
                metadata={"payment_attempt_id": "attempt-receipt-2"},
                receipt=PaymentReceipt(
                    customer_email="buyer@example.com",
                    items=(
                        ReceiptItem(
                            description="Kaigo Starter",
                            quantity=Decimal("1.00"),
                            amount=Money(100_000, "RUB"),
                            vat_code=1,
                            measure="piece",
                            payment_mode="full_payment",
                            payment_subject="service",
                        ),
                    ),
                ),
            )
        )
    assert requests == []
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_receipt_quantity_is_serialized_as_json_number() -> None:
    from app.billing.contracts import PaymentReceipt, ReceiptItem

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "pay_fractional_receipt",
                "status": "pending",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "confirmation": {
                    "confirmation_url": "https://yoomoney.ru/checkout/fractional"
                },
                "metadata": {"payment_attempt_id": "fractional-receipt"},
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
    await provider.create_checkout(
        CheckoutCommand(
            idempotency_key="fractional-receipt-key",
            amount=Money(199_000, "RUB"),
            description="Kaigo fractional receipt",
            metadata={"payment_attempt_id": "fractional-receipt"},
            receipt=PaymentReceipt(
                customer_email="buyer@example.com",
                items=(
                    ReceiptItem(
                        description="Kaigo service units",
                        quantity=Decimal("1.250"),
                        amount=Money(159_200, "RUB"),
                        vat_code=1,
                        measure="piece",
                        payment_mode="full_payment",
                        payment_subject="service",
                    ),
                ),
            ),
        )
    )

    quantity = json.loads(requests[0].content)["receipt"]["items"][0]["quantity"]
    assert quantity == 1.25
    assert isinstance(quantity, (int, float))
    await provider.aclose()


@pytest.mark.parametrize(
    "quantity",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("100000"),
        Decimal("1.0001"),
        Decimal("NaN"),
    ],
)
def test_yookassa_receipt_quantity_uses_yookassa_bounds_and_precision(
    quantity: Decimal,
) -> None:
    from app.billing.contracts import ReceiptItem

    with pytest.raises(ValueError, match="quantity"):
        ReceiptItem(
            description="Kaigo service",
            quantity=quantity,
            amount=Money(100, "RUB"),
            vat_code=1,
            measure="piece",
            payment_mode="full_payment",
            payment_subject="service",
        )


@pytest.mark.parametrize(
    ("measure", "payment_mode"),
    [("box", "full_payment"), ("piece", "partial_payment")],
)
def test_yookassa_receipt_rejects_unsupported_measure_or_payment_mode(
    measure: str,
    payment_mode: str,
) -> None:
    from app.billing.contracts import ReceiptItem

    with pytest.raises(ValueError):
        ReceiptItem(
            description="Kaigo service",
            quantity=Decimal("1"),
            amount=Money(100, "RUB"),
            vat_code=1,
            measure=measure,
            payment_mode=payment_mode,
            payment_subject="service",
        )


def test_yookassa_receipt_rejects_more_than_80_items() -> None:
    from app.billing.contracts import PaymentReceipt, ReceiptItem

    item = ReceiptItem(
        description="Kaigo service",
        quantity=Decimal("1"),
        amount=Money(100, "RUB"),
        vat_code=1,
        measure="piece",
        payment_mode="full_payment",
        payment_subject="service",
    )
    with pytest.raises(ValueError, match="80"):
        PaymentReceipt(customer_email="buyer@example.com", items=(item,) * 81)


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
async def test_yookassa_get_payment_verifies_id_mode_amount_and_metadata() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "provider-payment-id",
                "status": "succeeded",
                "paid": True,
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
    payment = await provider.get_payment(
        "provider-payment-id",
        expected_amount=Money(199_000, "RUB"),
        expected_metadata={"payment_attempt_id": "attempt-1"},
    )

    assert payment.provider_payment_id == "provider-payment-id"
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v3/payments/provider-payment-id"
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_get_payment_rejects_unsafe_id_before_request() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, request=request)

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(YooKassaVerificationError, match="payment id"):
        await provider.get_payment("../payments/other")
    assert requests == []
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_provider_revalidates_mutated_idempotency_key_before_request() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, request=request)

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    command = CheckoutCommand(
        idempotency_key="valid-key",
        amount=Money(199_000, "RUB"),
        description="Kaigo",
        metadata={"payment_attempt_id": "attempt-1"},
    )
    object.__setattr__(command, "idempotency_key", "invalid:key")

    with pytest.raises(YooKassaVerificationError, match="idempotency"):
        await provider.create_checkout(command)

    recurring = RecurringPaymentCommand(
        idempotency_key="valid-key",
        payment_method_id="opaque-method-id",
        amount=Money(199_000, "RUB"),
        description="Kaigo renewal",
        metadata={"payment_attempt_id": "attempt-1"},
    )
    object.__setattr__(recurring, "idempotency_key", "invalid:key")
    with pytest.raises(YooKassaVerificationError, match="idempotency"):
        await provider.create_recurring_payment(recurring)

    assert requests == []
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payment_method", "expected_method"),
    [
        (
            {"id": "opaque-method-id", "saved": True, "type": "bank_card"},
            ("opaque-method-id", True, "bank_card"),
        ),
        ({"id": "opaque-method-id", "saved": False, "type": "bank_card"}, None),
        (None, None),
    ],
)
async def test_yookassa_exposes_only_structurally_valid_saved_method(
    payment_method: object,
    expected_method: tuple[str, bool, str | None] | None,
) -> None:
    payload = {
        "id": "pay_saved_1",
        "status": "succeeded",
        "paid": True,
        "amount": {"value": "1990.00", "currency": "RUB"},
        "metadata": {"payment_attempt_id": "attempt-saved-1"},
        "test": True,
    }
    if payment_method is not None:
        payload["payment_method"] = payment_method

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    payment = await provider.get_payment("pay_saved_1")

    if expected_method is None:
        assert payment.payment_method is None
    else:
        assert payment.payment_method is not None
        assert (
            payment.payment_method.provider_payment_method_id,
            payment.payment_method.saved,
            payment.payment_method.method_type,
        ) == expected_method
    assert "opaque-method-id" not in repr(payment)
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "paid"),
    [("succeeded", False), ("canceled", True)],
)
async def test_yookassa_rejects_inconsistent_terminal_payment_flags(
    status: str,
    paid: bool,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pay_inconsistent",
                "status": status,
                "paid": paid,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {"payment_attempt_id": "attempt-inconsistent"},
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
    with pytest.raises(YooKassaVerificationError, match="flags"):
        await provider.get_payment("pay_inconsistent")
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "paid"),
    [
        ("failed", False),
        ("waiting_for_capture", False),
        ("cancelled", False),
        ("pending", True),
    ],
)
async def test_yookassa_rejects_impossible_provider_status_or_flags(
    status: str,
    paid: bool,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pay_impossible",
                "status": status,
                "paid": paid,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {"payment_attempt_id": "attempt-impossible"},
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
    with pytest.raises(YooKassaVerificationError):
        await provider.get_payment("pay_impossible")
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_pending_payment_never_exposes_saved_method() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pay_pending_saved",
                "status": "pending",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {"payment_attempt_id": "attempt-pending-saved"},
                "payment_method": {
                    "id": "provider-returned-too-early",
                    "saved": True,
                    "type": "bank_card",
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
    payment = await provider.get_payment("pay_pending_saved")

    assert payment.status is PaymentStatus.PENDING
    assert payment.payment_method is None
    assert "provider-returned-too-early" not in repr(payment)
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_parses_typed_cancellation_reason_without_raw_text() -> None:
    from app.billing.contracts import PaymentCancellationReason

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pay_cancelled_typed",
                "status": "canceled",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {"payment_attempt_id": "attempt-cancelled-typed"},
                "cancellation_details": {
                    "party": "payment_network",
                    "reason": "insufficient_funds",
                    "raw_provider_text": "do not expose this message",
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
    payment = await provider.get_payment("pay_cancelled_typed")

    assert payment.cancellation_reason is PaymentCancellationReason.INSUFFICIENT_FUNDS
    assert "do not expose this message" not in repr(payment)
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_unknown_cancellation_reason_is_redacted_to_typed_unknown() -> None:
    from app.billing.contracts import PaymentCancellationReason

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pay_cancelled_unknown",
                "status": "canceled",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {"payment_attempt_id": "attempt-cancelled-unknown"},
                "cancellation_details": {
                    "party": "yoo_money",
                    "reason": "new_provider_reason_with_sensitive_text",
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
    payment = await provider.get_payment("pay_cancelled_unknown")

    assert payment.cancellation_reason is PaymentCancellationReason.UNKNOWN
    assert "new_provider_reason_with_sensitive_text" not in repr(payment)
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payment_method",
    [
        {"id": "../unsafe", "saved": True, "type": "bank_card"},
        {"id": "opaque-method-id", "saved": True, "type": 123},
        {"saved": True, "type": "bank_card"},
    ],
)
async def test_yookassa_rejects_malformed_saved_method(payment_method: object) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pay_saved_invalid",
                "status": "succeeded",
                "paid": True,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {"payment_attempt_id": "attempt-saved-invalid"},
                "payment_method": payment_method,
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
    with pytest.raises(YooKassaVerificationError, match="payment method"):
        await provider.get_payment("pay_saved_invalid")
    await provider.aclose()


@pytest.mark.asyncio
async def test_yookassa_recurring_payment_uses_saved_method_without_confirmation() -> None:
    from app.billing.contracts import RecurringPaymentCommand

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "pay_recurring_1",
                "status": "pending",
                "paid": False,
                "amount": {"value": "1990.00", "currency": "RUB"},
                "metadata": {"payment_attempt_id": "renewal-1"},
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
    result = await provider.create_recurring_payment(
        RecurringPaymentCommand(
            idempotency_key="renewal-key",
            payment_method_id="opaque-method-id",
            amount=Money(199_000, "RUB"),
            description="Kaigo Starter renewal",
            metadata={"payment_attempt_id": "renewal-1"},
        )
    )

    assert result.provider_payment_id == "pay_recurring_1"
    body = json.loads(requests[0].content)
    assert body["payment_method_id"] == "opaque-method-id"
    assert "confirmation" not in body
    assert "save_payment_method" not in body
    assert requests[0].headers["Idempotence-Key"] == "renewal-key"
    assert "opaque-method-id" not in repr(
        RecurringPaymentCommand(
            idempotency_key="renewal-key",
            payment_method_id="opaque-method-id",
            amount=Money(199_000, "RUB"),
            description="Kaigo Starter renewal",
            metadata={"payment_attempt_id": "renewal-1"},
        )
    )
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("payment_method_id", ["", "../unsafe", "has space", "x" * 256])
async def test_yookassa_recurring_payment_rejects_unsafe_method_id_before_request(
    payment_method_id: str,
) -> None:
    from app.billing.contracts import RecurringPaymentCommand

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, request=request)

    provider = YooKassaProvider(
        shop_id="shop",
        secret_key="secret",
        return_url="https://kaigo.space/billing/success",
        test_mode=True,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="payment_method_id"):
        await provider.create_recurring_payment(
            RecurringPaymentCommand(
                idempotency_key="renewal-unsafe-key",
                payment_method_id=payment_method_id,
                amount=Money(199_000, "RUB"),
                description="Kaigo Starter renewal",
                metadata={"payment_attempt_id": "renewal-unsafe"},
            )
        )
    assert requests == []
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


@pytest.mark.asyncio
async def test_merchant_account_fingerprint_is_stable_and_does_not_include_secret() -> None:
    first = YooKassaProvider(
        shop_id="merchant-123",
        secret_key="secret-a",
        return_url="https://kaigo.example/billing/success",
        test_mode=True,
    )
    rotated_secret = YooKassaProvider(
        shop_id="merchant-123",
        secret_key="secret-b",
        return_url="https://kaigo.example/billing/success",
        test_mode=True,
    )
    another_merchant = YooKassaProvider(
        shop_id="merchant-456",
        secret_key="secret-a",
        return_url="https://kaigo.example/billing/success",
        test_mode=True,
    )
    try:
        assert first.merchant_account_fingerprint == rotated_secret.merchant_account_fingerprint
        assert first.merchant_account_fingerprint != another_merchant.merchant_account_fingerprint
        assert len(first.merchant_account_fingerprint) == 64
        assert "merchant-123" not in first.merchant_account_fingerprint
        assert "secret-a" not in first.merchant_account_fingerprint
    finally:
        await first.aclose()
        await rotated_secret.aclose()
        await another_merchant.aclose()
