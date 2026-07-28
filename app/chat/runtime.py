from __future__ import annotations

import inspect
from collections.abc import Callable

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import AppConfig
from app.db.session import get_session_factory
from app.models.providers.gemini import GeminiModelProvider
from app.models.router import (
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
    SqlModelCallAudit,
)

from .service import RoutedChatService


CHAT_SERVICE_KEY = web.AppKey("bounded_chat_service", object)
ProviderFactory = Callable[..., object]
ServiceFactory = Callable[..., RoutedChatService]


async def _close_provider(provider: object) -> None:
    close = getattr(provider, "aclose", None)
    if not callable(close):
        close = getattr(provider, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


async def create_routed_chat_service(
    config: AppConfig,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_factory: ProviderFactory = GeminiModelProvider,
    service_factory: ServiceFactory = RoutedChatService,
) -> RoutedChatService | None:
    if not config.chat_provider_api_key:
        return None
    provider: object | None = None
    router: ModelRouter | None = None
    try:
        provider = provider_factory(
            api_key=config.chat_provider_api_key,
            base_url=config.chat_provider_base_url,
        )
        router = ModelRouter(
            providers={"gemini": provider},  # type: ignore[dict-item]
            policies={
                ("chat_visitor", "express"): ModelPolicy(
                    prompt_version="chat-visitor-v1",
                    targets=(ProviderTarget(
                        provider="gemini",
                        model=config.chat_model,
                        input_price_microusd_per_million=(
                            config.chat_input_price_microusd_per_million
                        ),
                        output_price_microusd_per_million=(
                            config.chat_output_price_microusd_per_million
                        ),
                    ),),
                )
            },
            audit=SqlModelCallAudit(session_factory),
        )
        return service_factory(
            router=router,
            timeout_seconds=config.chat_timeout_seconds,
            session_ttl_seconds=config.chat_session_ttl_seconds,
            max_sessions=config.chat_max_sessions,
            rate_limit_requests=config.chat_rate_limit_requests,
            client_rate_limit_requests=config.chat_user_rate_limit_requests,
            rate_limit_window_seconds=config.chat_rate_limit_window_seconds,
            max_requests_per_session=config.chat_max_requests_per_session,
            global_concurrency=config.chat_global_concurrency,
        )
    except BaseException:
        if router is not None:
            await router.aclose()
        elif provider is not None:
            await _close_provider(provider)
        raise


def setup_chat_runtime(
    app: web.Application,
    *,
    service_factory=create_routed_chat_service,
) -> None:
    async def chat_runtime_context(application: web.Application):
        service = None
        try:
            service = await service_factory(
                application["config"],
                get_session_factory(application),
            )
            application[CHAT_SERVICE_KEY] = service
            yield
        finally:
            if service is not None:
                await service.close()

    app.cleanup_ctx.append(chat_runtime_context)


__all__ = ["CHAT_SERVICE_KEY", "create_routed_chat_service", "setup_chat_runtime"]
