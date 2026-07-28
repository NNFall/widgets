from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import get_session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.chat.runtime import CHAT_SERVICE_KEY, create_routed_chat_service, setup_chat_runtime
from app.config import AppConfig
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY, get_session_factory
from app.models.contracts import ModelRequest, ModelResponse, ModelUsage, ProviderCapabilities
from app.saas.models import AuthSession, GenerationArtifact, GenerationRun, ModelCall, Project
from app.server import create_app
from tests.builder_lab_cases.test_validation import artifact


class ClosingProvider:
    capabilities = ProviderCapabilities()

    def __init__(self) -> None:
        self.closed = False

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        return ModelResponse(text="unused")

    async def aclose(self) -> None:
        self.closed = True


def _config(**changes) -> AppConfig:
    values = {
        "database_url": "sqlite+aiosqlite:///unused.db",
        "chat_provider_api_key": "secret-key",
        "chat_provider_base_url": "https://generativelanguage.googleapis.com",
        "chat_model": "gemini-chat-test",
        "chat_timeout_seconds": 5,
        "chat_session_ttl_seconds": 60,
        "chat_max_sessions": 4,
        "chat_rate_limit_requests": 3,
        "chat_user_rate_limit_requests": 5,
        "chat_rate_limit_window_seconds": 60,
        "chat_max_requests_per_session": 4,
        "chat_global_concurrency": 1,
        "chat_input_price_microusd_per_million": 2_000_000,
        "chat_output_price_microusd_per_million": 4_000_000,
    }
    values.update(changes)
    return AppConfig(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chat_model", ""),
        ("chat_timeout_seconds", 0),
        ("chat_timeout_seconds", True),
        ("chat_session_ttl_seconds", 29),
        ("chat_max_sessions", 0),
        ("chat_rate_limit_requests", 0),
        ("chat_user_rate_limit_requests", 0),
        ("chat_rate_limit_window_seconds", 0),
        ("chat_max_requests_per_session", 0),
        ("chat_global_concurrency", 0),
        ("chat_input_price_microusd_per_million", -1),
        ("chat_output_price_microusd_per_million", -1),
        ("publication_chat_capability_ttl_seconds", 29),
        ("publication_chat_key_rate_limit_requests", 0),
        ("publication_chat_ip_rate_limit_requests", 0),
    ],
)
def test_chat_config_is_strictly_bounded(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _config(**{field: value})


def test_chat_provider_secret_is_redacted_from_config_repr() -> None:
    rendered = repr(
        _config(
            chat_provider_api_key="never-print-this-secret",
            publication_chat_signing_secret="never-print-publication-secret",
        )
    )
    assert "never-print-this-secret" not in rendered
    assert "chat_provider_api_key" not in rendered
    assert "never-print-publication-secret" not in rendered
    assert "publication_chat_signing_secret" not in rendered


def test_production_chat_requires_a_stable_publication_signing_secret() -> None:
    with pytest.raises(ValueError, match="publication_chat_signing_secret"):
        _config(environment="production", publication_chat_signing_secret=None)
    with pytest.raises(ValueError, match="publication_chat_signing_secret"):
        _config(environment="production", publication_chat_signing_secret="too-short")
    with pytest.raises(ValueError, match="publication_chat_signing_secret"):
        _config(environment="production", publication_chat_signing_secret=" " * 32)

    config = _config(
        environment="production",
        publication_chat_signing_secret="p" * 32,
    )
    assert "p" * 32 not in repr(config)


def test_publication_trusted_proxy_cidrs_are_validated() -> None:
    with pytest.raises(ValueError, match="trusted proxy CIDR"):
        _config(publication_chat_trusted_proxy_cidrs=("not-a-network",))

    config = _config(
        publication_chat_trusted_proxy_cidrs=("127.0.0.1/8", "2001:db8::1/48")
    )
    assert config.publication_chat_trusted_proxy_cidrs == (
        "127.0.0.0/8",
        "2001:db8::/48",
    )


@pytest.mark.asyncio
async def test_runtime_factory_closes_provider_when_service_construction_fails(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = ClosingProvider()

    class BrokenService:
        def __init__(self, **_kwargs) -> None:
            raise RuntimeError("service construction failed")

    try:
        with pytest.raises(RuntimeError, match="service construction failed"):
            await create_routed_chat_service(
                _config(),
                factory,
                provider_factory=lambda **_kwargs: provider,
                service_factory=BrokenService,
            )
        assert provider.closed is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_app_cleanup_context_installs_after_database_and_closes_service(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'context.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    events: list[str] = []

    class FakeService:
        async def close(self) -> None:
            events.append("closed")

    service = FakeService()

    async def build(config, installed_factory):
        assert isinstance(config, AppConfig)
        assert installed_factory is factory
        events.append("built")
        return service

    app = web.Application()
    app["config"] = _config()
    app[SESSION_FACTORY_KEY] = factory
    setup_chat_runtime(app, service_factory=build)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        assert app[CHAT_SERVICE_KEY] is service
        assert events == ["built"]
    finally:
        await runner.cleanup()
        await engine.dispose()
    assert events == ["built", "closed"]


@pytest.mark.asyncio
async def test_runtime_factory_is_explicitly_unavailable_without_provider_key(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'disabled.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        service = await create_routed_chat_service(
            _config(chat_provider_api_key=None),
            factory,
        )
        assert service is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_app_lifecycle_serves_owner_chat_and_closes_router(
    tmp_path,
    monkeypatch,
) -> None:
    provider = ClosingProvider()
    provider.requests = []

    async def generate(request: ModelRequest, *, model: str) -> ModelResponse:
        assert model == "gemini-chat-test"
        provider.requests.append(request)
        return ModelResponse(
            text="Lifecycle reply",
            usage=ModelUsage(input_tokens=3, output_tokens=2),
            request_id="lifecycle-provider-request",
        )

    provider.generate = generate

    async def build(config, factory):
        return await create_routed_chat_service(
            config,
            factory,
            provider_factory=lambda **_kwargs: provider,
        )

    async def skip_legacy_history_db() -> None:
        return None

    monkeypatch.setattr("app.server.history_db.init_db", skip_legacy_history_db)
    config = _config(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'production-app.db'}",
        chat_input_price_microusd_per_million=1_000_000,
        chat_output_price_microusd_per_million=2_000_000,
    )
    app = await create_app(config, chat_service_factory=build)

    async def login(request: web.Request) -> web.Response:
        session = await get_session(request)
        session.update({
            "user_id": 10,
            "tenant_id": 1,
            "email": "owner@example.com",
            "csrf_token": "lifecycle-csrf",
        })
        return web.json_response({"ok": True})

    app.router.add_post("/test/login", login)
    client = TestClient(TestServer(app))
    try:
        await client.start_server()
        factory = get_session_factory(app)
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Alpha", slug="alpha"))
            database.add(User(id=10, tenant_id=1, email="owner@example.com"))
            await database.flush()
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
                brief="Durable lifecycle brief",
            )
            database.add(project)
            await database.flush()
            run = GenerationRun(
                project_id=project.id,
                mode="express",
                state="completed",
                progress=100,
                idempotency_key="lifecycle-run",
            )
            database.add(run)
            await database.flush()
            candidate = artifact(revision=1, art_direction="Lifecycle identity")
            database.add(GenerationArtifact(
                run_id=run.id,
                revision=1,
                stage=candidate.stage.value,
                html=candidate.body_html,
                css=candidate.css,
                javascript=candidate.javascript,
                config={"artifact": candidate.to_dict()},
                quality_status="accepted",
            ))
            run_id = run.id

        assert app[CHAT_SERVICE_KEY] is not None
        await client.post("/test/login")
        response = await client.post(
            f"/api/runs/{run_id}/chat",
            json={
                "request_id": "lifecycle-request-1",
                "message": "What is available?",
                "revision": 1,
            },
            headers={"X-CSRF-Token": "lifecycle-csrf"},
        )
        assert response.status == 200
        assert await response.json() == {
            "request_id": "lifecycle-request-1",
            "reply": "Lifecycle reply",
        }
        assert len(provider.requests) == 1
        assert "Durable lifecycle brief" in provider.requests[0].prompt
        async with factory() as database:
            call = (await database.execute(select(ModelCall))).scalar_one()
            stored_session = (await database.execute(select(AuthSession))).scalar_one()
        assert (call.role, call.run_id, call.cost_microusd) == (
            "chat_visitor",
            run_id,
            7,
        )
        chat_session_id = stored_session.payload["session"]["project_chat_session_id"]
        assert isinstance(chat_session_id, str)
        assert len(chat_session_id) >= 24
        assert chat_session_id != "lifecycle-csrf"
    finally:
        await client.close()
    assert provider.closed is True
