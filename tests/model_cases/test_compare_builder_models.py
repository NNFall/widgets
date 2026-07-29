from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.contracts import (
    InvalidModelResponse,
    ModelResponse,
    ModelUsage,
)
from builder_lab.browser_audit import BrowserAuditError
from builder_lab.models import Stage
from scripts import compare_builder_models
from scripts.compare_builder_models import (
    _browser_failure_finding,
    build_report,
    run_target,
)
from tests.builder_lab_cases.test_validation import artifact


def test_compare_builder_models_cli_is_runnable_from_repository_root() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/compare_builder_models.py", "--help"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "--evidence" in result.stdout


def fixture_runs() -> list[dict]:
    return [
        {
            "provider": "agentrouter",
            "model": "glm-5.2",
            "elapsed_seconds": 12.5,
            "usage": {
                "input_tokens": 100_000,
                "output_tokens": 20_000,
                "thinking_tokens": 5_000,
            },
            "pricing": {
                "input_usd_per_million": 6.0,
                "output_usd_per_million": 6.0,
            },
            "retry_count": 1,
            "repair_count": 2,
            "visual_score": 0.86,
            "validator_passed": True,
            "publishable": True,
            "request_ids": ["request-glm"],
            "prompt": "private grounded page text",
            "authorization": "Bearer secret",
        },
        {
            "provider": "agentrouter",
            "model": "gpt-5.5",
            "elapsed_seconds": 10.0,
            "usage": {
                "input_tokens": 80_000,
                "output_tokens": 10_000,
                "thinking_tokens": 1_000,
            },
            "pricing": {
                "input_usd_per_million": 7.0,
                "output_usd_per_million": 7.0,
            },
            "retry_count": 0,
            "repair_count": 1,
            "visual_score": 0.92,
            "validator_passed": True,
            "publishable": True,
            "request_ids": ["request-gpt"],
            "api_key": "secret-key",
        },
    ]


def test_report_compares_same_evidence_and_plan() -> None:
    report = build_report(
        evidence=b"frozen evidence",
        composition=b"frozen composition",
        runs=fixture_runs(),
        usd_to_rub=100,
    )

    assert len(report["input_identity"]["evidence_sha256"]) == 64
    assert len(report["input_identity"]["composition_sha256"]) == 64
    assert {row["model"] for row in report["runs"]} == {"glm-5.2", "gpt-5.5"}
    assert all("cost_rub" in row and "publishable" in row for row in report["runs"])
    assert report["runs"][0]["cost_usd"] == 0.72
    assert report["runs"][0]["cost_rub"] == 72.0


def test_public_report_whitelists_metrics_and_excludes_private_inputs() -> None:
    report = build_report(
        evidence=b"private page text",
        composition=b'{"private":"plan"}',
        runs=fixture_runs(),
        usd_to_rub=100,
    )

    encoded = json.dumps(report, ensure_ascii=False)
    assert "private grounded page text" not in encoded
    assert "Bearer secret" not in encoded
    assert "secret-key" not in encoded
    assert "private page text" not in encoded


def test_report_applies_one_frozen_visual_review_to_both_models() -> None:
    visual_review = json.dumps(
        {
            "schema_version": 1,
            "source": "codex_independent_visual_review",
            "rubric": "kaigo-strict-visual-v1",
            "models": {
                "glm-5.2": {
                    "normalized_score": 0.6,
                    "weighted_score": 3.0,
                    "assessments": {
                        name: 3 for name in compare_builder_models._VISUAL_DIMENSIONS
                    },
                },
                "gpt-5.5": {
                    "normalized_score": 0.8,
                    "weighted_score": 4.0,
                    "assessments": {
                        name: 4 for name in compare_builder_models._VISUAL_DIMENSIONS
                    },
                },
            },
        }
    ).encode()

    report = build_report(
        evidence=b"frozen evidence",
        composition=b"frozen composition",
        visual_review=visual_review,
        runs=fixture_runs(),
        usd_to_rub=100,
    )

    assert len(report["input_identity"]["visual_review_sha256"]) == 64
    assert report["visual_evaluation"] == {
        "source": "codex_independent_visual_review",
        "rubric": "kaigo-strict-visual-v1",
    }
    assert [row["visual_score"] for row in report["runs"]] == [0.6, 0.8]


