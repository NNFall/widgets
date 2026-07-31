from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from aiohttp import web
from sqlalchemy import select

from app.billing.payments import (
    BillingError,
    BillingService,
    CheckoutIdempotencyConflict,
    MerchantAccountMismatch,
    PaymentNotFound,
    UnknownPlan,
)
from app.billing.service import GenerationCreditService
from app.billing.yookassa import YooKassaError, YooKassaVerificationError
from app.db.session import get_session_factory
from app.projects.routes import _require_csrf, _scope
from app.saas.models import PaymentAttempt, Subscription

BILLING_SERVICE_KEY = "billing_service"
_MERCHANT_CUTOVER_MESSAGE = (
    "Оплата начата в другом аккаунте магазина. "
    "Верните прежнюю платёжную конфигурацию для завершения."
)


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def _time(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _payment(attempt: PaymentAttempt) -> dict[str, object]:
    return {
        "id": str(attempt.id),
        "plan_code": attempt.plan_code,
        "status": attempt.status,
        "amount_minor": attempt.amount_minor,
        "currency": attempt.currency,
        "created_at": _time(attempt.created_at),
    }


def _subscription(
    row: Subscription,
    *,
    generation_tokens_remaining: int | None = None,
) -> dict[str, object]:
    return {
        "id": str(row.id),
        "plan_code": row.plan_code,
        "status": row.status,
        "current_period_start": _time(row.current_period_start),
        "current_period_end": _time(row.current_period_end),
        "auto_renew": row.auto_renew,
        "next_renewal_at": _time(row.next_renewal_at),
        "generation_tokens_remaining": generation_tokens_remaining,
    }


def _merchant_recovery(
    service: BillingService,
    attempt: PaymentAttempt,
) -> dict[str, object]:
    if service.is_current_merchant_account(attempt):
        return {
            "status": "ready",
            "recoverable": True,
            "action": "continue_checkout",
            "message": "Платёж можно безопасно продолжить.",
        }
    return {
        "status": "merchant_cutover_required",
        "recoverable": True,
        "action": "restore_previous_merchant",
        "message": _MERCHANT_CUTOVER_MESSAGE,
    }


def _service(request: web.Request) -> BillingService:
    service = request.app.get(BILLING_SERVICE_KEY)
    if not isinstance(service, BillingService):
        raise web.HTTPServiceUnavailable(
            text=json.dumps(_error("billing_unavailable", "Оплата пока недоступна")),
            content_type="application/json",
        )
    return service


async def create_checkout(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request, verified=False)
    await _require_csrf(request)
    try:
        payload = await request.json()
    except Exception as error:  # noqa: BLE001
        raise web.HTTPBadRequest(
            text=json.dumps(_error("invalid_json", "Некорректный JSON")),
            content_type="application/json",
        ) from error
    if (
        not isinstance(payload, dict)
        or not set(payload).issubset({"plan_code", "project_id", "auto_renew"})
        or "plan_code" not in payload
    ):
        return web.json_response(
            _error(
                "invalid_request",
                "Передайте код тарифа и при необходимости согласие на автопродление",
            ),
            status=400,
        )
    plan_code = payload.get("plan_code")
    raw_project_id = payload.get("project_id")
    auto_renew = payload.get("auto_renew", False)
    idempotency_key = request.headers.get("Idempotency-Key", "")
    if (
        not isinstance(plan_code, str)
        or not isinstance(auto_renew, bool)
        or (raw_project_id is not None and not isinstance(raw_project_id, str))
    ):
        return web.json_response(_error("invalid_plan", "Тариф не выбран"), status=400)
    try:
        project_id = UUID(raw_project_id) if isinstance(raw_project_id, str) else None
    except ValueError:
        return web.json_response(_error("invalid_request", "invalid project"), status=400)
    try:
        result = await _service(request).create_checkout(
            user_id,
            plan_code,
            idempotency_key,
            project_id=project_id,
            auto_renew=auto_renew,
        )
    except (UnknownPlan, ValueError):
        return web.json_response(
            _error("invalid_request", "Тариф или ключ некорректны"), status=400
        )
    except CheckoutIdempotencyConflict:
        return web.json_response(
            _error(
                "idempotency_conflict",
                "Этот ключ уже использован с другими параметрами оплаты",
            ),
            status=409,
        )
    except MerchantAccountMismatch:
        return web.json_response(
            _error(
                "merchant_cutover_required",
                _MERCHANT_CUTOVER_MESSAGE,
            ),
            status=409,
        )
    except YooKassaVerificationError:
        return web.json_response(
            _error(
                "provider_response_invalid", "Платёжная система вернула неверный ответ"
            ),
            status=502,
        )
    except YooKassaError:
        response = web.json_response(
            _error(
                "provider_unavailable",
                "Платёжная система временно недоступна",
                retryable=True,
            ),
            status=503,
        )
        response.headers["Retry-After"] = "30"
        return response
    except BillingError:
        return web.json_response(
            _error("checkout_failed", "Не удалось создать оплату"), status=502
        )
    factory = get_session_factory(request.app)
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, result.payment_id)
        if attempt is None or attempt.user_id != user_id:
            raise web.HTTPNotFound()
        body = {
            "payment": _payment(attempt),
            "checkout_url": result.checkout_url,
            "created": result.created,
        }
    return web.json_response(body, status=201 if result.created else 200)


