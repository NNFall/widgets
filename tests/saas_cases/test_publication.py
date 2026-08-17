from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

from app.db.base import Base
from app.chat import CHAT_SERVICE_KEY, ChatReply, RoutedChatService
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.models.contracts import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderCapabilities,
)
from app.models.router import ModelPolicy, ModelRouter, ProviderTarget, SqlModelCallAudit
from app.publication import routes as publication_routes
from app.publication.routes import setup_publication_routes
from app.publication.service import (
    InvalidAllowedDomain,
    InvalidPublicationArtifact,
    PublicationConflict,
    PublicationIdentityUnverified,
    PublicationNotFound,
    PublicationService,
    PublicationUpgradeRequired,
    ReleaseCorrupt,
)
from app.widgets.loader import render_loader, render_runtime
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    Project,
    ProjectVersion,
    Publication,
    PublicationRelease,
    Subscription,
    UserIdentity,
)
from builder_lab.models import AssistantPersona, BuilderRequest, EngineName
from tests.builder_lab_cases.test_validation import artifact


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


def _assistant_persona() -> AssistantPersona:
    return AssistantPersona.from_dict(
        {
            "schema_version": "kaigo.assistant-persona.v1",
            "employee_type": "concierge",
            "display_name": "Анна",
            "role_summary": "Помогает посетителю сориентироваться в проверенных услугах.",
            "voice_style": "friendly",
            "opening_line": "Добрый день! Чем помочь?",
            "behavior_rules": ["Отвечай кратко.", "Уточняй задачу посетителя."],
            "safeguards": ["Не придумывай факты."],
            "decision_rationale": "Дружелюбный консьерж уместен для публичного чата.",
        }
    )


class FakePublicChatService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reply(self, **kwargs) -> ChatReply:
        self.calls.append(kwargs)
        return ChatReply(
            request_id=kwargs["request_id"],
            text="Публичный проверенный ответ",
        )

    async def close(self) -> None:
        return None


class FakePublicChatProvider:
    capabilities = ProviderCapabilities()

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        assert model == "fake-public-chat-model"
        self.requests.append(request)
        return ModelResponse(
            text="Проверенный публичный ответ",
            usage=ModelUsage(input_tokens=11, output_tokens=7, thinking_tokens=2),
            request_id=f"public-provider-{len(self.requests)}",
        )

    async def aclose(self) -> None:
        return None


def _runtime_capability(runtime_text: str) -> str:
    match = re.search(r"const chatCapability=(\"[^\"]+\");", runtime_text)
    assert match is not None
    return json.loads(match.group(1))


def _legacy_builder_request_without_reference_context() -> dict:
    payload = BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Valid legacy publication request",
        source_url="https://example.com/",
    ).to_dict()
    del payload["reference_context"]
    return payload


def test_production_unknown_loopback_proxy_disables_ip_bucket() -> None:
    request = SimpleNamespace(
        remote="127.0.0.1",
        headers={"X-Forwarded-For": "198.51.100.25"},
        app={
            publication_routes.PUBLICATION_CHAT_TRUSTED_PROXIES_KEY: (),
            "config": SimpleNamespace(environment="production"),
        },
    )

    assert publication_routes._visitor_remote_address(request) is None


async def _seed(factory):
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add_all([
            User(id=10, tenant_id=1, email="owner@example.com"),
            User(id=11, tenant_id=1, email="other@example.com"),
        ])
        await database.flush()
        database.add_all([
            UserIdentity(
                user_id=10,
                provider="google",
                provider_subject="owner",
                email="owner@example.com",
                email_verified=True,
            ),
            UserIdentity(
                user_id=11,
                provider="google",
                provider_subject="other",
                email="other@example.com",
                email_verified=True,
            ),
            Subscription(
                user_id=10,
                provider="test",
                plan_code="pro",
                status="active",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=1),
            ),
            Subscription(
                user_id=11,
                provider="test",
                plan_code="pro",
                status="active",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=1),
            ),
        ])
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://Example.COM/products/widget?campaign=private",
            brief="Private prompt must never be published",
        )
        foreign_project = Project(
            tenant_id=1,
            owner_user_id=11,
            source_url="https://other.example/",
        )
        database.add_all([project, foreign_project])
        await database.flush()
        run = GenerationRun(
            project_id=project.id,
            mode="express",
            state="completed",
            idempotency_key="run-one",
        )
        second_run = GenerationRun(
            project_id=project.id,
            mode="express",
            state="completed",
            idempotency_key="run-two",
        )
        foreign_run = GenerationRun(
            project_id=foreign_project.id,
            mode="express",
            state="completed",
            idempotency_key="foreign-run",
        )
        database.add_all([run, second_run, foreign_run])
        await database.flush()

        def stored(target_run, revision, quality="verified"):
            candidate = artifact(revision=revision)
            return GenerationArtifact(
                run_id=target_run.id,
                revision=revision,
                stage=candidate.stage.value,
                html=candidate.body_html,
                css=candidate.css,
                javascript=candidate.javascript,
                config={"artifact": candidate.to_dict(), "prompt": "never publish"},
                quality_status=quality,
                provenance={"raw_response": "never publish"},
            )

        first = stored(run, 1)
        same_revision_other_run = stored(second_run, 1)
        draft = stored(run, 2, "needs_repair")
        foreign = stored(foreign_run, 1)
        database.add_all([first, same_revision_other_run, draft, foreign])
        await database.flush()
        first_version = ProjectVersion(
            project_id=project.id,
            ordinal=1,
            run_id=run.id,
            artifact_id=first.id,
            kind="initial",
        )
        database.add(first_version)
        await database.flush()
        second_version = ProjectVersion(
            project_id=project.id,
            ordinal=2,
            run_id=second_run.id,
            artifact_id=same_revision_other_run.id,
            parent_version_id=first_version.id,
            kind="refinement",
            change_request="Use the alternate verified version",
        )
        database.add(second_version)
        await database.flush()
        draft_version = ProjectVersion(
            project_id=project.id,
            ordinal=3,
            run_id=run.id,
            artifact_id=draft.id,
            parent_version_id=second_version.id,
            kind="refinement",
            change_request="Publish an intentionally unreviewed artifact",
        )
        foreign_version = ProjectVersion(
            project_id=foreign_project.id,
            ordinal=1,
            run_id=foreign_run.id,
            artifact_id=foreign.id,
            kind="initial",
        )
        database.add_all([draft_version, foreign_version])
        await database.flush()
        project.active_run_id = run.id
        project.active_revision = 1
        project.active_version_id = first_version.id
        return {
            "project": project.id,
            "foreign_project": foreign_project.id,
            "first": first.id,
            "same_revision_other_run": same_revision_other_run.id,
            "draft": draft.id,
            "foreign": foreign.id,
            "first_version": first_version.id,
            "second_version": second_version.id,
            "draft_version": draft_version.id,
            "foreign_version": foreign_version.id,
        }


@pytest_asyncio.fixture
async def publication_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'publication.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(factory)
    try:
        yield engine, factory, ids
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_publication_body_stops_streaming_at_32_kib() -> None:
    class ChunkedContent:
        def __init__(self) -> None:
            self.consumed = 0

        async def iter_chunked(self, _size: int):
            for chunk in (b"x" * 32_769, b"must-not-be-consumed"):
                self.consumed += 1
                yield chunk

    class StreamingRequest:
        content_length = None

        def __init__(self) -> None:
            self.content = ChunkedContent()

        async def read(self) -> bytes:
            raise AssertionError("publication bodies must not be fully buffered")

    request = StreamingRequest()
    with pytest.raises(web.HTTPRequestEntityTooLarge):
        await publication_routes._body(
            request,
            allowed_keys=frozenset({"project_version_id"}),
            required_keys=frozenset({"project_version_id"}),
        )
    assert request.content.consumed == 1


