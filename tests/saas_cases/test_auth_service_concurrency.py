from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.oauth import OAuthIdentity
from app.auth.service import link_identity_and_claim_draft
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import AnonymousDraft, Project, TrialEntitlement, UserIdentity


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


async def _seed_claim_owner(
    factory: async_sessionmaker,
    *,
    marker: str,
    ordinal: int,
) -> OAuthIdentity:
    identity = OAuthIdentity(
        provider="yandex",
        subject=f"draft-claim-{marker}-{ordinal}",
        email=f"draft-claim-{marker}-{ordinal}@example.com",
        email_verified=True,
        display_name=f"Draft owner {ordinal}",
        profile={"display_name": f"Draft owner {ordinal}"},
    )
    async with factory() as database:
        tenant = Tenant(
            name=f"Draft owner {ordinal}",
            slug=f"draft-claim-{marker}-{ordinal}",
        )
        database.add(tenant)
        await database.flush()
        user = User(
            tenant_id=tenant.id,
            email=identity.email,
            password_hash=None,
            role="tenant_admin",
        )
        database.add(user)
        await database.flush()
        database.add_all(
            [
                UserIdentity(
                    user_id=user.id,
                    provider=identity.provider,
                    provider_subject=identity.subject,
                    email=identity.email,
                    email_verified=True,
                    profile=dict(identity.profile),
                ),
                TrialEntitlement(user_id=user.id),
            ]
        )
        await database.commit()
    return identity


