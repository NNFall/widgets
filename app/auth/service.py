from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oauth import OAuthIdentity
from app.db.models import Tenant, User
from app.saas.models import (
    AnonymousDraft,
    Project,
    TrialEntitlement,
    UserIdentity,
)


async def link_identity_and_claim_draft(
    database: AsyncSession,
    identity: OAuthIdentity,
    *,
    draft_id: UUID | None,
    claim_token_digest: str | None,
) -> tuple[User, Project | None]:
    linked = (
        await database.execute(
            select(UserIdentity).where(
                UserIdentity.provider == identity.provider,
                UserIdentity.provider_subject == identity.subject,
            )
        )
    ).scalar_one_or_none()

    if linked is not None:
        user = await database.get(User, linked.user_id)
        if user is None:
            raise RuntimeError("OAuth identity points to a missing user")
    else:
        user = (
            await database.execute(
                select(User).where(User.email == identity.email.lower())
            )
        ).scalar_one_or_none()
        if user is None:
            suffix = uuid4().hex[:10]
            tenant = Tenant(
                name=identity.display_name or identity.email,
                slug=f"personal-{suffix}",
            )
            database.add(tenant)
            await database.flush()
            user = User(
                tenant_id=tenant.id,
                email=identity.email.lower(),
                password_hash=None,
                role="tenant_admin",
            )
            database.add(user)
            await database.flush()
        database.add(
            UserIdentity(
                user_id=user.id,
                provider=identity.provider,
                provider_subject=identity.subject,
                email=identity.email.lower(),
                email_verified=identity.email_verified,
                profile=dict(identity.profile),
            )
        )

    trial = (
        await database.execute(
            select(TrialEntitlement).where(TrialEntitlement.user_id == user.id)
        )
    ).scalar_one_or_none()
    if trial is None:
        database.add(TrialEntitlement(user_id=user.id))

    project = None
    if draft_id is not None and claim_token_digest:
        draft = (
            await database.execute(
                select(AnonymousDraft).where(
                    AnonymousDraft.id == draft_id,
                    AnonymousDraft.claim_token_digest == claim_token_digest,
                    AnonymousDraft.claimed_at.is_(None),
                    AnonymousDraft.expires_at > datetime.now(UTC),
                )
            )
        ).scalar_one_or_none()
        if draft is not None:
            project = Project(
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
