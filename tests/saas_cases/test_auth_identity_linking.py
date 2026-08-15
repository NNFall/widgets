from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.oauth import OAuthIdentity
from app.auth.service import IdentityLinkConflict, link_identity_and_claim_draft
from app.db.base import Base


def _identity(*, provider: str, subject: str, email: str) -> OAuthIdentity:
    return OAuthIdentity(
        provider=provider,
        subject=subject,
        email=email,
        email_verified=True,
        display_name="Owner",
        profile={"name": "Owner"},
    )


async def _login(factory, identity: OAuthIdentity):
    async with factory() as database:
        user, project = await link_identity_and_claim_draft(
            database,
            identity,
            draft_id=None,
            claim_token_digest=None,
        )
        assert project is None
        await database.commit()
        return user


@pytest.mark.asyncio
async def test_vk_email_cannot_attach_to_an_existing_google_account() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _login(
            factory,
            _identity(
                provider="google",
                subject="google-owner",
                email="owner@example.com",
            ),
        )

        with pytest.raises(IdentityLinkConflict):
            await _login(
                factory,
                _identity(
                    provider="vk",
                    subject="vk-owner",
                    email="owner@example.com",
                ),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_google_email_cannot_attach_to_an_existing_vk_account() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        vk_user = await _login(
            factory,
            _identity(
                provider="vk",
                subject="vk-owner",
                email="owner@example.com",
            ),
        )

        with pytest.raises(IdentityLinkConflict):
            await _login(
                factory,
                _identity(
                    provider="google",
                    subject="google-owner",
                    email="owner@example.com",
                ),
            )

        repeated = await _login(
            factory,
            _identity(
                provider="vk",
                subject="vk-owner",
                email="owner@example.com",
            ),
        )
        assert repeated.id == vk_user.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verified_google_and_yandex_emails_still_link_one_account() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        google_user = await _login(
            factory,
            _identity(
                provider="google",
                subject="google-owner",
                email="owner@example.com",
            ),
        )
        yandex_user = await _login(
            factory,
            _identity(
                provider="yandex",
                subject="yandex-owner",
                email="OWNER@example.com",
            ),
        )

        assert yandex_user.id == google_user.id
    finally:
        await engine.dispose()
