"""Build the public, redacted Agent Kernel model comparison package."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODELS = ("glm-5.2", "gpt-5.5")
SCREENSHOTS = (
    "desktop-after_turn_2.jpg",
    "desktop-closed.jpg",
    "desktop-open_initial.jpg",
    "mobile-after_turn_2.jpg",
    "mobile-closed.jpg",
    "mobile-open_initial.jpg",
)


@dataclass(frozen=True)
class ComparisonInputs:
    report: Path
    private_root: Path


def _load_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("benchmark report schema is invalid")
    runs = report.get("runs")
    if not isinstance(runs, list):
        raise ValueError("benchmark report runs are invalid")
    models = tuple(run.get("model") for run in runs if isinstance(run, dict))
    if models != MODELS:
        raise ValueError("benchmark must contain exactly glm-5.2 and gpt-5.5")
    return report


def _copy_public_files(private_root: Path, output: Path) -> None:
    for model in MODELS:
        source_root = private_root / model
        preview = source_root / "preview.html"
        if not preview.is_file() or preview.is_symlink():
            raise ValueError(f"missing safe preview for {model}")
        preview_target = output / model / "index.html"
        preview_target.parent.mkdir(parents=True)
        shutil.copyfile(preview, preview_target)

        screenshot_target = output / "assets" / model
        screenshot_target.mkdir(parents=True)
        for name in SCREENSHOTS:
            screenshot = source_root / "browser-screenshots" / name
            if not screenshot.is_file() or screenshot.is_symlink():
                raise ValueError(f"missing safe screenshot for {model}: {name}")
            shutil.copyfile(screenshot, screenshot_target / name)


def _render_page(report: dict[str, Any]) -> str:
    models = ", ".join(str(run["model"]) for run in report["runs"])
    return (
        "<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\">"
        "<title>Kaigo — сравнение моделей</title>"
        f"<main><h1>Сравнение {models}</h1>"
        "<p>Публичный пакет содержит только проверенные preview и screenshots.</p>"
        "</main></html>"
    )


def build_public_comparison(inputs: ComparisonInputs, output: Path) -> Path:
    report = _load_report(inputs.report)
    private_root = inputs.private_root.resolve()
    destination = output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to replace existing package: {destination}")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        _copy_public_files(private_root, temporary)
        (temporary / "index.html").write_text(
            _render_page(report), encoding="utf-8", newline="\n"
        )
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


__all__ = ["ComparisonInputs", "build_public_comparison"]
