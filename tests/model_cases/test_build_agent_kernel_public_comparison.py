from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_agent_kernel_public_comparison import (
    ComparisonInputs,
    build_public_comparison,
)


MODELS = ("glm-5.2", "gpt-5.5")
SCREENSHOTS = (
    "desktop-after_turn_2.jpg",
    "desktop-closed.jpg",
    "desktop-open_initial.jpg",
    "mobile-after_turn_2.jpg",
    "mobile-closed.jpg",
    "mobile-open_initial.jpg",
)


def _report(models: tuple[str, ...] = MODELS) -> dict[str, object]:
    records = {
        "glm-5.2": {
            "elapsed_seconds": 786.888,
            "usage": {
                "input_tokens": 220_294,
                "output_tokens": 50_334,
                "thinking_tokens": 0,
            },
            "cost_rub": 162.38,
            "retry_count": 4,
            "repair_count": 4,
            "browser_repair_count": 3,
            "visual_score": 0.616,
            "validator_passed": True,
            "browser_passed": False,
            "publishable": False,
            "failure_code": "invalid_response",
            "request_ids": ["private-glm-request"],
        },
        "gpt-5.5": {
            "elapsed_seconds": 1482.472,
            "usage": {
                "input_tokens": 109_645,
                "output_tokens": 34_783,
                "thinking_tokens": 1_654,
            },
            "cost_rub": 101.1,
            "retry_count": 1,
            "repair_count": 4,
            "browser_repair_count": 3,
            "visual_score": 0.772,
            "validator_passed": True,
            "browser_passed": False,
            "publishable": False,
            "failure_code": "provider_unavailable",
            "request_ids": ["private-gpt-request"],
        },
    }
    return {
        "schema_version": 1,
        "generated_at": "2026-07-29T01:03:52+00:00",
        "usd_to_rub": 100.0,
        "input_identity": {
            "evidence_sha256": "e" * 64,
            "composition_sha256": "c" * 64,
            "visual_review_sha256": "v" * 64,
        },
        "runs": [
            {
                "provider": "agentrouter",
                "model": model,
                **records.get(model, records["gpt-5.5"]),
            }
            for model in models
        ],
        "visual_evaluation": {
            "source": "codex_independent_visual_review",
            "rubric": "kaigo-strict-visual-v1",
        },
    }


def _inputs(
    tmp_path: Path, *, models: tuple[str, ...] = MODELS
) -> ComparisonInputs:
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(_report(models), ensure_ascii=False), encoding="utf-8"
    )
    private_root = tmp_path / "private"
    for model in MODELS:
        model_root = private_root / model
        screenshot_root = model_root / "browser-screenshots"
        screenshot_root.mkdir(parents=True)
        (model_root / "preview.html").write_text(
            f"<!doctype html><title>{model}</title><button>Открыть</button>",
            encoding="utf-8",
        )
        (model_root / "technical.json").write_text(
            '{"request_ids":["must-not-leak"]}', encoding="utf-8"
        )
        (model_root / "unrelated.db").write_bytes(b"private database")
        for screenshot in SCREENSHOTS:
            (screenshot_root / screenshot).write_bytes(
                f"{model}:{screenshot}".encode()
            )
    return ComparisonInputs(report=report_path, private_root=private_root)


def _expected_public_files() -> set[str]:
    files = {"index.html"}
    for model in MODELS:
        files.add(f"{model}/index.html")
        files.update(f"assets/{model}/{name}" for name in SCREENSHOTS)
    return files


def test_build_copies_only_public_allowlist_and_omits_request_ids(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    output = build_public_comparison(inputs, tmp_path / "out")

    files = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    }

    assert files == _expected_public_files()
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "request_ids" not in page
    assert "private-glm-request" not in page
    assert "private-gpt-request" not in page
    assert "must-not-leak" not in page
    assert str(inputs.private_root) not in page
    assert (output / "glm-5.2" / "index.html").read_text(
        encoding="utf-8"
    ).endswith("<button>Открыть</button>")


def test_build_rejects_unexpected_models(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path, models=("glm-5.2", "other"))

    with pytest.raises(ValueError, match="exactly glm-5.2 and gpt-5.5"):
        build_public_comparison(inputs, tmp_path / "out")
