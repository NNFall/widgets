from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Callable

from google import genai
from google.genai import types

from .engines.gemini_direct import build_http_options
from .model_config import generation_policy, normalize_thinking_level
from .models import TokenUsage


LOGGER = logging.getLogger(__name__)
MAX_CHAT_TEXT_CHARS = 1_000
MAX_CHAT_REPLY_CHARS = 4_000
MAX_HISTORY_PAIRS = 8
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,95}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{2,255}$")


class ChatServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        status: int,
        retryable: bool = False,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.status = status
        self.retryable = retryable
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class ChatReply:
    request_id: str
    text: str
    provider_request_id: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class _CachedReply:
    request_text: str
    reply: ChatReply


@dataclass
class _PendingRequest:
    request_text: str
    task: asyncio.Task[ChatReply]


@dataclass
class _Session:
    history: list[tuple[str, str]] = field(default_factory=list)
    responses: OrderedDict[str, _CachedReply] = field(default_factory=OrderedDict)
    attempted_at: deque[datetime] = field(default_factory=deque)
    successful_requests: int = 0
    pending: dict[str, _PendingRequest] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _validate_text(text: str, *, field_name: str, maximum: int) -> str:
    if not isinstance(text, str):
        raise ChatServiceError(
            "invalid_chat_request", "Сообщение должно быть текстом", status=400
        )
    normalized = text.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ChatServiceError(
            "invalid_chat_request", "Сообщение пустое или слишком длинное", status=400
        )
    return normalized


def _provider_error(exc: BaseException) -> ChatServiceError:
    if isinstance(exc, ChatServiceError):
        return exc
    diagnostic = f"{type(exc).__name__}: {exc}"
    lowered = diagnostic.lower()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in lowered:
        return ChatServiceError(
            "chat_timeout",
            "Ответ занимает слишком много времени. Повторите отправку.",
            status=504,
            retryable=True,
            diagnostic=diagnostic,
        )
    if any(token in lowered for token in ("429", "quota", "resource_exhausted")):
        return ChatServiceError(
            "chat_rate_limited",
            "Gemini временно занят. Повторите отправку чуть позже.",
            status=429,
            retryable=True,
            diagnostic=diagnostic,
        )
    return ChatServiceError(
        "chat_provider_unavailable",
        "Не удалось получить ответ Gemini. Текст сохранён — попробуйте ещё раз.",
        status=503,
        retryable=True,
        diagnostic=diagnostic,
    )


def _non_thought_text(response: Any) -> str:
    candidates = getattr(response, "candidates", None)
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or ()
        values = [
            str(getattr(part, "text", ""))
            for part in parts
            if not bool(getattr(part, "thought", False))
            and str(getattr(part, "text", "")).strip()
        ]
        text = "\n".join(values).strip()
    else:
        # Compatibility fallback for small fake clients and older SDK responses.
        text = str(getattr(response, "text", "") or "").strip()
    if not text:
        raise ChatServiceError(
            "chat_empty_response",
            "Gemini не вернул текстовый ответ. Повторите отправку.",
            status=502,
            retryable=True,
        )
    return text[:MAX_CHAT_REPLY_CHARS]


def _usage(response: Any) -> TokenUsage:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=int(getattr(metadata, "prompt_token_count", 0) or 0),
        output_tokens=int(getattr(metadata, "candidates_token_count", 0) or 0),
        thinking_tokens=int(getattr(metadata, "thoughts_token_count", 0) or 0),
    )


