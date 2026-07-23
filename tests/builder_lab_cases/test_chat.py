import asyncio
import types as std_types
import unittest
from datetime import datetime, timedelta, timezone

from builder_lab.chat import ChatServiceError, GeminiDemoChatService


MOJIBAKE_MARKERS = ("РЎ", "Рµ", "Р°", "СЃ", "С‚", "вЂ", "\ufffd")


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, asyncio.Event):
            await outcome.wait()
            return response("Ответ после ожидания")
        return outcome


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)
        self.aio = std_types.SimpleNamespace(models=self.models)


def response(text, *, thought="скрыто", response_id="provider-1"):
    parts = []
    if thought is not None:
        parts.append(std_types.SimpleNamespace(text=thought, thought=True))
    parts.append(std_types.SimpleNamespace(text=text, thought=False))
    return std_types.SimpleNamespace(
        candidates=[
            std_types.SimpleNamespace(
                content=std_types.SimpleNamespace(parts=parts)
            )
        ],
        response_id=response_id,
        usage_metadata=std_types.SimpleNamespace(
            prompt_token_count=21,
            candidates_token_count=7,
            thoughts_token_count=3,
        ),
    )


class MutableClock:
    def __init__(self):
        self.now = datetime(2026, 7, 19, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class GeminiDemoChatServiceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, outcomes, **changes):
        options = {
            "api_key": "secret",
            "model": "gemini-3.5-flash-lite",
            "thinking_level": "medium",
            "client": FakeClient(outcomes),
            "timeout_seconds": 2,
        }
        options.update(changes)
        return GeminiDemoChatService(**options)

    async def test_sends_medium_thinking_bounded_request_and_keeps_successful_history(self):
        service = self.service(
            [response("Первый ответ"), response("Ответ с учётом первого хода")]
        )

        with self.assertLogs("builder_lab.chat", level="INFO") as logs:
            first = await service.reply(
                scope="demo:artifact-a",
                session_id="session-a",
                request_id="request-0001",
                text="Какие проекты вы делаете?",
                system_prompt="Отвечай только по фактам архитектурного бюро.",
            )
        second = await service.reply(
            scope="demo:artifact-a",
            session_id="session-a",
            request_id="request-0002",
            text="А что было в моём первом вопросе?",
            system_prompt="Отвечай только по фактам архитектурного бюро.",
        )

        self.assertEqual(first.text, "Первый ответ")
        self.assertEqual(first.usage.prompt_tokens, 21)
        self.assertEqual(first.usage.output_tokens, 7)
        self.assertEqual(first.usage.thinking_tokens, 3)
        self.assertEqual(second.text, "Ответ с учётом первого хода")
        self.assertNotIn("скрыто", first.text)
        audit = "\n".join(logs.output)
        self.assertRegex(audit, r"session_hash=[0-9a-f]{16}")
        self.assertIn("history_before=0", audit)
        self.assertIn("history_after=2", audit)
        self.assertIn("prompt_tokens=21", audit)
        self.assertIn("output_tokens=7", audit)
        self.assertIn("thinking_tokens=3", audit)
        self.assertNotIn("Какие проекты", audit)
        self.assertNotIn("Отвечай только", audit)
        self.assertNotIn("generativelanguage", audit)
        call = service._client.models.calls[0]
        config = call["config"]
        self.assertEqual(call["model"], "gemini-3.5-flash-lite")
        self.assertIsNone(config.temperature)
        self.assertIsNone(config.top_p)
        self.assertEqual(config.max_output_tokens, 384)
        self.assertIn("MEDIUM", str(config.thinking_config.thinking_level).upper())
        self.assertFalse(config.tools)
        second_contents = service._client.models.calls[1]["contents"]
        rendered = "\n".join(
            part.text
            for item in second_contents
            for part in item.parts
            if getattr(part, "text", None)
        )
        self.assertIn("Какие проекты вы делаете?", rendered)
        self.assertIn("Первый ответ", rendered)

    async def test_gemini_2_5_omits_unsupported_thinking_level(self):
        service = self.service([response("OK")], model="gemini-2.5-flash")

        await service.reply(
            scope="demo:artifact-25",
            session_id="session-25",
            request_id="request-2501",
            text="Hello",
            system_prompt="Answer briefly.",
        )

        thinking = service._client.models.calls[0]["config"].thinking_config
        self.assertIsNone(thinking.thinking_level)
        self.assertEqual(thinking.thinking_budget, 0)
        self.assertFalse(thinking.include_thoughts)

    async def test_public_errors_are_readable_utf8_without_mojibake(self):
        with self.assertRaises(ChatServiceError) as missing_key:
            GeminiDemoChatService(api_key=None)
        message = missing_key.exception.public_message
        self.assertEqual(message, "Для Gemini-чата не настроен API-ключ")
        self.assertFalse(any(marker in message for marker in MOJIBAKE_MARKERS))

        service = self.service([RuntimeError("provider down")])
        with self.assertRaises(ChatServiceError) as unavailable:
            await service.reply(
                scope="demo:artifact-a",
                session_id="session-a",
                request_id="request-readable",
                text="Проверка читаемой ошибки",
                system_prompt="Отвечай кратко.",
            )
        self.assertIn("Не удалось получить ответ Gemini", unavailable.exception.public_message)
        self.assertFalse(
            any(
                marker in unavailable.exception.public_message
                for marker in MOJIBAKE_MARKERS
            )
        )

    async def test_provider_failure_does_not_commit_and_same_request_can_retry(self):
        service = self.service(
            [RuntimeError("provider down"), response("Восстановленный ответ")]
        )
        kwargs = dict(
            scope="demo:artifact-a",
            session_id="session-a",
            request_id="request-retry",
            text="Текст для повторной отправки",
            system_prompt="Проверенный системный prompt.",
        )

        with self.assertRaises(ChatServiceError) as caught:
            await service.reply(**kwargs)
        self.assertEqual(caught.exception.code, "chat_provider_unavailable")
        self.assertEqual(await service.history_for("demo:artifact-a", "session-a"), ())

        result = await service.reply(**kwargs)
        self.assertEqual(result.text, "Восстановленный ответ")
        self.assertEqual(len(service._client.models.calls), 2)
        cached = await service.reply(**kwargs)
        self.assertEqual(cached, result)
        self.assertEqual(len(service._client.models.calls), 2)
        with self.assertRaises(ChatServiceError) as conflict:
            await service.reply(**{**kwargs, "text": "Другой текст"})
        self.assertEqual(conflict.exception.code, "chat_request_conflict")

    async def test_one_in_flight_per_session_but_same_request_is_idempotent(self):
        release = asyncio.Event()
        service = self.service([release])
        kwargs = dict(
            scope="run:abc:2",
            session_id="session-a",
            request_id="request-shared",
            text="Первый вопрос",
            system_prompt="Системный prompt.",
        )
        first = asyncio.create_task(service.reply(**kwargs))
        await asyncio.sleep(0)
        same = asyncio.create_task(service.reply(**kwargs))
        await asyncio.sleep(0)

        with self.assertRaises(ChatServiceError) as busy:
            await service.reply(
                **{**kwargs, "request_id": "request-other", "text": "Второй вопрос"}
            )
        self.assertEqual(busy.exception.code, "chat_busy")
        release.set()
        self.assertEqual((await first).text, "Ответ после ожидания")
        self.assertEqual((await same).text, "Ответ после ожидания")
        self.assertEqual(len(service._client.models.calls), 1)

    async def test_history_is_bounded_to_eight_successful_pairs(self):
        service = self.service(
            [response(f"Ответ {index}") for index in range(9)],
            max_requests_per_session=20,
        )
        with self.assertLogs("builder_lab.chat", level="INFO") as logs:
            for index in range(9):
                await service.reply(
                    scope="demo:artifact-a",
                    session_id="session-a",
                    request_id=f"request-{index:04d}",
                    text=f"Вопрос {index}",
                    system_prompt="Системный prompt.",
                )
        history = await service.history_for("demo:artifact-a", "session-a")
        self.assertEqual(len(history), 16)
        self.assertEqual(history[0], ("user", "Вопрос 1"))
        self.assertEqual(history[-1], ("model", "Ответ 8"))
        self.assertIn("history_after=16", logs.output[-1])

    async def test_validates_text_and_enforces_rate_budget_ttl_and_capacity(self):
        clock = MutableClock()
        service = self.service(
            [response("A"), response("B"), response("C")],
            clock=clock,
            max_sessions=1,
            session_ttl_seconds=60,
            rate_limit_requests=1,
            rate_limit_window_seconds=10,
            max_requests_per_session=2,
        )
        base = dict(
            scope="demo:a",
            session_id="session-a",
            system_prompt="Системный prompt.",
        )
        for invalid in ("", " " * 4, "x" * 1001):
            with self.assertRaises(ChatServiceError) as caught:
                await service.reply(
                    **base, request_id="request-invalid", text=invalid
                )
            self.assertEqual(caught.exception.code, "invalid_chat_request")

        await service.reply(**base, request_id="request-1", text="Первый")
        with self.assertRaises(ChatServiceError) as rate:
            await service.reply(**base, request_id="request-2", text="Второй")
        self.assertEqual(rate.exception.code, "chat_rate_limited")
        clock.advance(11)
        await service.reply(**base, request_id="request-2", text="Второй")
        clock.advance(11)
        with self.assertRaises(ChatServiceError) as budget:
            await service.reply(**base, request_id="request-3", text="Третий")
        self.assertEqual(budget.exception.code, "chat_budget_exhausted")

        with self.assertRaises(ChatServiceError) as capacity:
            await service.reply(
                scope="demo:b",
                session_id="session-b",
                request_id="request-b",
                text="Другой диалог",
                system_prompt="Системный prompt.",
            )
        self.assertEqual(capacity.exception.code, "chat_capacity")
        clock.advance(61)
        result = await service.reply(
            scope="demo:b",
            session_id="session-b",
            request_id="request-b",
            text="Другой диалог",
            system_prompt="Системный prompt.",
        )
        self.assertEqual(result.text, "C")

    async def test_scopes_isolate_histories_for_the_same_cookie_session(self):
        service = self.service([response("Demo"), response("Run")])
        common = dict(
            session_id="same-cookie",
            system_prompt="Системный prompt.",
        )
        await service.reply(
            **common,
            scope="demo:artifact",
            request_id="request-demo",
            text="Demo question",
        )
        await service.reply(
            **common,
            scope="run:id:2",
            request_id="request-run",
            text="Run question",
        )
        run_call = service._client.models.calls[1]
        rendered = "\n".join(
            part.text
            for item in run_call["contents"]
            for part in item.parts
            if getattr(part, "text", None)
        )
        self.assertNotIn("Demo question", rendered)
        self.assertNotIn("Demo", rendered)

    async def test_ip_rate_limit_spans_cookie_sessions_without_exposing_raw_ip(self):
        clock = MutableClock()
        service = self.service(
            [response("A"), response("B")],
            clock=clock,
            rate_limit_requests=10,
            ip_rate_limit_requests=1,
            rate_limit_window_seconds=60,
        )
        common = dict(
            scope="demo:artifact",
            system_prompt="Системный prompt.",
            client_id="iphash-aabbccddeeff0011",
        )
        await service.reply(
            **common,
            session_id="session-one",
            request_id="request-ip-0001",
            text="Первый",
        )
        with self.assertRaises(ChatServiceError) as limited:
            await service.reply(
                **common,
                session_id="session-two",
                request_id="request-ip-0002",
                text="Второй с новой cookie",
            )
        self.assertEqual(limited.exception.code, "chat_rate_limited")
        result = await service.reply(
            **{**common, "client_id": "iphash-1122334455667788"},
            session_id="session-two",
            request_id="request-ip-0002",
            text="Второй с новой cookie",
        )
        self.assertEqual(result.text, "B")

    async def test_ip_limited_cookie_rotation_does_not_consume_session_capacity(self):
        service = self.service(
            [response("First"), response("Legitimate")],
            max_sessions=2,
            rate_limit_requests=10,
            ip_rate_limit_requests=1,
        )
        common = dict(
            scope="demo:artifact",
            system_prompt="Системный prompt.",
            client_id="iphash-aabbccddeeff0011",
        )
        await service.reply(
            **common,
            session_id="session-one",
            request_id="request-ip-fill-1",
            text="Первый",
        )
        for index in range(2):
            with self.assertRaises(ChatServiceError) as limited:
                await service.reply(
                    **common,
                    session_id=f"rotated-session-{index}",
                    request_id=f"request-ip-fill-{index + 2}",
                    text="Попытка обойти лимит cookie",
                )
            self.assertEqual(limited.exception.code, "chat_rate_limited")
        self.assertEqual(len(service._sessions), 1)

        result = await service.reply(
            **{**common, "client_id": "iphash-1122334455667788"},
            session_id="legitimate-session",
            request_id="request-legitimate",
            text="Разрешённый запрос",
        )
        self.assertEqual(result.text, "Legitimate")


if __name__ == "__main__":
    unittest.main()
