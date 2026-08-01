"""Export one verified SaaS generation revision as a frozen preview document.

The script is intended for release evidence and public comparison packages. It
reads the application database configured by ``DATABASE_URL`` and never changes
database state.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import _build_engine
from app.saas.models import GenerationArtifact
from builder_lab.models import WidgetArtifact
from builder_lab.preview import build_trusted_runtime_document
from builder_lab.validation import validate_artifact


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a verified Kaigo generation revision as HTML."
    )
    parser.add_argument("run_id", type=UUID)
    parser.add_argument("revision", type=int)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--channel",
        default="kaigo-public-frozen-preview-v1",
        help="Preview postMessage channel identifier.",
    )
    return parser.parse_args()


async def _export(arguments: argparse.Namespace) -> None:
    if arguments.revision < 1:
        raise ValueError("revision must be positive")

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    engine = _build_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as database:
            record = await database.scalar(
                select(GenerationArtifact).where(
                    GenerationArtifact.run_id == arguments.run_id,
                    GenerationArtifact.revision == arguments.revision,
                    GenerationArtifact.quality_status.in_(("accepted", "verified")),
                )
            )
        if record is None:
            raise RuntimeError("verified generation artifact was not found")

        payload = record.config.get("artifact") if isinstance(record.config, dict) else None
        if not isinstance(payload, dict):
            raise RuntimeError("generation artifact payload is missing")
        artifact = WidgetArtifact.from_dict(payload)
        issues = validate_artifact(
            artifact,
            previous_revision=max(0, artifact.revision - 1),
        )
        if issues:
            raise RuntimeError(f"generation artifact is unsafe: {issues}")

        document = build_trusted_runtime_document(
            artifact,
            channel_id=arguments.channel,
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(document, encoding="utf-8", newline="\n")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_export(_arguments()))


if __name__ == "__main__":
    main()
