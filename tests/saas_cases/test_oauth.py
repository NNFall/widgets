from __future__ import annotations

import httpx
import pytest
from dataclasses import replace

from app.auth.oauth import (
    GoogleOAuthProvider,
    InvalidOAuthCallback,
    OAuthCallback,
    OAuthTransaction,
    UnverifiedIdentity,
    YandexOAuthProvider,
)


def _transaction() -> OAuthTransaction:
    return OAuthTransaction(
        state="state-123",
        code_verifier="verifier-123",
        code_challenge="challenge-123",
        nonce="nonce-123",
        redirect_uri="https://kaigo.space/api/auth/google/callback",
    )


def test_google_authorization_url_uses_state_nonce_and_pkce() -> None:
    provider = GoogleOAuthProvider("client-id", "client-secret")

    url = provider.authorization_url(_transaction())

    assert "state=state-123" in url
    assert "nonce=nonce-123" in url
    assert "code_challenge=challenge-123" in url
    assert "code_challenge_method=S256" in url


@pytest.mark.asyncio
async def test_google_exchange_returns_verified_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://oauth2.googleapis.com/token"
        assert b"code_verifier=verifier-123" in request.content
        return httpx.Response(200, json={"id_token": "signed-id-token", "access_token": "access"})

    provider = GoogleOAuthProvider(
        "client-id",
        "client-secret",
        transport=httpx.MockTransport(handler),
        id_token_verifier=lambda token, audience: {
            "iss": "https://accounts.google.com",
            "aud": audience,
            "sub": "google-user",
            "email": "owner@example.com",
            "email_verified": True,
            "nonce": "nonce-123",
            "name": "Owner",
        },
    )

    identity = await provider.exchange(
        _transaction(), OAuthCallback(code="code-123", state="state-123")
    )

    assert identity.provider == "google"
    assert identity.subject == "google-user"
    assert identity.email == "owner@example.com"
    assert identity.email_verified is True


@pytest.mark.asyncio
async def test_google_rejects_unverified_email() -> None:
    provider = GoogleOAuthProvider(
        "client-id",
        "client-secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id_token": "token"})
        ),
        id_token_verifier=lambda token, audience: {
            "iss": "https://accounts.google.com",
            "aud": audience,
            "sub": "google-user",
            "email": "owner@example.com",
            "email_verified": False,
            "nonce": "nonce-123",
        },
    )

    with pytest.raises(UnverifiedIdentity):
        await provider.exchange(
            _transaction(), OAuthCallback(code="code-123", state="state-123")
        )


@pytest.mark.asyncio
async def test_yandex_exchange_uses_official_profile_endpoint() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == "https://oauth.yandex.ru/token":
            return httpx.Response(200, json={"access_token": "access"})
        assert request.headers["Authorization"] == "OAuth access"
        return httpx.Response(
            200,
            json={"id": "yandex-user", "default_email": "owner@yandex.ru", "display_name": "Owner"},
        )

    transaction = replace(
        _transaction(), redirect_uri="https://kaigo.space/api/auth/yandex/callback"
    )
    provider = YandexOAuthProvider(
        "client-id", "client-secret", transport=httpx.MockTransport(handler)
    )
    identity = await provider.exchange(
        transaction, OAuthCallback(code="code-123", state="state-123")
    )

    assert calls == ["https://oauth.yandex.ru/token", "https://login.yandex.ru/info?format=json"]
    assert identity.provider == "yandex"
    assert identity.subject == "yandex-user"
    assert identity.email_verified is True


@pytest.mark.asyncio
async def test_state_mismatch_is_rejected_before_network() -> None:
    provider = GoogleOAuthProvider("client-id", "client-secret")

    with pytest.raises(InvalidOAuthCallback, match="state"):
        await provider.exchange(
            _transaction(), OAuthCallback(code="code-123", state="wrong")
        )
