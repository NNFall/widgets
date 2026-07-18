from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from builder_lab.demo import DemoUnavailable, save_demo


DEFAULT_BRIEF = (
    "Создай премиального AI-сотрудника для архитектурного бюро: русский текст, "
    "метафора чертежа и света, выразительная типографика и аккуратное движение."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test Kaigo builder-lab")
    parser.add_argument("--base-url", default="http://127.0.0.1:8091")
    parser.add_argument("--engine", choices=("direct", "antigravity"), default="direct")
    parser.add_argument("--brief", default=DEFAULT_BRIEF)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--demo-output", type=Path)
    parser.add_argument(
        "--model", default=os.getenv("GEMINI_BUILDER_MODEL", "gemini-3.5-flash")
    )
    return parser.parse_args()


def validate_smoke_evidence(
    engine: str,
    events: list[dict],
    snapshot: dict,
) -> None:
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.get("type") in {"run.completed", "run.failed", "run.cancelled"}
        ),
        None,
    )
    if terminal is None or snapshot.get("status") != "completed":
        raise RuntimeError(
            f"builder run did not complete: {snapshot.get('error_code') or snapshot.get('status')}"
        )
    committed = [event for event in events if event.get("type") == "artifact.committed"]
    if engine == "direct" and len(committed) < 4:
        raise RuntimeError("direct smoke requires at least four committed revisions")
    usage = snapshot.get("usage") or {}
    if int(usage.get("total_tokens", 0) or 0) <= 0:
        raise RuntimeError("smoke requires positive provider token usage")


async def run_smoke(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    events: list[dict] = []
    async with asyncio.timeout(args.timeout):
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None)) as client:
            response = await client.post(
            f"{base_url}/api/runs",
            json={"engine": args.engine, "brief": args.brief, "creativity": 0.9},
            )
            response.raise_for_status()
            run_id = response.json()["run_id"]
            async with client.stream(
                "GET", f"{base_url}/api/runs/{run_id}/events"
            ) as stream:
                stream.raise_for_status()
                async for line in stream.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    events.append(event)
                    print(
                        json.dumps(
                            {
                                "sequence": event["sequence"],
                                "type": event["type"],
                                "stage": event["stage"],
                                "status": event["status"],
                                "revision": event["revision"],
                            },
                            ensure_ascii=False,
                        )
                    )
            snapshot_response = await client.get(f"{base_url}/api/runs/{run_id}")
            snapshot_response.raise_for_status()
            snapshot = snapshot_response.json()
            validate_smoke_evidence(args.engine, events, snapshot)
            preview = await client.get(f"{base_url}/api/runs/{run_id}/preview")
            preview.raise_for_status()
            if "kaigo-builder-preview" not in preview.text:
                raise RuntimeError("preview acknowledgement runtime is missing")
            if args.demo_output:
                save_demo(args.demo_output, snapshot, model=args.model)
            print(
                json.dumps(
                    {
                        "status": snapshot["status"],
                        "revision": snapshot["artifact"]["revision"],
                        "stage": snapshot["artifact"]["stage"],
                        "usage": snapshot["usage"],
                        "elapsed_seconds": snapshot["elapsed_seconds"],
                        "committed_revisions": len(
                            [event for event in events if event["type"] == "artifact.committed"]
                        ),
                    },
                    ensure_ascii=False,
                )
            )
            return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run_smoke(args))
    except TimeoutError:
        print("builder-lab smoke timed out", file=sys.stderr)
        return 2
    except (httpx.HTTPError, RuntimeError, ValueError, DemoUnavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