async def _cleanup_claim_fixture(
    factory: async_sessionmaker,
    *,
    marker: str,
    draft_id,
) -> None:
    async with factory() as database:
        await database.execute(delete(AnonymousDraft).where(AnonymousDraft.id == draft_id))
        await database.execute(
            delete(Tenant).where(Tenant.slug.like(f"draft-claim-{marker}-%"))
        )
        await database.commit()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_concurrent_same_owner_draft_claim_replays_one_deterministic_project() -> None:
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid4().hex
    draft_id = uuid4()
    digest = uuid4().hex + uuid4().hex
    expected_project_id = uuid5(
        NAMESPACE_URL, f"https://kaigo.space/drafts/{draft_id}"
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    identity = await _seed_claim_owner(factory, marker=marker, ordinal=1)
    async with factory() as database:
        database.add(
            AnonymousDraft(
                id=draft_id,
                source_url=f"https://same-owner-{marker}.example",
                brief="Concurrent claim",
                campaign={},
                claim_token_digest=digest,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await database.commit()

    start = asyncio.Event()

    async def claim_once():
        async with factory() as database:
            await start.wait()
            user, project = await link_identity_and_claim_draft(
                database,
                identity,
                draft_id=draft_id,
                claim_token_digest=digest,
            )
            await database.commit()
            return user.id, project.id if project is not None else None

    tasks = [asyncio.create_task(claim_once()) for _ in range(4)]
    start.set()
    try:
        claims = await asyncio.gather(*tasks)
        assert len({user_id for user_id, _ in claims}) == 1
        assert {project_id for _, project_id in claims} == {expected_project_id}

        async with factory() as database:
            projects = list(
                (
                    await database.scalars(
                        select(Project).where(Project.id == expected_project_id)
                    )
                ).all()
            )
            draft = await database.get(AnonymousDraft, draft_id)
        assert len(projects) == 1
        assert draft is not None
        assert draft.claimed_by_user_id == claims[0][0]
    finally:
        await _cleanup_claim_fixture(factory, marker=marker, draft_id=draft_id)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_concurrent_cross_owner_draft_claim_has_one_winner_and_no_second_project() -> None:
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid4().hex
    draft_id = uuid4()
    digest = uuid4().hex + uuid4().hex
    expected_project_id = uuid5(
        NAMESPACE_URL, f"https://kaigo.space/drafts/{draft_id}"
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    identities = [
        await _seed_claim_owner(factory, marker=marker, ordinal=ordinal)
        for ordinal in (1, 2)
    ]
    async with factory() as database:
        database.add(
            AnonymousDraft(
                id=draft_id,
                source_url=f"https://cross-owner-{marker}.example",
                brief="Only one owner",
                campaign={},
                claim_token_digest=digest,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await database.commit()

    start = asyncio.Event()

    async def claim_once(identity: OAuthIdentity):
        async with factory() as database:
            await start.wait()
            user, project = await link_identity_and_claim_draft(
                database,
                identity,
                draft_id=draft_id,
                claim_token_digest=digest,
            )
            await database.commit()
            return user.id, project.id if project is not None else None

    tasks = [asyncio.create_task(claim_once(identity)) for identity in identities]
    start.set()
    try:
        claims = await asyncio.gather(*tasks)
        winners = [(user_id, project_id) for user_id, project_id in claims if project_id]
        losers = [(user_id, project_id) for user_id, project_id in claims if not project_id]
        assert len(winners) == 1
        assert winners[0][1] == expected_project_id
        assert len(losers) == 1

        async with factory() as database:
            projects = list(
                (
                    await database.scalars(
                        select(Project).where(Project.id == expected_project_id)
                    )
                ).all()
            )
            draft = await database.get(AnonymousDraft, draft_id)
        assert len(projects) == 1
        assert draft is not None
        assert draft.claimed_by_user_id == winners[0][0]
        assert projects[0].owner_user_id == winners[0][0]
    finally:
        await _cleanup_claim_fixture(factory, marker=marker, draft_id=draft_id)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_concurrent_identity_links_are_database_idempotent() -> None:
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid4().hex
    email = f"oauth-race-{marker}@example.com"
    subject = f"oauth-race-{marker}"

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as database:
        tenant = Tenant(name="OAuth race", slug=f"oauth-race-{marker}")
        database.add(tenant)
        await database.flush()
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=None,
            role="tenant_admin",
        )
        database.add(user)
        await database.commit()
        expected_user_id = user.id

    identity = OAuthIdentity(
        provider="google",
        subject=subject,
        email=email,
        email_verified=True,
        display_name="OAuth race",
        profile={"name": "OAuth race"},
    )
    start = asyncio.Event()

    async def link_once() -> int:
        async with factory() as database:
            await start.wait()
            linked_user, project = await link_identity_and_claim_draft(
                database,
                identity,
                draft_id=None,
                claim_token_digest=None,
            )
            assert project is None
            await database.commit()
            return linked_user.id

    tasks = [asyncio.create_task(link_once()) for _ in range(12)]
    start.set()
    try:
        user_ids = await asyncio.gather(*tasks)
        assert user_ids == [expected_user_id] * len(tasks)

        async with factory() as database:
            identities = list(
                (
                    await database.scalars(
                        select(UserIdentity).where(
                            UserIdentity.provider == "google",
                            UserIdentity.provider_subject == subject,
                        )
                    )
                ).all()
            )
            trials = list(
                (
                    await database.scalars(
                        select(TrialEntitlement).where(
                            TrialEntitlement.user_id == expected_user_id
                        )
                    )
                ).all()
            )
            assert len(identities) == 1
            assert identities[0].user_id == expected_user_id
            assert len(trials) == 1
    finally:
        async with factory() as database:
            await database.execute(
                delete(Tenant).where(Tenant.slug == f"oauth-race-{marker}")
            )
            await database.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_concurrent_first_logins_create_one_account_and_trial() -> None:
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid4().hex
    email = f"oauth-first-login-{marker}@example.com"
    subject = f"oauth-first-login-{marker}"

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    identity = OAuthIdentity(
        provider="yandex",
        subject=subject,
        email=email,
        email_verified=True,
        display_name="Concurrent owner",
        profile={"display_name": "Concurrent owner"},
    )
    start = asyncio.Event()

    async def link_once() -> int:
        async with factory() as database:
            await start.wait()
            linked_user, project = await link_identity_and_claim_draft(
                database,
                identity,
                draft_id=None,
                claim_token_digest=None,
            )
            assert project is None
            await database.commit()
            return linked_user.id

    tasks = [asyncio.create_task(link_once()) for _ in range(12)]
    start.set()
    tenant_id = None
    try:
        user_ids = await asyncio.gather(*tasks)
        assert len(set(user_ids)) == 1

        async with factory() as database:
            users = list(
                (
                    await database.scalars(select(User).where(User.email == email))
                ).all()
            )
            assert len(users) == 1
            tenant_id = users[0].tenant_id
            assert user_ids == [users[0].id] * len(tasks)
            assert (
                await database.scalar(
                    select(UserIdentity).where(
                        UserIdentity.provider == "yandex",
                        UserIdentity.provider_subject == subject,
                    )
                )
            ).user_id == users[0].id
            assert (
                await database.scalar(
                    select(TrialEntitlement).where(
                        TrialEntitlement.user_id == users[0].id
                    )
                )
            ) is not None
    finally:
        if tenant_id is not None:
            async with factory() as database:
                await database.execute(
                    delete(Tenant).where(Tenant.id == tenant_id)
                )
                await database.commit()
        await engine.dispose()
