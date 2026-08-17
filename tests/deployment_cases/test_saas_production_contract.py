from __future__ import annotations

import asyncio
import os
import re
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from pathlib import Path
from textwrap import dedent
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import AppConfig


ROOT = Path(__file__).resolve().parents[2]
POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PRODUCTION_COMPOSE = (
    "COMPOSE_PROJECT_NAME=ai_project docker compose "
    "--file /opt/kaigo/current/docker-compose.yml "
    "--file /etc/kaigo/docker-compose.pattern-selection.yml"
)


def _service(compose: str, name: str, next_name: str) -> str:
    return compose.split(f"  {name}:", 1)[1].split(f"\n  {next_name}:", 1)[0]


def _location(config: str, declaration: str) -> str:
    return config.split(f"{declaration} {{", 1)[1].split("\n}", 1)[0]


def _unit_environment(unit: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in unit.splitlines():
        if not line.startswith("Environment=KAIGO_WORKER_"):
            continue
        name, value = line.removeprefix("Environment=").split("=", 1)
        values[name] = value
    return values


def _bash_block_containing(document: str, marker: str) -> str:
    marker_index = document.index(marker)
    block_start = document.rfind("```bash", 0, marker_index)
    assert block_start >= 0
    content_start = block_start + len("```bash")
    block_end = document.index("```", marker_index)
    return dedent(document[content_start:block_end]).strip()


def _worker_db_policy_allows(
    environment: dict[str, str],
    *,
    ingress_bridge: str,
    source: str,
    destination: str,
    port: int,
) -> bool:
    exact_database = (
        ingress_bridge == environment["KAIGO_WORKER_DATABASE_BRIDGE"]
        and source == environment["KAIGO_WORKER_DATABASE_CLIENT_ADDRESS"]
        and destination == environment["KAIGO_WORKER_DATABASE_ADDRESS"]
        and port == int(environment["KAIGO_WORKER_DATABASE_PORT"])
    )
    if exact_database:
        return True
    private_destinations = (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
    if any(ip_address(destination) in ip_network(value) for value in private_destinations):
        return False
    return ingress_bridge == environment["KAIGO_WORKER_BUILD_BRIDGE"]


def test_production_rejects_schema_auto_creation() -> None:
    with pytest.raises(ValueError, match="schema auto-creation"):
        AppConfig(
            database_url="postgresql+asyncpg://kaigo:test@db/kaigo",
            environment="production",
            auto_create_schema=True,
        )


def test_schema_auto_creation_is_explicit_and_disabled_by_default() -> None:
    config = AppConfig(database_url="sqlite+aiosqlite:///:memory:")
    assert config.auto_create_schema is False

    source = (ROOT / "app" / "db" / "session.py").read_text(encoding="utf-8")
    assert "if config.auto_create_schema:" in source


def test_alembic_has_one_production_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0019_founder_publication_funnel"]
    assert script.get_revision("0019_founder_publication_funnel").down_revision == (
        "0018_stage_aware_pattern_library"
    )
    assert script.get_revision("0018_stage_aware_pattern_library").down_revision == (
        "0017_funnel_journeys"
    )
    assert script.get_revision("0017_funnel_journeys").down_revision == (
        "0016_project_versions"
    )
    assert script.get_revision("0014_generation_forensics").down_revision == (
        "0013_pattern_registry"
    )


def test_preflight_classifies_only_the_exact_known_legacy_schema() -> None:
    from scripts.preflight_saas_schema import (
        MigrationPlan,
        classify_schema,
        known_legacy_snapshot,
    )

    legacy = known_legacy_snapshot()
    assert classify_schema(legacy) == MigrationPlan(
        stamp_revision="0002_widget_settings",
        upgrade_revision="head",
    )

    unknown_columns = dict(legacy.columns)
    unknown_columns["widgets"] = (
        *unknown_columns["widgets"],
        ("unexpected", "text", True),
    )
    assert classify_schema(legacy.replace(columns=unknown_columns)) is None

    unknown_tables = dict(legacy.columns)
    unknown_tables["operator_notes"] = (("id", "integer", False),)
    assert classify_schema(legacy.replace(columns=unknown_tables)) is None

    assert (
        classify_schema(
            legacy.replace(defaults=legacy.defaults | {"widgets.status='published'"})
        )
        is None
    )


def test_preflight_dry_run_never_calls_alembic_mutations(monkeypatch) -> None:
    from scripts import preflight_saas_schema as preflight

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        preflight,
        "inspect_schema",
        lambda _url: preflight.known_legacy_snapshot(),
    )
    monkeypatch.setattr(
        preflight.command,
        "stamp",
        lambda _config, revision: calls.append(("stamp", revision)),
    )
    monkeypatch.setattr(
        preflight.command,
        "upgrade",
        lambda _config, revision: calls.append(("upgrade", revision)),
    )

    assert preflight.run_preflight("postgresql+asyncpg://example", apply=False) == 0
    assert calls == []


@pytest.mark.parametrize("environment", [None, "prodution"])
def test_preflight_cli_requires_explicit_production_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: str | None,
) -> None:
    from scripts import preflight_saas_schema as preflight

    if environment is None:
        monkeypatch.delenv("KAIGO_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("KAIGO_ENVIRONMENT", environment)
    monkeypatch.setattr(
        "sys.argv",
        ["preflight_saas_schema.py", "--database-url", "postgresql://example"],
    )
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        lambda *_args, **_kwargs: pytest.fail(
            "schema inspection must not run outside explicit production mode"
        ),
    )

    assert preflight.main() == 2
    assert "KAIGO_ENVIRONMENT=production" in capsys.readouterr().err


def test_preflight_apply_stamps_exact_legacy_then_upgrades(monkeypatch) -> None:
    from scripts import preflight_saas_schema as preflight

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        preflight,
        "inspect_schema",
        lambda _url: preflight.known_legacy_snapshot(),
    )
    monkeypatch.setattr(
        preflight.command,
        "stamp",
        lambda _config, revision: calls.append(("stamp", revision)),
    )
    monkeypatch.setattr(
        preflight.command,
        "upgrade",
        lambda _config, revision: calls.append(("upgrade", revision)),
    )

    assert preflight.run_preflight("postgresql+asyncpg://example", apply=True) == 0
    assert calls == [
        ("stamp", "0002_widget_settings"),
        ("upgrade", "head"),
    ]


