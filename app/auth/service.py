from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oauth import OAuthIdentity
from app.db.models import Tenant, User
from app.saas.models import (
    AnonymousDraft,
    Project,
    TrialEntitlement,
    UserIdentity,
)


def draft_project_id(draft_id: UUID) -> UUID:
    """Return the stable project identity shared by every draft-claim path."""

    return uuid5(NAMESPACE_URL, f"https://kaigo.space/drafts/{draft_id}")


async def _linked_user(
    database: AsyncSession, identity: OAuthIdentity
) -> User | None:
    linked = (
        await database.execute(
            select(UserIdentity).where(
                UserIdentity.provider == identity.provider,
                UserIdentity.provider_subject == identity.subject,
            )
        )
    ).scalar_one_or_none()
    if linked is None:
        return None
    user = await database.get(User, linked.user_id)
    if user is None:
        raise RuntimeError("OAuth identity points to a missing user")
    return user


def _identity_row(identity: OAuthIdentity, *, user_id: int) -> UserIdentity:
    return UserIdentity(
        user_id=user_id,
        provider=identity.provider,
        provider_subject=identity.subject,
        email=identity.email.lower(),
        email_verified=identity.email_verified,
        profile=dict(identity.profile),
    )


async def _attach_identity(
    database: AsyncSession,
    identity: OAuthIdentity,
    *,
    user: User,
) -> User:
    try:
        async with database.begin_nested():
            database.add(_identity_row(identity, user_id=user.id))
            await database.flush()
        return user
    except IntegrityError:
        # Another callback may have linked this provider identity while this
        # transaction was waiting on the unique constraint. The savepoint keeps
        # the outer callback transaction usable, and the committed winner is
        # now visible under PostgreSQL READ COMMITTED.
        linked_user = await _linked_user(database, identity)
        if linked_user is None:
            raise
        return linked_user


async def _resolve_identity_user(
    database: AsyncSession, identity: OAuthIdentity
) -> User:
    linked_user = await _linked_user(database, identity)
    if linked_user is not None:
        return linked_user

    normalized_email = identity.email.lower()
    user = (
        await database.execute(select(User).where(User.email == normalized_email))
    ).scalar_one_or_none()
    if user is not None:
        return await _attach_identity(database, identity, user=user)

    try:
        async with database.begin_nested():
            suffix = uuid4().hex[:10]
            tenant = Tenant(
                name=identity.display_name or identity.email,
                slug=f"personal-{suffix}",
            )
            database.add(tenant)
            await database.flush()
            user = User(
                tenant_id=tenant.id,
                email=normalized_email,
                password_hash=None,
                role="tenant_admin",
            )
            database.add(user)
            await database.flush()
            database.add(_identity_row(identity, user_id=user.id))
            await database.flush()
        return user
    except IntegrityError:
        # Either the globally unique email or provider subject won in another
        # transaction. Prefer the provider identity, then attach it to the
        # already-created email account when this was a cross-provider race.
        linked_user = await _linked_user(database, identity)
        if linked_user is not None:
            return linked_user
        user = (
            await database.execute(
                select(User).where(User.email == normalized_email)
            )
        ).scalar_one_or_none()
        if user is None:
            raise
        return await _attach_identity(database, identity, user=user)


async def _ensure_trial_entitlement(
    database: AsyncSession, *, user_id: int
) -> None:
    trial = (
        await database.execute(
            select(TrialEntitlement).where(TrialEntitlement.user_id == user_id)
        )
    ).scalar_one_or_none()
    if trial is not None:
        return
    try:
        async with database.begin_nested():
            database.add(TrialEntitlement(user_id=user_id))
            await database.flush()
    except IntegrityError:
        # Concurrent first logins share one entitlement. Never grant two free
        # generations merely because two OAuth callbacks finished together.
        trial = (
            await database.execute(
                select(TrialEntitlement).where(
                    TrialEntitlement.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if trial is None:
            raise


async def link_identity_and_claim_draft(
    database: AsyncSession,
    identity: OAuthIdentity,
    *,
    draft_id: UUID | None,
    claim_token_digest: str | None,
) -> tuple[User, Project | None]:
    user = await _resolve_identity_user(database, identity)
    await _ensure_trial_entitlement(database, user_id=user.id)

    project = None
    if draft_id is not None and claim_token_digest:
        draft = (
            await database.execute(
                select(AnonymousDraft)
                .where(
                    AnonymousDraft.id == draft_id,
                    AnonymousDraft.claim_token_digest == claim_token_digest,
                    AnonymousDraft.expires_at > datetime.now(UTC),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if draft is not None:
            project_id = draft_project_id(draft.id)
            project = await database.get(Project, project_id)
            if draft.claimed_at is not None:
                if (
                    draft.claimed_by_user_id != user.id
                    or project is None
                    or project.owner_user_id != user.id
                    or project.tenant_id != user.tenant_id
                ):
                    # Authentication still succeeds when another account won
                    # the draft. Only the claim is refused.
                    return user, None
                return user, project
            if project is not None:
                # A deterministic project without its matching claimed draft
                # is an inconsistent/colliding row and must never be adopted.
                return user, None
            project = Project(
                id=project_id,
                tenant_id=user.tenant_id,
                owner_user_id=user.id,
                journey_id=draft.journey_id,
                source_url=draft.source_url,
                brief=draft.brief,
                status="draft",
            )
            database.add(project)
            await database.flush()
            draft.claimed_by_user_id = user.id
            draft.claimed_at = datetime.now(UTC)

    return user, project


__all__ = ["draft_project_id", "link_identity_and_claim_draft"]
