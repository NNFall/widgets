from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cleanup_generation_forensics.py"


def test_cleanup_cli_help_is_lazy_and_requires_no_runtime_secrets() -> None:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name == "DATABASE_URL" or name.startswith("KAIGO_GENERATION_FORENSICS_"):
            environment.pop(name)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=SCRIPT.parents[1],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "--dry-run" in result.stdout
    assert "--batch-size" in result.stdout
    assert "DATABASE_URL" not in result.stderr


def test_cleanup_cli_rejects_naive_now_before_loading_runtime_config() -> None:
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--now", "2026-07-30T12:00:00"],
        cwd=SCRIPT.parents[1],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert "timezone" in result.stderr.lower()
    assert "DATABASE_URL" not in result.stderr
