from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.ai_service import get_ai_response


@pytest.mark.asyncio
async def test_chat_request_has_no_output_token_cap() -> None:
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        close=AsyncMock(),
    )

    with patch("core.ai_service._async_client", return_value=client):
        result = await get_ai_response(
            history=[{"role": "user", "content": "Hello"}],
            system_prompt=None,
            tools=None,
        )

    assert result.content == "ok"
    request = create.await_args.kwargs
    assert "max_tokens" not in request
    assert "max_output_tokens" not in request
    client.close.assert_awaited_once_with()
