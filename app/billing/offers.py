from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID
from weakref import WeakValueDictionary

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.service import record_funnel_event
from app.billing.catalog import snapshot_fingerprint
from app.db.models import User
from app.saas.models import (
    FounderAccessGrant,
    PaymentAttempt,
    Project,
    Subscription,
    UsageLedger,
)


FOUNDER_CAPACITY = 20
FOUNDER_GENERATION_TOKENS = 1_500_000
FOUNDER_PERIOD_DAYS = 14
FOUNDER_ADVISORY_LOCK_KEY = 0x4B4149474F464E44


class FounderOfferUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FounderOffer:
    eligible: bool
    reason: str | None
    remaining: int
    capacity: int = FOUNDER_CAPACITY


@dataclass(frozen=True, slots=True)
class FounderClaimResult:
    grant: FounderAccessGrant
    created: bool


_SQLITE_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def _origin(source_url: str) -> str:
    parsed = urlsplit(source_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise FounderOfferUnavailable("source_url_unavailable")
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as error:
        raise FounderOfferUnavailable("source_url_unavailable") from error
    return f"https://{host}{f':{port}' if port and port != 443 else ''}"


def _founder_snapshot() -> dict[str, object]:
    return {
        "code": "founder_14d",
        "title": "Kaigo Founder, 14 дней",
        "amount_minor": 0,
        "currency": "RUB",
        "period_days": FOUNDER_PERIOD_DAYS,
        "generation_tokens": FOUNDER_GENERATION_TOKENS,
        "public": False,
        "grant": True,
    }


class FounderAccessService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        bind = getattr(session_factory, "kw", {}).get("bind")
        self._dialect = bind.dialect.name if bind is not None else ""

    def _now(self) -> datetime:
        value = self._clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    async def _project(self, database: AsyncSession, user_id: int, project_id: UUID) -> Project:
        project = await database.scalar(
            select(Project).where(Project.id == project_id, Project.owner_user_id == user_id)
        )
        if project is None:
            raise FounderOfferUnavailable("project_unavailable")
        if project.status not in {"free_result_ready", "completed", "published"}:
            raise FounderOfferUnavailable("project_not_ready")
        return project

    async def offer(self, user_id: int, project_id: UUID) -> FounderOffer:
        now = self._now()
        async with self._sessions() as database:
            project = await self._project(database, user_id, project_id)
            origin = _origin(project.source_url)
            existing = await database.scalar(
                select(FounderAccessGrant).where(FounderAccessGrant.user_id == user_id)
            )
            claimed_origin = await database.scalar(
                select(FounderAccessGrant.id).where(FounderAccessGrant.source_origin == origin)
            )
            count = int(
                await database.scalar(select(func.count()).select_from(FounderAccessGrant)) or 0
            )
            pending_paid_intro = await self._pending_paid_intro(
                database,
                user_id,
            )
            paid_access_used = await self._paid_access_used(
                database,
                user_id,
            )
            active_subscription = await database.scalar(
                select(Subscription.id)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == "active",
                    Subscription.current_period_end > now,
                )
                .limit(1)
            )
        if existing is not None:
            return FounderOffer(False, "already_claimed", max(0, FOUNDER_CAPACITY - count))
        if claimed_origin is not None:
            return FounderOffer(False, "domain_already_claimed", max(0, FOUNDER_CAPACITY - count))
        if pending_paid_intro:
            return FounderOffer(False, "pending_paid_intro", max(0, FOUNDER_CAPACITY - count))
        if paid_access_used:
            return FounderOffer(False, "paid_access_used", max(0, FOUNDER_CAPACITY - count))
        if active_subscription is not None:
            return FounderOffer(False, "active_subscription", max(0, FOUNDER_CAPACITY - count))
        if count >= FOUNDER_CAPACITY:
            return FounderOffer(False, "quota_exhausted", 0)
        return FounderOffer(True, None, FOUNDER_CAPACITY - count)

    @staticmethod
    async def _pending_paid_intro(
        database: AsyncSession,
        user_id: int,
        *,
        lock: bool = False,
    ) -> bool:
        statement = (
            select(PaymentAttempt.id)
            .where(
                PaymentAttempt.user_id == user_id,
                PaymentAttempt.purpose == "initial",
                PaymentAttempt.plan_code == "starter_intro_15d",
                PaymentAttempt.status.in_(
                    ("creating", "pending", "failed", "dispatch_unknown")
                ),
            )
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        return await database.scalar(statement) is not None

    @staticmethod
    async def _paid_access_used(
        database: AsyncSession,
        user_id: int,
        *,
        lock: bool = False,
    ) -> bool:
        statement = (
            select(PaymentAttempt.id)
            .where(
                PaymentAttempt.user_id == user_id,
                PaymentAttempt.purpose == "initial",
                PaymentAttempt.status == "succeeded",
            )
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        return await database.scalar(statement) is not None

    async def claim(self, user_id: int, project_id: UUID) -> FounderClaimResult:
        lock = _SQLITE_LOCKS.setdefault(id(self._sessions.kw["bind"]), asyncio.Lock())
        async with lock if self._dialect == "sqlite" else _noop_lock():
            try:
                return await self._claim_once(user_id, project_id)
            except IntegrityError as error:
                raise FounderOfferUnavailable("claim_conflict") from error

    async def _claim_once(self, user_id: int, project_id: UUID) -> FounderClaimResult:
        now = self._now()
        async with self._sessions() as database, database.begin():
            await database.scalar(
                select(User.id).where(User.id == user_id).with_for_update()
            )
            if self._dialect == "postgresql":
                await database.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": FOUNDER_ADVISORY_LOCK_KEY},
                )
            project = await self._project(database, user_id, project_id)
            origin = _origin(project.source_url)
            existing = await database.scalar(
                select(FounderAccessGrant)
                .where(FounderAccessGrant.user_id == user_id)
                .with_for_update()
            )
            if existing is not None:
                if existing.project_id == project_id:
                    return FounderClaimResult(existing, False)
                raise FounderOfferUnavailable("already_claimed")
            if await database.scalar(
                select(FounderAccessGrant.id)
                .where(FounderAccessGrant.source_origin == origin)
                .with_for_update()
            ) is not None:
                raise FounderOfferUnavailable("domain_already_claimed")
            if await self._pending_paid_intro(database, user_id, lock=True):
                raise FounderOfferUnavailable("pending_paid_intro")
            if await self._paid_access_used(database, user_id, lock=True):
                raise FounderOfferUnavailable("paid_access_used")
            active = await database.scalar(
                select(Subscription.id).where(
                    Subscription.user_id == user_id,
                    Subscription.status == "active",
                    Subscription.current_period_end > now,
                )
            )
            if active is not None:
                raise FounderOfferUnavailable("active_subscription")
            count = int(
                await database.scalar(select(func.count()).select_from(FounderAccessGrant)) or 0
            )
            if count >= FOUNDER_CAPACITY:
                raise FounderOfferUnavailable("quota_exhausted")
            snapshot = _founder_snapshot()
            subscription = Subscription(
                user_id=user_id,
                provider="founder",
                plan_code="founder_14d",
                plan_snapshot=snapshot,
                plan_fingerprint=snapshot_fingerprint(snapshot),
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=FOUNDER_PERIOD_DAYS),
                auto_renew=False,
            )
            database.add(subscription)
            await database.flush()
            grant = FounderAccessGrant(
                user_id=user_id,
                project_id=project_id,
                subscription_id=subscription.id,
                source_origin=origin,
                position=count + 1,
                starts_at=now,
                ends_at=now + timedelta(days=FOUNDER_PERIOD_DAYS),
            )
            database.add(grant)
            await database.flush()
            database.add(
                UsageLedger(
                    user_id=user_id,
                    project_id=project_id,
                    payment_attempt_id=None,
                    founder_grant_id=grant.id,
                    bucket="tokens",
                    entry_type="subscription.credit",
                    amount=FOUNDER_GENERATION_TOKENS,
                    idempotency_key=f"founder:{grant.id}:tokens",
                    payload={
                        "plan_code": "founder_14d",
                        "founder_grant_id": str(grant.id),
                    },
                )
            )
            await record_funnel_event(
                database,
                event_type="founder_claimed",
                event_key=f"founder_claimed:grant:{grant.id}",
                journey_id=project.journey_id,
                user_id=user_id,
                project_id=project_id,
            )
            return FounderClaimResult(grant, True)


class _noop_lock:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False
