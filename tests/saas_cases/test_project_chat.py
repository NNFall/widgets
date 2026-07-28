from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.models.contracts import ModelRequest, ModelResponse, ModelUsage, ProviderCapabilities
from app.models.router import ModelPolicy, ModelRouter, ProviderTarget, SqlModelCallAudit
from app.chat import (
    ChatContext,
    ChatServiceError,
    RoutedChatService,
)
from app.saas.models import GenerationRun, ModelCall, Project


class FakeChatProvider:
    capabilities = ProviderCapabilities()

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.closed = False

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        assert model == "fake-chat-model"
        self.requests.append(request)
        return ModelResponse(
            text="Проверенный ответ",
            usage=ModelUsage(input_tokens=11, output_tokens=7, thinking_tokens=2),
            request_id="provider-request-1",
        )

    async def aclose(self) -> None:
        self.closed = True


async def _runtime(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        await database.flush()
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://example.com/",
            brief="Secret brief marker",
        )
        database.add(project)
        await database.flush()
        run = GenerationRun(
            project_id=project.id,
            mode="express",
            state="completed",
            idempotency_key="chat-service",
        )
        database.add(run)
        await database.flush()
        run_id = run.id
    provider = FakeChatProvider()
    router = ModelRouter(
        providers={"fake": provider},
        policies={
            ("chat_visitor", "express"): ModelPolicy(
                prompt_version="chat-visitor-v1",
                targets=(ProviderTarget(
                    provider="fake",
                    model="fake-chat-model",
                    input_price_microusd_per_million=2_000_000,
                    output_price_microusd_per_million=4_000_000,
                ),),
            )
        },
        audit=SqlModelCallAudit(factory),
    )
    return engine, factory, run_id, provider, router


def _context() -> ChatContext:
    return ChatContext(
        source_url="https://example.com/",
        brief="Secret brief marker",
        art_direction="Calm expert",
    )


@pytest.mark.asyncio
async def test_routed_chat_is_idempotent_and_persists_exact_usage_cost(tmp_path) -> None:
    engine, factory, run_id, provider, router = await _runtime(tmp_path)
    service = RoutedChatService(
        router=router,
        timeout_seconds=5,
        rate_limit_requests=3,
        client_rate_limit_requests=3,
        rate_limit_window_seconds=60,
        max_requests_per_session=4,
        max_sessions=4,
        session_ttl_seconds=60,
        global_concurrency=1,
    )
    try:
        first = await service.reply(
            scope=f"tenant:1:user:10:run:{run_id}:revision:1",
            session_id="session-12345678",
            client_id="tenant:1:user:10",
            request_id="request-12345678",
            text="Что вы предлагаете?",
            context=_context(),
            run_id=run_id,
        )
        duplicate = await service.reply(
            scope=f"tenant:1:user:10:run:{run_id}:revision:1",
            session_id="session-12345678",
            client_id="tenant:1:user:10",
            request_id="request-12345678",
            text="Что вы предлагаете?",
            context=_context(),
            run_id=run_id,
        )

        assert first == duplicate
        assert first.request_id == "request-12345678"
        assert first.text == "Проверенный ответ"
        assert len(provider.requests) == 1
        assert "Secret brief marker" in provider.requests[0].prompt
        async with factory() as database:
            call = (await database.execute(select(ModelCall))).scalar_one()
        assert call.role == "chat_visitor"
        assert call.run_id == run_id
        assert (call.input_tokens, call.output_tokens, call.thinking_tokens) == (11, 7, 2)
        assert call.cost_microusd == 50
        assert call.pricing_snapshot == {
            "currency": "USD",
            "billing_unit_tokens": 1_000_000,
            "input_price_microusd_per_million": 2_000_000,
            "output_price_microusd_per_million": 4_000_000,
        }
    finally:
        await service.close()
        await engine.dispose()
    assert provider.closed is True


@pytest.mark.asyncio
async def test_routed_chat_rejects_request_id_conflicts_and_rate_overflow(tmp_path) -> None:
    engine, _, run_id, _, router = await _runtime(tmp_path)
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
    kwargs = {
        "scope": f"tenant:1:user:10:run:{run_id}:revision:1",
        "session_id": "session-12345678",
        "client_id": "tenant:1:user:10",
        "context": _context(),
        "run_id": run_id,
    }
    try:
        await service.reply(
            **kwargs,
            request_id="request-12345678",
            text="Первый вопрос",
        )
        with pytest.raises(ChatServiceError) as conflict:
            await service.reply(
                **kwargs,
                request_id="request-12345678",
                text="Другой вопрос",
            )
        assert conflict.value.code == "chat_request_conflict"
        assert conflict.value.status == 409

        with pytest.raises(ChatServiceError) as limited:
            await service.reply(
                **kwargs,
                request_id="request-87654321",
                text="Второй вопрос",
            )
        assert limited.value.code == "chat_rate_limited"
        assert limited.value.status == 429
    finally:
        await service.close()
        await engine.dispose()
