from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.forensics.storage import GenerationForensicStorage


RUN_ID = UUID("7f000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("7f000000-0000-4000-8000-000000000002")
CREATED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
EVENT_PAYLOAD = {"status": "synthetic", "source": "persistence-smoke"}


def _config(root: Path) -> GenerationForensicsConfig:
    return GenerationForensicsConfig(
        enabled=True,
        root=root,
        ttl_hours=120,
        max_bytes=32 * 1024 * 1024,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Token-free persistence smoke for Kaigo generation evidence."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("seed", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
    return parser


def _seed(root: Path) -> dict[str, object]:
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(
        user_id=1,
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        created_at=CREATED_AT,
    )
    result = storage.write_event(
        run_id=RUN_ID,
        sequence=1,
        event_type="run.created",
        payload=EVENT_PAYLOAD,
        created_at=CREATED_AT,
    )
    if result.degraded or result.manifest is None:
        raise RuntimeError("synthetic evidence write failed")
    return {
        "status": "succeeded",
        "run_id": str(RUN_ID),
        "entry_count": len(result.manifest.entries),
    }


def _verify(root: Path) -> dict[str, object]:
    storage = GenerationForensicStorage.open_existing(
        _config(root),
        writable=False,
    )
    manifest = storage.load_manifest(RUN_ID)
    checksum_valid = storage.event_payload_matches(
        run_id=RUN_ID,
        sequence=1,
        event_type="run.created",
        payload=EVENT_PAYLOAD,
    )
    storage.manifest_digest(RUN_ID)
    return {
        "status": "succeeded",
        "run_id": str(RUN_ID),
        "entry_count": len(manifest.entries),
        "checksum_valid": checksum_valid,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _seed(args.root) if args.command == "seed" else _verify(args.root)
    except (OSError, RuntimeError, ValueError):
        print(json.dumps({"status": "failed", "error": "smoke_failed"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
