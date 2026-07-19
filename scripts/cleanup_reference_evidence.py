from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder_lab.reference_storage import cleanup_expired_evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete expired Kaigo private reference-evidence run directories."
    )
    parser.add_argument("root", type=Path, help="Parent directory containing run directories")
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        help="Timezone-aware ISO timestamp for deterministic operations/tests",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        removed = cleanup_expired_evidence(args.root, now=args.now)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "message": str(exc)[:1000]},
                ensure_ascii=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "succeeded",
                "removed_count": len(removed),
                "removed": [str(path) for path in removed],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
