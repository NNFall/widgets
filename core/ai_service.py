from __future__ import annotations

import base64
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessage

from . import config

logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_TYPES = {
    "audio/aac",
    "audio/aiff",
    "audio/flac",
    "audio/mp3",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
}


@dataclass
class AIServiceError(Exception):
    code: str
    public_message: str
    detail: str
    status_code: int = 502

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def normalize_chat_model(model: str | None) -> str:
    value = (model or config.settings.default_model or "").strip()
    if not value:
        return "gemini-2.5-pro"
    if value.startswith("google/"):
        return value.split("/", 1)[1]
    if value.startswith("openai/") or value.startswith("gpt-"):
        logger.warning(
            "Legacy chat model %s is not available on Google AI Studio; using %s",
            value,
            config.settings.default_model,
        )
        return config.settings.default_model
    return value


def normalize_stt_model(model: str | None) -> str:
    value = (model or config.settings.default_stt_model or "").strip()
    if not value:
        return "gemini-2.5-flash"
    if value.startswith("google/"):
        return value.split("/", 1)[1]
    if value.startswith("stt-openai/") or "whisper" in value.lower():
        logger.warning(
            "Legacy STT model %s is not available on Google AI Studio; using %s",
            value,
            config.settings.default_stt_model,
        )
        return config.settings.default_stt_model
    return value


def _api_key() -> str:
    return (config.settings.GOOGLE_AI_API_KEY or "").strip()


def _require_api_key() -> str:
    key = _api_key()
    if not key:
        raise AIServiceError(
            code="missing_api_key",
            public_message=(
                "Google AI Studio не настроен: отсутствует GOOGLE_AI_API_KEY, "
                "GOOGLE_API_KEY или GEMINI_API_KEY."
            ),
            detail="GOOGLE_AI_API_KEY/GOOGLE_API_KEY/GEMINI_API_KEY is empty",
            status_code=503,
        )
    return key


def _async_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=_require_api_key(),
        base_url=config.settings.GOOGLE_AI_BASE_URL,
        timeout=config.settings.GOOGLE_AI_REQUEST_TIMEOUT,
        max_retries=config.settings.GOOGLE_AI_MAX_RETRIES,
    )


def _classify_provider_detail(detail: str, status_code: int = 502) -> AIServiceError:
    lowered = detail.lower()

    invalid_key_markers = (
        "api_key_invalid",
        "api key not valid",
        "api key not found",
        "api key expired",
        "invalid api key",
        "user with this api key not found",
    )
    if any(marker in lowered for marker in invalid_key_markers):
        return AIServiceError(
            code="invalid_api_key",
            public_message=(
                "Google AI Studio не принял API-ключ. Обновите GOOGLE_AI_API_KEY, "
                "GOOGLE_API_KEY или GEMINI_API_KEY."
            ),
            detail=detail,
            status_code=502,
        )
    if status_code in (401, 403) and "api key" in lowered:
        return AIServiceError(
            code="invalid_api_key",
            public_message=(
                "Google AI Studio не принял API-ключ. Обновите GOOGLE_AI_API_KEY, "
                "GOOGLE_API_KEY или GEMINI_API_KEY."
            ),
            detail=detail,
            status_code=502,
        )
    if "subscription end" in lowered or "freezed" in lowered or "frozen" in lowered:
        return AIServiceError(
            code="account_inactive",
            public_message="AI-аккаунт неактивен или требует продления подписки.",
            detail=detail,
            status_code=502,
        )
    if "model" in lowered and ("not found" in lowered or "not supported" in lowered or "unsupported" in lowered):
        return AIServiceError(
            code="model_not_found",
            public_message="Указанная Gemini-модель недоступна. Проверьте GOOGLE_AI_MODEL или настройки виджета.",
            detail=detail,
            status_code=502,
        )
    if status_code == 429 or "rate limit" in lowered or "quota" in lowered or "resource_exhausted" in lowered:
        return AIServiceError(
            code="rate_limited",
            public_message="Google AI Studio временно ограничил запросы или квота исчерпана.",
            detail=detail,
            status_code=429,
        )
    if "timeout" in lowered:
        return AIServiceError(
            code="provider_timeout",
            public_message="Google AI Studio не ответил вовремя. Попробуйте еще раз.",
            detail=detail,
            status_code=504,
        )
    return AIServiceError(
        code="provider_error",
        public_message="Не удалось получить ответ от Google AI Studio.",
        detail=detail,
        status_code=status_code or 502,
    )


