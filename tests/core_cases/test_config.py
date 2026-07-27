from __future__ import annotations

from core.config import AppSettings


def test_core_settings_have_no_default_output_token_cap() -> None:
    settings = AppSettings()

    assert not hasattr(settings, "default_max_tokens")