def test_visual_review_must_cover_every_compared_model() -> None:
    visual_review = json.dumps(
        {
            "schema_version": 1,
            "source": "codex_independent_visual_review",
            "rubric": "kaigo-strict-visual-v1",
            "models": {
                "glm-5.2": {
                    "normalized_score": 0.6,
                    "weighted_score": 3.0,
                    "assessments": {
                        name: 3 for name in compare_builder_models._VISUAL_DIMENSIONS
                    },
                }
            },
        }
    ).encode()

    with pytest.raises(ValueError, match="gpt-5.5"):
        build_report(
            evidence=b"frozen evidence",
            composition=b"frozen composition",
            visual_review=visual_review,
            runs=fixture_runs(),
            usd_to_rub=100,
        )


def test_visual_review_score_is_derived_from_the_complete_rubric() -> None:
    visual_review = json.dumps(
        {
            "schema_version": 1,
            "source": "codex_independent_visual_review",
            "rubric": "kaigo-strict-visual-v1",
            "models": {
                model: {
                    "normalized_score": 0.99,
                    "weighted_score": 3.0,
                    "assessments": {
                        name: 3 for name in compare_builder_models._VISUAL_DIMENSIONS
                    },
                }
                for model in ("glm-5.2", "gpt-5.5")
            },
        }
    ).encode()

    with pytest.raises(ValueError, match="derived score"):
        build_report(
            evidence=b"frozen evidence",
            composition=b"frozen composition",
            visual_review=visual_review,
            runs=fixture_runs(),
            usd_to_rub=100,
        )


