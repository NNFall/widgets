import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import scripts.smoke_builder_lab as smoke_script
from scripts.smoke_builder_lab import (
    REQUIRED_VISUAL_SCREENSHOT_IDS,
    build_preview_smoke_url,
    parse_args,
    validate_real_chat_evidence,
    validate_smoke_evidence,
)


def event(event_type, revision=None):
    return {"type": event_type, "revision": revision}


def snapshot(*, total_tokens=25):
    return {
        "status": "completed",
        "error_code": None,
        "usage": {"total_tokens": total_tokens},
        "artifact": {"revision": 5, "stage": "motion_polish"},
    }


def visual_event(
    event_type,
    *,
    screenshot_id=None,
    total_tokens=0,
    status="completed",
    revision=5,
):
    message = event_type
    if screenshot_id is not None:
        message = f"Снимок визуальной проверки: {screenshot_id} (1200 bytes)"
    return {
        "type": event_type,
        "revision": revision,
        "status": status,
        "message": message,
        "usage": {"total_tokens": total_tokens},
    }


def passing_visual_tail():
    visual = [visual_event("visual_audit.started", status="running")]
    visual.extend(
        visual_event("screenshot.captured", screenshot_id=screenshot_id)
        for screenshot_id in REQUIRED_VISUAL_SCREENSHOT_IDS
    )
    visual.extend(
        [
            visual_event("visual_audit.completed", total_tokens=41),
            visual_event("visual_audit.passed"),
            visual_event("artifact.committed"),
            visual_event("run.completed"),
        ]
    )
    return visual


class FakeChatResponse:
    def __init__(self, payload, *, cookie_header=""):
        self._payload = payload
        self.request = SimpleNamespace(headers={"Cookie": cookie_header})

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeDemoChatClient:
    def __init__(self):
        self.calls = []
        self.cookies = SimpleNamespace(jar=[])
        self.history_marker = None

    async def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        if self.history_marker is None:
            match = re.search(r"context-[0-9a-f]+", json["message"])
            self.history_marker = match.group(0)
            self.cookies.jar.append(
                SimpleNamespace(name="kaigo_chat_session", value="signed-session")
            )
            reply = "Первый опубликованный ответ"
            cookie_header = ""
        else:
            reply = f"Второй опубликованный ответ {self.history_marker}"
            cookie_header = "kaigo_chat_session=signed-session"
        return FakeChatResponse(
            {"request_id": json["request_id"], "reply": reply},
            cookie_header=cookie_header,
        )


