from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from playwright.async_api import async_playwright
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.publication.routes import setup_publication_routes
from app.publication.service import (
    InvalidAllowedDomain,
    InvalidPublicationArtifact,
    PublicationIdentityUnverified,
    PublicationNotFound,
    PublicationService,
    PublicationUpgradeRequired,
    ReleaseCorrupt,
)
from app.saas.models import (
    GenerationArtifact,
    GenerationRun,
    Project,
    Publication,
    PublicationRelease,
    Subscription,
    UserIdentity,
)
from tests.builder_lab_cases.test_validation import artifact


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
                current_period_end=datetime.now(UTC) + timedelta(days=1),
            ),
            Subscription(
                user_id=11,
                provider="test",
                plan_code="pro",
                status="active",
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
        project.active_run_id = run.id
        project.active_revision = 1
        return {
            "project": project.id,
            "foreign_project": foreign_project.id,
            "first": first.id,
            "same_revision_other_run": same_revision_other_run.id,
            "draft": draft.id,
            "foreign": foreign.id,
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


async def _publication_app(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(factory)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    setup_session(app, SimpleCookieStorage(cookie_name="kaigo_publication_test"))

    async def login(request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        session = await get_session(request)
        session["user_id"] = user_id
        session["tenant_id"] = 1
        session["csrf_token"] = "csrf"
        return web.json_response({"csrf_token": "csrf"})

    app.router.add_post("/test/login/{user_id}", login)
    setup_publication_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, factory, client, ids


@pytest.mark.asyncio
async def test_publish_route_requires_auth_csrf_owner_verified_and_subscription(tmp_path) -> None:
    engine, factory, client, ids = await _publication_app(tmp_path)
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
        assert payload["embed_url"].endswith(f"/embed/{payload['stable_key']}.js")
        assert payload["runtime_url"].endswith(f"/runtime/{payload['stable_key']}")
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
        key = (await published.json())["stable_key"]

        loader = await client.get(f"/embed/{key}.js")
        loader_text = await loader.text()
        assert loader.status == 200
        assert "document.currentScript" in loader_text
        assert "document.write" not in loader_text
        assert 'sandbox="allow-scripts"' in loader_text or "sandbox', 'allow-scripts'" in loader_text
        assert "strict-origin" in loader_text
        assert "event.source" in loader_text
        assert "Math.min" in loader_text and "Math.max" in loader_text
        assert loader.headers["Cache-Control"] == "public, max-age=300"
        assert loader.headers["X-Content-Type-Options"] == "nosniff"

        runtime = await client.get(f"/runtime/{key}")
        runtime_text = await runtime.text()
        assert runtime.status == 200
        assert "default-src 'none'" in runtime.headers["Content-Security-Policy"]
        assert "connect-src 'none'" in runtime.headers["Content-Security-Policy"]
        assert "frame-src blob:" in runtime.headers["Content-Security-Policy"]
        assert "frame-ancestors https://example.com" in runtime.headers["Content-Security-Policy"]
        assert "X-Frame-Options" not in runtime.headers
        assert runtime.headers["Cache-Control"] == "no-store"
        assert "kaigo-widget" in runtime_text

        unknown_loader = await client.get("/embed/unknown.js")
        unknown_runtime = await client.get("/runtime/unknown")
        assert unknown_loader.status == unknown_runtime.status == 404
        assert await unknown_loader.text() == await unknown_runtime.text()
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_generated_javascript_cannot_navigate_runtime_to_external_origin(tmp_path) -> None:
    engine, factory, client, ids = await _publication_app(tmp_path)
    malicious_url = "https://evil.example/phish"
    try:
        malicious = artifact(
            revision=1,
            javascript=f"location.replace({malicious_url!r})",
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


def test_manifest_checksum_uses_canonical_json() -> None:
    manifest = {"version": 1, "artifact": {"css": "x", "body_html": "y"}}
    expected = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert PublicationService.manifest_checksum(manifest) == expected
