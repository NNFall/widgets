import importlib

from app.saas.models import GenerationEvent, GenerationRun


def test_worker_indexes_are_declared_in_model_metadata() -> None:
    run_indexes = {index.name: index for index in GenerationRun.__table__.indexes}
    event_indexes = {index.name: index for index in GenerationEvent.__table__.indexes}

    assert "ix_generation_runs_worker_claim" in run_indexes
    assert [
        expression.name
        for expression in run_indexes["ix_generation_runs_worker_claim"].expressions
    ] == [
        "state",
        "retry_not_before",
        "lease_expires_at",
        "created_at",
        "id",
    ]
    assert "ix_generation_events_run_type_sequence_desc" in event_indexes


def test_worker_hardening_migration_upgrade_and_downgrade(monkeypatch) -> None:
    migration = importlib.import_module(
        "migrations.versions.0004_worker_hardening"
    )
    calls = []

    class FakeOp:
        def add_column(self, table, column):
            calls.append(("add_column", table, column.name))

        def drop_column(self, table, column):
            calls.append(("drop_column", table, column))

        def create_index(self, name, table, columns, **kwargs):
            calls.append((
                "create_index",
                name,
                table,
                tuple(str(column) for column in columns),
                kwargs,
            ))

        def drop_index(self, name, table_name=None):
            calls.append(("drop_index", name, table_name))

    monkeypatch.setattr(migration, "op", FakeOp())

    migration.upgrade()

    assert ("add_column", "generation_runs", "stage_retry_count") in calls
    assert ("add_column", "generation_runs", "retry_not_before") in calls
    assert any(
        call[:3] == (
            "create_index",
            "ix_generation_runs_worker_claim",
            "generation_runs",
        )
        and "postgresql_where" in call[4]
        for call in calls
    )
    assert any(
        call[:3] == (
            "create_index",
            "ix_generation_events_run_type_sequence_desc",
            "generation_events",
        )
        and "DESC" in call[3][-1].upper()
        for call in calls
    )

    calls.clear()
    migration.downgrade()

    assert calls == [
        (
            "drop_index",
            "ix_generation_events_run_type_sequence_desc",
            "generation_events",
        ),
        (
            "drop_index",
            "ix_generation_runs_worker_claim",
            "generation_runs",
        ),
        ("drop_column", "generation_runs", "retry_not_before"),
        ("drop_column", "generation_runs", "stage_retry_count"),
    ]
