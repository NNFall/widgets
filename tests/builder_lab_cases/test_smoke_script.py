import unittest

from scripts.smoke_builder_lab import validate_smoke_evidence


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


if __name__ == "__main__":
    unittest.main()