@pytest_asyncio.fixture
async def postgres_publication_db():
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(factory)
    try:
        yield engine, factory, ids
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_concurrent_version_updates_have_one_cas_winner(
    postgres_publication_db,
) -> None:
    _engine, factory, ids = postgres_publication_db
    service = PublicationService(factory)
    first = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["first_version"],
        expected_active_release_id=None,
        allowed_domains=["https://example.com"],
    )

    outcomes = await asyncio.gather(
        service.publish_version(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            project_version_id=ids["second_version"],
            expected_active_release_id=first.release_id,
            allowed_domains=["https://winner-a.example"],
        ),
        service.publish_version(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            project_version_id=ids["second_version"],
            expected_active_release_id=first.release_id,
            allowed_domains=["https://winner-b.example"],
        ),
        return_exceptions=True,
    )

    winners = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, PublicationConflict)]
    assert len(winners) == 1
    assert len(conflicts) == 1
    winner = winners[0]
    state = await service.get_project_state(
        ids["project"], actor_user_id=10, tenant_id=1
    )
    assert state is not None
    assert state.active_release.release_id == winner.release_id
    assert state.allowed_domains == winner.allowed_domains
    async with factory() as database:
        assert await database.scalar(select(func.count()).select_from(Publication)) == 1
        assert (
            await database.scalar(select(func.count()).select_from(PublicationRelease))
            == 2
        )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_concurrent_first_publish_preserves_stable_membership(
    postgres_publication_db,
) -> None:
    _engine, factory, ids = postgres_publication_db
    first, second = await asyncio.gather(
        *[
            PublicationService(factory).publish_version(
                ids["project"],
                actor_user_id=10,
                tenant_id=1,
                project_version_id=ids["first_version"],
                expected_active_release_id=None,
                allowed_domains=["https://example.com"],
            )
            for _ in range(2)
        ]
    )

    assert first.stable_key == second.stable_key
    assert first.release_id == second.release_id
    async with factory() as database:
        publication = await database.scalar(select(Publication))
        release = await database.scalar(select(PublicationRelease))
        assert publication is not None
        assert release is not None
        assert await database.scalar(select(func.count()).select_from(Publication)) == 1
        assert (
            await database.scalar(select(func.count()).select_from(PublicationRelease))
            == 1
        )
        assert publication.project_id == ids["project"]
        assert publication.active_release_id == release.id
        assert release.publication_id == publication.id
        assert release.project_id == ids["project"]
        assert release.project_version_id == ids["first_version"]


@pytest.mark.asyncio
async def test_publish_is_artifact_idempotent_stable_and_immutable(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)

    first = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["first"]
    )
    duplicate = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["first"]
    )
    second = await service.publish(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        artifact_id=ids["same_revision_other_run"],
    )

    assert first.created is True
    assert duplicate.created is False
    assert second.created is True
    assert duplicate.release_id == first.release_id
    assert second.stable_key == first.stable_key
    assert second.release_id != first.release_id
    assert second.revision == first.revision == 1
    resolved = await service.resolve(first.stable_key)
    assert resolved.release_id == second.release_id
    assert resolved.manifest["artifact"]["body_html"]
    serialized = json.dumps(resolved.manifest)
    assert "Private prompt" not in serialized
    assert "never publish" not in serialized
    assert "source_url" not in serialized

    async with factory() as database:
        assert await database.scalar(select(func.count()).select_from(Publication)) == 1
        assert await database.scalar(select(func.count()).select_from(PublicationRelease)) == 2
        original = await database.get(PublicationRelease, first.release_id)
        assert original.asset_manifest == first.manifest
        assert original.project_version_id is None
        active = await database.get(PublicationRelease, second.release_id)
        assert active.project_version_id is None


@pytest.mark.asyncio
async def test_publish_carries_selected_persona_into_public_runtime(publication_db) -> None:
    _engine, factory, ids = publication_db
    async with factory() as database, database.begin():
        artifact_record = await database.get(GenerationArtifact, ids["first"])
        assert artifact_record is not None
        artifact_record.config = {
            **artifact_record.config,
            "assistant_persona": _assistant_persona().to_dict(),
        }

    published = await PublicationService(factory).publish(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        artifact_id=ids["first"],
    )

    assert published.manifest["assistant_persona"]["display_name"] == "Анна"
    runtime = render_runtime(published)
    assert '<iframe id="kaigo-generated-widget" title="Анна"' in runtime
    assert "AI-консультант" not in runtime
    assert "AI-консультант" not in render_loader()


@pytest.mark.asyncio
async def test_publish_version_creates_manifest_v2_and_enforces_compare_and_swap(
    publication_db,
) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)

    first = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["first_version"],
        expected_active_release_id=None,
        allowed_domains=["https://example.com"],
    )
    second = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["second_version"],
        expected_active_release_id=first.release_id,
        allowed_domains=["https://example.com"],
    )

    assert first.project_version_id == ids["first_version"]
    assert second.project_version_id == ids["second_version"]
    assert second.manifest["version"] == 2
    assert second.manifest["project_version_id"] == str(ids["second_version"])

    with pytest.raises(PublicationConflict):
        await service.publish_version(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            project_version_id=ids["first_version"],
            expected_active_release_id=first.release_id,
            allowed_domains=["https://other.example"],
        )

    unchanged = await service.get_project_state(
        ids["project"], actor_user_id=10, tenant_id=1
    )
    assert unchanged is not None
    assert unchanged.active_release.release_id == second.release_id
    assert unchanged.allowed_domains == ("https://example.com",)

    replay = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["second_version"],
        expected_active_release_id=first.release_id,
        allowed_domains=["https://example.com"],
    )
    assert replay.release_id == second.release_id
    assert replay.created is False

    with pytest.raises(PublicationConflict):
        await service.publish_version(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            project_version_id=ids["second_version"],
            expected_active_release_id=first.release_id,
            allowed_domains=["https://changed.example"],
        )


@pytest.mark.asyncio
async def test_publish_version_rejects_foreign_and_unpublishable_versions(
    publication_db,
) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)

    for version_id in (ids["foreign_version"], ids["draft_version"]):
        with pytest.raises(InvalidPublicationArtifact):
            await service.publish_version(
                ids["project"],
                actor_user_id=10,
                tenant_id=1,
                project_version_id=version_id,
                expected_active_release_id=None,
            )


@pytest.mark.asyncio
async def test_versioned_rollback_uses_compare_and_swap_and_replays_active_target(
    publication_db,
) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    first = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["first_version"],
        expected_active_release_id=None,
    )
    second = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["second_version"],
        expected_active_release_id=first.release_id,
    )

    rolled_back = await service.rollback(
        first.publication_id,
        actor_user_id=10,
        tenant_id=1,
        target_release_id=first.release_id,
        expected_active_release_id=second.release_id,
    )
    assert rolled_back.release_id == first.release_id

    replay = await service.rollback(
        first.publication_id,
        actor_user_id=10,
        tenant_id=1,
        target_release_id=first.release_id,
        expected_active_release_id=second.release_id,
    )
    assert replay.release_id == first.release_id

    with pytest.raises(PublicationConflict):
        await service.rollback(
            first.publication_id,
            actor_user_id=10,
            tenant_id=1,
            target_release_id=second.release_id,
            expected_active_release_id=second.release_id,
        )


@pytest.mark.asyncio
async def test_publication_state_returns_active_release_and_immutable_history(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)

    first = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["first"]
    )
    second = await service.publish(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        artifact_id=ids["same_revision_other_run"],
    )

    state = await service.get_project_state(
        ids["project"], actor_user_id=10, tenant_id=1
    )

    assert state is not None
    assert state.publication_id == first.publication_id
    assert state.active_release.release_id == second.release_id
    assert state.allowed_domains == ("https://example.com",)
    assert tuple(release.release_id for release in state.releases) == (
        first.release_id,
        second.release_id,
    )
    assert state.releases[1].previous_release_id == first.release_id
    assert all(release.created_at is not None for release in state.releases)

    assert await service.get_project_state(
        ids["foreign_project"], actor_user_id=11, tenant_id=1
    ) is None
    with pytest.raises(PublicationNotFound):
        await service.get_project_state(
            ids["project"], actor_user_id=11, tenant_id=1
        )

    foreign = await service.publish(
        ids["foreign_project"],
        actor_user_id=11,
        tenant_id=1,
        artifact_id=ids["foreign"],
    )
    async with factory() as database, database.begin():
        active = await database.get(PublicationRelease, second.release_id)
        active.previous_release_id = foreign.release_id
    with pytest.raises(ReleaseCorrupt):
        await service.get_project_state(
            ids["project"], actor_user_id=10, tenant_id=1
        )


@pytest.mark.asyncio
async def test_publish_rejects_drafts_foreign_artifacts_and_inconsistent_rows(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)

    with pytest.raises(InvalidPublicationArtifact):
        await service.publish(
            ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["draft"]
        )
    with pytest.raises(InvalidPublicationArtifact):
        await service.publish(
            ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["foreign"]
        )

    async with factory() as database, database.begin():
        row = await database.get(GenerationArtifact, ids["first"])
        row.html = "<main>does not match config</main>"
    with pytest.raises(InvalidPublicationArtifact):
        await service.publish(
            ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["first"]
        )


@pytest.mark.asyncio
async def test_service_revalidates_actor_identity_and_subscription_in_mutation_transaction(
    publication_db,
) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)

    with pytest.raises(PublicationNotFound):
        await service.publish(
            ids["project"],
            actor_user_id=11,
            tenant_id=1,
            artifact_id=ids["first"],
        )

    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(Subscription.user_id == 10)
        )
        subscription.status = "cancelled"
    with pytest.raises(PublicationUpgradeRequired):
        await service.publish(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            artifact_id=ids["first"],
        )

    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(Subscription.user_id == 10)
        )
        subscription.status = "active"
        identity = await database.scalar(
            select(UserIdentity).where(UserIdentity.user_id == 10)
        )
        identity.email_verified = False
    with pytest.raises(PublicationIdentityUnverified):
        await service.publish(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            artifact_id=ids["first"],
        )

    async with factory() as database:
        assert await database.scalar(select(func.count()).select_from(Publication)) == 0


