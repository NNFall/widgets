from __future__ import annotations

import httpx
import pytest
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from app.auth.oauth import (
    GoogleOAuthProvider,
    InvalidOAuthCallback,
    OAuthCallback,
    OAuthTransaction,
    UnverifiedIdentity,
    VKOAuthProvider,
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


def test_vk_authorization_url_uses_email_state_and_pkce() -> None:
    transaction = replace(
        _transaction(), redirect_uri="https://kaigo.space/api/auth/vk/callback"
    )
    provider = VKOAuthProvider(54721213)

    parsed = urlsplit(provider.authorization_url(transaction))
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "id.vk.ru"
    assert parsed.path == "/authorize"
    assert query == {
        "client_id": ["54721213"],
        "redirect_uri": ["https://kaigo.space/api/auth/vk/callback"],
        "response_type": ["code"],
        "scope": ["email"],
        "state": ["state-123"],
        "code_challenge": ["challenge-123"],
        "code_challenge_method": ["S256"],
    }


@pytest.mark.asyncio
async def test_vk_exchange_uses_device_id_pkce_and_verified_profile() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth2/auth":
            assert dict(request.url.params) == {
                "grant_type": "authorization_code",
                "redirect_uri": "https://kaigo.space/api/auth/vk/callback",
                "client_id": "54721213",
                "code_verifier": "verifier-123",
                "state": "state-123",
                "device_id": "device-123",
            }
            assert request.content == b"code=code-123"
            return httpx.Response(
                200,
                json={
                    "state": "state-123",
                    "access_token": "vk-access",
                    "refresh_token": "vk-refresh",
                },
            )
        assert request.url.path == "/oauth2/user_info"
        assert dict(request.url.params) == {"client_id": "54721213"}
        assert request.content == b"access_token=vk-access"
        return httpx.Response(
            200,
            json={
                "user": {
                    "user_id": "vk-user-1",
                    "email": "owner@example.com",
                    "first_name": "Никита",
                    "last_name": "Новосельцев",
                    "avatar": "https://sun.example/avatar.jpg",
                }
            },
        )

    transaction = replace(
        _transaction(), redirect_uri="https://kaigo.space/api/auth/vk/callback"
    )
    provider = VKOAuthProvider(54721213, transport=httpx.MockTransport(handler))

    identity = await provider.exchange(
        transaction,
        OAuthCallback(
            code="code-123", state="state-123", device_id="device-123"
        ),
    )

    assert len(requests) == 2
    assert identity.provider == "vk"
    assert identity.subject == "vk-user-1"
    assert identity.email == "owner@example.com"
    assert identity.email_verified is True
    assert identity.display_name == "Никита Новосельцев"
    assert identity.profile == {
        "name": "Никита Новосельцев",
        "picture": "https://sun.example/avatar.jpg",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", [None, ""])
async def test_vk_exchange_requires_device_id(device_id: str | None) -> None:
    provider = VKOAuthProvider(54721213)
    transaction = replace(
        _transaction(), redirect_uri="https://kaigo.space/api/auth/vk/callback"
    )

    with pytest.raises(InvalidOAuthCallback, match="device"):
        await provider.exchange(
            transaction,
            OAuthCallback(code="code-123", state="state-123", device_id=device_id),
        )


@pytest.mark.asyncio
async def test_vk_exchange_rejects_provider_state_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"state": "other-state", "access_token": "vk-access"},
        )

    provider = VKOAuthProvider(54721213, transport=httpx.MockTransport(handler))
    transaction = replace(
        _transaction(), redirect_uri="https://kaigo.space/api/auth/vk/callback"
    )

    with pytest.raises(InvalidOAuthCallback, match="state"):
        await provider.exchange(
            transaction,
            OAuthCallback(
                code="code-123", state="state-123", device_id="device-123"
            ),
        )


@pytest.mark.asyncio
async def test_state_mismatch_is_rejected_before_network() -> None:
    provider = GoogleOAuthProvider("client-id", "client-secret")

    with pytest.raises(InvalidOAuthCallback, match="state"):
        await provider.exchange(
            _transaction(), OAuthCallback(code="code-123", state="wrong")
        )
