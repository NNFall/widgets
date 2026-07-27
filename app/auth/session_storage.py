from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiohttp import web
from aiohttp_session import AbstractStorage, Session
from sqlalchemy import select, update

from app.auth.tokens import issue_token, token_digest
from app.db.session import get_session_factory
from app.saas.models import AuthSession


class DatabaseSessionStorage(AbstractStorage):
    """Opaque-cookie session storage backed by the application database."""

    async def load_session(self, request: web.Request) -> Session:
        raw = self.load_cookie(request)
        if not raw:
            return await self.new_session()

        now = datetime.now(UTC)
        factory = get_session_factory(request.app)
        async with factory() as database:
            record = (
                await database.execute(
                    select(AuthSession).where(
                        AuthSession.token_digest == token_digest(raw),
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > now,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                return await self.new_session()
            record.last_seen_at = now
            await database.commit()
            return Session(raw, data=record.payload, new=False, max_age=self.max_age)

    async def save_session(
        self,
        request: web.Request,
        response: web.StreamResponse,
        session: Session,
    ) -> None:
        factory = get_session_factory(request.app)
        now = datetime.now(UTC)

        if session.empty:
            if session.identity:
                async with factory() as database:
                    await database.execute(
                        update(AuthSession)
                        .where(AuthSession.token_digest == token_digest(str(session.identity)))
                        .values(revoked_at=now)
                    )
                    await database.commit()
            self.save_cookie(response, "", max_age=0)
            return

        payload = dict(self._get_session_data(session))
        expires_at = now + timedelta(seconds=self.max_age or 14 * 24 * 60 * 60)
        if session.new or not session.identity:
            raw, digest = issue_token()
            async with factory() as database:
                database.add(
                    AuthSession(
                        token_digest=digest,
                        user_id=_user_id(payload),
                        payload=payload,
                        expires_at=expires_at,
                    )
                )
                await database.commit()
        else:
            raw = str(session.identity)
            async with factory() as database:
                await database.execute(
                    update(AuthSession)
                    .where(AuthSession.token_digest == token_digest(raw))
                    .values(
                        user_id=_user_id(payload),
                        payload=payload,
                        expires_at=expires_at,
                        last_seen_at=now,
                    )
                )
                await database.commit()

        self.save_cookie(response, raw, max_age=self.max_age)

    async def revoke(self, request: web.Request, raw: str) -> None:
        factory = get_session_factory(request.app)
        async with factory() as database:
            await database.execute(
                update(AuthSession)
                .where(AuthSession.token_digest == token_digest(raw))
                .values(revoked_at=datetime.now(UTC))
            )
            await database.commit()


def _user_id(payload: dict[str, object]) -> int | None:
    mapping = payload.get("session")
    if not isinstance(mapping, dict):
        return None
    candidate = mapping.get("user_id") or mapping.get("client_user_id")
    return candidate if isinstance(candidate, int) else None
