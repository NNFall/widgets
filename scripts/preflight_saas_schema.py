from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


ROOT = Path(__file__).resolve().parents[1]
LEGACY_REVISION = "0002_widget_settings"

ColumnSignature = tuple[str, str, bool]


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    columns: Mapping[str, tuple[ColumnSignature, ...]]
    constraints: frozenset[str]
    indexes: frozenset[str] = frozenset()
    defaults: frozenset[str] = frozenset()
    alembic_revisions: tuple[str, ...] = ()

    def replace(self, **changes) -> "SchemaSnapshot":
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    stamp_revision: str | None
    upgrade_revision: str


def known_legacy_snapshot() -> SchemaSnapshot:
    columns = {
        "tenants": (
            ("id", "integer", False),
            ("name", "character varying(255)", False),
            ("slug", "character varying(255)", False),
            ("created_at", "timestamp with time zone", False),
        ),
        "users": (
            ("id", "integer", False),
            ("tenant_id", "integer", False),
            ("email", "character varying(320)", False),
            ("password_hash", "character varying(255)", False),
            ("role", "character varying(50)", False),
            ("created_at", "timestamp with time zone", False),
        ),
        "widgets": (
            ("id", "integer", False),
            ("tenant_id", "integer", False),
            ("name", "character varying(255)", False),
            ("slug", "character varying(255)", False),
            ("ai_model", "character varying(255)", False),
            ("prompt_source", "text", True),
            ("intro_text", "text", True),
            ("status", "character varying(50)", False),
            ("created_at", "timestamp with time zone", False),
            ("updated_at", "timestamp with time zone", False),
            ("template", "character varying(50)", False),
            ("stt_model", "character varying(255)", True),
            ("temperature", "double precision", False),
            ("max_tokens", "integer", False),
        ),
        "widget_assets": (
            ("id", "integer", False),
            ("widget_id", "integer", False),
            ("html", "text", True),
            ("css", "text", True),
            ("js", "text", True),
            ("version", "integer", False),
            ("created_at", "timestamp with time zone", False),
        ),
        "widget_bindings": (
            ("id", "integer", False),
            ("widget_id", "integer", False),
            ("domain", "character varying(255)", False),
            ("is_active", "boolean", False),
        ),
    }
    primary_dialect_options = {"postgresql_include": []}
    unique_dialect_options = {
        "postgresql_include": [],
        "postgresql_nulls_not_distinct": False,
    }
    constraint_specs = (
        (
            "pk",
            "tenants",
            {
                "name": "tenants_pkey",
                "constrained_columns": ["id"],
                "dialect_options": primary_dialect_options,
            },
        ),
        (
            "uq",
            "tenants",
            {
                "name": "tenants_slug_key",
                "column_names": ["slug"],
                "dialect_options": unique_dialect_options,
            },
        ),
        (
            "pk",
            "users",
            {
                "name": "users_pkey",
                "constrained_columns": ["id"],
                "dialect_options": primary_dialect_options,
            },
        ),
        (
            "uq",
            "users",
            {
                "name": "users_email_key",
                "column_names": ["email"],
                "dialect_options": unique_dialect_options,
            },
        ),
        (
            "fk",
            "users",
            {
                "name": "users_tenant_id_fkey",
                "constrained_columns": ["tenant_id"],
                "referred_schema": "public",
                "referred_table": "tenants",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
        ),
        (
            "pk",
            "widgets",
            {
                "name": "widgets_pkey",
                "constrained_columns": ["id"],
                "dialect_options": primary_dialect_options,
            },
        ),
        (
            "uq",
            "widgets",
            {
                "name": "widgets_slug_key",
                "column_names": ["slug"],
                "dialect_options": unique_dialect_options,
            },
        ),
        (
            "fk",
            "widgets",
            {
                "name": "widgets_tenant_id_fkey",
                "constrained_columns": ["tenant_id"],
                "referred_schema": "public",
                "referred_table": "tenants",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
        ),
        (
            "pk",
            "widget_assets",
            {
                "name": "widget_assets_pkey",
                "constrained_columns": ["id"],
                "dialect_options": primary_dialect_options,
            },
        ),
        (
            "fk",
            "widget_assets",
            {
                "name": "widget_assets_widget_id_fkey",
                "constrained_columns": ["widget_id"],
                "referred_schema": "public",
                "referred_table": "widgets",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
        ),
        (
            "pk",
            "widget_bindings",
            {
                "name": "widget_bindings_pkey",
                "constrained_columns": ["id"],
                "dialect_options": primary_dialect_options,
            },
        ),
        (
            "uq",
            "widget_bindings",
            {
                "name": "widget_bindings_widget_id_domain_key",
                "column_names": ["widget_id", "domain"],
                "dialect_options": unique_dialect_options,
            },
        ),
        (
            "fk",
            "widget_bindings",
            {
                "name": "widget_bindings_widget_id_fkey",
                "constrained_columns": ["widget_id"],
                "referred_schema": "public",
                "referred_table": "widgets",
                "referred_columns": ["id"],
                "options": {"ondelete": "CASCADE"},
            },
        ),
    )
    constraints = frozenset(
        _constraint_signature(kind, table, constraint)
        for kind, table, constraint in constraint_specs
    )
    defaults = frozenset(
        {
            "tenants.id=nextval('tenants_id_seq'::regclass)",
            "tenants.created_at=now()",
            "users.id=nextval('users_id_seq'::regclass)",
            "users.role='tenant_admin'::character varying",
            "users.created_at=now()",
            "widgets.id=nextval('widgets_id_seq'::regclass)",
            "widgets.status='draft'::character varying",
            "widgets.created_at=now()",
            "widgets.updated_at=now()",
            "widget_assets.id=nextval('widget_assets_id_seq'::regclass)",
            "widget_assets.version=1",
            "widget_assets.created_at=now()",
            "widget_bindings.id=nextval('widget_bindings_id_seq'::regclass)",
            "widget_bindings.is_active=true",
        }
    )
    return SchemaSnapshot(
        columns=MappingProxyType(columns),
        constraints=constraints,
        defaults=defaults,
    )


def _schema_fingerprint(snapshot: SchemaSnapshot) -> str:
    payload = {
        "columns": [
            [table, [list(column) for column in columns]]
            for table, columns in sorted(snapshot.columns.items())
        ],
        "constraints": sorted(snapshot.constraints),
        "indexes": sorted(snapshot.indexes),
        "defaults": sorted(snapshot.defaults),
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Generated by upgrading a disposable postgres:15-alpine database one revision at
# a time and hashing _snapshot(). The revision-coverage contract test forces every
# new migration to add its expected fingerprint before it can pass preflight.
EXPECTED_VERSIONED_SCHEMA_FINGERPRINTS: Mapping[str, str] = MappingProxyType(
    {
        "0001_initial": (
            "257345ba73dd06df48627569bbbf1ba170d39357c9d46dd000c8f50c78c2add5"
        ),
        LEGACY_REVISION: (
            "7efd69b709a41696eaac4b34007e4e0dd66e6b813a9fe66f221eb9b7586de7c4"
        ),
        "0003_saas_foundation": (
            "3aa7909793609a701d84206a3f03daa507777e32ba69c8e078d77dd2790ac671"
        ),
        "0004_worker_hardening": (
            "3b37c0c37806c8a0abdc83bfcbb7f0d5bff5b5caedfca0940cbf2513d3f648f4"
        ),
        "0005_trial_cycles": (
            "2cfbe5acd6f81b00d4f9aa232fa53148afa72a8cfe28bc9de712e3bcfc221f2d"
        ),
        "0006_project_api": (
            "438c297ea3c797731771b117eabb0cda6f43e9aa8c4e44701ab9cf912f980f5d"
        ),
        "0007_project_recovery": (
            "4e7e88089b78554fc0c5c8e152da2bd643427cb540d50ec2e4e026f9b84663b7"
        ),
        "0008_publication_releases": (
            "278aa15024fd84a50a531b813551d096e608a5a7b43500f68519ab56bb21dba3"
        ),
        "0009_billing_foundation": (
            "0aa064427f2c9bca48d30be8b24d18e4356a5b12884654d4c579ddda7be51adb"
        ),
        "0010_funnel_events": (
            "f47fcb95c9d788ef6a1f36f6ab5a7a3713679a2484d00d4b5d7867674e60f5fa"
        ),
        "0011_payment_merchant_account": (
            "875d953726f877857cbafed6e1f5f0b0df757bc14f47eef21aeef1e4d0546861"
        ),
        "0012_worker_service_readiness": (
            "d63a3a839d428f63a0ddc1e98bfaefdb3a6ca18c046563e25b6f3f9e4fa18727"
        ),
        "0013_pattern_registry": (
            "c36861fe43b23e4da3009ed3df1270c9404468732064582083559dab560ee3b2"
        ),
    }
)


def classify_schema(snapshot: SchemaSnapshot) -> MigrationPlan | None:
    if snapshot.alembic_revisions:
        if len(snapshot.alembic_revisions) != 1:
            return None
        revision = snapshot.alembic_revisions[0]
        expected_fingerprint = EXPECTED_VERSIONED_SCHEMA_FINGERPRINTS.get(revision)
        if expected_fingerprint == _schema_fingerprint(snapshot):
            return MigrationPlan(stamp_revision=None, upgrade_revision="head")
        return None
    if (
        not snapshot.columns
        and not snapshot.constraints
        and not snapshot.indexes
        and not snapshot.defaults
    ):
        return MigrationPlan(stamp_revision=None, upgrade_revision="head")
    if snapshot == known_legacy_snapshot():
        return MigrationPlan(
            stamp_revision=LEGACY_REVISION,
            upgrade_revision="head",
        )
    return None


def _normalize_type(raw: object) -> str:
    if raw.__class__.__name__.lower() in {"datetime", "timestamp"}:
        return (
            "timestamp with time zone"
            if bool(getattr(raw, "timezone", False))
            else "timestamp without time zone"
        )
    value = str(raw).strip().lower()
    aliases = {
        "varchar(50)": "character varying(50)",
        "varchar(255)": "character varying(255)",
        "varchar(320)": "character varying(320)",
        "timestamp with time zone": "timestamp with time zone",
        "float": "double precision",
    }
    return aliases.get(value, value)


def _normalize_default(raw: object) -> str:
    # PostgreSQL may schema-qualify an owned sequence in inspector output even
    # when the canonical default was created without that qualification.
    return str(raw).strip().replace('"public".', "")


def _normalize_index_value(raw: object) -> object:
    if isinstance(raw, Mapping):
        return {
            str(key): _normalize_index_value(value)
            for key, value in sorted(raw.items(), key=lambda item: str(item[0]))
        }
    if isinstance(raw, (list, tuple)):
        return [_normalize_index_value(value) for value in raw]
    if raw is None or isinstance(raw, (bool, int, float)):
        return raw
    return " ".join(str(raw).split())


def _index_signature(table: str, index: Mapping[str, object]) -> str:
    columns = index.get("column_names") or ()
    payload = {
        "table": table,
        "name": index.get("name"),
        "columns": columns,
        "expressions": index.get("expressions") or (),
        "unique": bool(index.get("unique")),
        "include_columns": index.get("include_columns") or (),
        "column_sorting": index.get("column_sorting") or {},
        "dialect_options": index.get("dialect_options") or {},
    }
    normalized = _normalize_index_value(payload)
    return "ix:" + json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _constraint_signature(
    kind: str,
    table: str,
    constraint: Mapping[str, object],
) -> str:
    common = {
        "table": table,
        "name": constraint.get("name"),
        "options": constraint.get("options") or {},
        "dialect_options": constraint.get("dialect_options") or {},
    }
    if kind == "pk":
        payload = {
            **common,
            "columns": constraint.get("constrained_columns") or (),
        }
    elif kind == "uq":
        payload = {
            **common,
            "columns": constraint.get("column_names") or (),
        }
    elif kind == "fk":
        payload = {
            **common,
            "constrained_columns": constraint.get("constrained_columns") or (),
            "referred_schema": constraint.get("referred_schema"),
            "referred_table": constraint.get("referred_table"),
            "referred_columns": constraint.get("referred_columns") or (),
        }
    elif kind == "ck":
        payload = {
            **common,
            "sqltext": constraint.get("sqltext") or "",
        }
    else:
        raise ValueError(f"Unsupported constraint kind: {kind}")
    normalized = _normalize_index_value(payload)
    return (
        kind
        + ":"
        + json.dumps(
            normalized,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _snapshot(connection) -> SchemaSnapshot:
    inspector = inspect(connection)
    table_names = tuple(sorted(inspector.get_table_names(schema="public")))
    revisions: tuple[str, ...] = ()
    if "alembic_version" in table_names:
        revisions = tuple(
            sorted(
                row[0]
                for row in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            )
        )
        table_names = tuple(name for name in table_names if name != "alembic_version")

    columns: dict[str, tuple[ColumnSignature, ...]] = {}
    constraints: set[str] = set()
    indexes: set[str] = set()
    defaults: set[str] = set()
    for table in table_names:
        inspected_columns = inspector.get_columns(table, schema="public")
        columns[table] = tuple(
            (column["name"], _normalize_type(column["type"]), bool(column["nullable"]))
            for column in inspected_columns
        )
        defaults.update(
            f"{table}.{column['name']}={_normalize_default(column['default'])}"
            for column in inspected_columns
            if column.get("default") is not None
        )
        primary_key = inspector.get_pk_constraint(table, schema="public")
        primary_columns = primary_key.get("constrained_columns") or ()
        if primary_columns:
            constraints.add(_constraint_signature("pk", table, primary_key))
        for unique in inspector.get_unique_constraints(table, schema="public"):
            constraints.add(_constraint_signature("uq", table, unique))
        for foreign_key in inspector.get_foreign_keys(table, schema="public"):
            constraints.add(_constraint_signature("fk", table, foreign_key))
        for check in inspector.get_check_constraints(table, schema="public"):
            constraints.add(_constraint_signature("ck", table, check))
        for index in inspector.get_indexes(table, schema="public"):
            if index.get("duplicates_constraint"):
                continue
            indexes.add(_index_signature(table, index))
    return SchemaSnapshot(
        columns=MappingProxyType(columns),
        constraints=frozenset(constraints),
        indexes=frozenset(indexes),
        defaults=frozenset(defaults),
        alembic_revisions=revisions,
    )


async def _inspect_schema(database_url: str) -> SchemaSnapshot:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_snapshot)
    finally:
        await engine.dispose()


def inspect_schema(database_url: str) -> SchemaSnapshot:
    return asyncio.run(_inspect_schema(database_url))


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def run_preflight(database_url: str, *, apply: bool) -> int:
    snapshot = inspect_schema(database_url)
    plan = classify_schema(snapshot)
    if plan is None:
        print(
            "Refusing migration: schema does not exactly match its Alembic "
            "revision, an empty database, or the known legacy 0002 schema.",
            file=sys.stderr,
        )
        return 2

    action = (
        f"stamp {plan.stamp_revision}, then upgrade {plan.upgrade_revision}"
        if plan.stamp_revision
        else f"upgrade {plan.upgrade_revision}"
    )
    if not apply:
        print(f"Dry run only; would {action}. Re-run with --apply to mutate.")
        return 0

    config = _alembic_config(database_url)
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        if plan.stamp_revision:
            command.stamp(config, plan.stamp_revision)
        command.upgrade(config, plan.upgrade_revision)
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
    print(f"Applied schema preflight: {action}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed Alembic preflight for the Kaigo SaaS schema."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", ""),
        help="database URL (defaults to DATABASE_URL)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="allow stamp/upgrade mutations; omitted means inspection-only",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if os.getenv("KAIGO_ENVIRONMENT") != "production":
        print(
            "KAIGO_ENVIRONMENT=production is required for the production schema preflight",
            file=sys.stderr,
        )
        return 2
    if not args.database_url.strip():
        print("DATABASE_URL or --database-url is required", file=sys.stderr)
        return 2
    return run_preflight(args.database_url.strip(), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