async def resume_checkout(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request, verified=False)
    await _require_csrf(request)
    try:
        payment_id = UUID(request.match_info["payment_id"])
    except (ValueError, TypeError) as error:
        raise web.HTTPNotFound() from error
    try:
        result = await _service(request).resume_checkout(user_id, payment_id)
    except PaymentNotFound:
        raise web.HTTPNotFound() from None
    except MerchantAccountMismatch:
        return web.json_response(
            _error(
                "merchant_cutover_required",
                _MERCHANT_CUTOVER_MESSAGE,
            ),
            status=409,
        )
    except YooKassaVerificationError:
        return web.json_response(
            _error(
                "provider_response_invalid", "Платёжная система вернула неверный ответ"
            ),
            status=502,
        )
    except YooKassaError:
        response = web.json_response(
            _error(
                "provider_unavailable",
                "Платёжная система временно недоступна",
                retryable=True,
            ),
            status=503,
        )
        response.headers["Retry-After"] = "30"
        return response
    except BillingError:
        return web.json_response(
            _error("checkout_recovery_failed", "Не удалось восстановить оплату"),
            status=409,
        )
    factory = get_session_factory(request.app)
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, result.payment_id)
        if attempt is None or attempt.user_id != user_id:
            raise web.HTTPNotFound()
        return web.json_response(
            {
                "payment": _payment(attempt),
                "checkout_url": result.checkout_url,
                "created": result.created,
            }
        )


async def payment_status(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request)
    try:
        payment_id = UUID(request.match_info["payment_id"])
    except (ValueError, TypeError) as error:
        raise web.HTTPNotFound() from error
    factory = get_session_factory(request.app)
    async with factory() as database:
        attempt = await database.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.id == payment_id,
                PaymentAttempt.user_id == user_id,
            )
        )
        if attempt is None:
            raise web.HTTPNotFound()
        return web.json_response({"payment": _payment(attempt)})


