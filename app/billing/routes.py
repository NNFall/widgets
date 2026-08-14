from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import UUID

from aiohttp import web
from sqlalchemy import select

from app.analytics.service import record_funnel_event
from app.billing.catalog import BillingPlan, PLAN_CATALOG, public_billing_plans
from app.billing.offers import (
    FOUNDER_GENERATION_TOKENS,
    FOUNDER_PERIOD_DAYS,
    FounderAccessService,
    FounderOfferUnavailable,
)
from app.billing.payments import (
    BillingError,
    BillingService,
    CheckoutIdempotencyConflict,
    IntroOfferUnavailable,
    MerchantAccountMismatch,
    PaymentNotFound,
    UnknownPlan,
)
from app.billing.service import GenerationCreditService
from app.billing.yookassa import YooKassaError, YooKassaVerificationError
from app.db.models import User
from app.db.session import get_session_factory
from app.projects.routes import _require_csrf, _scope
from app.saas.models import (
    CustomerContactRequest,
    FounderAccessGrant,
    PaymentAttempt,
    Project,
    Subscription,
)

BILLING_SERVICE_KEY = "billing_service"
_CONTACT_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{16,128}$")
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
    plan_title = row.plan_code
    next_charge = None
    access_kind = "founder" if row.provider == "founder" else "paid"
    if access_kind == "founder":
        title = row.plan_snapshot.get("title") if isinstance(row.plan_snapshot, dict) else None
        if isinstance(title, str) and title:
            plan_title = title
    else:
        try:
            current_plan = BillingPlan.from_snapshot(row.plan_snapshot)
        except ValueError:
            current_plan = PLAN_CATALOG.get(row.plan_code)
        if current_plan is not None:
            plan_title = current_plan.title
            if row.auto_renew and row.next_renewal_at is not None:
                renewal_plan = (
                    PLAN_CATALOG.get(current_plan.renewal_plan_code)
                    if current_plan.renewal_plan_code is not None
                    else current_plan
                )
                if renewal_plan is not None:
                    next_charge = {
                        "plan_code": renewal_plan.code,
                        "amount_minor": renewal_plan.amount.amount_minor,
                        "currency": renewal_plan.amount.currency,
                        "period_days": renewal_plan.period_days,
                        "at": _time(row.next_renewal_at),
                    }
    return {
        "id": str(row.id),
        "plan_code": row.plan_code,
        "status": row.status,
        "current_period_start": _time(row.current_period_start),
        "current_period_end": _time(row.current_period_end),
        "auto_renew": row.auto_renew,
        "next_renewal_at": _time(row.next_renewal_at),
        "generation_tokens_remaining": generation_tokens_remaining,
        "access_kind": access_kind,
        "plan_title": plan_title,
        "next_charge": next_charge,
    }


def _public_plan(plan) -> dict[str, object]:
    renewal = None
    if plan.renewal_plan_code is not None:
        next_plan = PLAN_CATALOG[plan.renewal_plan_code]
        following = None
        if next_plan.renewal_plan_code is not None:
            following_plan = PLAN_CATALOG[next_plan.renewal_plan_code]
            following = {
                "plan_code": following_plan.code,
                "amount_minor": following_plan.amount.amount_minor,
                "currency": following_plan.amount.currency,
                "period_days": following_plan.period_days,
            }
        renewal = {
            "plan_code": next_plan.code,
            "amount_minor": next_plan.amount.amount_minor,
            "currency": next_plan.amount.currency,
            "period_days": next_plan.period_days,
            "following": following,
        }
    return {
        "code": plan.code,
        "title": plan.title,
        "amount_minor": plan.amount.amount_minor,
        "currency": plan.amount.currency,
        "period_days": plan.period_days,
        "generation_tokens": plan.generation_tokens,
        "renewal": renewal,
    }


def _project_id(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("project id is invalid")
    return UUID(value)


async def publication_offer(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request, verified=True)
    try:
        project_id = _project_id(request.query.get("project_id"))
        founder = await FounderAccessService(
            get_session_factory(request.app)
        ).offer(user_id, project_id)
    except (ValueError, FounderOfferUnavailable) as error:
        return web.json_response(
            _error("offer_unavailable", str(error)), status=409
        )
    plans = list(public_billing_plans())
    if not await _service(request).intro_offer_available(user_id):
        plans = [plan for plan in plans if plan.code != "starter_intro_15d"]
    return web.json_response(
        {
            "founder": {
                "eligible": founder.eligible,
                "reason": founder.reason,
                "remaining": founder.remaining,
                "capacity": founder.capacity,
                "period_days": FOUNDER_PERIOD_DAYS,
                "generation_tokens": FOUNDER_GENERATION_TOKENS,
            },
            "plans": [_public_plan(plan) for plan in plans],
        }
    )


async def claim_founder_access(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request, verified=True)
    await _require_csrf(request)
    try:
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) != {"project_id"}:
            raise ValueError("invalid request")
        project_id = _project_id(payload["project_id"])
        result = await FounderAccessService(
            get_session_factory(request.app)
        ).claim(user_id, project_id)
    except ValueError:
        return web.json_response(_error("invalid_request", "Некорректный проект"), status=400)
    except FounderOfferUnavailable as error:
        return web.json_response(_error("founder_unavailable", str(error)), status=409)
    factory = get_session_factory(request.app)
    async with factory() as database:
        subscription = await database.get(Subscription, result.grant.subscription_id)
        remaining = await GenerationCreditService.available_for_subscription_in_session(
            database, subscription
        )
    return web.json_response(
        {
            "created": result.created,
            "founder": {
                "position": result.grant.position,
                "ends_at": _time(result.grant.ends_at),
            },
            "subscription": _subscription(
                subscription, generation_tokens_remaining=remaining
            ),
        },
        status=201 if result.created else 200,
    )


