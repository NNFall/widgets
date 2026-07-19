from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder_lab.config import BuilderLabConfig
from builder_lab.reference_crawler import (
    ReferenceCrawlLimits,
    UnsafeReferenceUrl,
    VisualReferenceCrawler,
    sanitize_url_for_log,
)
from builder_lab.reference_storage import (
    cleanup_expired_evidence,
    private_write_new,
    validate_evidence_output_dir,
    write_evidence_expiry_marker,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture bounded private visual evidence for one public site."
    )
    parser.add_argument("url", help="One explicit public HTTP(S) URL")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Private directory for JSON, screenshots, and failed-run traces",
    )
    parser.add_argument(
        "--allow-workspace",
        action="store_true",
        help="Explicitly allow raw evidence inside the Git workspace",
    )
    return parser


def _emit_json(payload: object) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    stdout = getattr(sys.stdout, "buffer", None)
    if stdout is None:
        sys.stdout.write(data.decode("utf-8"))
        sys.stdout.flush()
    else:
        stdout.write(data)
        stdout.flush()


def _limits(config: BuilderLabConfig) -> ReferenceCrawlLimits:
    return ReferenceCrawlLimits(
        max_pages=config.reference_max_pages,
        max_depth=config.reference_max_depth,
        total_timeout_seconds=config.reference_timeout_seconds,
        page_timeout_seconds=config.reference_page_timeout_seconds,
        max_total_bytes=config.reference_max_total_bytes,
        max_page_bytes=config.reference_max_page_bytes,
        max_retries=config.reference_max_retries,
        max_scroll_steps=config.reference_max_scroll_steps,
        scroll_delay_ms=config.reference_scroll_delay_ms,
        warmup_ms=config.reference_warmup_ms,
        final_settle_ms=config.reference_final_settle_ms,
        max_scroll_height=config.reference_max_scroll_height,
        trace_ttl_seconds=config.reference_trace_ttl_seconds,
        respect_robots=config.reference_respect_robots,
    )


async def _run(url: str, output_dir: Path, *, allow_workspace: bool) -> int:
    config = BuilderLabConfig.from_env()
    try:
        output_dir = validate_evidence_output_dir(
            output_dir,
            workspace_root=WORKSPACE_ROOT,
            allow_workspace=allow_workspace,
        )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        cleanup_expired_evidence(output_dir.parent)
        output_dir.mkdir(mode=0o700, exist_ok=True)
        if any(output_dir.iterdir()):
            raise ValueError("evidence output directory must be empty")
        try:
            output_dir.chmod(0o700)
        except OSError:
            pass
    except (OSError, ValueError) as exc:
        _emit_json(
            {
                "source_url": sanitize_url_for_log(url),
                "status": "failed",
                "failure": {"code": "output_path", "message": str(exc)[:1000]},
            }
        )
        return 2
    crawler = VisualReferenceCrawler(limits=_limits(config))
    try:
        result = await crawler.crawl(url)
    except UnsafeReferenceUrl as exc:
        payload = {
            "source_url": sanitize_url_for_log(url),
            "status": "failed",
            "failure": {"code": "unsafe_url", "message": str(exc)},
        }
        _emit_json(payload)
        return 2
    except Exception as exc:
        payload = {
            "source_url": sanitize_url_for_log(url),
            "status": "failed",
            "failure": {"code": "crawl_failed", "message": str(exc)[:1000]},
        }
        _emit_json(payload)
        return 1

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=config.reference_trace_ttl_seconds
    )
    if result.trace is not None:
        expires_at = min(expires_at, result.trace.expires_at)
    write_evidence_expiry_marker(output_dir, expires_at)
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(mode=0o700)
    try:
        screenshots_dir.chmod(0o700)
    except OSError:
        pass
    for screenshot_id, data in result.screenshot_bytes().items():
        private_write_new(screenshots_dir / f"{screenshot_id}.jpg", data)
    if result.trace is not None and result.trace.data is not None:
        private_write_new(output_dir / f"{result.trace.trace_id}.zip", result.trace.data)
    public_json = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    private_write_new(output_dir / "reference.json", public_json.encode("utf-8"))
    _emit_json(result.to_dict())
    return 0 if result.status == "succeeded" else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(
        _run(args.url, args.output_dir, allow_workspace=args.allow_workspace)
    )


if __name__ == "__main__":
    sys.exit(main())
