from __future__ import annotations

import importlib


def test_project_recovery_migration_adds_dispatch_marker_and_backfills_non_trial_runs(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "migrations.versions.0007_project_recovery_hardening"
    )
    calls = []

    class FakeOp:
        def add_column(self, table, column):
            calls.append(("add_column", table, column.name, column.server_default))

        def alter_column(self, table, column, **kwargs):
            calls.append(("alter_column", table, column, kwargs))

        def execute(self, statement):
            calls.append(("execute", str(statement)))

        def drop_column(self, table, column):
            calls.append(("drop_column", table, column))

    monkeypatch.setattr(migration, "op", FakeOp())

    migration.upgrade()

    assert any(call[:3] == ("add_column", "model_calls", "provider_dispatched") for call in calls)
    backfill = next(
        call[1]
        for call in calls
        if call[0] == "execute" and "not_applicable" in call[1]
    )
    assert "not_applicable" in backfill
    assert "trial.reserve" in backfill

    calls.clear()
    migration.downgrade()
    assert calls == [("drop_column", "model_calls", "provider_dispatched")]