async def create_contact_request(request: web.Request) -> web.Response:
    user_id, _tenant_id = await _scope(request, verified=False)
    await _require_csrf(request)
    idempotency_key = request.headers.get("Idempotency-Key", "")
    if not _CONTACT_IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        return web.json_response(
            _error(
                "invalid_idempotency_key",
                "Передайте корректный ключ повторной отправки",
            ),
            status=400,
        )
    try:
        payload = await request.json()
    except Exception as error:  # noqa: BLE001
        raise web.HTTPBadRequest() from error
    allowed = {"project_id", "kind", "message", "rating", "testimonial_allowed"}
    if not isinstance(payload, dict) or not set(payload).issubset(allowed):
        return web.json_response(_error("invalid_request", "Некорректное обращение"), status=400)
    kind = payload.get("kind")
    message = payload.get("message")
    rating = payload.get("rating")
    testimonial_allowed = payload.get("testimonial_allowed", False)
    try:
        project_id = _project_id(payload.get("project_id"))
    except ValueError:
        return web.json_response(_error("invalid_request", "Некорректный проект"), status=400)
    if (
        kind not in {"support", "founder_feedback"}
        or not isinstance(message, str)
        or not 10 <= len(message.strip()) <= 4_000
        or (rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5))
        or not isinstance(testimonial_allowed, bool)
    ):
        return web.json_response(_error("invalid_request", "Проверьте текст обращения"), status=400)
    normalized_message = message.strip()
    factory = get_session_factory(request.app)
    async with factory() as database, database.begin():
        locked_user = await database.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if locked_user is None:
            raise web.HTTPUnauthorized()
        existing = await database.scalar(
            select(CustomerContactRequest).where(
                CustomerContactRequest.user_id == user_id,
                CustomerContactRequest.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.project_id != project_id
                or existing.kind != kind
                or existing.message != normalized_message
                or existing.rating != rating
                or existing.testimonial_allowed != testimonial_allowed
            ):
                return web.json_response(
                    _error(
                        "idempotency_conflict",
                        "Этот ключ уже использован для другого обращения",
                    ),
                    status=409,
                )
            return web.json_response(
                {"request_id": str(existing.id), "accepted": True},
                status=200,
            )
        project = await database.scalar(
            select(Project).where(Project.id == project_id, Project.owner_user_id == user_id)
        )
        if project is None:
            raise web.HTTPNotFound()
        founder = None
        if kind == "founder_feedback":
            founder = await database.scalar(
                select(FounderAccessGrant).where(
                    FounderAccessGrant.user_id == user_id,
                    FounderAccessGrant.project_id == project_id,
                )
            )
            if founder is None:
                return web.json_response(
                    _error("founder_feedback_unavailable", "Founder-пилот не найден"),
                    status=409,
                )
            subscription = await database.get(Subscription, founder.subscription_id)
        else:
            subscription = await database.scalar(
                select(Subscription)
                .where(Subscription.user_id == user_id)
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
        row = CustomerContactRequest(
            user_id=user_id,
            idempotency_key=idempotency_key,
            project_id=project_id,
            subscription_id=subscription.id if subscription is not None else None,
            founder_grant_id=founder.id if founder is not None else None,
            kind=kind,
            message=normalized_message,
            rating=rating,
            testimonial_allowed=testimonial_allowed,
        )
        database.add(row)
        if founder is not None:
            founder.feedback_state = "received"
        await database.flush()
        request_id = row.id
        await record_funnel_event(
            database,
            event_type="support_requested",
            event_key=f"support_requested:contact:{row.id}",
            journey_id=project.journey_id,
            user_id=user_id,
            project_id=project_id,
        )
    return web.json_response({"request_id": str(request_id), "accepted": True}, status=201)


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
    except IntroOfferUnavailable:
        return web.json_response(
            _error(
                "intro_offer_unavailable",
                "Пробные 15 дней уже использованы. Выберите месяц или квартал.",
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
    app.router.add_get("/api/billing/offer", publication_offer)
    app.router.add_post("/api/billing/founder/claim", claim_founder_access)
    app.router.add_post("/api/billing/contact", create_contact_request)
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
