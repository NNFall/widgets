from __future__ import annotations

from app.db.base import Base
from app.saas import models as saas_models  # noqa: F401


EXPECTED = {
    "widget_pattern_versions",
    "composition_plans",
    "composition_plan_items",
    "pattern_outcomes",
}


def test_pattern_tables_are_registered() -> None:
    assert EXPECTED <= set(Base.metadata.tables)


def test_pattern_tables_have_named_integrity_constraints() -> None:
    pattern_versions = Base.metadata.tables["widget_pattern_versions"]
    plan_items = Base.metadata.tables["composition_plan_items"]
    outcomes = Base.metadata.tables["pattern_outcomes"]

    pattern_names = {constraint.name for constraint in pattern_versions.constraints}
    item_names = {constraint.name for constraint in plan_items.constraints}
    outcome_names = {constraint.name for constraint in outcomes.constraints}

    assert "uq_pattern_version" in pattern_names
    assert "ck_pattern_version_positive" in pattern_names
    assert "uq_composition_slot" in item_names
    assert "ck_pattern_outcome_repairs_nonnegative" in outcome_names
    assert "uq_pattern_outcome_idempotency" in outcome_names


def test_pattern_foreign_keys_have_explicit_delete_contracts() -> None:
    plans = Base.metadata.tables["composition_plans"]
    outcomes = Base.metadata.tables["pattern_outcomes"]

    plan_foreign_keys = {
        foreign_key.constraint.name: foreign_key.ondelete
        for foreign_key in plans.foreign_keys
    }
    outcome_foreign_keys = {
        foreign_key.constraint.name: foreign_key.ondelete
        for foreign_key in outcomes.foreign_keys
    }

    assert plan_foreign_keys == {
        "fk_composition_plans_run_id": "CASCADE",
        "fk_composition_plans_direction_artifact_id": "SET NULL",
        "fk_composition_plans_planner_model_call_id": "SET NULL",
    }
    assert outcome_foreign_keys["fk_pattern_outcomes_run_id"] == "CASCADE"
    assert outcome_foreign_keys["fk_pattern_outcomes_final_artifact_id"] == "SET NULL"
    assert outcome_foreign_keys["fk_pattern_outcomes_model_call_id"] == "SET NULL"