async def pending_payment(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request)
    service = _service(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        attempts = list(
            await database.scalars(
                select(PaymentAttempt)
                .where(
                    PaymentAttempt.user_id == user_id,
                    PaymentAttempt.status.in_(
                        ("creating", "pending", "failed", "dispatch_unknown")
                    ),
                )
                .order_by(
                    PaymentAttempt.updated_at.desc(),
                    PaymentAttempt.created_at.desc(),
                )
            )
        )
        if not attempts:
            return web.json_response(
                {"payment": None, "checkout_url": None, "recovery": None}
            )
        # A stale attempt from another merchant account takes precedence over a
        # newer current-account attempt.  That historical state can exist after
        # an older deployment; exposing either checkout while the account is in
        # cutover would make payment fulfillment ambiguous.
        attempt = next(
            (row for row in attempts if not service.is_current_merchant_account(row)),
            attempts[0],
        )
        recovery = _merchant_recovery(service, attempt)
        checkout_url = attempt.checkout_url if recovery["status"] == "ready" else None
        return web.json_response(
            {
                "payment": _payment(attempt),
                "checkout_url": checkout_url,
                "recovery": recovery,
            }
        )


async def subscription_status(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        row = await database.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.current_period_end > datetime.now(UTC),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        remaining = (
            await GenerationCreditService.available_for_subscription_in_session(
                database,
                row,
            )
            if row is not None
            else None
        )
        return web.json_response(
            {
                "subscription": (
                    _subscription(row, generation_tokens_remaining=remaining)
                    if row is not None
                    else None
                )
            }
        )


async def disable_auto_renew(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request)
    await _require_csrf(request)
    try:
        subscription_id = UUID(request.match_info["subscription_id"])
    except (ValueError, TypeError) as error:
        raise web.HTTPNotFound() from error
    try:
        subscription = await _service(request).disable_auto_renew(
            user_id,
            subscription_id,
        )
    except PaymentNotFound:
        raise web.HTTPNotFound() from None
    except BillingError as error:
        if str(error) == "renewal_in_progress":
            return web.json_response(
                _error(
                    "renewal_in_progress",
                    "Продление уже обрабатывается; проверьте статус платежа",
                    retryable=True,
                ),
                status=409,
            )
        raise
    remaining = await GenerationCreditService(
        get_session_factory(request.app)
    ).available_tokens(user_id)
    return web.json_response(
        {
            "subscription": _subscription(
                subscription,
                generation_tokens_remaining=remaining,
            )
        }
    )


async def yookassa_webhook(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        result = await _service(request).handle_notification(payload)
    except (PaymentNotFound, YooKassaVerificationError, ValueError):
        return web.json_response(
            _error("invalid_notification", "Платёж не подтверждён"), status=400
        )
    except YooKassaError:
        response = web.json_response(
            _error(
                "provider_unavailable",
                "Проверка платежа временно недоступна",
                retryable=True,
            ),
            status=503,
        )
        response.headers["Retry-After"] = "30"
        return response
    except MerchantAccountMismatch:
        response = web.json_response(
            _error(
                "merchant_cutover_required",
                _MERCHANT_CUTOVER_MESSAGE,
                retryable=True,
            ),
            status=503,
        )
        # YooKassa retries non-2xx notifications.  Keep the event recoverable
        # while an operator restores the merchant account that created it.
        response.headers["Retry-After"] = "300"
        return response
    except BillingError:
        return web.json_response(
            _error("fulfillment_failed", "Платёж пока не применён"), status=500
        )
    return web.json_response({"accepted": True, "processed": result.processed})


async def billing_success(_request: web.Request) -> web.Response:
    # This redirect is deliberately informational. Only a provider-verified
    # webhook may activate a subscription.
    return web.Response(
        text="""<!doctype html><html lang="ru"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Проверяем платёж — Kaigo</title><body>
        <main><h1>Проверяем платёж</h1><p>Вернитесь в студию: статус обновится автоматически после подтверждения платёжной системой.</p>
        <p><a href="/studio">Вернуться в студию</a></p></main></body></html>""",
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def setup_billing_routes(app: web.Application) -> None:
    app.router.add_post("/api/billing/checkout", create_checkout)
    app.router.add_get("/api/billing/payments/pending", pending_payment)
    app.router.add_post("/api/billing/payments/{payment_id}/resume", resume_checkout)
    app.router.add_get("/api/billing/payments/{payment_id}", payment_status)
    app.router.add_get("/api/billing/subscription", subscription_status)
    app.router.add_post(
        "/api/billing/subscriptions/{subscription_id}/auto-renew/off",
        disable_auto_renew,
    )
    app.router.add_post("/api/billing/webhooks/yookassa", yookassa_webhook)
    app.router.add_get("/billing/success", billing_success)


__all__ = ["BILLING_SERVICE_KEY", "setup_billing_routes"]
