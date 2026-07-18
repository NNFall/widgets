from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    deadline = time.monotonic() + args.timeout
    with httpx.Client(timeout=httpx.Timeout(30, read=None)) as client:
        response = client.post(
            f"{base_url}/api/runs",
            json={"engine": args.engine, "brief": args.brief, "creativity": 0.9},
        )
        response.raise_for_status()
        run = response.json()
        run_id = run["run_id"]
        terminal_event = None
        with client.stream("GET", f"{base_url}/api/runs/{run_id}/events") as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if time.monotonic() >= deadline:
                    raise TimeoutError("builder-lab smoke timed out")
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
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
                if event["type"] in {"run.completed", "run.failed", "run.cancelled"}:
                    terminal_event = event
        snapshot = client.get(f"{base_url}/api/runs/{run_id}").json()
        if terminal_event is None or snapshot["status"] != "completed":
            print(
                json.dumps(
                    {
                        "status": snapshot.get("status"),
                        "error_code": snapshot.get("error_code"),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 1
        preview = client.get(f"{base_url}/api/runs/{run_id}/preview")
        preview.raise_for_status()
        if "kaigo-builder-preview" not in preview.text:
            raise RuntimeError("preview acknowledgement runtime is missing")
        print(
            json.dumps(
                {
                    "status": snapshot["status"],
                    "revision": snapshot["artifact"]["revision"],
                    "stage": snapshot["artifact"]["stage"],
                    "usage": snapshot["usage"],
                    "elapsed_seconds": snapshot["elapsed_seconds"],
                },
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
