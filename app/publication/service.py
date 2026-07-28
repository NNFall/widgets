from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Sequence
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.saas.models import (
    GenerationArtifact,
    GenerationRun,
    Project,
    Publication,
    PublicationRelease,
    Subscription,
    UserIdentity,
)
from builder_lab.models import WidgetArtifact
from builder_lab.validation import validate_artifact

_RELEASE_QUALITY = frozenset({"accepted", "verified"})
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class PublicationError(RuntimeError):
    pass


class PublicationNotFound(PublicationError):
    pass


class InvalidPublicationArtifact(PublicationError):
    pass


class InvalidAllowedDomain(PublicationError):
    pass


class ReleaseCorrupt(PublicationError):
    pass


class PublicationUpgradeRequired(PublicationError):
    pass


class PublicationIdentityUnverified(PublicationError):
    pass


@dataclass(frozen=True, slots=True)
class PublishedRelease:
    publication_id: UUID
    release_id: UUID
    artifact_id: UUID
    stable_key: str
    revision: int
    allowed_domains: tuple[str, ...]
    manifest: dict[str, Any]
    checksum: str
    created: bool = False


class PublicationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        allow_insecure_origins: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._allow_insecure_origins = allow_insecure_origins

    @staticmethod
    def manifest_checksum(manifest: dict[str, Any]) -> str:
        canonical = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    async def publish(
        self,
        project_id: UUID,
        *,
        actor_user_id: int,
        tenant_id: int,
        artifact_id: UUID | None = None,
        revision: int | None = None,
        allowed_domains: Sequence[str] | None = None,
    ) -> PublishedRelease:
        if artifact_id is None and revision is None:
            raise InvalidPublicationArtifact("artifact_id is required")
        async with self._session_factory() as database, database.begin():
            # Global lock ordering is Project -> Publication. It serializes the
            # concurrent first-publish path before the unique project key is hit.
            project = await database.scalar(
                select(Project)
                .where(
                    Project.id == project_id,
                    Project.owner_user_id == actor_user_id,
                    Project.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if project is None:
                raise PublicationNotFound("project not found")
            await self._require_entitlement(database, actor_user_id)
            publication = await database.scalar(
                select(Publication)
                .where(Publication.project_id == project.id)
                .with_for_update()
            )
            artifact = await self._select_artifact(
                database,
                project,
                artifact_id=artifact_id,
                revision=revision,
            )
            candidate = self._validated_artifact(artifact)
            domains = self._domains(allowed_domains, source_url=project.source_url)

            if publication is None:
                publication = Publication(
                    project_id=project.id,
                    stable_key=secrets.token_urlsafe(24),
                    allowed_domains=list(domains),
                    state="draft",
                )
                database.add(publication)
                await database.flush()
            else:
                publication.allowed_domains = list(domains)

            existing = await database.scalar(
                select(PublicationRelease).where(
                    PublicationRelease.publication_id == publication.id,
                    PublicationRelease.artifact_id == artifact.id,
                )
            )
            if existing is not None:
                self._verify_release(existing)
                publication.active_release_id = existing.id
                publication.state = "published"
                return self._snapshot(publication, existing, created=False)

            manifest = {
                "version": 1,
                "artifact_id": str(artifact.id),
                # Persist only runtime material. Prompts, source URLs,
                # provenance, review text and provider traces stay private.
                "artifact": {
                    "schema_version": candidate.schema_version,
                    "revision": candidate.revision,
                    "stage": candidate.stage.value,
                    "body_html": candidate.body_html,
                    "css": candidate.css,
                    "javascript": candidate.javascript,
                    "theme_tokens": dict(candidate.theme_tokens),
                    "layout_contract": dict(candidate.layout_contract),
                },
            }
            release = PublicationRelease(
                id=uuid4(),
                publication_id=publication.id,
                artifact_id=artifact.id,
                previous_release_id=publication.active_release_id,
                revision=artifact.revision,
                asset_manifest=manifest,
                checksum=self.manifest_checksum(manifest),
            )
            database.add(release)
            await database.flush()
            publication.active_release_id = release.id
            publication.state = "published"
            return self._snapshot(publication, release, created=True)

    async def rollback(
        self,
        publication_id: UUID,
        *,
        actor_user_id: int,
        tenant_id: int,
        target_release_id: UUID,
    ) -> PublishedRelease:
        async with self._session_factory() as database, database.begin():
            # Resolve the parent through a join while locking only Project, then
            # acquire Publication. This preserves the global Project ->
            # Publication lock order even during concurrent rollback/publish.
            project_id = await database.scalar(
                select(Project.id)
                .join(Publication, Publication.project_id == Project.id)
                .where(
                    Publication.id == publication_id,
                    Project.owner_user_id == actor_user_id,
                    Project.tenant_id == tenant_id,
                )
                .with_for_update(of=Project)
            )
            if project_id is None:
                raise PublicationNotFound("publication not found")
            await self._require_entitlement(database, actor_user_id)
            publication = await database.scalar(
                select(Publication)
                .where(Publication.id == publication_id)
                .with_for_update()
            )
            release = await database.scalar(
                select(PublicationRelease).where(
                    PublicationRelease.id == target_release_id,
                    PublicationRelease.publication_id == publication_id,
                )
            )
            if publication is None or release is None:
                raise PublicationNotFound("release not found")
            self._verify_release(release)
            publication.active_release_id = release.id
            publication.state = "published"
            return self._snapshot(publication, release, created=False)

    @staticmethod
    async def _require_entitlement(database: AsyncSession, user_id: int) -> None:
        verified = await database.scalar(
            select(UserIdentity).where(
                UserIdentity.user_id == user_id,
                UserIdentity.email_verified.is_(True),
            ).limit(1).with_for_update()
        )
        if verified is None:
            raise PublicationIdentityUnverified("verified OAuth identity required")
        active = await database.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.current_period_end > datetime.now(UTC),
            ).limit(1).with_for_update()
        )
        if active is None:
            raise PublicationUpgradeRequired("active subscription required")

    async def resolve(self, stable_key: str) -> PublishedRelease:
        async with self._session_factory() as database:
            row = (
                await database.execute(
                    select(Publication, PublicationRelease)
                    .join(
                        PublicationRelease,
                        PublicationRelease.id == Publication.active_release_id,
                    )
                    .where(
                        Publication.stable_key == stable_key,
                        Publication.state == "published",
                    )
                )
            ).one_or_none()
            if row is None:
                raise PublicationNotFound("publication not found")
            publication, release = row
            if release.publication_id != publication.id:
                raise ReleaseCorrupt("active release belongs to another publication")
            self._verify_release(release)
            return self._snapshot(publication, release, created=False)

    async def _select_artifact(
        self,
        database: AsyncSession,
        project: Project,
        *,
        artifact_id: UUID | None,
        revision: int | None,
    ) -> GenerationArtifact:
        statement = (
            select(GenerationArtifact)
            .join(GenerationRun, GenerationArtifact.run_id == GenerationRun.id)
            .where(
                GenerationRun.project_id == project.id,
                GenerationArtifact.quality_status.in_(_RELEASE_QUALITY),
            )
        )
        if artifact_id is not None:
            statement = statement.where(GenerationArtifact.id == artifact_id)
        else:
            if project.active_run_id is None:
                raise InvalidPublicationArtifact("project has no active run")
            statement = statement.where(
                GenerationArtifact.run_id == project.active_run_id,
                GenerationArtifact.revision == revision,
            )
        artifact = await database.scalar(statement)
        if artifact is None:
            raise InvalidPublicationArtifact("artifact is not publishable")
        return artifact

    @staticmethod
    def _validated_artifact(artifact: GenerationArtifact) -> WidgetArtifact:
        configured = artifact.config.get("artifact") if isinstance(artifact.config, dict) else None
        if not isinstance(configured, dict):
            raise InvalidPublicationArtifact("artifact config is missing")
        try:
            candidate = WidgetArtifact.from_dict(configured)
            issues = validate_artifact(
                candidate,
                previous_revision=max(0, candidate.revision - 1),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidPublicationArtifact("artifact config is invalid") from error
        consistent = (
            candidate.revision == artifact.revision
            and candidate.stage.value == artifact.stage
            and candidate.body_html == artifact.html
            and candidate.css == artifact.css
            and candidate.javascript == artifact.javascript
        )
        if issues or not consistent:
            raise InvalidPublicationArtifact("artifact failed publication validation")
        return candidate

    def _domains(
        self,
        supplied: Sequence[str] | None,
        *,
        source_url: str,
    ) -> tuple[str, ...]:
        if supplied is None:
            return (self._source_origin(source_url),)
        if not isinstance(supplied, (list, tuple)):
            raise InvalidAllowedDomain("allowed_domains must be an array")
        return tuple(dict.fromkeys(self._normalize_origin(value) for value in supplied))

    def _source_origin(self, source_url: str) -> str:
        parsed = urlsplit(source_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise InvalidAllowedDomain("project source URL has no safe HTTPS origin")
        value = f"https://{parsed.hostname}"
        if parsed.port not in (None, 443):
            value += f":{parsed.port}"
        return self._normalize_origin(value)

    def _normalize_origin(self, value: str) -> str:
        if not isinstance(value, str) or not value or any(ord(char) < 33 for char in value):
            raise InvalidAllowedDomain("invalid allowed origin")
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme != "https" and not self._allow_insecure_origins:
            raise InvalidAllowedDomain("allowed origin must use HTTPS")
        if scheme not in ({"http", "https"} if self._allow_insecure_origins else {"https"}):
            raise InvalidAllowedDomain("unsupported origin scheme")
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise InvalidAllowedDomain("allowed domain must be an exact origin")
        try:
            port = parsed.port
        except ValueError as error:
            raise InvalidAllowedDomain("invalid origin port") from error
        host = parsed.hostname.rstrip(".").lower()
        browser_address = _parse_browser_ipv4(host)
        if browser_address is not None and host != str(browser_address):
            raise InvalidAllowedDomain("legacy numeric IPv4 origins are disabled")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError as error:
                raise InvalidAllowedDomain("invalid origin host") from error
            if not host or any(not _HOST_LABEL.fullmatch(label) for label in host.split(".")):
                raise InvalidAllowedDomain("invalid origin host")
            if (
                host == "localhost" or host.endswith(".localhost")
            ) and not self._allow_insecure_origins:
                raise InvalidAllowedDomain("localhost is disabled")
        else:
            if address.is_loopback and not self._allow_insecure_origins:
                raise InvalidAllowedDomain("loopback origins are disabled")
            if address.version == 6:
                host = f"[{host}]"
        default_port = 443 if scheme == "https" else 80
        suffix = f":{port}" if port is not None and port != default_port else ""
        return f"{scheme}://{host}{suffix}"

    def normalize_origin(self, value: str) -> str:
        """Normalize an embed origin with the service's production policy."""
        return self._normalize_origin(value)

    def _verify_release(self, release: PublicationRelease) -> None:
        if self.manifest_checksum(release.asset_manifest) != release.checksum:
            raise ReleaseCorrupt("release checksum mismatch")
        manifest = release.asset_manifest
        if not isinstance(manifest, dict):
            raise ReleaseCorrupt("release manifest is invalid")
        artifact = manifest.get("artifact")
        if (
            manifest.get("version") != 1
            or manifest.get("artifact_id") != str(release.artifact_id)
            or not isinstance(artifact, dict)
            or artifact.get("revision") != release.revision
            or not isinstance(artifact.get("body_html"), str)
            or not isinstance(artifact.get("css"), str)
            or not isinstance(artifact.get("javascript", ""), str)
        ):
            raise ReleaseCorrupt("release manifest is invalid")

    def _snapshot(
        self,
        publication: Publication,
        release: PublicationRelease,
        *,
        created: bool,
    ) -> PublishedRelease:
        try:
            domains = tuple(
                self._normalize_origin(value) for value in publication.allowed_domains
            )
        except (InvalidAllowedDomain, TypeError) as error:
            raise ReleaseCorrupt("publication domain policy is invalid") from error
        return PublishedRelease(
            publication_id=publication.id,
            release_id=release.id,
            artifact_id=release.artifact_id,
            stable_key=publication.stable_key,
            revision=release.revision,
            allowed_domains=domains,
            manifest=dict(release.asset_manifest),
            checksum=release.checksum,
            created=created,
        )


def _parse_browser_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse WHATWG-style numeric IPv4 hosts without performing DNS."""
    if not host or any(char not in "0123456789abcdefxABCDEF." for char in host):
        return None
    raw_parts = host.split(".")
    if not 1 <= len(raw_parts) <= 4 or any(not part for part in raw_parts):
        return None
    numbers: list[int] = []
    for part in raw_parts:
        base = 10
        digits = part
        if part.lower().startswith("0x"):
            base, digits = 16, part[2:]
        elif len(part) > 1 and part.startswith("0"):
            base, digits = 8, part[1:]
        if not digits:
            return None
        try:
            numbers.append(int(digits, base))
        except ValueError:
            return None
    if any(value > 255 for value in numbers[:-1]):
        return None
    final_limit = (256 ** (5 - len(numbers))) - 1
    if numbers[-1] > final_limit:
        return None
    numeric = numbers[-1]
    for index, value in enumerate(numbers[:-1]):
        numeric += value * (256 ** (3 - index))
    try:
        return ipaddress.IPv4Address(numeric)
    except ipaddress.AddressValueError:
        return None


__all__ = [
    "InvalidAllowedDomain",
    "InvalidPublicationArtifact",
    "PublicationNotFound",
    "PublicationIdentityUnverified",
    "PublicationService",
    "PublicationUpgradeRequired",
    "PublishedRelease",
    "ReleaseCorrupt",
]
