from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessage

from . import config

logger = logging.getLogger(__name__)


@dataclass
class AIServiceError(Exception):
    code: str
    public_message: str
    detail: str
    status_code: int = 502

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def _api_key() -> str:
    return (config.settings.VSEGPT_API_KEY or "").strip()


def _require_api_key() -> str:
    key = _api_key()
    if not key:
        raise AIServiceError(
            code="missing_api_key",
            public_message="AI-провайдер не настроен: отсутствует VSEGPT_API_KEY.",
            detail="VSEGPT_API_KEY is empty",
            status_code=503,
        )
    return key


def _async_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=_require_api_key(),
        base_url=config.settings.VSEGPT_BASE_URL,
        timeout=config.settings.VSEGPT_REQUEST_TIMEOUT,
        max_retries=config.settings.VSEGPT_MAX_RETRIES,
    )


def _sync_client() -> OpenAI:
    return OpenAI(
        api_key=_require_api_key(),
        base_url=config.settings.VSEGPT_BASE_URL,
        timeout=config.settings.VSEGPT_REQUEST_TIMEOUT,
        max_retries=config.settings.VSEGPT_MAX_RETRIES,
    )


def _classify_provider_error(exc: Exception) -> AIServiceError:
    detail = str(exc)
    lowered = detail.lower()
    status_code = getattr(exc, "status_code", None) or 502

    if "api key not found" in lowered or "user with this api key not found" in lowered:
        return AIServiceError(
            code="invalid_api_key",
            public_message="AI-провайдер не принял API-ключ. Обновите VSEGPT_API_KEY.",
            detail=detail,
            status_code=502,
        )
    if "subscription end" in lowered or "freezed" in lowered or "frozen" in lowered:
        return AIServiceError(
            code="account_inactive",
            public_message="AI-аккаунт VseGPT неактивен или требует продления подписки.",
            detail=detail,
            status_code=502,
        )
    if isinstance(exc, RateLimitError) or "rate limit" in lowered:
        return AIServiceError(
            code="rate_limited",
            public_message="AI-провайдер временно ограничил запросы. Попробуйте позже.",
            detail=detail,
            status_code=429,
        )
    if isinstance(exc, (APITimeoutError, TimeoutError)) or "timeout" in lowered:
        return AIServiceError(
            code="provider_timeout",
            public_message="AI-провайдер не ответил вовремя. Попробуйте еще раз.",
            detail=detail,
            status_code=504,
        )
    if isinstance(exc, APIConnectionError):
        return AIServiceError(
            code="provider_connection_error",
            public_message="Не удалось подключиться к AI-провайдеру.",
            detail=detail,
            status_code=502,
        )
    if isinstance(exc, APIStatusError):
        return AIServiceError(
            code="provider_api_error",
            public_message="AI-провайдер вернул ошибку. Проверьте настройки модели и аккаунта.",
            detail=detail,
            status_code=status_code,
        )
    return AIServiceError(
        code="provider_error",
        public_message="Не удалось получить ответ от AI-провайдера.",
        detail=detail,
        status_code=status_code,
    )


async def get_ai_response(
    history: List[Dict[str, Any]],
    system_prompt: str | None,
    tools: List[Dict[str, Any]] | None,
    model: str = config.settings.default_model,
    temperature: float = config.settings.default_temperature,
    max_tokens: int = config.settings.default_max_tokens,
) -> ChatCompletionMessage:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history)

    logger.info(
        "Отправка запроса к модели %s с %s сообщениями и %s инструментами.",
        model,
        len(messages),
        len(tools) if tools else 0,
    )

    client = _async_client()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
            temperature=temperature,
            max_tokens=max_tokens,
            n=1,
        )
    except Exception as exc:  # noqa: BLE001
        error = _classify_provider_error(exc)
        logger.error("VseGPT error [%s]: %s", error.code, error.detail)
        raise error from exc
    finally:
        await client.close()

    if not response.choices:
        raise AIServiceError(
            code="empty_provider_response",
            public_message="AI-провайдер вернул пустой ответ.",
            detail="response.choices is empty",
            status_code=502,
        )

    ai_message = response.choices[0].message
    logger.info("Ответ от AI успешно получен.")
    return ai_message


async def check_provider(model: str | None = None) -> dict[str, Any]:
    try:
        message = await get_ai_response(
            history=[{"role": "user", "content": "Ответь ровно одним словом: OK"}],
            system_prompt=None,
            tools=None,
            model=model or config.settings.default_model,
            temperature=0,
            max_tokens=8,
        )
    except AIServiceError as exc:
        return {
            "status": "error",
            "code": exc.code,
            "message": exc.public_message,
            "status_code": exc.status_code,
        }

    return {
        "status": "ok",
        "model": model or config.settings.default_model,
        "sample": (message.content or "").strip()[:80],
    }


async def transcribe_audio(audio_data: bytes, model: str = config.settings.default_stt_model) -> str:
    logger.info("Отправка аудио на распознавание моделью %s...", model)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    try:
        tmp.write(audio_data)
        tmp_path = tmp.name
    finally:
        tmp.close()

    try:
        return await transcribe_audio_file(tmp_path, model=model)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


async def transcribe_audio_file(file_path: str, model: str = config.settings.default_stt_model) -> str:
    logger.info("Отправка аудио-файла на распознавание моделью %s: %s", model, file_path)
    client = _sync_client()

    def _do_sync(path: str):
        with open(path, "rb") as audio_file:
            return client.audio.transcriptions.create(
                model=model,
                response_format="json",
                language="ru",
                file=audio_file,
            )

    try:
        transcription = await asyncio.to_thread(_do_sync, file_path)
    except Exception as exc:  # noqa: BLE001
        error = _classify_provider_error(exc)
        logger.error("VseGPT STT error [%s]: %s", error.code, error.detail)
        raise error from exc
    finally:
        client.close()

    text = getattr(transcription, "text", None)
    if not text and isinstance(transcription, dict):
        text = transcription.get("text")
    if not text:
        text = str(transcription)
    text = (text or "").strip()
    logger.info("Аудио-файл успешно распознан.")
    return text
