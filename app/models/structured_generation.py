from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from app.models.contracts import ModelRequest, ModelResponse, ProviderUnavailable
from app.models.providers.gemini import GeminiModelProvider
from app.models.router import ModelRouter


class StructuredGenerationBackend(Protocol):
    @property
    def model_name(self) -> str: ...

    async def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def aclose(self) -> None: ...


class RoutedStructuredGenerationBackend:
    def __init__(
        self,
        *,
        router: ModelRouter,
        role: str,
        mode: str,
        run_id: UUID,
    ) -> None:
        self._router = router
        self._role = role
        self._mode = mode
        self._run_id = run_id

    @property
    def model_name(self) -> str:
        return "routed"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return await self._router.generate(
            role=self._role,
            mode=self._mode,
            run_id=self._run_id,
            request=request,
        )

    async def aclose(self) -> None:
        return None


class GeminiStructuredGenerationBackend:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str,
        timeout_seconds: float,
        client=None,
    ) -> None:
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._provider = GeminiModelProvider(
            api_key=api_key,
            base_url=base_url,
            client=client,
        )

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, request: ModelRequest) -> ModelResponse:
        retry_delays = (0.5, 1.5, 3.0, 5.0)
        for attempt in range(len(retry_delays) + 1):
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await self._provider.generate(request, model=self._model)
            except asyncio.CancelledError:
                raise
            except ProviderUnavailable:
                if attempt == len(retry_delays):
                    raise
                await asyncio.sleep(retry_delays[attempt])
        raise AssertionError("unreachable structured generation retry loop")

    async def aclose(self) -> None:
        await self._provider.aclose()


__all__ = [
    "GeminiStructuredGenerationBackend",
    "RoutedStructuredGenerationBackend",
    "StructuredGenerationBackend",
]
