from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.patterns.candidate_repository import PatternCandidateRepository
from app.saas.models import WidgetPatternVersion
from builder_lab.patterns.atomic_quality import (
    AtomicPatternRole,
    compute_atomic_quality_profile,
)
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not value.startswith("postgresql+asyncpg://"):
        raise RuntimeError("PostgreSQL with asyncpg is required")
    return value


def _load_decisions(path: Path) -> dict[tuple[str, int], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("review decisions contract is invalid")
    decisions = payload.get("decisions")
    if payload.get("schema_version") != 1 or not isinstance(decisions, list):
        raise ValueError("review decisions contract is invalid")
    result: dict[tuple[str, int], str] = {}
    for item in decisions:
        if not isinstance(item, Mapping):
            raise ValueError("review decision must be an object")
        pattern_id = item.get("pattern_id")
        version = item.get("version")
        state = item.get("state")
        if not isinstance(pattern_id, str) or not pattern_id:
            raise ValueError("review decision pattern_id is invalid")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ValueError("review decision version is invalid")
        if not isinstance(state, str):
            raise ValueError("review decision state is invalid")
        key = (pattern_id, version)
        if state not in {"approved", "rejected"} or key in result:
            raise ValueError("review decision is invalid or duplicated")
        result[key] = state
    return result


def desired_pilot_states(
    *,
    decisions: Mapping[tuple[str, int], str],
) -> dict[tuple[str, int], str]:
    """Return explicit decisions, preserving every untouched manifest state.

    A pilot dry run must be non-destructive: ``ready_for_review`` is evidence
    that an owner still needs to inspect the asset, not an implicit approval.
    """

    registry = load_builtin_atomic_registry()
    known = {(item.pattern_id, item.version) for item in registry.definitions}
    unknown = set(decisions) - known
    if unknown:
        raise ValueError(f"review decisions contain unknown versions: {sorted(unknown)!r}")
    by_key = {(item.pattern_id, item.version): item for item in registry.definitions}
    for key, state in decisions.items():
        if state != "approved":
            continue
        definition = by_key[key]
        quality = compute_atomic_quality_profile(
            definition,
            effective_review_state="approved",
        )
        if not quality.selector_eligible:
            raise ValueError(
                f"review decision cannot approve quality-ineligible pattern: {key[0]}@{key[1]}"
            )
    result: dict[tuple[str, int], str] = {}
    for definition in registry.definitions:
        if definition.status.value != "active":
            continue
        key = (definition.pattern_id, definition.version)
        manifest_state = str(definition.provenance.get("review_state", ""))
        if manifest_state not in {"ready_for_review", "approved", "rejected"}:
            manifest_state = "ready_for_review"
        result[key] = decisions.get(key, manifest_state)
    return result


def _quality_counts(
    desired: Mapping[tuple[str, int], str],
    registry,
) -> dict[str, int]:
    """Report catalog, review and selector counts as separate quantities."""

    approved = ready = rejected = selectable = approved_fixtures = 0
    by_key = {(item.pattern_id, item.version): item for item in registry.definitions}
    for key, state in desired.items():
        if state == "approved":
            approved += 1
        elif state == "ready_for_review":
            ready += 1
        elif state == "rejected":
            rejected += 1
        definition = by_key.get(key)
        if definition is None:
            continue
        profile = compute_atomic_quality_profile(
            definition,
            effective_review_state=state,
        )
        if state == "approved" and profile.role is AtomicPatternRole.FIXTURE:
            approved_fixtures += 1
        if state == "approved" and profile.selector_eligible:
            selectable += 1
    return {
        "catalog_versions": len(desired),
        "approved": approved,
        "approved_versions": approved,
        "approved_fixtures": approved_fixtures,
        "selectable_versions": selectable,
        "ready_for_review": ready,
        "rejected": rejected,
    }


async def _apply(
    *,
    decisions_path: Path,
    reviewer: str,
) -> dict[str, int]:
    decisions = _load_decisions(decisions_path)
    desired = desired_pilot_states(decisions=decisions)
    registry = load_builtin_atomic_registry()
    engine = create_async_engine(_database_url(), future=True, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    changed = 0
    unchanged = 0
    try:
        async with factory() as database, database.begin():
            repository = PatternCandidateRepository(database)
            await repository.sync_registry(registry)
            rows = (
                await database.execute(
                    select(
                        WidgetPatternVersion.id,
                        WidgetPatternVersion.pattern_id,
                        WidgetPatternVersion.version,
                    )
                )
            ).all()
            persisted = {
                (str(pattern_id), int(version)): pattern_version_id
                for pattern_version_id, pattern_id, version in rows
            }
            for key, state in sorted(desired.items()):
                pattern_version_id = persisted.get(key)
                if pattern_version_id is None:
                    raise RuntimeError(f"pattern version was not synchronized: {key!r}")
                current = await repository.effective_review_state(pattern_version_id)
                # Only explicit owner decisions become durable review rows.  A
                # manifest state with no decision must remain untouched; this
                # is what keeps a pilot from blanket-approving new assets.
                if key not in decisions or current == state:
                    unchanged += 1
                    continue
                await repository.append_review(
                    pattern_version_id,
                    reviewer,
                    state,
                    "Owner-authorized atomic catalog pilot; technical gates passed.",
                )
                changed += 1
    finally:
        await engine.dispose()
    return {
        **_quality_counts(desired, registry),
        "changed": changed,
        "unchanged": unchanged,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Promote the owner-authorized atomic catalog pilot in PostgreSQL."
    )
    parser.add_argument(
        "--review-decisions",
        type=Path,
        default=REPO_ROOT
        / "data/pattern-selection/user-review-decisions-2026-08-07.json",
    )
    parser.add_argument("--reviewer", default="pattern-pilot@kaigo.invalid")
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    decisions = _load_decisions(args.review_decisions)
    desired = desired_pilot_states(decisions=decisions)
    if not args.apply:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    **_quality_counts(desired, load_builtin_atomic_registry()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    result = asyncio.run(_apply(decisions_path=args.review_decisions, reviewer=args.reviewer))
    print(json.dumps({"dry_run": False, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