@pytest.mark.asyncio
async def test_invalid_structured_response_is_metered_without_aborting_benchmark(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class InvalidProvider:
        prompts: list[str] = []

        def __init__(self, **_kwargs) -> None:
            self.calls = 0

        async def generate(self, request, *, model: str):
            self.calls += 1
            self.prompts.append(request.prompt)
            raise InvalidModelResponse(
                (
                    "not exact JSON; Authorization: Bearer test-only; "
                    "api_key=private-provider-value"
                ),
                usage=ModelUsage(
                    input_tokens=100 * self.calls,
                    output_tokens=20 * self.calls,
                    thinking_tokens=5 * self.calls,
                ),
                request_id=f"invalid-{model}-{self.calls}",
            )

    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-only")
    monkeypatch.setattr(
        compare_builder_models,
        "AgentRouterQwenProvider",
        InvalidProvider,
    )
    root = Path(__file__).resolve().parents[2]
    result = await run_target(
        target="agentrouter:glm-5.2",
        evidence_bytes=(
            root / "benchmarks/agentrouter/agent-kernel-frozen-v1/evidence.json"
        ).read_bytes(),
        composition_bytes=(
            root / "benchmarks/agentrouter/agent-kernel-frozen-v1/composition.json"
        ).read_bytes(),
        private_dir=tmp_path / "private",
        timeout_seconds=30,
        repair_limit=1,
    )

    assert result["retry_count"] == 2
    assert result["repair_count"] == 0
    assert result["usage"] == {
        "input_tokens": 300,
        "output_tokens": 60,
        "thinking_tokens": 15,
    }
    assert result["failure_code"] == "invalid_response"
    assert result["publishable"] is False
    first_audit = json.loads(
        (tmp_path / "private" / "attempt-1-error.json").read_text(encoding="utf-8")
    )
    second_audit = json.loads(
        (tmp_path / "private" / "attempt-2-error.json").read_text(encoding="utf-8")
    )
    assert first_audit["role"] == "widget_generator"
    assert first_audit["provider"] == "agentrouter"
    assert first_audit["model"] == "glm-5.2"
    first_prompt = InvalidProvider.prompts[0].encode("utf-8")
    assert first_audit["prompt_sha256"] == hashlib.sha256(first_prompt).hexdigest()
    assert first_audit["prompt_bytes"] == len(first_prompt)
    assert first_audit["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "thinking_tokens": 5,
    }
    assert first_audit["aggregate_usage"] == first_audit["usage"]
    assert second_audit["aggregate_usage"] == result["usage"]
    assert first_audit["error_class"] == "InvalidModelResponse"
    assert "error_message" not in first_audit
    encoded_audit = json.dumps(first_audit, ensure_ascii=False)
    assert "test-only" not in encoded_audit
    assert "private-provider-value" not in encoded_audit
    assert "Bearer" not in encoded_audit
    assert InvalidProvider.prompts[0] not in encoded_audit


def test_private_attempt_receipts_are_append_only(tmp_path: Path) -> None:
    private_dir = tmp_path / "private"
    private_dir.mkdir()

    for request_id in ("request-one", "request-two"):
        compare_builder_models._write_attempt_result(
            private_dir,
            attempt=1,
            kind="attempt",
            role="widget_generator",
            provider="agentrouter",
            model="glm-5.2",
            prompt="same prompt",
            usage=ModelUsage(input_tokens=10, output_tokens=2, thinking_tokens=1),
            aggregate_usage=ModelUsage(input_tokens=10, output_tokens=2, thinking_tokens=1),
            request_id=request_id,
        )

    receipts = sorted(private_dir.glob("attempt-1-result*.json"))
    assert len(receipts) == 2
    assert {
        json.loads(path.read_text(encoding="utf-8"))["request_id"]
        for path in receipts
    } == {"request-one", "request-two"}


def test_browser_failure_is_converted_to_bounded_visual_repair_finding() -> None:
    error = BrowserAuditError(
        "browser_gate_failed",
        "blocked",
        failures=(
            "desktop.open_initial: panel is clipped",
            "desktop.open_initial: target.interactive.4.label=1.00×1.00px",
        ),
    )

    finding = _browser_failure_finding(error, repair_number=1)

    assert finding.screenshot_id == "desktop.open_initial"
    assert set(finding.artifact_fields) == {"body_html", "css"}
    assert "1.00×1.00px" in finding.evidence
    assert "горизонт" in finding.repair_instruction
    assert "scrollWidth" in finding.repair_instruction


@pytest.mark.asyncio
async def test_seeded_browser_failure_gets_separate_visual_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seed_artifact = artifact(revision=1, stage=Stage.FOUNDATION)
    repaired_artifact = artifact(
        revision=2,
        stage=Stage.FOUNDATION,
        css=seed_artifact.css + "\n.kaigo-widget { overflow: visible; }",
    )

    class RepairProvider:
        def __init__(self, **_kwargs) -> None:
            pass

        async def generate(self, request, *, model: str):
            assert "visual_repair" in request.prompt
            return ModelResponse(
                text="{}",
                parsed=repaired_artifact.to_dict(),
                usage=ModelUsage(input_tokens=50, output_tokens=10, thinking_tokens=2),
                request_id=f"repair-{model}",
            )

    class Audit:
        def __init__(self) -> None:
            self.calls = 0

        async def audit(self, _artifact):
            self.calls += 1
            if self.calls == 1:
                raise BrowserAuditError(
                    "browser_gate_failed",
                    "blocked",
                    failures=("desktop.open_initial: panel is clipped",),
                )
            return SimpleNamespace(screenshots=())

    audit = Audit()
    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-only")
    monkeypatch.setattr(
        compare_builder_models,
        "AgentRouterQwenProvider",
        RepairProvider,
    )
    root = Path(__file__).resolve().parents[2]
    result = await run_target(
        target="agentrouter:gpt-5.5",
        evidence_bytes=(
            root / "benchmarks/agentrouter/agent-kernel-frozen-v1/evidence.json"
        ).read_bytes(),
        composition_bytes=(
            root / "benchmarks/agentrouter/agent-kernel-frozen-v1/composition.json"
        ).read_bytes(),
        private_dir=tmp_path / "private",
        timeout_seconds=30,
        repair_limit=0,
        browser_repair_limit=1,
        seed_artifact=seed_artifact,
        seed_run={
            "elapsed_seconds": 12.0,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "thinking_tokens": 5,
            },
            "retry_count": 0,
            "repair_count": 1,
            "browser_repair_count": 0,
            "request_ids": ["seed"],
        },
        audit_factory=lambda: audit,
    )

    assert result["publishable"] is True
    assert result["browser_repair_count"] == 1
    assert result["repair_count"] == 2
    assert result["usage"] == {
        "input_tokens": 150,
        "output_tokens": 30,
        "thinking_tokens": 7,
    }
    assert result["failure_code"] is None
    audit = json.loads(
        (tmp_path / "private" / "browser-repair-1-result.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["role"] == "repair"
    assert audit["provider"] == "agentrouter"
    assert audit["model"] == "gpt-5.5"
    assert len(audit["prompt_sha256"]) == 64
    assert audit["prompt_bytes"] > 0
    assert audit["usage"] == {
        "input_tokens": 50,
        "output_tokens": 10,
        "thinking_tokens": 2,
    }
    assert audit["aggregate_usage"] == result["usage"]
    assert audit["request_id"] == "repair-gpt-5.5"
    assert "error_class" not in audit
    assert "error_message" not in audit
