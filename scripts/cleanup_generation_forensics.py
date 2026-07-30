from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder_lab.forensics.cleanup_integration import GenerationForensicCleanup
from builder_lab.forensics.config import GenerationForensicsConfig


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--now must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--now must include a timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove only terminal Kaigo generation evidence after exactly 120 hours."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report eligible runs without deleting evidence or database rows",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--time-budget-seconds", type=float, default=30.0)
    parser.add_argument(
        "--now",
        type=_aware_datetime,
        help="timezone-aware ISO timestamp for deterministic operator checks",
    )
    return parser


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("database_not_configured")
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


async def _run(args: argparse.Namespace) -> dict[str, object]:
    environment = os.getenv("KAIGO_ENVIRONMENT", "development")
    config = GenerationForensicsConfig.from_env(environment=environment)
    if not config.enabled:
        raise RuntimeError("generation_forensics_disabled")
    engine = create_async_engine(_database_url(), future=True, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        summary = await GenerationForensicCleanup(factory, config).run(
            now=args.now,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            time_budget_seconds=args.time_budget_seconds,
        )
        return {"status": "succeeded", **summary.to_dict()}
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except (OSError, RuntimeError, ValueError):
        print(
            json.dumps(
                {"status": "failed", "error": "cleanup_failed"},
                ensure_ascii=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