def _classify_provider_error(exc: Exception) -> AIServiceError:
    response = getattr(exc, "response", None)
    response_text = ""
    if response is not None:
        try:
            response_text = response.text
        except Exception:  # noqa: BLE001
            response_text = ""

    detail = str(exc)
    if response_text and response_text not in detail:
        detail = f"{detail}; response={response_text[:1000]}"

    status_code = getattr(exc, "status_code", None) or getattr(response, "status_code", None) or 502

    if isinstance(exc, RateLimitError):
        return _classify_provider_detail(detail, 429)
    if isinstance(exc, (APITimeoutError, TimeoutError, httpx.TimeoutException)):
        return AIServiceError(
            code="provider_timeout",
            public_message="Google AI Studio не ответил вовремя. Попробуйте еще раз.",
            detail=detail,
            status_code=504,
        )
    if isinstance(exc, (APIConnectionError, httpx.ConnectError, httpx.NetworkError)):
        return AIServiceError(
            code="provider_connection_error",
            public_message="Не удалось подключиться к Google AI Studio.",
            detail=detail,
            status_code=502,
        )
    if isinstance(exc, (APIStatusError, httpx.HTTPStatusError)):
        classified = _classify_provider_detail(detail, int(status_code))
        if classified.code != "provider_error":
            return classified
        return AIServiceError(
            code="provider_api_error",
            public_message="Google AI Studio вернул ошибку. Проверьте ключ, модель и квоты.",
            detail=detail,
            status_code=int(status_code),
        )

    return _classify_provider_detail(detail, int(status_code))


def _audio_mime_type(file_path: str | None = None, default: str = "audio/ogg") -> str:
    guessed = None
    if file_path:
        guessed, _ = mimetypes.guess_type(file_path)
    if guessed == "audio/mpeg":
        guessed = "audio/mp3"
    if guessed in SUPPORTED_AUDIO_TYPES:
        return guessed
    return default


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    for candidate in payload.get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                return str(text).strip()
    return ""


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

    chat_model = normalize_chat_model(model)
    logger.info(
        "Отправка запроса к Gemini model=%s с %s сообщениями и %s инструментами.",
        chat_model,
        len(messages),
        len(tools) if tools else 0,
    )

    request: dict[str, Any] = {
        "model": chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        request["tools"] = tools
        request["tool_choice"] = "auto"

    client = _async_client()
    try:
        response = await client.chat.completions.create(**request)
    except Exception as exc:  # noqa: BLE001
        error = _classify_provider_error(exc)
        logger.error("Google AI Studio error [%s]: %s", error.code, error.detail)
        raise error from exc
    finally:
        await client.close()

    if not response.choices:
        raise AIServiceError(
            code="empty_provider_response",
            public_message="Google AI Studio вернул пустой ответ.",
            detail="response.choices is empty",
            status_code=502,
        )

    ai_message = response.choices[0].message
    logger.info("Ответ от Gemini успешно получен.")
    return ai_message


async def check_provider(model: str | None = None) -> dict[str, Any]:
    chat_model = normalize_chat_model(model)
    try:
        message = await get_ai_response(
            history=[{"role": "user", "content": "Ответь ровно одним словом: OK"}],
            system_prompt=None,
            tools=None,
            model=chat_model,
            temperature=0,
            max_tokens=8,
        )
    except AIServiceError as exc:
        return {
            "status": "error",
            "provider": "google_ai_studio",
            "code": exc.code,
            "message": exc.public_message,
            "status_code": exc.status_code,
        }

    return {
        "status": "ok",
        "provider": "google_ai_studio",
        "model": chat_model,
        "sample": (message.content or "").strip()[:80],
    }


async def transcribe_audio(
    audio_data: bytes,
    model: str = config.settings.default_stt_model,
    mime_type: str = "audio/ogg",
) -> str:
    stt_model = normalize_stt_model(model)
    logger.info("Отправка аудио на распознавание Gemini model=%s...", stt_model)
    return await _transcribe_audio_bytes(audio_data, model=stt_model, mime_type=mime_type)


async def transcribe_audio_file(file_path: str, model: str = config.settings.default_stt_model) -> str:
    stt_model = normalize_stt_model(model)
    path = Path(file_path)
    logger.info("Отправка аудио-файла на распознавание Gemini model=%s: %s", stt_model, path)
    audio_data = path.read_bytes()
    return await _transcribe_audio_bytes(audio_data, model=stt_model, mime_type=_audio_mime_type(str(path)))


async def _transcribe_audio_bytes(audio_data: bytes, model: str, mime_type: str) -> str:
    if not audio_data:
        raise AIServiceError(
            code="empty_audio",
            public_message="Аудиофайл пустой.",
            detail="audio_data is empty",
            status_code=400,
        )

    api_key = _require_api_key()
    audio_b64 = base64.b64encode(audio_data).decode("ascii")
    url = f"{config.settings.GOOGLE_AI_NATIVE_BASE_URL}/models/{model}:generateContent"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Сделай точную транскрипцию речи из аудио. "
                            "Если речь на русском, транскрибируй по-русски. "
                            "Верни только текст без комментариев."
                        )
                    },
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": audio_b64,
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=config.settings.GOOGLE_AI_REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                json=payload,
            )
            response.raise_for_status()
            response_payload = response.json()
    except Exception as exc:  # noqa: BLE001
        error = _classify_provider_error(exc)
        logger.error("Google AI Studio STT error [%s]: %s", error.code, error.detail)
        raise error from exc

    text = _extract_gemini_text(response_payload)
    if not text:
        raise AIServiceError(
            code="empty_provider_response",
            public_message="Google AI Studio не вернул текст распознавания.",
            detail=f"Gemini response has no text parts: {response_payload}",
            status_code=502,
        )

    logger.info("Аудио успешно распознано через Gemini.")
    return text.strip()
