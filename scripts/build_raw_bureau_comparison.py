"""Freeze and render the reproducible RAW BUREAU generator comparison."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder_lab.comparison import (
    ComparisonVariant,
    freeze_bundle,
    render_comparison_page,
    verify_bundle,
)


DEFAULT_BRIEF = (
    "Проанализируй https://rawbureau.ru/ визуально и по содержанию. Создай "
    "компактного AI-сотрудника в стиле сайта: реальный чат, свободный JavaScript, "
    "выразительные анимации, адаптивный desktop/mobile интерфейс и никакие "
    "декоративные кнопки без работающего действия."
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def freeze(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle_path = output / "input-bundle"
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary)
        (source / "brief.txt").write_text(args.brief, encoding="utf-8")
        baseline = source / "baseline-evidence"
        shutil.copytree(args.existing_baseline.resolve(), baseline)
        frozen = freeze_bundle(
            source,
            bundle_path,
            metadata={
                "source_url": args.source_url,
                "brief_sha256": __import__("hashlib").sha256(
                    args.brief.encode("utf-8")
                ).hexdigest(),
                "model_matrix": {
                    "direct": "gemini-3.6-flash/high",
                    "visual_critic": "gemini-3.5-flash/high",
                    "visitor_chat": "gemini-3.5-flash-lite/medium",
                    "antigravity": "antigravity-preview-05-2026",
                },
                "acceptance_profile": "kaigo-widget-experimental-v2",
            },
        )
    archive = output / "archive" / "raw-bureau-v1"
    archive.mkdir(parents=True, exist_ok=True)
    evidence = archive / "evidence"
    if not evidence.exists():
        shutil.copytree(args.existing_baseline.resolve(), evidence)
    _write_json(
        archive / "report.json",
        {
            "title": "RAW BUREAU — baseline",
            "model": "Gemini 2.5 Flash",
            "thinking": "low",
            "status": "archived",
            "summary": "Исходная версия до перехода на новые модели и свободный JavaScript.",
            "input_bundle_digest": frozen.digest,
        },
    )
    print(json.dumps({"bundle": str(bundle_path), "digest": frozen.digest}, ensure_ascii=False))
    return 0


def _variant(root: Path, slug: str, fallback: dict[str, str]) -> ComparisonVariant:
    report_path = root / slug / "report.json"
    payload = fallback
    if report_path.is_file():
        loaded = json.loads(report_path.read_text(encoding="utf-8"))
        payload = {**fallback, **{key: str(value) for key, value in loaded.items()}}
    return ComparisonVariant(
        slug=slug,
        title=payload["title"],
        model=payload["model"],
        thinking=payload["thinking"],
        status=payload["status"],
        summary=payload["summary"],
    )


def render(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if not verify_bundle(output / "input-bundle"):
        raise SystemExit("input bundle is missing or was modified")
    variants = (
        _variant(
            output,
            "archive/raw-bureau-v1",
            {
                "title": "RAW BUREAU — baseline",
                "model": "Gemini 2.5 Flash",
                "thinking": "low",
                "status": "archived",
                "summary": "Исходная версия.",
            },
        ),
        _variant(
            output,
            "direct-3-6",
            {
                "title": "Direct",
                "model": "Gemini 3.6 Flash",
                "thinking": "high",
                "status": "incomplete",
                "summary": "Direct-прогон ещё не завершён.",
            },
        ),
        _variant(
            output,
            "antigravity-3-6",
            {
                "title": "Antigravity",
                "model": "antigravity-preview-05-2026",
                "thinking": "agent",
                "status": "incomplete",
                "summary": "Antigravity-прогон ещё не завершён.",
            },
        ),
    )
    (output / "index.html").write_text(
        render_comparison_page(variants),
        encoding="utf-8",
    )
    print(json.dumps({"index": str(output / "index.html")}, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    freeze_command = commands.add_parser("freeze")
    freeze_command.add_argument("--source-url", required=True)
    freeze_command.add_argument("--existing-baseline", required=True, type=Path)
    freeze_command.add_argument("--output", required=True, type=Path)
    freeze_command.add_argument("--brief", default=DEFAULT_BRIEF)
    freeze_command.set_defaults(handler=freeze)
    render_command = commands.add_parser("render")
    render_command.add_argument("--output", required=True, type=Path)
    render_command.set_defaults(handler=render)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
