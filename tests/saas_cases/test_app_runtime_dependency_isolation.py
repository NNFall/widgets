from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_production_app_import_does_not_require_builder_browser_dependencies() -> None:
    script = """
import importlib.abc
import sys

class BlockBuilderBrowserDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (
            fullname == "PIL"
            or fullname.startswith("PIL.")
            or fullname == "crawlee"
            or fullname.startswith("crawlee.")
        ):
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockBuilderBrowserDependencies())
import app.server
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