def test_preflight_apply_scopes_alembic_to_the_selected_database(monkeypatch) -> None:
    from scripts import preflight_saas_schema as preflight

    selected = "postgresql+asyncpg://selected/db"
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://wrong/db")
    monkeypatch.setattr(
        preflight,
        "inspect_schema",
        lambda _url: preflight.SchemaSnapshot({}, frozenset()),
    )
    observed: list[str] = []
    monkeypatch.setattr(
        preflight.command,
        "upgrade",
        lambda _config, _revision: observed.append(os.environ["DATABASE_URL"]),
    )

    assert preflight.run_preflight(selected, apply=True) == 0
    assert observed == [selected]
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://wrong/db"


def test_preflight_unknown_unversioned_schema_fails_closed(monkeypatch) -> None:
    from scripts import preflight_saas_schema as preflight

    unknown = preflight.known_legacy_snapshot().replace(
        constraints=frozenset({"unknown_constraint"})
    )
    monkeypatch.setattr(preflight, "inspect_schema", lambda _url: unknown)
    monkeypatch.setattr(
        preflight.command,
        "stamp",
        lambda *_args: pytest.fail("unknown schema must not be stamped"),
    )
    monkeypatch.setattr(
        preflight.command,
        "upgrade",
        lambda *_args: pytest.fail("unknown schema must not be upgraded"),
    )

    assert preflight.run_preflight("postgresql+asyncpg://example", apply=True) == 2


def test_preflight_known_version_with_schema_drift_fails_before_mutation(
    monkeypatch,
) -> None:
    from scripts import preflight_saas_schema as preflight

    legacy = preflight.known_legacy_snapshot()
    versioned = legacy.replace(alembic_revisions=(preflight.LEGACY_REVISION,))
    assert preflight.classify_schema(versioned) == preflight.MigrationPlan(
        stamp_revision=None,
        upgrade_revision="head",
    )

    extra_table = dict(legacy.columns)
    extra_table["operator_notes"] = (("id", "integer", False),)
    missing_table = dict(legacy.columns)
    missing_table.pop("widget_bindings")
    extra_column = dict(legacy.columns)
    extra_column["widgets"] = (
        *extra_column["widgets"],
        ("unexpected", "text", True),
    )
    missing_column = dict(legacy.columns)
    missing_column["widgets"] = missing_column["widgets"][:-1]
    altered_column = dict(legacy.columns)
    altered_column["widgets"] = (
        *altered_column["widgets"][:-1],
        ("max_tokens", "bigint", False),
    )
    missing_constraint = next(iter(legacy.constraints))
    drifted_schemas = (
        versioned.replace(columns=extra_table),
        versioned.replace(columns=missing_table),
        versioned.replace(columns=extra_column),
        versioned.replace(columns=missing_column),
        versioned.replace(columns=altered_column),
        versioned.replace(constraints=legacy.constraints | {"ck:widgets:false"}),
        versioned.replace(constraints=legacy.constraints - {missing_constraint}),
        versioned.replace(indexes=frozenset({"ix:widgets:status:False"})),
    )
    monkeypatch.setattr(
        preflight.command,
        "stamp",
        lambda *_args: pytest.fail("drifted schema must not be stamped"),
    )
    monkeypatch.setattr(
        preflight.command,
        "upgrade",
        lambda *_args: pytest.fail("drifted schema must not be upgraded"),
    )

    for drifted in drifted_schemas:
        monkeypatch.setattr(preflight, "inspect_schema", lambda _url: drifted)
        assert preflight.run_preflight("postgresql+asyncpg://example", apply=True) == 2


def test_preflight_has_exact_fingerprint_for_every_migration_revision() -> None:
    from scripts import preflight_saas_schema as preflight

    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revisions = {revision.revision for revision in scripts.walk_revisions()}

    assert set(preflight.EXPECTED_VERSIONED_SCHEMA_FINGERPRINTS) == revisions


def test_preflight_sqlalchemy_inspector_is_exactly_pinned() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    sqlalchemy_requirements = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip().lower().startswith("sqlalchemy")
    ]

    # Schema fingerprints include SQLAlchemy-inspected constraint text, whose
    # rendering can change between dependency releases without database drift.
    assert sqlalchemy_requirements == ["SQLAlchemy==2.0.49"]


def test_preflight_treats_column_order_as_non_semantic() -> None:
    from scripts import preflight_saas_schema as preflight

    legacy = preflight.known_legacy_snapshot()
    reordered_columns = dict(legacy.columns)
    reordered_columns["widgets"] = tuple(reversed(reordered_columns["widgets"]))
    reordered = legacy.replace(columns=reordered_columns)

    assert preflight._schema_fingerprint(reordered) == preflight._schema_fingerprint(
        legacy
    )
    assert preflight.classify_schema(reordered) == preflight.MigrationPlan(
        stamp_revision=preflight.LEGACY_REVISION,
        upgrade_revision="head",
    )


def test_preflight_fingerprint_includes_database_objects() -> None:
    from scripts import preflight_saas_schema as preflight

    legacy = preflight.known_legacy_snapshot()
    drifted = legacy.replace(
        database_objects=frozenset(
            {
                'function:{"name":"kaigo_0014_guard_legacy_model_call_insert"}',
                'function:{"name":"kaigo_0014_reconcile_legacy_model_call_update"}',
                'trigger:{"name":"trg_model_calls_0014_guard_legacy_insert"}',
                'trigger:{"name":"trg_model_calls_0014_legacy_terminal_update"}',
            }
        )
    )

    assert preflight._schema_fingerprint(drifted) != preflight._schema_fingerprint(
        legacy
    )
    assert preflight.classify_schema(drifted) is None


def test_preflight_index_signature_captures_partial_predicate_and_identity() -> None:
    from scripts import preflight_saas_schema as preflight

    active = {
        "name": "uq_subscriptions_one_active_user",
        "column_names": ["user_id"],
        "expressions": ["user_id"],
        "unique": True,
        "dialect_options": {
            "postgresql_include": [],
            "postgresql_nulls_not_distinct": False,
            "postgresql_where": "(status = 'active'::text)",
        },
    }
    expired = {
        **active,
        "dialect_options": {
            **active["dialect_options"],
            "postgresql_where": "(status = 'expired'::text)",
        },
    }

    active_signature = preflight._index_signature("subscriptions", active)
    expired_signature = preflight._index_signature("subscriptions", expired)

    assert active_signature != expired_signature
    assert "uq_subscriptions_one_active_user" in active_signature
    assert "user_id" in active_signature
    assert "status = 'active'" in active_signature


