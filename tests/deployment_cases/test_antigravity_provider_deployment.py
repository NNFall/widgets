from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_builder_worker_receives_antigravity_api_configuration() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["builder-worker"]["environment"]

    assert environment["KAIGO_ANTIGRAVITY_API_ENABLED"] == (
        "${KAIGO_ANTIGRAVITY_API_ENABLED:-false}"
    )
    assert environment["KAIGO_ANTIGRAVITY_API_KEY"] == (
        "${KAIGO_ANTIGRAVITY_API_KEY:-}"
    )
    assert environment["KAIGO_ANTIGRAVITY_API_BASE_URL"] == (
        "${KAIGO_ANTIGRAVITY_API_BASE_URL:-https://kaigo.space/antigravity-api}"
    )
    assert environment["KAIGO_ANTIGRAVITY_API_TIMEOUT_SECONDS"] == (
        "${KAIGO_ANTIGRAVITY_API_TIMEOUT_SECONDS:-180}"
    )
    assert environment["KAIGO_ANTIGRAVITY_API_MODEL"] == (
        "${KAIGO_ANTIGRAVITY_API_MODEL:-gemini-3.7-flash-high}"
    )
    assert environment["KAIGO_ANTIGRAVITY_API_REASONING_EFFORT"] == (
        "${KAIGO_ANTIGRAVITY_API_REASONING_EFFORT:-high}"
    )


def test_environment_example_is_disabled_and_contains_no_antigravity_secret() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "KAIGO_ANTIGRAVITY_API_ENABLED=false" in example
    assert "KAIGO_ANTIGRAVITY_API_KEY=" in example
    assert (
        "KAIGO_ANTIGRAVITY_API_BASE_URL=https://kaigo.space/antigravity-api"
        in example
    )
    assert "KAIGO_ANTIGRAVITY_API_TIMEOUT_SECONDS=180" in example
    assert "KAIGO_ANTIGRAVITY_API_MODEL=gemini-3.7-flash-high" in example
    assert "KAIGO_ANTIGRAVITY_API_REASONING_EFFORT=high" in example