class SmokeEvidenceTests(unittest.TestCase):
    def test_cli_reads_utf8_brief_file_and_preserves_legacy_inline_brief(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            brief_path = Path(temp_dir) / "brief.txt"
            brief_path.write_text("Создай виджет для RAW BUREAU", encoding="utf-8")

            args = parse_args(["--brief-file", str(brief_path)])
            self.assertEqual(args.brief, "Создай виджет для RAW BUREAU")
            self.assertEqual(parse_args(["--brief", "legacy brief"]).brief, "legacy brief")

            with self.assertRaises(SystemExit):
                parse_args(
                    [
                        "--brief",
                        "inline",
                        "--brief-file",
                        str(brief_path),
                    ]
                )

    def test_cli_exposes_strict_require_flags_and_chat_prompt_alias(self):
        args = parse_args(
            [
                "--require-real-chat",
                "--require-visual-audit",
                "--chat-prompt-file",
                "chat-prompt.txt",
            ]
        )
        self.assertTrue(args.real_chat)
        self.assertTrue(args.require_visual_audit)
        self.assertEqual(args.chat_system_prompt_file, Path("chat-prompt.txt"))

        legacy = parse_args(
            ["--real-chat", "--chat-system-prompt-file", "legacy-prompt.txt"]
        )
        self.assertTrue(legacy.real_chat)
        self.assertFalse(legacy.require_visual_audit)
        self.assertEqual(
            legacy.chat_system_prompt_file,
            Path("legacy-prompt.txt"),
        )

    def test_preflight_loads_only_bounded_analysis_from_reference_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reference.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "kaigo.reference.v1",
                        "source": {"url": "https://rawbureau.ru/"},
                        "analysis": {
                            "visual_summary": "sharp monochrome grid",
                            "public_facts": [],
                            "visual_tokens": {"palette": [], "typography": [], "geometry": [], "motion": []},
                        },
                        "screenshots": [{"private_path": "/must/not/pass"}],
                    }
                ),
                encoding="utf-8",
            )

            args = parse_args(["--reference-json", str(path)])
            smoke_script.preflight_smoke_args(args)

            self.assertIn("sharp monochrome grid", args.reference_context)
            self.assertNotIn("private_path", args.reference_context)
            self.assertNotIn("/must/not/pass", args.reference_context)

    def test_preflight_rejects_ignored_or_incomplete_demo_grounding(self):
        invalid_argv = (
            ["--source-url", "https://rawbureau.ru/"],
            ["--chat-prompt-file", "missing.txt"],
            ["--demo-output", "latest.json", "--source-url", "https://rawbureau.ru/"],
            ["--require-real-chat"],
        )
        for argv in invalid_argv:
            with self.subTest(argv=argv):
                with self.assertRaises(ValueError):
                    smoke_script.preflight_smoke_args(parse_args(argv))

        for argv in (
            ["--chat-question-one", "Этот вопрос иначе игнорируется"],
            ["--chat-question-two", "Этот вопрос тоже игнорируется"],
            ["--model", "gemini-explicit-but-ignored"],
        ):
            with self.subTest(ignored=argv):
                with self.assertRaisesRegex(ValueError, "ignored"):
                    smoke_script.preflight_smoke_args(parse_args(argv))

    def test_preflight_reads_strict_bounded_utf8_before_paid_post(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prompt_path = root / "prompt.txt"
            common = [
                "--demo-output",
                str(root / "latest.json"),
                "--source-url",
                "https://rawbureau.ru/",
                "--chat-prompt-file",
                str(prompt_path),
            ]
            for invalid in (b"\xff\xfe", b"prompt\x00suffix", b"x" * 16_001):
                prompt_path.write_bytes(invalid)
                with self.subTest(size=len(invalid)):
                    with self.assertRaises(ValueError):
                        args = parse_args(common)
                        with patch.object(
                            smoke_script.httpx,
                            "AsyncClient",
                            side_effect=AssertionError("paid HTTP client was created"),
                        ):
                            asyncio.run(smoke_script.run_smoke(args))

            prompt_path.write_text("Проверенный системный промпт", encoding="utf-8")
            args = parse_args(common + ["--require-real-chat"])
            self.assertEqual(
                smoke_script.preflight_smoke_args(args),
                "Проверенный системный промпт",
            )

            invalid_source = list(common)
            invalid_source[invalid_source.index("https://rawbureau.ru/")] = (
                "http://127.0.0.1/private"
            )
            with self.assertRaisesRegex(ValueError, "source URL"):
                args = parse_args(invalid_source)
                with patch.object(
                    smoke_script.httpx,
                    "AsyncClient",
                    side_effect=AssertionError("paid HTTP client was created"),
                ):
                    asyncio.run(smoke_script.run_smoke(args))

    def test_real_chat_smoke_uses_published_demo_endpoint_for_both_turns(self):
        client = FakeDemoChatClient()
        replies = asyncio.run(
            smoke_script.run_real_chat_smoke(
                client,
                base_url="https://widgets.example",
                revision=5,
                questions=("Первый вопрос", "Второй вопрос"),
            )
        )
        self.assertEqual(len(replies), 2)
        self.assertEqual(
            [url for url, _payload, _headers in client.calls],
            [
                "https://widgets.example/demo/chat",
                "https://widgets.example/demo/chat",
            ],
        )

    def test_demo_restore_is_atomic_and_does_not_clobber_concurrent_publication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "latest.json"
            with smoke_script.DemoPublicationLock(path) as publication_lock:
                with self.assertRaisesRegex(RuntimeError, "publication lock"):
                    with smoke_script.DemoPublicationLock(path):
                        pass
                path.write_bytes(b"previous-demo")
                path.write_bytes(b"failed-candidate")
                smoke_script.restore_demo_publication(
                    path,
                    previous_bytes=b"previous-demo",
                    published_bytes=b"failed-candidate",
                    publication_lock=publication_lock,
                )
            self.assertEqual(path.read_bytes(), b"previous-demo")

            with smoke_script.DemoPublicationLock(path) as publication_lock:
                path.write_bytes(b"newer-concurrent-demo")
                smoke_script.restore_demo_publication(
                    path,
                    previous_bytes=b"previous-demo",
                    published_bytes=b"failed-candidate",
                    publication_lock=publication_lock,
                )
            self.assertEqual(path.read_bytes(), b"newer-concurrent-demo")

    def test_preview_probe_supplies_revision_and_protocol_channel(self):
        url = build_preview_smoke_url(
            "https://example.test/lab", "run-123", 5,
            channel="channel-1234567890abcdef",
        )
        parsed = urlsplit(url)
        self.assertEqual(parsed.path, "/lab/api/runs/run-123/preview")
        self.assertEqual(
            parse_qs(parsed.query),
            {"revision": ["5"], "channel": ["channel-1234567890abcdef"]},
        )

    def test_direct_requires_at_least_four_committed_revisions(self):
        events = [event("artifact.committed", revision=value) for value in (1, 2, 3)]
        events.append(event("run.completed", revision=3))
        with self.assertRaisesRegex(RuntimeError, "committed revisions"):
            validate_smoke_evidence("direct", events, snapshot())

    def test_requires_positive_provider_usage(self):
        events = [event("artifact.committed", revision=value) for value in range(1, 6)]
        events.append(event("run.completed", revision=5))
        with self.assertRaisesRegex(RuntimeError, "token usage"):
            validate_smoke_evidence("direct", events, snapshot(total_tokens=0))

    def test_accepts_complete_direct_evidence(self):
        events = [event("artifact.committed", revision=value) for value in range(1, 6)]
        events.append(event("run.completed", revision=5))
        validate_smoke_evidence("direct", events, snapshot())

    def test_required_visual_audit_rejects_generation_only_success(self):
        events = [event("artifact.committed", revision=value) for value in range(1, 6)]
        events.append(event("run.completed", revision=5))
        with self.assertRaisesRegex(RuntimeError, "visual audit passed"):
            validate_smoke_evidence(
                "direct",
                events,
                snapshot(),
                require_visual_audit=True,
            )

    def test_required_visual_audit_requires_exact_final_screenshots_and_critic_usage(self):
        prefix = [event("artifact.committed", revision=value) for value in range(1, 5)]
        visual = passing_visual_tail()
        validate_smoke_evidence(
            "direct",
            prefix + visual,
            snapshot(),
            require_visual_audit=True,
        )

        missing_screenshot = prefix + [visual[0]] + visual[2:]
        with self.assertRaisesRegex(RuntimeError, "six required screenshots"):
            validate_smoke_evidence(
                "direct",
                missing_screenshot,
                snapshot(),
                require_visual_audit=True,
            )

        zero_usage = [
            {**item, "usage": {"total_tokens": 0}}
            if item.get("type") == "visual_audit.completed"
            else item
            for item in prefix + visual
        ]
        with self.assertRaisesRegex(RuntimeError, "critic token usage"):
            validate_smoke_evidence(
                "direct",
                zero_usage,
                snapshot(),
                require_visual_audit=True,
            )

        non_exact_id = [dict(item) for item in prefix + visual]
        first_screenshot = next(
            item for item in non_exact_id if item.get("type") == "screenshot.captured"
        )
        first_screenshot["message"] = first_screenshot["message"].replace(
            "desktop.closed", "desktop.closed.NOT_EXACT"
        )
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_smoke_evidence(
                "direct",
                non_exact_id,
                snapshot(),
                require_visual_audit=True,
            )

    def test_required_visual_audit_rejects_out_of_order_evidence(self):
        events = [event("artifact.committed", revision=value) for value in range(1, 5)]
        events.extend(
            [
                visual_event("visual_audit.started", status="running"),
                visual_event("visual_audit.completed", total_tokens=41),
                *(
                    visual_event("screenshot.captured", screenshot_id=screenshot_id)
                    for screenshot_id in REQUIRED_VISUAL_SCREENSHOT_IDS
                ),
                visual_event("visual_audit.passed"),
                visual_event("artifact.committed"),
                visual_event("run.completed"),
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "ordered visual audit evidence"):
            validate_smoke_evidence(
                "direct",
                events,
                snapshot(),
                require_visual_audit=True,
            )

    def test_required_visual_audit_rejects_stale_pass_or_wrong_started_revision(self):
        prefix = [event("artifact.committed", revision=value) for value in range(1, 5)]
        stale = prefix + passing_visual_tail()[:-1]
        stale.extend(
            [
                visual_event("visual_audit.started", status="running"),
                visual_event("run.completed"),
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "final event tail"):
            validate_smoke_evidence(
                "direct",
                stale,
                snapshot(),
                require_visual_audit=True,
            )

        wrong_started = prefix + passing_visual_tail()
        wrong_started[len(prefix)] = visual_event(
            "visual_audit.started",
            status="completed",
            revision=4,
        )
        with self.assertRaisesRegex(RuntimeError, "started evidence"):
            validate_smoke_evidence(
                "direct",
                wrong_started,
                snapshot(),
                require_visual_audit=True,
            )

    def test_real_chat_requires_two_correlated_distinct_assistant_replies(self):
        valid = [
            {"request_id": "request-chat-1", "reply": "Первый ответ"},
            {"request_id": "request-chat-2", "reply": "Второй ответ с контекстом"},
        ]
        validate_real_chat_evidence(
            valid, session_cookie="session-cookie", session_cookie_sent=True
        )
        validate_real_chat_evidence(
            [valid[0], {**valid[1], "reply": valid[1]["reply"] + " context-7af31b"}],
            session_cookie="session-cookie",
            session_cookie_sent=True,
            history_marker="context-7af31b",
        )
        with self.assertRaisesRegex(RuntimeError, "history"):
            validate_real_chat_evidence(
                valid,
                session_cookie="session-cookie",
                session_cookie_sent=True,
                history_marker="context-7af31b",
            )

        for invalid in (
            valid[:1],
            [valid[0], {"request_id": "request-chat-2", "reply": "Первый ответ"}],
            [valid[0], {"request_id": "request-chat-2", "reply": ""}],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(RuntimeError, "chat"):
                    validate_real_chat_evidence(
                        invalid,
                        session_cookie="session-cookie",
                        session_cookie_sent=True,
                    )
        with self.assertRaisesRegex(RuntimeError, "session"):
            validate_real_chat_evidence(
                valid, session_cookie=None, session_cookie_sent=False
            )
        with self.assertRaisesRegex(RuntimeError, "not sent"):
            validate_real_chat_evidence(
                valid,
                session_cookie="session-cookie",
                session_cookie_sent=False,
            )


if __name__ == "__main__":
    unittest.main()
