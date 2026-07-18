import json
import tempfile
import unittest
from pathlib import Path

from builder_lab.demo import DemoUnavailable, load_demo, save_demo
from builder_lab.models import BuilderRequest, EngineName
from tests.builder_lab_cases.test_validation import artifact


def completed_snapshot(**changes):
    payload = {
        "status": "completed",
        "request": BuilderRequest(
            engine=EngineName.DIRECT,
            brief="Премиальный AI-куратор архитектурного бюро",
            creativity=0.9,
        ).to_dict(),
        "artifact": artifact().to_dict(),
        "usage": {
            "prompt_tokens": 120,
            "output_tokens": 80,
            "thinking_tokens": 20,
            "total_tokens": 220,
        },
        "elapsed_seconds": 12.5,
        "provider_diagnostic": "secret provider detail",
    }
    payload.update(changes)
    return payload


class BuilderDemoTests(unittest.TestCase):
    def test_saves_and_loads_only_safe_completed_demo_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            save_demo(path, completed_snapshot(), model="gemini-3.5-flash")
            demo = load_demo(path)

            self.assertEqual(demo.model, "gemini-3.5-flash")
            self.assertEqual(demo.request.brief, "Премиальный AI-куратор архитектурного бюро")
            self.assertEqual(demo.artifact.revision, 2)
            self.assertEqual(demo.usage.total_tokens, 220)
            self.assertNotIn("provider_diagnostic", path.read_text(encoding="utf-8"))
            self.assertNotIn("secret provider detail", path.read_text(encoding="utf-8"))

    def test_rejects_unfinished_or_missing_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            with self.assertRaises(DemoUnavailable):
                save_demo(path, completed_snapshot(status="running"), model="gemini-3.5-flash")
            with self.assertRaises(DemoUnavailable):
                save_demo(path, completed_snapshot(artifact=None), model="gemini-3.5-flash")
            self.assertFalse(path.exists())

    def test_revalidates_loaded_artifact_and_rejects_corrupt_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            bad = completed_snapshot()
            bad["artifact"]["body_html"] += '<script>alert("bad")</script>'
            document = {
                "schema_version": 1,
                "generated_at": "2026-07-18T12:00:00+00:00",
                "model": "gemini-3.5-flash",
                "request": bad["request"],
                "usage": bad["usage"],
                "elapsed_seconds": bad["elapsed_seconds"],
                "artifact": bad["artifact"],
            }
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(DemoUnavailable):
                load_demo(path)

            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(DemoUnavailable):
                load_demo(path)


if __name__ == "__main__":
    unittest.main()
