import json
import tempfile
import unittest
from pathlib import Path

from builder_lab.demo import DemoUnavailable, load_demo, render_demo_page, save_demo
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
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], 1)
            self.assertNotIn("chat_system_prompt", persisted)
            self.assertNotIn("source_url", persisted)
            self.assertFalse(demo.chat_enabled)
            self.assertNotIn("provider_diagnostic", path.read_text(encoding="utf-8"))
            self.assertNotIn("secret provider detail", path.read_text(encoding="utf-8"))

    def test_v2_persists_server_only_grounding_and_artifact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            demo = save_demo(
                path,
                completed_snapshot(),
                model="gemini-3.5-flash",
                source_url="https://rawbureau.ru/",
                chat_system_prompt="Отвечай только по проверенным фактам RAW BUREAU.",
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))
            page = render_demo_page(demo)

        self.assertEqual(persisted["schema_version"], 2)
        self.assertEqual(demo.source_url, "https://rawbureau.ru/")
        self.assertTrue(demo.chat_enabled)
        self.assertRegex(demo.artifact_identity, r"^[0-9a-f]{64}$")
        self.assertEqual(persisted["artifact_identity"], demo.artifact_identity)
        self.assertNotIn(demo.chat_system_prompt, page)
        self.assertNotIn("Отвечай только", page)
        self.assertNotIn(demo.source_url, page)
        self.assertNotIn("rawbureau.ru", page)
        self.assertIn('id="demo-preview"', page)
        self.assertNotIn('src="preview"', page)
        self.assertIn("new URL('chat',demoBase)", page)
        self.assertIn("data.version!==2", page)
        self.assertIn("event.source!==frame.contentWindow", page)

    def test_reads_v1_as_visual_only_and_does_not_invent_chat_grounding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            payload = {
                "schema_version": 1,
                "generated_at": "2026-07-18T12:00:00+00:00",
                "model": "gemini-3.5-flash",
                "request": completed_snapshot()["request"],
                "usage": completed_snapshot()["usage"],
                "elapsed_seconds": 12.5,
                "artifact": completed_snapshot()["artifact"],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            demo = load_demo(path)
            page = render_demo_page(demo)

        self.assertEqual(demo.schema_version, 1)
        self.assertFalse(demo.chat_enabled)
        self.assertIsNone(demo.source_url)
        self.assertIsNone(demo.chat_system_prompt)
        self.assertIn("только визуальный preview", page.lower())

    def test_rejects_unverified_or_incomplete_v2_chat_grounding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            for source_url in (
                "http://rawbureau.ru/",
                "https://localhost/",
                "https://127.0.0.1/",
                "https://user:pass@rawbureau.ru/",
                "https://rawbureau.ru/?token=secret",
                "https://bad host/",
            ):
                with self.subTest(source_url=source_url):
                    with self.assertRaises(DemoUnavailable):
                        save_demo(
                            path,
                            completed_snapshot(),
                            model="gemini-3.5-flash",
                            source_url=source_url,
                            chat_system_prompt="Проверенный prompt.",
                        )
            with self.assertRaises(DemoUnavailable):
                save_demo(
                    path,
                    completed_snapshot(),
                    model="gemini-3.5-flash",
                    source_url="https://rawbureau.ru/",
                )

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

    def test_rendered_demo_centers_widget_and_collapses_evidence_without_fixed_legacy_rail(self):
        with tempfile.TemporaryDirectory() as directory:
            demo = save_demo(
                Path(directory) / "latest.json",
                completed_snapshot(),
                model="gemini-3.5-flash",
            )
            page = render_demo_page(demo)

        self.assertIn("<details", page)
        self.assertIn("Основной интерактивный объект", page)
        self.assertNotIn("min-height:760px", page)
        self.assertNotIn("min-height:680px", page)
        self.assertNotIn("height:760px", page)
        self.assertNotIn("height:680px", page)


if __name__ == "__main__":
    unittest.main()