@pytest.mark.asyncio
async def test_revision_compatibility_is_limited_to_active_run(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    result = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, revision=1
    )
    assert result.artifact_id == ids["first"]


@pytest.mark.asyncio
async def test_allowed_domains_are_exact_origins_and_empty_is_deny_all(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    defaulted = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["first"]
    )
    assert defaulted.allowed_domains == ("https://example.com",)

    denied = await service.publish(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        artifact_id=ids["first"],
        allowed_domains=[],
    )
    assert denied.allowed_domains == ()

    bad_origins = [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/",
        "https://example.com/path",
        "https://example.com/?query=1",
        "https://example.com/#fragment",
        "https://example.com; frame-src *",
        "https://localhost:3000",
        "https://127.1",
        "https://2130706433",
        "https://0177.0.0.1",
        "https://0x7f.0x0.0x0.0x1",
    ]
    for value in bad_origins:
        with pytest.raises(InvalidAllowedDomain):
            await service.publish(
                ids["project"],
                actor_user_id=10,
                tenant_id=1,
                artifact_id=ids["first"],
                allowed_domains=[value],
            )

    development = PublicationService(factory, allow_insecure_origins=True)
    local = await development.publish(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        artifact_id=ids["first"],
        allowed_domains=["http://localhost:3000"],
    )
    assert local.allowed_domains == ("http://localhost:3000",)


@pytest.mark.asyncio
async def test_rollback_is_atomic_and_resolve_fails_closed_on_corruption(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    first = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["first"]
    )
    second = await service.publish(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        artifact_id=ids["same_revision_other_run"],
    )

    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(Subscription.user_id == 10)
        )
        subscription.status = "cancelled"
    with pytest.raises(PublicationUpgradeRequired):
        await service.rollback(
            second.publication_id,
            actor_user_id=10,
            tenant_id=1,
            target_release_id=first.release_id,
        )
    assert (await service.resolve(first.stable_key)).release_id == second.release_id

    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(Subscription.user_id == 10)
        )
        subscription.status = "active"
    rolled_back = await service.rollback(
        second.publication_id,
        actor_user_id=10,
        tenant_id=1,
        target_release_id=first.release_id,
    )
    assert rolled_back.release_id == first.release_id
    assert (await service.resolve(first.stable_key)).release_id == first.release_id

    async with factory() as database, database.begin():
        release = await database.get(PublicationRelease, first.release_id)
        release.asset_manifest = {**release.asset_manifest, "artifact": {"body_html": "tampered"}}
    with pytest.raises(ReleaseCorrupt):
        await service.publish(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            artifact_id=ids["first"],
        )
    with pytest.raises(ReleaseCorrupt):
        await service.resolve(first.stable_key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid_version", "versioned"),
    [
        (True, False),
        (1.0, False),
        (2.0, True),
        ("1", False),
        ("", False),
        (None, False),
        (None, True),
    ],
)
async def test_resolve_rejects_non_integer_manifest_versions(
    publication_db,
    invalid_version,
    versioned,
) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    if versioned:
        published = await service.publish_version(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            project_version_id=ids["first_version"],
            expected_active_release_id=None,
        )
    else:
        published = await service.publish(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            artifact_id=ids["first"],
        )

    async with factory() as database, database.begin():
        release = await database.get(PublicationRelease, published.release_id)
        manifest = {**release.asset_manifest, "version": invalid_version}
        release.asset_manifest = manifest
        flag_modified(release, "asset_manifest")
        release.checksum = service.manifest_checksum(manifest)

    with pytest.raises(ReleaseCorrupt, match="release manifest is invalid"):
        await service.resolve(published.stable_key)


@pytest.mark.asyncio
async def test_rollback_rejects_release_from_another_publication(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    owner = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["first"]
    )
    foreign = await service.publish(
        ids["foreign_project"],
        actor_user_id=11,
        tenant_id=1,
        artifact_id=ids["foreign"],
    )
    with pytest.raises(PublicationNotFound):
        await service.rollback(
            owner.publication_id,
            actor_user_id=10,
            tenant_id=1,
            target_release_id=foreign.release_id,
        )


@pytest.mark.asyncio
async def test_resolve_rejects_disabled_or_corrupt_domain_policy(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    published = await service.publish(
        ids["project"], actor_user_id=10, tenant_id=1, artifact_id=ids["first"]
    )
    async with factory() as database, database.begin():
        publication = await database.get(Publication, published.publication_id)
        publication.state = "disabled"
    with pytest.raises(PublicationNotFound):
        await service.resolve(published.stable_key)

    async with factory() as database, database.begin():
        publication = await database.get(Publication, published.publication_id)
        publication.state = "published"
        publication.allowed_domains = ["https://example.com; frame-src *"]
    with pytest.raises(ReleaseCorrupt):
        await service.resolve(published.stable_key)


@pytest.mark.asyncio
async def test_versioned_publish_replay_rejects_disabled_publication(
    publication_db,
) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    published = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["first_version"],
        expected_active_release_id=None,
        allowed_domains=["https://example.com"],
    )
    async with factory() as database, database.begin():
        publication = await database.get(Publication, published.publication_id)
        publication.state = "disabled"

    with pytest.raises(PublicationConflict):
        await service.publish_version(
            ids["project"],
            actor_user_id=10,
            tenant_id=1,
            project_version_id=ids["first_version"],
            expected_active_release_id=None,
            allowed_domains=["https://example.com"],
        )
    async with factory() as database:
        publication = await database.get(Publication, published.publication_id)
        assert publication.state == "disabled"


@pytest.mark.asyncio
async def test_rollback_replay_rejects_disabled_publication(publication_db) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    first = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["first_version"],
        expected_active_release_id=None,
    )
    second = await service.publish_version(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        project_version_id=ids["second_version"],
        expected_active_release_id=first.release_id,
    )
    await service.rollback(
        first.publication_id,
        actor_user_id=10,
        tenant_id=1,
        target_release_id=first.release_id,
        expected_active_release_id=second.release_id,
    )
    async with factory() as database, database.begin():
        publication = await database.get(Publication, first.publication_id)
        publication.state = "disabled"

    with pytest.raises(PublicationConflict):
        await service.rollback(
            first.publication_id,
            actor_user_id=10,
            tenant_id=1,
            target_release_id=first.release_id,
            expected_active_release_id=second.release_id,
        )
    async with factory() as database:
        publication = await database.get(Publication, first.publication_id)
        assert publication.state == "disabled"


