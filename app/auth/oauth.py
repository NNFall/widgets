from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode

import httpx


class OAuthError(RuntimeError):
    """Base class for safe OAuth failures."""


class InvalidOAuthCallback(OAuthError):
    pass


class UnverifiedIdentity(OAuthError):
    pass


@dataclass(frozen=True, slots=True)
class OAuthTransaction:
    state: str
    code_verifier: str
    code_challenge: str
    nonce: str
    redirect_uri: str


@dataclass(frozen=True, slots=True)
class OAuthCallback:
    code: str
    state: str
    device_id: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthIdentity:
    provider: str
    subject: str
    email: str
    email_verified: bool
    display_name: str | None
    profile: Mapping[str, Any]


class OAuthProvider(Protocol):
    def authorization_url(self, transaction: OAuthTransaction) -> str: ...

    async def exchange(
        self, transaction: OAuthTransaction, callback: OAuthCallback
    ) -> OAuthIdentity: ...


class GoogleOAuthProvider:
    authorization_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        id_token_verifier: Callable[[str, str], Mapping[str, Any]] | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._id_token_verifier = id_token_verifier
        self._timeout_seconds = timeout_seconds

    def authorization_url(self, transaction: OAuthTransaction) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": transaction.redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": transaction.state,
                "nonce": transaction.nonce,
                "code_challenge": transaction.code_challenge,
                "code_challenge_method": "S256",
                "access_type": "online",
                "prompt": "select_account",
            }
        )
        return f"{self.authorization_endpoint}?{query}"

    async def exchange(
        self, transaction: OAuthTransaction, callback: OAuthCallback
    ) -> OAuthIdentity:
        _validate_callback(transaction, callback)
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout_seconds
        ) as client:
            response = await client.post(
                self.token_endpoint,
                data={
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "code": callback.code,
                    "code_verifier": transaction.code_verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": transaction.redirect_uri,
                },
            )
            _raise_provider_error(response, "Google token exchange failed")
            token = response.json().get("id_token")
            if not isinstance(token, str) or not token:
                raise InvalidOAuthCallback("Google response did not contain an ID token")

        claims = await self._verify_id_token(token)
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise InvalidOAuthCallback("Google ID token issuer is invalid")
        if claims.get("aud") != self.client_id:
            raise InvalidOAuthCallback("Google ID token audience is invalid")
        if claims.get("nonce") != transaction.nonce:
            raise InvalidOAuthCallback("Google ID token nonce is invalid")
        return _identity_from_claims("google", claims)

    async def _verify_id_token(self, token: str) -> Mapping[str, Any]:
        if self._id_token_verifier is not None:
            result = self._id_token_verifier(token, self.client_id)
            if inspect.isawaitable(result):
                result = await result
            return result

        def verify() -> Mapping[str, Any]:
            from google.auth.transport.requests import Request
            from google.oauth2.id_token import verify_oauth2_token

            return verify_oauth2_token(token, Request(), self.client_id)

        return await asyncio.to_thread(verify)


class YandexOAuthProvider:
    authorization_endpoint = "https://oauth.yandex.ru/authorize"
    token_endpoint = "https://oauth.yandex.ru/token"
    profile_endpoint = "https://login.yandex.ru/info?format=json"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    def authorization_url(self, transaction: OAuthTransaction) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": transaction.redirect_uri,
                "response_type": "code",
                "state": transaction.state,
                "code_challenge": transaction.code_challenge,
                "code_challenge_method": "S256",
                "force_confirm": "yes",
            }
        )
        return f"{self.authorization_endpoint}?{query}"

    async def exchange(
        self, transaction: OAuthTransaction, callback: OAuthCallback
    ) -> OAuthIdentity:
        _validate_callback(transaction, callback)
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout_seconds
        ) as client:
            token_response = await client.post(
                self.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": callback.code,
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "code_verifier": transaction.code_verifier,
                    "redirect_uri": transaction.redirect_uri,
                },
            )
            _raise_provider_error(token_response, "Yandex token exchange failed")
            access_token = token_response.json().get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise InvalidOAuthCallback("Yandex response did not contain an access token")
            profile_response = await client.get(
                self.profile_endpoint,
                headers={"Authorization": f"OAuth {access_token}"},
            )
            _raise_provider_error(profile_response, "Yandex profile request failed")
            profile = profile_response.json()

        subject = profile.get("id")
        email = profile.get("default_email")
        if not isinstance(subject, str) or not subject:
            raise UnverifiedIdentity("Yandex profile has no stable subject")
        if not isinstance(email, str) or not email:
            raise UnverifiedIdentity("Yandex profile has no verified default email")
        return OAuthIdentity(
            provider="yandex",
            subject=subject,
            email=email,
            email_verified=True,
            display_name=_optional_text(profile.get("display_name")),
            profile=_public_profile(profile),
        )