def test_preflight_constraint_signature_captures_names_and_semantic_options() -> None:
    from scripts import preflight_saas_schema as preflight

    unique = {
        "name": "uq_payment_attempt_user_idempotency",
        "column_names": ["user_id", "idempotency_key"],
        "dialect_options": {},
    }
    renamed_unique = {**unique, "name": "uq_payment_attempt_user_idempotency_drift"}
    deferred_fk = {
        "name": "fk_publications_active_release_id",
        "constrained_columns": ["active_release_id"],
        "referred_schema": None,
        "referred_table": "publication_releases",
        "referred_columns": ["id"],
        "options": {
            "ondelete": "SET NULL",
            "deferrable": True,
            "initially": "DEFERRED",
        },
        "dialect_options": {},
    }
    immediate_fk = {
        **deferred_fk,
        "options": {
            **deferred_fk["options"],
            "deferrable": False,
            "initially": "IMMEDIATE",
        },
    }

    unique_signature = preflight._constraint_signature("uq", "payment_attempts", unique)
    renamed_signature = preflight._constraint_signature(
        "uq", "payment_attempts", renamed_unique
    )
    deferred_signature = preflight._constraint_signature(
        "fk", "publications", deferred_fk
    )
    immediate_signature = preflight._constraint_signature(
        "fk", "publications", immediate_fk
    )

    assert unique_signature != renamed_signature
    assert "uq_payment_attempt_user_idempotency" in unique_signature
    assert deferred_signature != immediate_signature
    assert "DEFERRABLE" not in deferred_signature
    assert '"deferrable":true' in deferred_signature
    assert '"initially":"DEFERRED"' in deferred_signature