async def _publication_app(tmp_path, *, config=None, chat_service=None):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(factory)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    if config is not None:
        app["config"] = config
    if chat_service is not None:
        app[CHAT_SERVICE_KEY] = chat_service
    setup_session(app, SimpleCookieStorage(cookie_name="kaigo_publication_test"))

    async def login(request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        session = await get_session(request)
        session["user_id"] = user_id
        session["tenant_id"] = 1
        session["csrf_token"] = "csrf"
        return web.json_response({"csrf_token": "csrf"})

    async def host(request: web.Request) -> web.Response:
        key = request.match_info["stable_key"]
        return web.Response(
            text=f"""<!doctype html><html><body>
            <button id="outside" style="position:fixed;right:130px;bottom:24px">Outside</button>
            <output id="outside-count">0</output>
            <script>document.getElementById('outside').onclick=()=>{{
              const out=document.getElementById('outside-count');
              out.textContent=String(Number(out.textContent)+1);
            }};</script>
            <script src="/embed/{key}.js"></script>
            <script src="/embed/{key}.js"></script>
            </body></html>""",
            content_type="text/html",
        )

    app.router.add_post("/test/login/{user_id}", login)
    app.router.add_get("/test/host/{stable_key}", host)
    setup_publication_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, factory, client, ids


@pytest.mark.asyncio
async def test_publish_route_requires_auth_csrf_owner_verified_and_subscription(tmp_path) -> None:
    config = SimpleNamespace(
        public_auth_enabled=True,
        public_base_url="https://kaigo.example/",
        environment="production",
        publication_allow_insecure_origins=False,
    )
    engine, factory, client, ids = await _publication_app(tmp_path, config=config)
    body = {"artifact_id": str(ids["first"]), "allowed_domains": ["https://example.com"]}
    try:
        assert (await client.post(f"/api/projects/{ids['project']}/publish", json=body)).status == 401
        await client.post("/test/login/11")
        assert (
            await client.post(
                f"/api/projects/{ids['project']}/publish",
                json=body,
                headers={"X-CSRF-Token": "csrf"},
            )
        ).status == 404
        await client.post("/test/login/10")
        assert (await client.post(f"/api/projects/{ids['project']}/publish", json=body)).status == 403

        async with factory() as database, database.begin():
            subscription = await database.scalar(
                select(Subscription).where(Subscription.user_id == 10)
            )
            subscription.status = "cancelled"
        upgrade = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json=body,
            headers={"X-CSRF-Token": "csrf"},
        )
        assert upgrade.status == 402
        assert (await upgrade.json())["error"]["code"] == "upgrade_required"

        async with factory() as database, database.begin():
            subscription = await database.scalar(
                select(Subscription).where(Subscription.user_id == 10)
            )
            subscription.status = "active"
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json=body,
            headers={"X-CSRF-Token": "csrf"},
        )
        assert published.status == 201
        payload = await published.json()
        assert payload["project_version_id"] is None
        assert payload["embed_url"] == (
            f"https://kaigo.example/embed/{payload['stable_key']}.js"
        )
        assert payload["runtime_url"] == (
            f"https://kaigo.example/runtime/{payload['stable_key']}"
        )

        duplicate = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json=body,
            headers={"X-CSRF-Token": "csrf", "Host": "attacker.invalid"},
        )
        assert duplicate.status == 200
        duplicate_payload = await duplicate.json()
        assert duplicate_payload["release_id"] == payload["release_id"]
        assert duplicate_payload["embed_url"].startswith("https://kaigo.example/")
        assert "attacker.invalid" not in duplicate_payload["embed_url"]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_publication_state_route_is_verified_owner_scoped_and_needs_no_csrf(tmp_path) -> None:
    config = SimpleNamespace(
        public_auth_enabled=True,
        public_base_url="https://kaigo.example/",
        environment="production",
        publication_allow_insecure_origins=False,
    )
    engine, factory, client, ids = await _publication_app(tmp_path, config=config)
    try:
        route = f"/api/projects/{ids['project']}/publication"
        assert (await client.get(route)).status == 401

        await client.post("/test/login/11")
        assert (await client.get(route)).status == 404

        await client.post("/test/login/10")
        async with factory() as database, database.begin():
            identity = await database.scalar(
                select(UserIdentity).where(UserIdentity.user_id == 10)
            )
            identity.email_verified = False
        assert (await client.get(route)).status == 403

        async with factory() as database, database.begin():
            identity = await database.scalar(
                select(UserIdentity).where(UserIdentity.user_id == 10)
            )
            identity.email_verified = True
        empty = await client.get(route)
        assert empty.status == 200
        assert await empty.json() == {"publication": None}

        first = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={"artifact_id": str(ids["first"])},
            headers={"X-CSRF-Token": "csrf"},
        )
        second = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={"artifact_id": str(ids["same_revision_other_run"])},
            headers={"X-CSRF-Token": "csrf"},
        )
        first_payload = await first.json()
        second_payload = await second.json()

        restored = await client.get(route)
        assert restored.status == 200
        payload = (await restored.json())["publication"]
        assert payload["publication_id"] == first_payload["publication_id"]
        assert payload["active_release"]["release_id"] == second_payload["release_id"]
        assert [release["release_id"] for release in payload["releases"]] == [
            first_payload["release_id"],
            second_payload["release_id"],
        ]
        assert payload["embed_url"] == (
            f"https://kaigo.example/embed/{payload['stable_key']}.js"
        )
        assert all("manifest" not in release for release in payload["releases"])
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_publish_fails_before_mutation_without_authoritative_base_url(
    tmp_path,
) -> None:
    config = SimpleNamespace(
        public_auth_enabled=True,
        public_base_url=None,
        environment="PrOdUcTiOn",
        publication_allow_insecure_origins=True,
    )
    engine, factory, client, ids = await _publication_app(tmp_path, config=config)
    try:
        await client.post("/test/login/10")
        response = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={"artifact_id": str(ids["first"])},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert response.status == 503
        assert (await response.json())["error"]["code"] == "public_base_url_unavailable"
        async with factory() as database:
            assert await database.scalar(select(func.count()).select_from(Publication)) == 0
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_environment_case_cannot_enable_insecure_publication_origins(tmp_path) -> None:
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url="https://kaigo.example",
        environment="PrOdUcTiOn",
        publication_allow_insecure_origins=True,
    )
    engine, _factory, client, ids = await _publication_app(tmp_path, config=config)
    try:
        await client.post("/test/login/10")
        response = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "artifact_id": str(ids["first"]),
                "allowed_domains": ["http://localhost:3000"],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert response.status == 422
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_loader_and_runtime_are_secure_stable_and_network_free(tmp_path) -> None:
    engine, _factory, client, ids = await _publication_app(tmp_path)
    try:
        await client.post("/test/login/10")
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={"artifact_id": str(ids["first"]), "allowed_domains": ["https://example.com"]},
            headers={"X-CSRF-Token": "csrf"},
        )
        published_payload = await published.json()
        key = published_payload["stable_key"]

        loader = await client.get(f"/embed/{key}.js")
        loader_text = await loader.text()
        assert loader.status == 200
        assert "document.currentScript" in loader_text
        assert "document.write" not in loader_text
        assert "__kaigoWidgetRegistryV1" in loader_text
        assert 'sandbox="allow-scripts"' in loader_text or "sandbox', 'allow-scripts'" in loader_text
        assert "strict-origin" in loader_text
        assert "event.source" in loader_text
        assert "Math.min" in loader_text and "Math.max" in loader_text
        assert "pointer-events:none" in loader_text
        assert "kaigo:geometry" in loader_text
        assert loader.headers["Cache-Control"] == "public, max-age=300"
        assert loader.headers["X-Content-Type-Options"] == "nosniff"

        runtime = await client.get(f"/runtime/{key}")
        runtime_text = await runtime.text()
        assert runtime.status == 200
        assert "frame.srcdoc=" in runtime_text
        assert "URL.createObjectURL" not in runtime_text
        assert "new Blob" not in runtime_text
        assert 'sandbox="allow-scripts"' in runtime_text
        assert "default-src 'none'" in runtime.headers["Content-Security-Policy"]
        assert "connect-src 'self'" in runtime.headers["Content-Security-Policy"]
        assert "frame-src 'none'" in runtime.headers["Content-Security-Policy"]
        assert "frame-ancestors https://example.com" in runtime.headers["Content-Security-Policy"]
        assert "X-Frame-Options" not in runtime.headers
        assert runtime.headers["Cache-Control"] == "no-store"
        assert "kaigo-widget" in runtime_text
        assert (
            f'data-kaigo-release-id="{published_payload["release_id"]}"'
            in runtime_text
        )
        assert 'data-kaigo-project-version-id=""' in runtime_text
        assert "kaigo:inner-geometry" in runtime_text
        assert "chat.request" in runtime_text
        assert "'/runtime/'" in runtime_text and "+'/chat'" in runtime_text

        unknown_loader = await client.get("/embed/unknown.js")
        unknown_runtime = await client.get("/runtime/unknown")
        assert unknown_loader.status == unknown_runtime.status == 404
        assert await unknown_loader.text() == await unknown_runtime.text()
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_published_runtime_keeps_fixed_widget_interactions_and_closed_hitbox(
    tmp_path,
) -> None:
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url=None,
        environment="development",
        publication_allow_insecure_origins=True,
    )
    chat_service = FakePublicChatService()
    engine, factory, client, ids = await _publication_app(
        tmp_path,
        config=config,
        chat_service=chat_service,
    )
    try:
        interactive = artifact(
            revision=1,
            body_html=artifact(revision=1).body_html.replace(
                '<header class="kaigo-widget__header" data-region="header">',
                '<header class="kaigo-widget__header" data-region="header">'
                '<button type="button" data-action="close">Close</button>',
            ),
            css=artifact(revision=1).css
            + """
.kaigo-widget { position: fixed; right: 0; bottom: 0; }
.kaigo-widget [data-region="launcher"] { width: 64px; height: 64px; }
.kaigo-widget [data-region="panel"] { width: 360px; height: 520px; background: white; }
.kaigo-widget [data-region="panel"][aria-hidden="true"] { display: none; }
""",
        )
        async with factory() as database, database.begin():
            row = await database.get(GenerationArtifact, ids["first"])
            row.html = interactive.body_html
            row.css = interactive.css
            row.javascript = interactive.javascript
            row.config = {"artifact": interactive.to_dict()}

        await client.post("/test/login/10")
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "artifact_id": str(ids["first"]),
                "allowed_domains": [str(client.make_url("/")).rstrip("/")],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert published.status == 201
        key = (await published.json())["stable_key"]

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                page.set_default_timeout(15_000)
                response = await page.goto(
                    str(client.make_url(f"/test/host/{key}")),
                    # The product contract is asserted by the explicit widget,
                    # iframe, geometry, and chat waits below. Waiting for the host
                    # page DOMContentLoaded would also serialize the nested embed
                    # bootstrap and becomes flaky after the full browser suite.
                    wait_until="commit",
                    timeout=60_000,
                )
                assert response is not None and response.ok
                widget = page.locator(f'iframe[data-kaigo-widget-key="{key}"]')
                await widget.wait_for()
                await page.wait_for_function(
                    "key => { const f=document.querySelector(`iframe[data-kaigo-widget-key=\"${key}\"]`); return f && parseInt(f.style.width,10) <= 80 && parseInt(f.style.height,10) <= 80; }",
                    arg=key,
                    timeout=5_000,
                )
                assert await page.locator(f'iframe[data-kaigo-widget-key="{key}"]').count() == 1
                await page.locator("#outside").click()
                assert await page.locator("#outside-count").text_content() == "1"

                runtime_frame = page.frames[1]
                generated_frame = runtime_frame.child_frames[0]
                await generated_frame.locator('[data-region="launcher"]').click()
                await generated_frame.locator('[data-region="panel"]').wait_for(state="visible")
                await page.wait_for_function(
                    "key => { const f=document.querySelector(`iframe[data-kaigo-widget-key=\"${key}\"]`); return f && parseInt(f.style.width,10) >= 350 && parseInt(f.style.height,10) >= 500; }",
                    arg=key,
                    timeout=5_000,
                )
                await generated_frame.locator('[data-region="suggestions"] button').click()
                await generated_frame.locator('[data-kaigo-runtime-message="user"]').wait_for()
                assistant = generated_frame.locator(
                    '[data-kaigo-runtime-message="assistant"] [data-kaigo-runtime-content]'
                )
                await assistant.wait_for()
                assert await assistant.text_content() == "Публичный проверенный ответ"
                composer = generated_frame.locator('[data-kaigo-runtime-input="true"]')
                await composer.fill("Второй вопрос")
                await composer.press("Enter")
                await generated_frame.locator(
                    '[data-kaigo-runtime-message="assistant"]'
                ).nth(1).wait_for()
                await generated_frame.locator('[data-action="close"]').click()
                await page.wait_for_function(
                    "key => { const f=document.querySelector(`iframe[data-kaigo-widget-key=\"${key}\"]`); return f && parseInt(f.style.width,10) <= 80 && parseInt(f.style.height,10) <= 80; }",
                    arg=key,
                    timeout=5_000,
                )
            finally:
                await browser.close()
        assert len(chat_service.calls) == 2
        call = chat_service.calls[0]
        second_call = chat_service.calls[1]
        assert call["scope"].startswith("publication:")
        assert ":release:" in call["scope"] and ":domain:" in call["scope"]
        assert call["client_id"].startswith("publication:")
        assert call["scope"] == second_call["scope"]
        assert call["session_id"] == second_call["session_id"]
        assert call["request_id"] != second_call["request_id"]
        assert call["context"].brief == "Private prompt must never be published"
        assert call["context"].art_direction == interactive.art_direction
        assert call["run_id"]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("viewport", "bootstrap", "minimum_open"),
    (
        pytest.param(
            {"width": 1280, "height": 800},
            {"width": 420, "height": 640},
            {"width": 350, "height": 500},
            id="desktop",
        ),
        pytest.param(
            {"width": 320, "height": 480},
            {"width": 288, "height": 448},
            {"width": 260, "height": 420},
            id="mobile",
        ),
    ),
)
async def test_public_runtime_bootstraps_responsive_panel_before_settled_measurement(
    tmp_path,
    viewport: dict[str, int],
    bootstrap: dict[str, int],
    minimum_open: dict[str, int],
) -> None:
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url=None,
        environment="development",
        publication_allow_insecure_origins=True,
    )
    engine, factory, client, ids = await _publication_app(tmp_path, config=config)
    try:
        responsive = artifact(
            revision=1,
            body_html=artifact(revision=1).body_html.replace(
                '<header class="kaigo-widget__header" data-region="header">',
                '<header class="kaigo-widget__header" data-region="header">'
                '<button type="button" data-action="close">Close</button>',
            ),
            css=artifact(revision=1).css
            + """
.kaigo-widget { position: fixed; right: 0; bottom: 0; width: 100%; height: 100%; }
.kaigo-widget [data-region="launcher"] { width: 64px; height: 60px; }
.kaigo-widget [data-region="panel"] {
  width: min(360px, calc(100vw - 16px));
  height: min(520px, calc(100vh - 16px));
  background: white;
}
.kaigo-widget [data-region="panel"][aria-hidden="true"] { display: none; }
""",
        )
        async with factory() as database, database.begin():
            row = await database.get(GenerationArtifact, ids["first"])
            row.html = responsive.body_html
            row.css = responsive.css
            row.javascript = responsive.javascript
            row.config = {"artifact": responsive.to_dict()}

        await client.post("/test/login/10")
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "artifact_id": str(ids["first"]),
                "allowed_domains": [str(client.make_url("/")).rstrip("/")],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert published.status == 201
        key = (await published.json())["stable_key"]

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport=viewport)
                await page.add_init_script(
                    """(() => {
                      if (!location.pathname.startsWith('/runtime/')) return;
                      const nativeRequestAnimationFrame = requestAnimationFrame.bind(window);
                      window.requestAnimationFrame = callback => nativeRequestAnimationFrame(
                        timestamp => setTimeout(() => callback(timestamp), 75)
                      );
                    })();"""
                )
                page.set_default_timeout(15_000)
                response = await page.goto(
                    str(client.make_url(f"/test/host/{key}")),
                    wait_until="commit",
                    timeout=60_000,
                )
                assert response is not None and response.ok
                await page.wait_for_function(
                    'key => { const f=document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`); return f && parseInt(f.style.width,10) <= 80 && parseInt(f.style.height,10) <= 80; }',
                    arg=key,
                    timeout=5_000,
                )
                await page.evaluate(
                    """key => {
                      const frame = document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`);
                      window.__kaigoGeometryHistory = [];
                      const record = () => window.__kaigoGeometryHistory.push({
                        width: parseInt(frame.style.width, 10),
                        height: parseInt(frame.style.height, 10)
                      });
                      new MutationObserver(record).observe(frame, {
                        attributes: true,
                        attributeFilter: ['style']
                      });
                      record();
                    }""",
                    key,
                )

                runtime_frame = page.frames[1]
                generated_frame = runtime_frame.child_frames[0]
                await generated_frame.locator('[data-region="launcher"]').click()
                await generated_frame.locator('[data-region="panel"]').wait_for(
                    state="visible"
                )
                await page.wait_for_function(
                    """({key, bootstrap}) => {
                      const frame = document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`);
                      return frame
                        && parseInt(frame.style.width, 10) === bootstrap.width
                        && parseInt(frame.style.height, 10) === bootstrap.height;
                    }""",
                    arg={"key": key, "bootstrap": bootstrap},
                    timeout=5_000,
                )
                await generated_frame.locator('[data-action="close"]').click()
                await page.wait_for_function(
                    'key => { const f=document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`); return f && parseInt(f.style.width,10) <= 80 && parseInt(f.style.height,10) <= 80; }',
                    arg=key,
                    timeout=5_000,
                )
                await generated_frame.locator('[data-region="launcher"]').click()
                await generated_frame.locator('[data-region="panel"]').wait_for(
                    state="visible"
                )
                try:
                    await page.wait_for_function(
                        """({key, minimum}) => {
                          const frame = document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`);
                          return frame
                            && parseInt(frame.style.width, 10) >= minimum.width
                            && parseInt(frame.style.height, 10) >= minimum.height;
                        }""",
                        arg={"key": key, "minimum": minimum_open},
                        timeout=5_000,
                    )
                except PlaywrightTimeoutError as error:
                    diagnostics = await page.evaluate(
                        """key => {
                          const frame = document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`);
                          return {
                            current: {
                              width: parseInt(frame.style.width, 10),
                              height: parseInt(frame.style.height, 10)
                            },
                            history: window.__kaigoGeometryHistory
                          };
                        }""",
                        key,
                    )
                    diagnostics["inner"] = await generated_frame.evaluate(
                        """() => {
                          const panel = document.querySelector('[data-region="panel"]');
                          const rect = panel.getBoundingClientRect();
                          const style = getComputedStyle(panel);
                          return {
                            viewport: {width: innerWidth, height: innerHeight},
                            rect: {width: rect.width, height: rect.height},
                            computed: {width: style.width, height: style.height}
                          };
                        }"""
                    )
                    raise AssertionError(
                        f"responsive panel stayed clipped: {diagnostics}"
                    ) from error
                await page.wait_for_timeout(1_500)
                settled_size = await page.evaluate(
                    """key => {
                      const frame = document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`);
                      return {
                        width: parseInt(frame.style.width, 10),
                        height: parseInt(frame.style.height, 10)
                      };
                    }""",
                    key,
                )
                await page.wait_for_timeout(250)
                stable_size = await page.evaluate(
                    """key => {
                      const frame = document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`);
                      return {
                        width: parseInt(frame.style.width, 10),
                        height: parseInt(frame.style.height, 10)
                      };
                    }""",
                    key,
                )
                assert settled_size == stable_size
                assert settled_size["width"] >= minimum_open["width"]
                assert settled_size["height"] >= minimum_open["height"]
                assert settled_size["width"] <= viewport["width"] - 32
                assert settled_size["height"] <= viewport["height"] - 32
                history = await page.evaluate("window.__kaigoGeometryHistory")
                assert any(
                    item["width"] == bootstrap["width"]
                    and item["height"] == bootstrap["height"]
                    for item in history
                )

                await generated_frame.locator('[data-action="close"]').click()
                await page.wait_for_function(
                    'key => { const f=document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`); return f && parseInt(f.style.width,10) <= 80 && parseInt(f.style.height,10) <= 80; }',
                    arg=key,
                    timeout=5_000,
                )

                await generated_frame.locator('[data-region="launcher"]').click()
                await generated_frame.locator('[data-region="panel"]').wait_for(
                    state="visible"
                )
                await page.wait_for_timeout(1_500)
                reopened_size = await page.evaluate(
                    """key => {
                      const frame = document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`);
                      return {
                        width: parseInt(frame.style.width, 10),
                        height: parseInt(frame.style.height, 10)
                      };
                    }""",
                    key,
                )
                await page.wait_for_timeout(250)
                reopened_stable_size = await page.evaluate(
                    """key => {
                      const frame = document.querySelector(`iframe[data-kaigo-widget-key="${key}"]`);
                      return {
                        width: parseInt(frame.style.width, 10),
                        height: parseInt(frame.style.height, 10)
                      };
                    }""",
                    key,
                )
                assert reopened_size == reopened_stable_size
                assert reopened_size["width"] >= minimum_open["width"]
                assert reopened_size["height"] >= minimum_open["height"]
                assert reopened_size["width"] <= viewport["width"] - 32
                assert reopened_size["height"] <= viewport["height"] - 32
                history = await page.evaluate("window.__kaigoGeometryHistory")
                assert (
                    sum(
                        item["width"] == bootstrap["width"]
                        and item["height"] == bootstrap["height"]
                        for item in history
                    )
                    >= 3
                )
            finally:
                await browser.close()
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_request_payload",
    (
        pytest.param(
            {"reference_context": ["not", "trusted", "text"]},
            id="non-text",
        ),
        pytest.param({"reference_context": "x" * 8_001}, id="oversize"),
        pytest.param(
            {"reference_context": '{"public_facts":["invalid builder"]}'},
            id="invalid-builder",
        ),
        pytest.param(
            _legacy_builder_request_without_reference_context(),
            id="valid_legacy_missing_reference_context",
        ),
    ),
)
async def test_public_chat_is_bound_to_active_release_and_approved_embed_origin(
    tmp_path,
    legacy_request_payload,
) -> None:
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url=None,
        environment="development",
        publication_allow_insecure_origins=True,
    )
    chat_service = FakePublicChatService()
    engine, factory, client, ids = await _publication_app(
        tmp_path,
        config=config,
        chat_service=chat_service,
    )
    host_origin = str(client.make_url("/")).rstrip("/")
    try:
        async with factory() as database, database.begin():
            published_artifact = await database.get(GenerationArtifact, ids["first"])
            assert published_artifact is not None
            published_artifact.config = {
                **published_artifact.config,
                "assistant_persona": _assistant_persona().to_dict(),
            }
            database.add(
                GenerationEvent(
                    id=702,
                    run_id=published_artifact.run_id,
                    sequence=1,
                    event_type="run.created",
                    public_message="Legacy generation queued",
                    payload={
                        "status": "queued",
                        "request": legacy_request_payload,
                    },
                )
            )
        await client.post("/test/login/10")
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "artifact_id": str(ids["first"]),
                "allowed_domains": [host_origin],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        payload = await published.json()
        key = payload["stable_key"]
        runtime = await client.get(
            f"/runtime/{key}",
            headers={"Referer": f"{host_origin}/embedded-page"},
        )
        capability = _runtime_capability(await runtime.text())
        request_body = {
            "request_id": "public-request-1234",
            "message": "Что вы предлагаете?",
            "revision": payload["revision"],
            "session_id": "public-session-1234",
            "host_origin": host_origin,
            "capability": capability,
        }
        response = await client.post(
            f"/runtime/{key}/chat",
            data=json.dumps(request_body),
            headers={
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "null",
                "Referer": str(client.make_url(f"/runtime/{key}")),
            },
        )
        assert response.status == 200
        assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert await response.json() == {
            "request_id": "public-request-1234",
            "reply": "Публичный проверенный ответ",
        }

        denied = await client.post(
            f"/runtime/{key}/chat",
            data=json.dumps({**request_body, "host_origin": "https://attacker.invalid"}),
            headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": "null"},
        )
        assert denied.status == 403

        stale = await client.post(
            f"/runtime/{key}/chat",
            data=json.dumps({**request_body, "revision": payload["revision"] + 1}),
            headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": "null"},
        )
        assert stale.status == 409
        assert len(chat_service.calls) == 1
        assert chat_service.calls[0]["context"].reference_context == ""
        assert chat_service.calls[0]["context"].assistant_persona == _assistant_persona()

        async with factory() as database, database.begin():
            malformed_artifact = await database.get(
                GenerationArtifact,
                ids["first"],
            )
            assert malformed_artifact is not None
            malformed_artifact.config = {
                **malformed_artifact.config,
                "assistant_persona": {"schema_version": "broken"},
            }
        malformed = await client.post(
            f"/runtime/{key}/chat",
            data=json.dumps(
                {
                    **request_body,
                    "request_id": "public-request-malformed-1",
                }
            ),
            headers={
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "null",
                "Referer": str(client.make_url(f"/runtime/{key}")),
            },
        )
        assert malformed.status == 409
        assert (await malformed.json())["error"]["code"] == "chat_not_ready"
        assert len(chat_service.calls) == 1
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_chat_rejects_missing_forged_or_expired_runtime_capability(
    tmp_path,
    monkeypatch,
) -> None:
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url=None,
        environment="development",
        publication_allow_insecure_origins=True,
    )
    chat_service = FakePublicChatService()
    engine, _factory, client, ids = await _publication_app(
        tmp_path,
        config=config,
        chat_service=chat_service,
    )
    host_origin = str(client.make_url("/")).rstrip("/")
    try:
        await client.post("/test/login/10")
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "artifact_id": str(ids["first"]),
                "allowed_domains": [host_origin],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        payload = await published.json()
        body = {
            "request_id": "public-abuse-1234",
            "message": "Consume model budget",
            "revision": payload["revision"],
            "session_id": "public-abuse-session",
            "host_origin": host_origin,
        }
        missing = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps(body),
            headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": "null"},
        )
        forged = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps({**body, "capability": "a.x"}),
            headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": "null"},
        )
        runtime = await client.get(
            f"/runtime/{payload['stable_key']}",
            headers={"Referer": f"{host_origin}/embedded-page"},
        )
        capability = _runtime_capability(await runtime.text())
        now = publication_routes.unix_time()
        monkeypatch.setattr(publication_routes, "unix_time", lambda: now + 300)
        expired = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps({**body, "capability": capability}),
            headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": "null"},
        )

        assert missing.status == 400
        assert forged.status == 403
        assert expired.status == 403
        assert len(chat_service.calls) == 0
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_route_preserves_ownerless_idempotency_and_session_rate_limit(
    tmp_path,
) -> None:
    public_fact_marker = "PUBLICATION-PUBLIC-FACT-a3c529"
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url=None,
        environment="development",
        publication_allow_insecure_origins=True,
    )
    engine, factory, client, ids = await _publication_app(tmp_path, config=config)
    provider = FakePublicChatProvider()
    router = ModelRouter(
        providers={"fake": provider},
        policies={
            ("chat_visitor", "express"): ModelPolicy(
                prompt_version="public-chat-v1",
                targets=(
                    ProviderTarget(
                        provider="fake",
                        model="fake-public-chat-model",
                        input_price_microusd_per_million=2_000_000,
                        output_price_microusd_per_million=4_000_000,
                    ),
                ),
            )
        },
        audit=SqlModelCallAudit(factory),
    )
    service = RoutedChatService(
        router=router,
        timeout_seconds=5,
        rate_limit_requests=1,
        client_rate_limit_requests=5,
        rate_limit_window_seconds=60,
        max_requests_per_session=4,
        max_sessions=4,
        session_ttl_seconds=60,
        global_concurrency=1,
    )
    client.server.app[CHAT_SERVICE_KEY] = service
    host_origin = str(client.make_url("/")).rstrip("/")
    try:
        async with factory() as database, database.begin():
            published_artifact = await database.get(GenerationArtifact, ids["first"])
            assert published_artifact is not None
            database.add(
                GenerationEvent(
                    id=701,
                    run_id=published_artifact.run_id,
                    sequence=1,
                    event_type="run.created",
                    public_message="Generation queued",
                    payload={
                        "status": "queued",
                        "request": BuilderRequest(
                            engine=EngineName.DIRECT,
                            brief="Private prompt must never be published",
                            reference_context=json.dumps(
                                {"public_facts": [public_fact_marker]}
                            ),
                            source_url="https://example.com/",
                        ).to_dict(),
                    },
                )
            )
        await client.post("/test/login/10")
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "artifact_id": str(ids["first"]),
                "allowed_domains": [host_origin],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        payload = await published.json()
        runtime = await client.get(
            f"/runtime/{payload['stable_key']}",
            headers={"Referer": f"{host_origin}/embedded-page"},
        )
        capability = _runtime_capability(await runtime.text())
        body = {
            "request_id": "ownerless-request-1234",
            "message": "Первый вопрос",
            "revision": payload["revision"],
            "session_id": "ownerless-session-1234",
            "host_origin": host_origin,
            "capability": capability,
        }
        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "null",
            "X-Forwarded-For": "198.51.100.10",
        }

        first = await client.post(
            f"/runtime/{payload['stable_key']}/chat", data=json.dumps(body), headers=headers
        )
        duplicate = await client.post(
            f"/runtime/{payload['stable_key']}/chat", data=json.dumps(body), headers=headers
        )
        limited = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps(
                {
                    **body,
                    "request_id": "ownerless-request-5678",
                    "message": "Второй вопрос",
                }
            ),
            headers={**headers, "X-Forwarded-For": "198.51.100.11"},
        )

        assert first.status == duplicate.status == 200
        assert await first.json() == await duplicate.json()
        assert limited.status == 429
        assert (await limited.json())["error"]["code"] == "chat_rate_limited"
        assert len(provider.requests) == 1
        assert "Private prompt must never be published" in provider.requests[0].prompt
        assert public_fact_marker in provider.requests[0].prompt
    finally:
        await service.close()
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_chat_only_uses_forwarded_ip_from_a_trusted_proxy(tmp_path) -> None:
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url=None,
        environment="development",
        publication_allow_insecure_origins=True,
        publication_chat_key_rate_limit_requests=10,
        publication_chat_ip_rate_limit_requests=1,
        publication_chat_trusted_proxy_cidrs=("127.0.0.0/8",),
        chat_rate_limit_window_seconds=60,
    )
    chat_service = FakePublicChatService()
    engine, _factory, client, ids = await _publication_app(
        tmp_path,
        config=config,
        chat_service=chat_service,
    )
    host_origin = str(client.make_url("/")).rstrip("/")
    try:
        await client.post("/test/login/10")
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "artifact_id": str(ids["first"]),
                "allowed_domains": [host_origin],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        payload = await published.json()
        runtime = await client.get(
            f"/runtime/{payload['stable_key']}",
            headers={"Referer": f"{host_origin}/embedded-page"},
        )
        capability = _runtime_capability(await runtime.text())
        body = {
            "request_id": "trusted-proxy-request-1",
            "message": "Первый вопрос",
            "revision": payload["revision"],
            "session_id": "trusted-proxy-session-1",
            "host_origin": host_origin,
            "capability": capability,
        }
        base_headers = {"Content-Type": "text/plain;charset=UTF-8", "Origin": "null"}

        first = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps(body),
            headers={**base_headers, "X-Forwarded-For": "198.51.100.10, 127.0.0.2"},
        )
        second = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps(
                {
                    **body,
                    "request_id": "trusted-proxy-request-2",
                    "session_id": "trusted-proxy-session-2",
                }
            ),
            headers={**base_headers, "X-Forwarded-For": "198.51.100.11, 127.0.0.2"},
        )
        repeated_ip = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps(
                {
                    **body,
                    "request_id": "trusted-proxy-request-3",
                    "session_id": "trusted-proxy-session-3",
                }
            ),
            headers={**base_headers, "X-Forwarded-For": "198.51.100.10, 127.0.0.2"},
        )

        assert first.status == second.status == 200
        assert repeated_ip.status == 429
        assert (await repeated_ip.json())["error"]["code"] == "chat_ip_rate_limited"
        assert len(chat_service.calls) == 2
        assert chat_service.calls[0]["client_id"] != chat_service.calls[1]["client_id"]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key_limit", "ip_limit", "expected_code"),
    [
        (1, 10, "chat_publication_rate_limited"),
        (10, 1, "chat_ip_rate_limited"),
    ],
)
async def test_public_chat_has_independent_publication_and_ip_caps(
    tmp_path,
    key_limit: int,
    ip_limit: int,
    expected_code: str,
) -> None:
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url=None,
        environment="development",
        publication_allow_insecure_origins=True,
        publication_chat_key_rate_limit_requests=key_limit,
        publication_chat_ip_rate_limit_requests=ip_limit,
        chat_rate_limit_window_seconds=60,
    )
    chat_service = FakePublicChatService()
    engine, _factory, client, ids = await _publication_app(
        tmp_path,
        config=config,
        chat_service=chat_service,
    )
    host_origin = str(client.make_url("/")).rstrip("/")
    try:
        await client.post("/test/login/10")
        published = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "artifact_id": str(ids["first"]),
                "allowed_domains": [host_origin],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        payload = await published.json()
        runtime = await client.get(
            f"/runtime/{payload['stable_key']}",
            headers={"Referer": f"{host_origin}/embedded-page"},
        )
        capability = _runtime_capability(await runtime.text())
        body = {
            "request_id": "public-limit-request-1",
            "message": "Первый вопрос",
            "revision": payload["revision"],
            "session_id": "public-limit-session-1",
            "host_origin": host_origin,
            "capability": capability,
        }
        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "null",
            "X-Forwarded-For": "198.51.100.10",
        }
        invalid_id = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps({**body, "request_id": "x" * 1_000}),
            headers=headers,
        )
        oversized_body = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps({**body, "message": "x" * 9_000}),
            headers=headers,
        )
        first = await client.post(
            f"/runtime/{payload['stable_key']}/chat", data=json.dumps(body), headers=headers
        )
        second = await client.post(
            f"/runtime/{payload['stable_key']}/chat",
            data=json.dumps(
                {
                    **body,
                    "request_id": "public-limit-request-2",
                    "session_id": "public-limit-session-2",
                }
            ),
            headers={**headers, "X-Forwarded-For": "198.51.100.11"},
        )

        assert invalid_id.status == oversized_body.status == 400
        assert (await invalid_id.json())["error"]["request_id"] is None
        assert (await oversized_body.json())["error"]["request_id"] is None
        assert first.status == 200
        assert second.status == 429
        assert (await second.json())["error"]["code"] == expected_code
        assert len(chat_service.calls) == 1
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_generated_javascript_cannot_replace_or_navigate_trusted_runtime(tmp_path) -> None:
    engine, factory, client, ids = await _publication_app(tmp_path)
    malicious_url = "https://evil.example/phish"
    try:
        malicious = artifact(
            revision=1,
            javascript=(
                "document.body.replaceChildren();"
                f"location.replace({malicious_url!r})"
            ),
        )
        async with factory() as database, database.begin():
            row = await database.get(GenerationArtifact, ids["first"])
            row.html = malicious.body_html
            row.css = malicious.css
            row.javascript = malicious.javascript
            row.config = {"artifact": malicious.to_dict()}

        await client.post("/test/login/10")
        response = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={"artifact_id": str(ids["first"])},
            headers={"X-CSRF-Token": "csrf"},
        )
        key = (await response.json())["stable_key"]
        runtime_url = str(client.make_url(f"/runtime/{key}"))

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                requested_urls: list[str] = []
                page.on("request", lambda request: requested_urls.append(request.url))
                await page.goto(runtime_url, wait_until="load")
                await page.wait_for_timeout(500)
                generated_frame = page.frames[1]
                await generated_frame.locator('[data-region="launcher"]').wait_for()
                assert await generated_frame.locator(
                    '[data-kaigo-runtime-input="true"]'
                ).count() == 1
                frame_urls = [frame.url for frame in page.frames]
                assert all(not url.startswith(malicious_url) for url in frame_urls)
                assert all(not url.startswith(malicious_url) for url in requested_urls)
            finally:
                await browser.close()
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_rollback_route_is_owner_scoped_and_preserves_stable_embed(tmp_path) -> None:
    engine, _factory, client, ids = await _publication_app(tmp_path)
    try:
        await client.post("/test/login/10")
        first_response = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={"artifact_id": str(ids["first"])},
            headers={"X-CSRF-Token": "csrf"},
        )
        first = await first_response.json()
        second_response = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={"artifact_id": str(ids["same_revision_other_run"])},
            headers={"X-CSRF-Token": "csrf"},
        )
        second = await second_response.json()
        assert first["stable_key"] == second["stable_key"]

        await client.post("/test/login/11")
        hidden = await client.post(
            f"/api/publications/{first['publication_id']}/rollback",
            json={"target_release_id": first["release_id"]},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert hidden.status == 404

        await client.post("/test/login/10")
        rolled_back = await client.post(
            f"/api/publications/{first['publication_id']}/rollback",
            json={"target_release_id": first["release_id"]},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert rolled_back.status == 200
        assert (await rolled_back.json())["release_id"] == first["release_id"]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_versioned_publish_route_enforces_exact_bounded_request_contract(
    tmp_path,
) -> None:
    config = SimpleNamespace(
        public_auth_enabled=True,
        public_base_url="https://kaigo.example",
        environment="production",
        publication_allow_insecure_origins=False,
        project_versions_enabled=True,
    )
    engine, factory, client, ids = await _publication_app(tmp_path, config=config)
    route = f"/api/projects/{ids['project']}/publish"
    headers = {"X-CSRF-Token": "csrf", "Content-Type": "application/json"}
    try:
        assert (await client.post(route, json={})).status == 401
        await client.post("/test/login/11")
        hidden_body = {
            "project_version_id": str(ids["first_version"]),
            "expected_active_release_id": None,
            "allowed_domains": ["https://example.com"],
        }
        assert (
            await client.post(
                route, json=hidden_body, headers={"X-CSRF-Token": "csrf"}
            )
        ).status == 404
        await client.post("/test/login/10")
        body = {
            "project_version_id": str(ids["first_version"]),
            "expected_active_release_id": None,
            "allowed_domains": ["https://example.com"],
        }
        assert (await client.post(route, json=body)).status == 403

        async with factory() as database, database.begin():
            subscription = await database.scalar(
                select(Subscription).where(Subscription.user_id == 10)
            )
            subscription.status = "cancelled"
        assert (
            await client.post(route, json=body, headers={"X-CSRF-Token": "csrf"})
        ).status == 402
        async with factory() as database, database.begin():
            subscription = await database.scalar(
                select(Subscription).where(Subscription.user_id == 10)
            )
            subscription.status = "active"

        first_response = await client.post(
            route, json=body, headers={"X-CSRF-Token": "csrf"}
        )
        assert first_response.status == 201
        first = await first_response.json()
        assert first["project_version_id"] == str(ids["first_version"])
        runtime = await client.get(f"/runtime/{first['stable_key']}")
        runtime_text = await runtime.text()
        assert f'data-kaigo-release-id="{first["release_id"]}"' in runtime_text
        assert (
            f'data-kaigo-project-version-id="{ids["first_version"]}"'
            in runtime_text
        )

        invalid_origin = await client.post(
            route,
            json={**body, "allowed_domains": ["http://example.com"]},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert invalid_origin.status == 422

        invalid_bodies = (
            json.dumps({**body, "unknown": True}),
            '{"project_version_id":"%s","project_version_id":"%s",'
            '"expected_active_release_id":null,"allowed_domains":[]}'
            % (ids["first_version"], ids["second_version"]),
            json.dumps({**body, "project_version_id": True}),
            json.dumps({**body, "project_version_id": "not-a-uuid"}),
            json.dumps({**body, "expected_active_release_id": "not-a-uuid"}),
            json.dumps({**body, "allowed_domains": ["https://example.com"] * 33}),
        )
        for raw in invalid_bodies:
            response = await client.post(route, data=raw, headers=headers)
            assert response.status == 400

        oversized = json.dumps(
            {
                **body,
                "allowed_domains": ["https://example.com/" + ("x" * 33_000)],
            }
        )
        oversized_response = await client.post(route, data=oversized, headers=headers)
        assert oversized_response.status == 413
        assert await oversized_response.json() == {
            "error": {"code": "request_too_large"}
        }

        second_body = {
            "project_version_id": str(ids["second_version"]),
            "expected_active_release_id": first["release_id"],
            "allowed_domains": ["https://example.com"],
        }
        second_response = await client.post(
            route, json=second_body, headers={"X-CSRF-Token": "csrf"}
        )
        assert second_response.status == 201

        stale = await client.post(
            route,
            json={
                **body,
                "allowed_domains": ["https://changed.example"],
                "expected_active_release_id": first["release_id"],
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert stale.status == 409
        assert await stale.json() == {
            "error": {
                "code": "publication_conflict",
                "message": "Publication changed; reload before retrying",
            }
        }
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_versioned_rollback_route_requires_expected_pointer(tmp_path) -> None:
    config = SimpleNamespace(
        public_auth_enabled=True,
        public_base_url="https://kaigo.example",
        environment="production",
        publication_allow_insecure_origins=False,
        project_versions_enabled=True,
    )
    engine, _factory, client, ids = await _publication_app(tmp_path, config=config)
    headers = {"X-CSRF-Token": "csrf", "Content-Type": "application/json"}
    try:
        await client.post("/test/login/10")
        first = await (
            await client.post(
                f"/api/projects/{ids['project']}/publish",
                json={
                    "project_version_id": str(ids["first_version"]),
                    "expected_active_release_id": None,
                    "allowed_domains": ["https://example.com"],
                },
                headers=headers,
            )
        ).json()
        second = await (
            await client.post(
                f"/api/projects/{ids['project']}/publish",
                json={
                    "project_version_id": str(ids["second_version"]),
                    "expected_active_release_id": first["release_id"],
                    "allowed_domains": ["https://example.com"],
                },
                headers=headers,
            )
        ).json()
        route = f"/api/publications/{first['publication_id']}/rollback"
        missing = await client.post(
            route, json={"target_release_id": first["release_id"]}, headers=headers
        )
        assert missing.status == 400

        invalid_requests = (
            (
                json.dumps(
                    {
                        "target_release_id": first["release_id"],
                        "expected_active_release_id": second["release_id"],
                        "unknown": True,
                    }
                ),
                "invalid_body",
            ),
            (
                '{"target_release_id":"%s","target_release_id":"%s",'
                '"expected_active_release_id":"%s"}'
                % (first["release_id"], second["release_id"], second["release_id"]),
                "invalid_json",
            ),
            (
                json.dumps(
                    {
                        "target_release_id": "not-a-uuid",
                        "expected_active_release_id": second["release_id"],
                    }
                ),
                "invalid_release_id",
            ),
            (
                json.dumps(
                    {
                        "target_release_id": first["release_id"],
                        "expected_active_release_id": "not-a-uuid",
                    }
                ),
                "invalid_release_id",
            ),
            (
                json.dumps(
                    {
                        "target_release_id": True,
                        "expected_active_release_id": second["release_id"],
                    }
                ),
                "invalid_release_id",
            ),
        )
        for raw, error_code in invalid_requests:
            response = await client.post(route, data=raw, headers=headers)
            assert response.status == 400
            assert (await response.json())["error"]["code"] == error_code

        oversized = json.dumps(
            {
                "target_release_id": "x" * 33_000,
                "expected_active_release_id": second["release_id"],
            }
        )
        oversized_response = await client.post(route, data=oversized, headers=headers)
        assert oversized_response.status == 413
        assert await oversized_response.json() == {
            "error": {"code": "request_too_large"}
        }

        restored = await client.post(
            route,
            json={
                "target_release_id": first["release_id"],
                "expected_active_release_id": second["release_id"],
            },
            headers=headers,
        )
        assert restored.status == 200
        assert (await restored.json())["project_version_id"] == str(ids["first_version"])
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_disabling_versions_keeps_owner_safe_legacy_rollback_for_v2_history(
    tmp_path,
) -> None:
    config = SimpleNamespace(
        public_auth_enabled=True,
        public_base_url="https://kaigo.example",
        environment="production",
        publication_allow_insecure_origins=False,
        project_versions_enabled=True,
    )
    engine, factory, client, ids = await _publication_app(tmp_path, config=config)
    headers = {"X-CSRF-Token": "csrf"}
    try:
        await client.post("/test/login/10")
        first_response = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "project_version_id": str(ids["first_version"]),
                "expected_active_release_id": None,
                "allowed_domains": ["https://example.com"],
            },
            headers=headers,
        )
        first = await first_response.json()
        second_response = await client.post(
            f"/api/projects/{ids['project']}/publish",
            json={
                "project_version_id": str(ids["second_version"]),
                "expected_active_release_id": first["release_id"],
                "allowed_domains": ["https://example.com"],
            },
            headers=headers,
        )
        second = await second_response.json()
        assert first_response.status == second_response.status == 201
        assert second["release_id"] != first["release_id"]

        config.project_versions_enabled = False
        route = f"/api/publications/{first['publication_id']}/rollback"
        await client.post("/test/login/11")
        hidden = await client.post(
            route,
            json={"target_release_id": first["release_id"]},
            headers=headers,
        )
        assert hidden.status == 404

        await client.post("/test/login/10")
        restored = await client.post(
            route,
            json={"target_release_id": first["release_id"]},
            headers=headers,
        )
        assert restored.status == 200
        assert (await restored.json())["project_version_id"] == str(
            ids["first_version"]
        )
        async with factory() as database:
            publication = await database.get(
                Publication, UUID(first["publication_id"])
            )
            release = await database.get(
                PublicationRelease, UUID(first["release_id"])
            )
            assert publication.active_release_id == release.id
            assert release.project_id == ids["project"]
            assert release.project_version_id == ids["first_version"]
    finally:
        await client.close()
        await engine.dispose()


def test_manifest_checksum_uses_canonical_json() -> None:
    manifest = {"version": 1, "artifact": {"css": "x", "body_html": "y"}}
    expected = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert PublicationService.manifest_checksum(manifest) == expected
