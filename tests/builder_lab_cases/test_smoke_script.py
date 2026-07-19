import unittest
from urllib.parse import parse_qs, urlsplit

from scripts.smoke_builder_lab import (
    build_preview_smoke_url,
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


class SmokeEvidenceTests(unittest.TestCase):
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