@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
def test_disposable_postgres_preflight_legacy_and_additive_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import preflight_saas_schema as preflight

    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_preflight_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)

    async def create_database() -> None:
        engine = create_async_engine(
            admin_url.render_as_string(hide_password=False),
            isolation_level="AUTOCOMMIT",
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        finally:
            await engine.dispose()

    async def execute(sql: str) -> None:
        engine = create_async_engine(rendered)
        try:
            async with engine.begin() as connection:
                await connection.execute(text(sql))
        finally:
            await engine.dispose()

    async def scalar(sql: str) -> str:
        engine = create_async_engine(rendered)
        try:
            async with engine.connect() as connection:
                value = await connection.scalar(text(sql))
                assert isinstance(value, str)
                return value
        finally:
            await engine.dispose()

    async def revision() -> str | None:
        engine = create_async_engine(rendered)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: MigrationContext.configure(sync).get_current_revision()
                )
        finally:
            await engine.dispose()

    async def drop_database() -> None:
        engine = create_async_engine(
            admin_url.render_as_string(hide_password=False),
            isolation_level="AUTOCOMMIT",
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid<>pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                await connection.execute(text(f'DROP DATABASE "{database_name}"'))
        finally:
            await engine.dispose()

    asyncio.run(create_database())
    monkeypatch.setenv("DATABASE_URL", rendered)
    config = Config(str(ROOT / "alembic.ini"))
    try:
        command.upgrade(config, "0002_widget_settings")
        asyncio.run(execute("DROP TABLE alembic_version"))

        snapshot = preflight.inspect_schema(rendered)
        assert snapshot == preflight.known_legacy_snapshot()
        assert preflight.run_preflight(rendered, apply=False) == 0
        assert preflight.inspect_schema(rendered).alembic_revisions == ()

        assert preflight.run_preflight(rendered, apply=True) == 0
        assert asyncio.run(revision()) == "0019_founder_publication_funnel"

        head_snapshot = preflight.inspect_schema(rendered)
        contact_columns = head_snapshot.columns["customer_contact_requests"]
        idempotency_column = (
            "idempotency_key",
            "varchar(128)",
            False,
        )
        assert idempotency_column in contact_columns
        idempotency_constraint = preflight._constraint_signature(
            "uq",
            "customer_contact_requests",
            {
                "name": "uq_customer_contact_request_user_idempotency",
                "column_names": ["user_id", "idempotency_key"],
                "dialect_options": {
                    "postgresql_include": [],
                    "postgresql_nulls_not_distinct": False,
                },
            },
        )
        assert idempotency_constraint in head_snapshot.constraints

        pre_idempotency_columns = tuple(
            column for column in contact_columns if column != idempotency_column
        )
        assert len(pre_idempotency_columns) == len(contact_columns) - 1
        pre_idempotency_snapshot = head_snapshot.replace(
            columns={
                **head_snapshot.columns,
                "customer_contact_requests": pre_idempotency_columns,
            },
            constraints=head_snapshot.constraints - {idempotency_constraint},
        )
        assert preflight._schema_fingerprint(pre_idempotency_snapshot) == (
            "ef9665be192c16c6fd6f4f0bcc3ccf952fc22ebf48c34ea600b2fc035f3696cd"
        )
        assert preflight._schema_fingerprint(head_snapshot) == (
            preflight.EXPECTED_VERSIONED_SCHEMA_FINGERPRINTS[
                "0019_founder_publication_funnel"
            ]
        )
        assert preflight.classify_schema(head_snapshot) == preflight.MigrationPlan(
            stamp_revision=None,
            upgrade_revision="head",
        )
        assert any(
            "kaigo_0014_guard_legacy_model_call_insert" in signature
            for signature in head_snapshot.database_objects
        )
        assert any(
            "trg_model_calls_0014_guard_legacy_insert" in signature
            for signature in head_snapshot.database_objects
        )
        assert any(
            "kaigo_0014_reconcile_legacy_model_call_update" in signature
            for signature in head_snapshot.database_objects
        )
        assert any(
            "trg_model_calls_0014_legacy_terminal_update" in signature
            for signature in head_snapshot.database_objects
        )

        legacy_update_function = asyncio.run(
            scalar(
                "SELECT pg_get_functiondef("
                "'public.kaigo_0014_reconcile_legacy_model_call_update()'"
                "::regprocedure)"
            )
        )
        legacy_insert_guard_function = asyncio.run(
            scalar(
                "SELECT pg_get_functiondef("
                "'public.kaigo_0014_guard_legacy_model_call_insert()'"
                "::regprocedure)"
            )
        )
        asyncio.run(
            execute(
                "DROP TRIGGER trg_model_calls_0014_legacy_terminal_update "
                "ON model_calls"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        asyncio.run(
            execute(
                "CREATE TRIGGER trg_model_calls_0014_legacy_terminal_update "
                "BEFORE UPDATE ON model_calls FOR EACH ROW EXECUTE FUNCTION "
                "kaigo_0014_reconcile_legacy_model_call_update()"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 0

        asyncio.run(
            execute(
                "DROP TRIGGER trg_model_calls_0014_legacy_terminal_update "
                "ON model_calls"
            )
        )
        asyncio.run(
            execute(
                "CREATE TRIGGER trg_model_calls_0014_legacy_terminal_update "
                "BEFORE UPDATE OF cost_state ON model_calls FOR EACH ROW "
                "EXECUTE FUNCTION "
                "kaigo_0014_reconcile_legacy_model_call_update()"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        asyncio.run(
            execute(
                "DROP TRIGGER trg_model_calls_0014_legacy_terminal_update "
                "ON model_calls"
            )
        )
        asyncio.run(
            execute(
                "CREATE TRIGGER trg_model_calls_0014_legacy_terminal_update "
                "BEFORE UPDATE ON model_calls FOR EACH ROW EXECUTE FUNCTION "
                "kaigo_0014_reconcile_legacy_model_call_update()"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 0

        asyncio.run(
            execute(
                "CREATE OR REPLACE FUNCTION "
                "kaigo_0014_reconcile_legacy_model_call_update() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RETURN NEW; END; $$"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        asyncio.run(execute(legacy_update_function))
        assert preflight.run_preflight(rendered, apply=False) == 0

        asyncio.run(
            execute(
                "ALTER FUNCTION "
                "kaigo_0014_reconcile_legacy_model_call_update() "
                "SET search_path TO pg_catalog"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        asyncio.run(
            execute(
                "ALTER FUNCTION "
                "kaigo_0014_reconcile_legacy_model_call_update() RESET ALL"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 0

        asyncio.run(
            execute(
                "DROP TRIGGER trg_model_calls_0014_guard_legacy_insert "
                "ON model_calls"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        asyncio.run(
            execute(
                "CREATE TRIGGER trg_model_calls_0014_guard_legacy_insert "
                "BEFORE INSERT ON model_calls FOR EACH ROW "
                "EXECUTE FUNCTION kaigo_0014_guard_legacy_model_call_insert()"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 0

        asyncio.run(
            execute(
                "CREATE OR REPLACE FUNCTION "
                "kaigo_0014_guard_legacy_model_call_insert() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        asyncio.run(execute(legacy_insert_guard_function))
        assert preflight.run_preflight(rendered, apply=False) == 0

        asyncio.run(execute("ALTER TABLE funnel_events ADD COLUMN unexpected TEXT"))
        assert preflight.run_preflight(rendered, apply=False) == 2
        assert asyncio.run(revision()) == "0019_founder_publication_funnel"
        asyncio.run(execute("ALTER TABLE funnel_events DROP COLUMN unexpected"))

        asyncio.run(
            execute(
                "ALTER TABLE payment_attempts RENAME CONSTRAINT "
                "uq_payment_attempt_user_idempotency TO "
                "uq_payment_attempt_user_idempotency_drift"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        asyncio.run(
            execute(
                "ALTER TABLE payment_attempts RENAME CONSTRAINT "
                "uq_payment_attempt_user_idempotency_drift TO "
                "uq_payment_attempt_user_idempotency"
            )
        )

        asyncio.run(
            execute(
                "ALTER TABLE publications ALTER CONSTRAINT "
                "fk_publications_active_release_id "
                "NOT DEFERRABLE INITIALLY IMMEDIATE"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        asyncio.run(
            execute(
                "ALTER TABLE publications ALTER CONSTRAINT "
                "fk_publications_active_release_id "
                "DEFERRABLE INITIALLY DEFERRED"
            )
        )
        restored_head = preflight.inspect_schema(rendered)
        assert preflight.classify_schema(restored_head) == preflight.MigrationPlan(
            stamp_revision=None,
            upgrade_revision="head",
        )

        command.downgrade(config, "0016_project_versions")
        assert asyncio.run(revision()) == "0016_project_versions"
        project_versions_snapshot = preflight.inspect_schema(rendered)
        assert preflight._schema_fingerprint(project_versions_snapshot) == (
            preflight.EXPECTED_VERSIONED_SCHEMA_FINGERPRINTS[
                "0016_project_versions"
            ]
        )
        assert preflight.classify_schema(
            project_versions_snapshot
        ) == preflight.MigrationPlan(
            stamp_revision=None,
            upgrade_revision="head",
        )
        command.upgrade(config, "head")
        assert asyncio.run(revision()) == "0019_founder_publication_funnel"

        command.downgrade(config, "0009_billing_foundation")
        assert asyncio.run(revision()) == "0009_billing_foundation"
        billing_snapshot = preflight.inspect_schema(rendered)
        assert preflight.classify_schema(billing_snapshot) == preflight.MigrationPlan(
            stamp_revision=None,
            upgrade_revision="head",
        )
        asyncio.run(execute("DROP INDEX uq_subscriptions_one_active_user"))
        asyncio.run(
            execute(
                "CREATE UNIQUE INDEX uq_subscriptions_one_active_user "
                "ON subscriptions (user_id) WHERE status = 'expired'"
            )
        )
        assert preflight.run_preflight(rendered, apply=False) == 2
        assert asyncio.run(revision()) == "0009_billing_foundation"
        asyncio.run(execute("DROP INDEX uq_subscriptions_one_active_user"))
        asyncio.run(
            execute(
                "CREATE UNIQUE INDEX uq_subscriptions_one_active_user "
                "ON subscriptions (user_id) WHERE status = 'active'"
            )
        )
        command.upgrade(config, "head")
        assert asyncio.run(revision()) == "0019_founder_publication_funnel"
    finally:
        asyncio.run(drop_database())


@pytest.mark.parametrize(
    "name",
    [
        "GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION",
        "GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION",
    ],
)
def test_production_generation_router_requires_positive_explicit_prices(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    from scripts import run_builder_worker

    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "100")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "200")
    monkeypatch.setenv(name, "0")

    with pytest.raises(RuntimeError, match=name):
        run_builder_worker.runtime_model_prices()

    monkeypatch.delenv(name)
    with pytest.raises(RuntimeError, match=name):
        run_builder_worker.runtime_model_prices()


def test_env_example_covers_public_runtime_and_pricing_without_values() -> None:
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    required = {
        "KAIGO_ENVIRONMENT",
        "KAIGO_AUTO_CREATE_SCHEMA",
        "KAIGO_PUBLIC_AUTH_ENABLED",
        "KAIGO_PUBLIC_BASE_URL",
        "KAIGO_SESSION_COOKIE_NAME",
        "KAIGO_SESSION_TTL_SECONDS",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "YANDEX_OAUTH_CLIENT_ID",
        "YANDEX_OAUTH_CLIENT_SECRET",
        "KAIGO_PUBLICATION_ALLOW_INSECURE_ORIGINS",
        "KAIGO_PUBLICATION_CHAT_SIGNING_SECRET",
        "KAIGO_PUBLICATION_CHAT_CAPABILITY_TTL_SECONDS",
        "KAIGO_PUBLICATION_CHAT_KEY_RATE_LIMIT_REQUESTS",
        "KAIGO_PUBLICATION_CHAT_IP_RATE_LIMIT_REQUESTS",
        "KAIGO_PUBLICATION_CHAT_TRUSTED_PROXY_CIDRS",
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "YOOKASSA_TEST_MODE",
        "YOOKASSA_TIMEOUT_SECONDS",
        "GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION",
        "GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION",
    }
    names = {
        line.split("=", 1)[0]
        for line in env.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert required <= names
    for secret_name in (
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "YANDEX_OAUTH_CLIENT_SECRET",
        "YOOKASSA_SECRET_KEY",
        "KAIGO_PUBLICATION_CHAT_SIGNING_SECRET",
    ):
        assert f"{secret_name}=\n" in env


def test_compose_isolates_database_and_delegates_worker_restart_to_systemd() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    app = _service(compose, "app", "builder-worker")
    worker = _service(compose, "builder-worker", "migration")
    migration = _service(compose, "migration", "database-ops")
    database_ops = _service(compose, "database-ops", "builder-lab")
    database = compose.split("\n  db:", 1)[1].split("\nvolumes:", 1)[0]

    assert "\n    ports:" not in database
    assert "kaigo_app_db" in app
    assert "kaigo_app_public" in app
    assert "kaigo_builder_research" in worker
    assert "kaigo_worker_db" in worker
    assert "env_file:" not in worker
    assert "kaigo_app_db" in database
    assert "kaigo_worker_db" in database
    assert "kaigo_builder_research" not in database
    assert compose.count("internal: true") == 2
    assert 'restart: "no"' in worker
    assert "profiles:" in worker and "saas-worker" in worker
    assert "cap_drop:" in worker and "- ALL" in worker
    assert "no-new-privileges:true" in worker
    assert "shm_size: 1gb" in worker
    assert "/app/data/builder-evidence:size=536870912" in worker
    assert "image: ${KAIGO_APP_IMAGE" in app
    assert "image: ${KAIGO_BUILDER_WORKER_IMAGE" in worker
    assert "image: ${KAIGO_APP_IMAGE" in migration
    assert "kaigo_app_db" in migration
    assert "kaigo_app_public" not in migration
    assert "kaigo_app_db" in database_ops
    assert "kaigo_app_public" not in database_ops
    assert "profiles:" in migration and "operations" in migration
    assert "profiles:" in database_ops and "operations" in database_ops

    unit = (ROOT / "deploy" / "systemd" / "kaigo-builder-worker.service").read_text(
        encoding="utf-8"
    )
    assert (
        "ExecStartPre=+/bin/bash /opt/kaigo/current/scripts/apply_builder_egress_guard.sh"
        in unit
    )
    assert "EnvironmentFile=/etc/kaigo/builder-worker-image.env" in unit
    assert "EnvironmentFile=/etc/kaigo/builder-worker-egress.env" in unit
    assert "EnvironmentFile=/etc/kaigo/builder-worker-antigravity.env" in unit
    assert "EnvironmentFile=-/etc/kaigo/builder-worker-egress.env" not in unit
    assert "EnvironmentFile=-/etc/kaigo/builder-worker-antigravity.env" not in unit
    assert (
        'test "$$(stat -c %u:%g:%a /etc/kaigo/builder-worker-egress.env)" '
        '= "0:0:600"'
    ) in unit
    assert (
        'test "$$(stat -c %u:%g:%a /etc/kaigo/builder-worker-antigravity.env)" '
        '= "0:0:600"'
    ) in unit
    assert (
        '[[ "$KAIGO_BUILDER_WORKER_IMAGE" =~ '
        "^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]" in unit
    )
    assert 'docker image inspect "$KAIGO_BUILDER_WORKER_IMAGE"' in unit
    assert (
        "docker compose --profile saas-worker up --no-build --no-deps "
        "--abort-on-container-exit --exit-code-from builder-worker builder-worker"
    ) in unit
    assert "Restart=always" in unit


def test_production_entrypoints_pin_exact_environment() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    app = _service(compose, "app", "builder-worker")
    worker = _service(compose, "builder-worker", "migration")
    migration = _service(compose, "migration", "database-ops")
    unit = (ROOT / "deploy" / "systemd" / "kaigo-builder-worker.service").read_text(
        encoding="utf-8"
    )
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    for service in (app, worker, migration):
        assert "KAIGO_ENVIRONMENT: production" in service
    assert "Environment=KAIGO_ENVIRONMENT=production" in unit
    assert unit.index("EnvironmentFile=/etc/kaigo/builder-worker-image.env") < unit.index(
        "Environment=KAIGO_ENVIRONMENT=production"
    )
    assert unit.index("EnvironmentFile=/etc/kaigo/builder-worker-egress.env") < unit.index(
        "ExecStartPre=+/bin/bash /opt/kaigo/current/scripts/apply_builder_egress_guard.sh"
    )
    assert "KAIGO_ENVIRONMENT=production" in runbook


def test_production_runbook_installs_fail_closed_builder_https_egress_file() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    assert "/etc/kaigo/builder-worker-egress.env" in runbook
    assert "KAIGO_WORKER_PUBLIC_HTTPS_HOST=5.129.236.90/32" in runbook
    assert "KAIGO_WORKER_PUBLIC_HTTPS_PORT=443" in runbook
    assert "install -o root -g root -m 600" in runbook
    assert "EnvironmentFile=/etc/kaigo/builder-worker-egress.env" in runbook
    assert "EnvironmentFile=-/etc/kaigo/builder-worker-egress.env" not in runbook
    install_block = _bash_block_containing(
        runbook, "KAIGO_WORKER_PUBLIC_HTTPS_HOST=5.129.236.90/32"
    )
    assert install_block.startswith("set -euo pipefail\nset +x\n")


def test_production_runbook_keeps_antigravity_secret_builder_only() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    assert "/etc/kaigo/builder-worker-antigravity.env" in runbook
    assert "EnvironmentFile=/etc/kaigo/builder-worker-antigravity.env" in runbook
    assert "install -o root -g root -m 600" in runbook
    assert "Do not put the AntiGravity bearer key in" in runbook
    assert "`/etc/kaigo/kaigo.env`" in runbook
    install_block = _bash_block_containing(
        runbook, "KAIGO_ANTIGRAVITY_API_BASE_URL=https://kaigo.space/antigravity-api"
    )
    assert install_block.startswith("set -euo pipefail\nset +x\n")
    assert "KAIGO_ANTIGRAVITY_API_ENABLED=false" in install_block
    assert "KAIGO_ANTIGRAVITY_API_ENABLED=true" not in install_block


def test_production_runbook_smokes_provider_before_atomic_antigravity_enable() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    provider_smoke = runbook.index("ANTIGRAVITY_PROVIDER_SMOKE_RESPONSE")
    enable = runbook.index(
        "s/^KAIGO_ANTIGRAVITY_API_ENABLED=false$/"
        "KAIGO_ANTIGRAVITY_API_ENABLED=true/"
    )
    worker_restart = runbook.index("systemctl restart kaigo-builder-worker", enable)
    canary = runbook.index("use a real browser OAuth session", enable)
    enable_block = _bash_block_containing(
        runbook,
        "s/^KAIGO_ANTIGRAVITY_API_ENABLED=false$/"
        "KAIGO_ANTIGRAVITY_API_ENABLED=true/",
    )

    assert provider_smoke < enable < worker_restart < canary
    assert enable_block.startswith("set -euo pipefail\nset +x\n")
    assert "mv -f" in enable_block
    assert "chown root:root" in enable_block
    assert "chmod 600" in enable_block
    assert "Authorization: Bearer" in runbook
    assert '"response_format":{"type":"json_schema"' in runbook
    assert 'and .reasoning_effort == "high"' in runbook
    assert 'and (.request_id | type == "string" and length > 0)' in runbook
    assert 'all(.[]; type == "number" and . >= 0 and floor == .)' in runbook
    assert 'and ((.output_text | fromjson) == {"ready":true})' in runbook
    assert 'jq -e \'\n  .conversation_deleted == true' in runbook


def test_production_runbook_has_atomic_antigravity_disable_for_rollback() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    rollback = runbook.split("## Rollback", 1)[1]
    disable_marker = (
        "s/^KAIGO_ANTIGRAVITY_API_ENABLED=true$/"
        "KAIGO_ANTIGRAVITY_API_ENABLED=false/"
    )
    disable_block = _bash_block_containing(rollback, disable_marker)

    assert disable_block.startswith("set -euo pipefail\nset +x\n")
    assert "mv -f" in disable_block
    assert "chown root:root" in disable_block
    assert "chmod 600" in disable_block
    assert rollback.index(disable_marker) < rollback.index(
        "systemctl restart kaigo-builder-worker"
    )


def test_production_runbook_enables_bridge_netfilter_before_worker_start() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    module_load = runbook.index("/etc/modules-load.d/kaigo-builder.conf")
    module_activate = runbook.index("modprobe br_netfilter")
    sysctl_config = runbook.index("net.bridge.bridge-nf-call-iptables = 1")
    sysctl_verify = runbook.index(
        'test "$(cat /proc/sys/net/bridge/bridge-nf-call-iptables)" = 1'
    )
    worker_start = runbook.index("systemctl restart kaigo-builder-worker")

    assert module_load < module_activate < sysctl_config < sysctl_verify < worker_start


def test_every_production_compose_command_uses_live_project_and_overlay() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    unit = (ROOT / "deploy" / "systemd" / "kaigo-builder-worker.service").read_text(
        encoding="utf-8"
    )

    compose_lines = [line for line in runbook.splitlines() if "docker compose" in line]
    assert compose_lines
    assert all(PRODUCTION_COMPOSE in line for line in compose_lines)
    assert "COMPOSE_PROJECT_NAME=kaigo docker compose" not in runbook
    # The checked-in base unit is intentionally overridden by the production
    # drop-in; this contract only prevents the operator runbook creating a new
    # project alongside the live ai_project stack.
    assert "Environment=COMPOSE_PROJECT_NAME=kaigo" in unit


def test_worker_network_contract_uses_non_overlapping_fixed_addresses() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker = _service(compose, "builder-worker", "migration")
    database = compose.split("\n  db:", 1)[1].split("\nvolumes:", 1)[0]
    unit = (ROOT / "deploy" / "systemd" / "kaigo-builder-worker.service").read_text(
        encoding="utf-8"
    )
    environment = _unit_environment(unit)

    subnets = [
        ip_network(value)
        for value in re.findall(r"^\s+- subnet: ([0-9./]+)$", compose, re.MULTILINE)
    ]
    assert len(subnets) == 3
    for index, subnet in enumerate(subnets):
        assert not any(subnet.overlaps(other) for other in subnets[index + 1 :])

    fixed_addresses = [
        ip_address(value)
        for value in re.findall(
            r"^\s+ipv4_address: ([0-9.]+)$", compose, re.MULTILINE
        )
    ]
    assert len(fixed_addresses) == len(set(fixed_addresses))
    assert all(any(address in subnet for subnet in subnets) for address in fixed_addresses)

    worker_address = environment["KAIGO_WORKER_DATABASE_CLIENT_ADDRESS"]
    database_address = environment["KAIGO_WORKER_DATABASE_ADDRESS"]
    database_subnet = ip_network(environment["KAIGO_WORKER_DATABASE_SUBNET"])
    database_gateway = ip_address(environment["KAIGO_WORKER_DATABASE_GATEWAY"])
    assert IPv4Address(worker_address) in database_subnet
    assert IPv4Address(database_address) in database_subnet
    assert database_gateway in database_subnet
    assert len({worker_address, database_address, str(database_gateway)}) == 3
    assert f"ipv4_address: {worker_address}" in worker
    assert f"ipv4_address: {database_address}" in database
    assert f"gateway: {database_gateway}" in compose
    assert "com.docker.network.bridge.name: br-kaigo-wdb" in compose
    assert environment["KAIGO_WORKER_DATABASE_BRIDGE"] == "br-kaigo-wdb"


def test_builder_egress_contract_contains_both_worker_interfaces_and_docker_dns() -> None:
    guard = (ROOT / "scripts" / "apply_builder_egress_guard.sh").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker = _service(compose, "builder-worker", "migration")
    unit = (ROOT / "deploy" / "systemd" / "kaigo-builder-worker.service").read_text(
        encoding="utf-8"
    )
    assert "169.254.0.0/16" in guard
    assert (
        "KAIGO_REFERENCE_NATIVE_TRANSPORT: "
        "${KAIGO_REFERENCE_NATIVE_TRANSPORT:-false}"
    ) in worker
    assert (
        "KAIGO_REFERENCE_VIEWPORT_CONCURRENCY: "
        "${KAIGO_REFERENCE_VIEWPORT_CONCURRENCY:-2}"
    ) in worker
    assert "127.0.0.0/8" in guard
    assert "10.0.0.0/8" in guard
    assert 'echo "-A ${FORWARD_CHAIN} -j RETURN"' in guard
    assert "\n    dns:" not in worker
    assert "127.0.0.11" in unit
    assert "KAIGO-WDB-FWD-G1" in unit
    assert "KAIGO-WDB-HOST-G1" in unit
    allow = (
        '-i "$${KAIGO_WORKER_DATABASE_BRIDGE}" '
        '-s "$${KAIGO_WORKER_DATABASE_CLIENT_ADDRESS}/32" '
        '-d "$${KAIGO_WORKER_DATABASE_ADDRESS}/32" -p tcp '
        '-m tcp --dport "$${KAIGO_WORKER_DATABASE_PORT}" '
        '-m conntrack --ctstate NEW -j ACCEPT'
    )
    reject = (
        '-i "$${KAIGO_WORKER_DATABASE_BRIDGE}" '
        '-m conntrack --ctstate NEW -j REJECT'
    )
    assert allow in unit
    assert reject in unit
    assert unit.index(allow) < unit.index(reject)
    assert "-A KAIGO-WDB-HOST-G1 -m conntrack --ctstate NEW -j REJECT" in unit
    environment = _unit_environment(unit)
    assert environment["KAIGO_WORKER_BUILD_BRIDGE"] == "br-kaigo-build"
    assert environment["KAIGO_WORKER_BUILD_CLIENT_ADDRESS"] == "172.30.240.2"
    assert "ipv4_address: 172.30.240.2" in worker
    interface_loop = (
        'for interface in "$${KAIGO_WORKER_BUILD_BRIDGE}" '
        '"$${KAIGO_WORKER_DATABASE_BRIDGE}"'
    )
    assert unit.count(interface_loop) == 4
    assert '-C DOCKER-USER -i "$$interface" -j KAIGO-WDB-FWD-G1' in unit
    assert '-C INPUT -i "$$interface" -j KAIGO-WDB-HOST-G1' in unit
    immutable_guard = unit.index(
        "ExecStartPre=+/bin/bash /opt/kaigo/current/scripts/apply_builder_egress_guard.sh"
    )
    first_policy_cleanup = unit.index(
        'while iptables -w 10 -C DOCKER-USER -i "$$interface"'
    )
    preflight = unit[:immutable_guard]
    setup = unit[immutable_guard:first_policy_cleanup]
    assert "/proc/sys/net/bridge/bridge-nf-call-iptables" in preflight
    assert "docker info" in preflight
    assert '[[ "$$worker_running" == false ]]' in preflight
    assert "docker info" in setup
    assert '[[ "$$worker_running" == false ]]' in setup

    hook_lines = (
        '-I DOCKER-USER 2 -i "$${KAIGO_WORKER_BUILD_BRIDGE}" '
        "-j KAIGO-WDB-FWD-G1",
        '-I DOCKER-USER 3 -i "$${KAIGO_WORKER_DATABASE_BRIDGE}" '
        "-j KAIGO-WDB-FWD-G1",
        '-I INPUT 2 -i "$${KAIGO_WORKER_BUILD_BRIDGE}" -j KAIGO-WDB-HOST-G1',
        '-I INPUT 3 -i "$${KAIGO_WORKER_DATABASE_BRIDGE}" -j KAIGO-WDB-HOST-G1',
    )
    hook_positions = [unit.index(line) for line in hook_lines]
    assert hook_positions == sorted(hook_positions)
    assert first_policy_cleanup < hook_positions[0]
    for private_destination in (
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
    ):
        assert private_destination in unit


def test_worker_db_firewall_smoke_blocks_rebinding_targets_and_tears_down() -> None:
    unit = (ROOT / "deploy" / "systemd" / "kaigo-builder-worker.service").read_text(
        encoding="utf-8"
    )
    environment = _unit_environment(unit)
    subnet = IPv4Network(environment["KAIGO_WORKER_DATABASE_SUBNET"])
    database = environment["KAIGO_WORKER_DATABASE_ADDRESS"]
    gateway = environment["KAIGO_WORKER_DATABASE_GATEWAY"]
    unused_rebinding_target = str(subnet.broadcast_address - 1)

    database_bridge = environment["KAIGO_WORKER_DATABASE_BRIDGE"]
    build_bridge = environment["KAIGO_WORKER_BUILD_BRIDGE"]
    database_source = environment["KAIGO_WORKER_DATABASE_CLIENT_ADDRESS"]
    build_source = "172.30.240.2"

    assert _worker_db_policy_allows(
        environment,
        ingress_bridge=database_bridge,
        source=database_source,
        destination=database,
        port=5432,
    )
    assert not _worker_db_policy_allows(
        environment,
        ingress_bridge=database_bridge,
        source=database_source,
        destination=database,
        port=80,
    )
    assert not _worker_db_policy_allows(
        environment,
        ingress_bridge=database_bridge,
        source=database_source,
        destination=gateway,
        port=5432,
    )
    assert not _worker_db_policy_allows(
        environment,
        ingress_bridge=database_bridge,
        source=database_source,
        destination=unused_rebinding_target,
        port=443,
    )
    assert not _worker_db_policy_allows(
        environment,
        ingress_bridge=build_bridge,
        source=database_source,
        destination="169.254.169.254",
        port=80,
    )
    assert not _worker_db_policy_allows(
        environment,
        ingress_bridge=database_bridge,
        source=build_source,
        destination=database,
        port=5432,
    )
    assert _worker_db_policy_allows(
        environment,
        ingress_bridge=build_bridge,
        source=database_source,
        destination="8.8.8.8",
        port=443,
    )

    assert unit.count("ExecStopPost=+") == 1
    stop = unit.split("ExecStopPost=+", 1)[1]
    teardown = stop.index("for interface in")
    assert "docker container inspect" in stop[:teardown]
    assert "docker info" in stop[:teardown]
    assert '[[ "$$worker_running" == false ]]' in stop[:teardown]
    for chain in ("KAIGO-WDB-FWD-G1", "KAIGO-WDB-HOST-G1"):
        assert chain in stop
    assert "for interface in" in stop
    assert 'while iptables -w 10 -C DOCKER-USER -i "$$interface"' in stop
    assert 'while iptables -w 10 -C INPUT -i "$$interface"' in stop
    assert "iptables -w 10 -F KAIGO-WDB-FWD-G1" in stop
    assert "iptables -w 10 -X KAIGO-WDB-FWD-G1" in stop
    assert "iptables -w 10 -F KAIGO-WDB-HOST-G1" in stop
    assert "iptables -w 10 -X KAIGO-WDB-HOST-G1" in stop


def test_nginx_public_studio_api_sse_publication_and_billing_contract() -> None:
    config = (ROOT / "deploy" / "nginx" / "kaigo-marketing-site.conf").read_text(
        encoding="utf-8"
    )
    studio = _location(config, "location = /studio")
    studio_slash = _location(config, "location = /studio/")
    builder = _location(config, "location ^~ /builder/")
    api = _location(config, "location ^~ /api/")
    runtime = _location(config, "location ^~ /runtime/")
    embed = config.split("location ~ ^/embed/", 1)[1].split("\n}", 1)[0]
    billing = _location(config, "location ^~ /billing/")

    assert "auth_basic" not in studio + studio_slash
    assert "auth_basic" in builder
    assert "proxy_pass http://127.0.0.1:8080;" in api
    assert "proxy_buffering off;" in api
    assert "proxy_cache off;" in api
    assert "proxy_read_timeout 1800s;" in api
    for block in (runtime, embed, billing):
        assert "proxy_pass http://127.0.0.1:8080;" in block
        assert "auth_basic" not in block
    assert "location = /api/billing/webhooks/yookassa" in config
    assert "Content-Security-Policy" in _location(config, "location /assets/")


def test_operations_runbook_orders_preflight_before_services_and_documents_rollback() -> (
    None
):
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(encoding="utf-8")
    local_build = runbook.index(
        'docker build --pull --tag "kaigo-app-build:$RELEASE_COMMIT" .'
    )
    local_digest = runbook.index("docker image inspect --format '{{.Id}}'")
    image_pull = runbook.index(
        f"{PRODUCTION_COMPOSE} pull app builder-worker migration"
    )
    image_digest = runbook.index('docker image inspect "$KAIGO_APP_IMAGE"')
    backup = runbook.index(
        f"{PRODUCTION_COMPOSE} --profile operations run --rm database-ops pg_dump"
    )
    dry_run = runbook.index(
        f"{PRODUCTION_COMPOSE} --profile operations run --rm migration python "
        "scripts/preflight_saas_schema.py"
    )
    apply = runbook.index(
        f"{PRODUCTION_COMPOSE} --profile operations run --rm migration python "
        "scripts/preflight_saas_schema.py --apply"
    )
    app_start = runbook.index(f"{PRODUCTION_COMPOSE} up -d --no-build app")
    worker_start = runbook.index("systemctl restart kaigo-builder-worker")
    assert local_build < local_digest < backup
    assert (
        image_pull < image_digest < backup < dry_run < apply < app_start < worker_start
    )
    assert "KAIGO_APP_IMAGE=" in runbook
    assert "KAIGO_BUILDER_WORKER_IMAGE=" in runbook
    assert "@sha256:" in runbook
    assert runbook.count("^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$") >= 5
    assert 'docker build --pull --tag "kaigo-app-build:$RELEASE_COMMIT" .' in runbook
    assert (
        "docker build --pull --file Dockerfile.builder-lab --tag "
        '"kaigo-worker-build:$RELEASE_COMMIT" .' in runbook
    )
    assert "docker image inspect --format '{{.Id}}'" in runbook
    assert "Local image IDs cannot be pulled" in runbook
    assert "never rebuild" in runbook
    assert "alembic downgrade -1" not in runbook
    assert 'alembic downgrade "$KAIGO_PREVIOUS_ALEMBIC_REVISION"' in runbook
    assert "rollback.env" in runbook
    assert "nginx -t" in runbook
    assert "No live rollout" in runbook


def test_rollback_resolves_schema_and_routes_before_starting_worker() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    rollback = runbook.split("## Rollback", 1)[1]

    stop = rollback.index("systemctl stop kaigo-builder-worker")
    downgrade = rollback.index(
        'alembic downgrade "$KAIGO_PREVIOUS_ALEMBIC_REVISION"'
    )
    final_preflight = rollback.rindex(
        'docker run --rm --network kaigo_app_db --env-file .env '
        '"$KAIGO_ROLLBACK_MIGRATION_IMAGE" python scripts/preflight_saas_schema.py'
    )
    app_start = rollback.index(f"{PRODUCTION_COMPOSE} up -d --no-build app")
    app_route_check = rollback.index("curl -fsS http://127.0.0.1:8080/api/health")
    edge_route_check = rollback.index("curl -fsS https://kaigo.space/studio/")
    worker_start = rollback.index("systemctl restart kaigo-builder-worker")
    readiness = rollback.index(
        'curl -fsS -H @"$KAIGO_READINESS_HEADER_FILE" '
        "https://kaigo.space/api/ready"
    )
    canary = rollback.index(
        "python scripts/smoke_saas_foundation.py"
    )

    assert (
        stop
        < downgrade
        < final_preflight
        < app_start
        < app_route_check
        < edge_route_check
        < worker_start
        < readiness
        < canary
    )
    assert rollback.count("systemctl restart kaigo-builder-worker") == 1


def test_rollback_uses_current_trusted_migration_image_and_explicit_decision() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    rollback = runbook.split("## Rollback", 1)[1]

    assert "KAIGO_ROLLBACK_SCHEMA_DECISION" in rollback
    assert "keep-forward-compatible" in rollback
    assert "downgrade-to-snapshot" in rollback
    assert "alembic downgrade -1" not in rollback
    assert 'alembic downgrade "$KAIGO_PREVIOUS_ALEMBIC_REVISION"' in rollback
    assert (
        'docker run --rm --network kaigo_app_db --env-file .env '
        '"$KAIGO_ROLLBACK_MIGRATION_IMAGE"' in rollback
    )
    assert (
        "docker image inspect \"$KAIGO_ROLLBACK_MIGRATION_IMAGE\""
        in rollback
    )
    assert (
        f"{PRODUCTION_COMPOSE} --profile operations run --rm migration" not in rollback
    )


def test_rollback_requires_complete_tuple_and_installs_root_only_envs_atomically() -> (
    None
):
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    rollback = runbook.split("## Rollback", 1)[1]

    for name in (
        "KAIGO_APP_IMAGE",
        "KAIGO_BUILDER_WORKER_IMAGE",
        "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY",
        "KAIGO_RELEASE_ID",
        "KAIGO_BUILDER_WORKER_BOOT_ID",
        "KAIGO_PREVIOUS_ALEMBIC_REVISION",
        "KAIGO_ROLLBACK_MIGRATION_IMAGE",
    ):
        assert f': "${{{name}:?' in rollback
    assert "release.env.tmp" in rollback
    assert "builder-worker-image.env.tmp" in rollback
    assert rollback.count("chmod 600") >= 2
    assert "mv -f" in rollback