class VKOAuthProvider:
    authorization_endpoint = "https://id.vk.ru/authorize"
    token_endpoint = "https://id.vk.ru/oauth2/auth"
    profile_endpoint = "https://id.vk.ru/oauth2/user_info"

    def __init__(
        self,
        app_id: int,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
            raise ValueError("VK OAuth app id must be a positive integer")
        self.app_id = app_id
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    def authorization_url(self, transaction: OAuthTransaction) -> str:
        query = urlencode(
            {
                "client_id": str(self.app_id),
                "redirect_uri": transaction.redirect_uri,
                "response_type": "code",
                "scope": "email",
                "state": transaction.state,
                "code_challenge": transaction.code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.authorization_endpoint}?{query}"

    async def exchange(
        self, transaction: OAuthTransaction, callback: OAuthCallback
    ) -> OAuthIdentity:
        _validate_callback(transaction, callback)
        if not isinstance(callback.device_id, str) or not callback.device_id.strip():
            raise InvalidOAuthCallback("VK callback has no device id")

        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout_seconds
        ) as client:
            token_response = await client.post(
                self.token_endpoint,
                params={
                    "grant_type": "authorization_code",
                    "redirect_uri": transaction.redirect_uri,
                    "client_id": str(self.app_id),
                    "code_verifier": transaction.code_verifier,
                    "state": transaction.state,
                    "device_id": callback.device_id,
                },
                data={"code": callback.code},
            )
            _raise_provider_error(token_response, "VK token exchange failed")
            token_payload = token_response.json()
            if token_payload.get("state") != transaction.state:
                raise InvalidOAuthCallback("VK token response state mismatch")
            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise InvalidOAuthCallback("VK response did not contain an access token")

            profile_response = await client.post(
                self.profile_endpoint,
                params={"client_id": str(self.app_id)},
                data={"access_token": access_token},
            )
            _raise_provider_error(profile_response, "VK profile request failed")
            profile_payload = profile_response.json()

        profile = profile_payload.get("user")
        if not isinstance(profile, Mapping):
            raise UnverifiedIdentity("VK profile response has no user")
        raw_subject = profile.get("user_id")
        subject = (
            str(raw_subject)
            if isinstance(raw_subject, (str, int)) and not isinstance(raw_subject, bool)
            else ""
        )
        email = profile.get("email")
        if not subject:
            raise UnverifiedIdentity("VK profile has no stable subject")
        if not isinstance(email, str) or not email:
            raise UnverifiedIdentity("VK profile has no verified email")

        display_name = " ".join(
            part
            for part in (
                _optional_text(profile.get("first_name")),
                _optional_text(profile.get("last_name")),
            )
            if part
        ) or None
        public_profile: dict[str, Any] = {}
        if display_name:
            public_profile["name"] = display_name
        avatar = _optional_text(profile.get("avatar"))
        if avatar:
            public_profile["picture"] = avatar
        return OAuthIdentity(
            provider="vk",
            subject=subject,
            email=email,
            email_verified=True,
            display_name=display_name,
            profile=public_profile,
        )


def _validate_callback(
    transaction: OAuthTransaction, callback: OAuthCallback
) -> None:
    if not callback.code:
        raise InvalidOAuthCallback("OAuth callback has no authorization code")
    if callback.state != transaction.state:
        raise InvalidOAuthCallback("OAuth state mismatch")


def _raise_provider_error(response: httpx.Response, message: str) -> None:
    if response.is_success:
        return
    raise InvalidOAuthCallback(f"{message} ({response.status_code})")


def _identity_from_claims(
    provider: str, claims: Mapping[str, Any]
) -> OAuthIdentity:
    subject = claims.get("sub")
    email = claims.get("email")
    verified = claims.get("email_verified") is True
    if not isinstance(subject, str) or not subject:
        raise UnverifiedIdentity(f"{provider} identity has no stable subject")
    if not isinstance(email, str) or not email or not verified:
        raise UnverifiedIdentity(f"{provider} email is not verified")
    return OAuthIdentity(
        provider=provider,
        subject=subject,
        email=email,
        email_verified=True,
        display_name=_optional_text(claims.get("name")),
        profile=_public_profile(claims),
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _public_profile(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    allowed = {"name", "display_name", "picture", "login", "default_avatar_id"}
    return {key: value for key, value in profile.items() if key in allowed}
