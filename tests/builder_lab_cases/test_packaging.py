import unittest
from importlib.metadata import version
from pathlib import Path

from google import genai


ROOT = Path(__file__).resolve().parents[2]


class BuilderLabPackagingTests(unittest.TestCase):
    def test_compose_service_receives_only_explicit_builder_variables(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        builder = compose.split("  builder-lab:", 1)[1].split("\n  db:", 1)[0]
        self.assertNotIn("env_file:", builder)
        self.assertIn("GOOGLE_AI_API_KEY:", builder)
        self.assertIn("GOOGLE_AI_NATIVE_BASE_URL:", builder)
        self.assertNotIn("MESSAGE_DATABASE_URL", builder)
        self.assertNotIn("POSTGRES_", builder)

    def test_declared_sdk_floor_matches_interactions_contract(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("google-genai>=2.12.1,<3.0.0", requirements)
        installed = tuple(int(part) for part in version("google-genai").split(".")[:3])
        self.assertGreaterEqual(installed, (2, 12, 1))
        client = genai.Client(api_key="contract-test")
        try:
            self.assertTrue(callable(client.aio.interactions.create))
            self.assertTrue(callable(client.aio.interactions.get))
            self.assertTrue(callable(client.aio.interactions.cancel))
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