class GeminiDemoChatService:
    """Bounded server-side Gemini chat for untrusted widget previews.

    Sessions are scoped by artifact/run identity. Only successful user/model pairs
    enter history, and a request ID is an idempotency key within that scope.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "gemini-3.5-flash-lite",
        thinking_level: str = "medium",
        base_url: str = "https://generativelanguage.googleapis.com",
        client: Any | None = None,
        timeout_seconds: float = 45,
        session_ttl_seconds: int = 3_600,
        max_sessions: int = 500,
        rate_limit_requests: int = 12,
        ip_rate_limit_requests: int = 60,
        rate_limit_window_seconds: int = 60,
        max_requests_per_session: int = 40,
        global_concurrency: int = 4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ChatServiceError(
                "missing_api_key", "Для Gemini-чата не настроен API-ключ", status=503
            )
        if not 1 <= timeout_seconds <= 180:
            raise ValueError("timeout_seconds must be between 1 and 180")
        if not 30 <= session_ttl_seconds <= 86_400:
            raise ValueError("session_ttl_seconds must be between 30 and 86400")
        if not 1 <= max_sessions <= 10_000:
            raise ValueError("max_sessions must be between 1 and 10000")
        if not 1 <= rate_limit_requests <= 120:
            raise ValueError("rate_limit_requests must be between 1 and 120")
        if not 1 <= ip_rate_limit_requests <= 1_000:
            raise ValueError("ip_rate_limit_requests must be between 1 and 1000")
        if not 1 <= rate_limit_window_seconds <= 3_600:
            raise ValueError("rate_limit_window_seconds must be between 1 and 3600")
        if not 1 <= max_requests_per_session <= 1_000:
            raise ValueError("max_requests_per_session must be between 1 and 1000")
        if not 1 <= global_concurrency <= 32:
            raise ValueError("global_concurrency must be between 1 and 32")
        self.model = model.strip()
        if not self.model:
            raise ValueError("model must not be empty")
        self.thinking_level = normalize_thinking_level(thinking_level)
        self._owned_client = client is None
        self._client = client or genai.Client(
            api_key=api_key.strip(), http_options=build_http_options(base_url)
        )
        self._timeout_seconds = float(timeout_seconds)
        self._session_ttl = timedelta(seconds=session_ttl_seconds)
        self._max_sessions = max_sessions
        self._rate_limit_requests = rate_limit_requests
        self._ip_rate_limit_requests = ip_rate_limit_requests
        self._rate_limit_window = timedelta(seconds=rate_limit_window_seconds)
        self._max_requests_per_session = max_requests_per_session
        self._global_slots = asyncio.Semaphore(global_concurrency)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sessions: dict[tuple[str, str], _Session] = {}
        self._ip_attempted_at: dict[str, deque[datetime]] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def _prune_locked(self, now: datetime) -> None:
        expired = [
            key
            for key, session in self._sessions.items()
            if not session.pending and now - session.updated_at > self._session_ttl
        ]
        for key in expired:
            self._sessions.pop(key, None)
        threshold = now - self._rate_limit_window
        for client_id, attempted in tuple(self._ip_attempted_at.items()):
            while attempted and attempted[0] <= threshold:
                attempted.popleft()
            if not attempted:
                self._ip_attempted_at.pop(client_id, None)

    def _session_locked(self, key: tuple[str, str], now: datetime) -> _Session:
        session = self._sessions.get(key)
        if session is not None:
            return session
        if len(self._sessions) >= self._max_sessions:
            raise ChatServiceError(
                "chat_capacity",
                "Все слоты диалогов заняты. Попробуйте немного позже.",
                status=429,
                retryable=True,
            )
        session = _Session(updated_at=now)
        self._sessions[key] = session
        return session

    async def reply(
        self,
        *,
        scope: str,
        session_id: str,
        request_id: str,
        text: str,
        system_prompt: str,
        client_id: str | None = None,
    ) -> ChatReply:
        text = _validate_text(text, field_name="text", maximum=MAX_CHAT_TEXT_CHARS)
        system_prompt = _validate_text(
            system_prompt, field_name="system_prompt", maximum=16_000
        )
        if not _SCOPE.fullmatch(scope):
            raise ChatServiceError(
                "invalid_chat_request", "Контекст чата некорректен", status=400
            )
        if not _SESSION_ID.fullmatch(session_id):
            raise ChatServiceError(
                "invalid_chat_request", "Сессия чата некорректна", status=400
            )
        if not _REQUEST_ID.fullmatch(request_id):
            raise ChatServiceError(
                "invalid_chat_request", "Идентификатор запроса некорректен", status=400
            )
        client_id = client_id or f"session:{session_id}"
        if not _SCOPE.fullmatch(client_id):
            raise ChatServiceError(
                "invalid_chat_request", "Источник запроса некорректен", status=400
            )

        key = (scope, session_id)
        async with self._lock:
            if self._closed:
                raise ChatServiceError(
                    "chat_provider_unavailable",
                    "Чат остановлен. Обновите страницу.",
                    status=503,
                    retryable=True,
                )
            now = self._clock()
            self._prune_locked(now)
            session = self._sessions.get(key)
            cached = session.responses.get(request_id) if session is not None else None
            if cached is not None:
                if cached.request_text != text:
                    raise ChatServiceError(
                        "chat_request_conflict",
                        "Этот идентификатор уже использован другим сообщением.",
                        status=409,
                    )
                session.updated_at = now
                session.responses.move_to_end(request_id)
                return cached.reply
            pending = session.pending.get(request_id) if session is not None else None
            if pending is not None:
                if pending.request_text != text:
                    raise ChatServiceError(
                        "chat_request_conflict",
                        "Этот идентификатор уже используется другим сообщением.",
                        status=409,
                    )
                task = pending.task
            else:
                if session is not None and session.pending:
                    raise ChatServiceError(
                        "chat_busy",
                        "Дождитесь ответа на предыдущее сообщение.",
                        status=409,
                        retryable=True,
                    )
                threshold = now - self._rate_limit_window
                if session is not None:
                    while (
                        session.attempted_at
                        and session.attempted_at[0] <= threshold
                    ):
                        session.attempted_at.popleft()
                if (
                    session is not None
                    and len(session.attempted_at) >= self._rate_limit_requests
                ):
                    raise ChatServiceError(
                        "chat_rate_limited",
                        "Слишком много сообщений. Подождите немного.",
                        status=429,
                        retryable=True,
                    )
                ip_attempted = self._ip_attempted_at.get(client_id)
                if (
                    ip_attempted is not None
                    and len(ip_attempted) >= self._ip_rate_limit_requests
                ):
                    raise ChatServiceError(
                        "chat_rate_limited",
                        "Слишком много сообщений с этого адреса. Подождите немного.",
                        status=429,
                        retryable=True,
                    )
                if session is None:
                    session = self._session_locked(key, now)
                if session.successful_requests >= self._max_requests_per_session:
                    raise ChatServiceError(
                        "chat_budget_exhausted",
                        "Лимит этого диалога исчерпан. Начните новую сессию.",
                        status=429,
                    )
                history = tuple(session.history)
                session.attempted_at.append(now)
                self._ip_attempted_at.setdefault(client_id, deque()).append(now)
                session.updated_at = now
                task = asyncio.create_task(
                    self._execute_and_commit(
                        key=key,
                        request_id=request_id,
                        request_text=text,
                        system_prompt=system_prompt,
                        history=history,
                    )
                )
                session.pending[request_id] = _PendingRequest(text, task)
        return await asyncio.shield(task)

    async def _execute_and_commit(
        self,
        *,
        key: tuple[str, str],
        request_id: str,
        request_text: str,
        system_prompt: str,
        history: tuple[tuple[str, str], ...],
    ) -> ChatReply:
        started = perf_counter()
        try:
            contents = [
                types.Content(role=role, parts=[types.Part.from_text(text=value)])
                for role, value in history
            ]
            contents.append(
                types.Content(
                    role="user", parts=[types.Part.from_text(text=request_text)]
                )
            )
            policy = generation_policy(
                self.model,
                self.thinking_level,
                temperature=0.35,
                include_thoughts=False,
            )
            config = types.GenerateContentConfig(
                **policy.sampling_kwargs,
                system_instruction=system_prompt,
                max_output_tokens=384,
                thinking_config=policy.thinking_config,
            )
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    async with self._global_slots:
                        response = await self._client.aio.models.generate_content(
                            model=self.model,
                            contents=contents,
                            config=config,
                        )
                result = ChatReply(
                    request_id=request_id,
                    text=_non_thought_text(response),
                    provider_request_id=getattr(response, "response_id", None),
                    usage=_usage(response),
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                raise _provider_error(exc) from exc

            async with self._lock:
                session = self._sessions.get(key)
                if session is None:
                    raise ChatServiceError(
                        "chat_provider_unavailable",
                        "Сессия завершилась до получения ответа.",
                        status=503,
                        retryable=True,
                    )
                session.history.extend(
                    (("user", request_text), ("model", result.text))
                )
                if len(session.history) > MAX_HISTORY_PAIRS * 2:
                    del session.history[: len(session.history) - MAX_HISTORY_PAIRS * 2]
                session.responses[request_id] = _CachedReply(request_text, result)
                while len(session.responses) > self._max_requests_per_session:
                    session.responses.popitem(last=False)
                session.successful_requests += 1
                session.updated_at = self._clock()
                session.pending.pop(request_id, None)
                history_after = len(session.history)
            session_hash = hashlib.sha256(
                f"{key[0]}\x00{key[1]}".encode("utf-8")
            ).hexdigest()[:16]
            LOGGER.info(
                "Gemini chat completed request_id=%s provider_request_id=%s "
                "session_hash=%s history_before=%d history_after=%d "
                "prompt_tokens=%d output_tokens=%d thinking_tokens=%d elapsed_ms=%d",
                request_id,
                result.provider_request_id or "-",
                session_hash,
                len(history),
                history_after,
                result.usage.prompt_tokens,
                result.usage.output_tokens,
                result.usage.thinking_tokens,
                int((perf_counter() - started) * 1_000),
            )
            return result
        except BaseException:
            async with self._lock:
                session = self._sessions.get(key)
                if session is not None:
                    session.pending.pop(request_id, None)
                    session.updated_at = self._clock()
            raise

    async def history_for(
        self, scope: str, session_id: str
    ) -> tuple[tuple[str, str], ...]:
        async with self._lock:
            session = self._sessions.get((scope, session_id))
            return tuple(session.history) if session is not None else ()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            tasks = [
                pending.task
                for session in self._sessions.values()
                for pending in session.pending.values()
            ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if not self._owned_client:
            return
        aio = getattr(self._client, "aio", None)
        if aio is not None and hasattr(aio, "aclose"):
            await aio.aclose()
        elif hasattr(self._client, "close"):
            self._client.close()
