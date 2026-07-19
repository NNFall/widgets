from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder_lab.config import BuilderLabConfig
from builder_lab.reference_crawler import (
    ReferenceCrawlLimits,
    UnsafeReferenceUrl,
    VisualReferenceCrawler,
)


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
    return parser


def _private_write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass


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


async def _run(url: str, output_dir: Path) -> int:
    config = BuilderLabConfig.from_env()
    crawler = VisualReferenceCrawler(limits=_limits(config))
    try:
        result = await crawler.crawl(url)
    except UnsafeReferenceUrl as exc:
        payload = {
            "source_url": url,
            "status": "failed",
            "failure": {"code": "unsafe_url", "message": str(exc)},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        payload = {
            "source_url": url,
            "status": "failed",
            "failure": {"code": "crawl_failed", "message": str(exc)[:1000]},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.chmod(0o700)
    except OSError:
        pass
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)
    try:
        screenshots_dir.chmod(0o700)
    except OSError:
        pass
    for screenshot_id, data in result.screenshot_bytes().items():
        _private_write(screenshots_dir / f"{screenshot_id}.jpg", data)
    if result.trace is not None and result.trace.data is not None:
        _private_write(output_dir / f"{result.trace.trace_id}.zip", result.trace.data)
    public_json = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    _private_write(output_dir / "reference.json", public_json.encode("utf-8"))
    print(public_json)
    return 0 if result.status == "succeeded" else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_run(args.url, args.output_dir))


if __name__ == "__main__":
    sys.exit(main())
