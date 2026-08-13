from __future__ import annotations

from app.billing.catalog import PLAN_CATALOG, public_billing_plans


def test_public_catalog_exposes_the_agreed_publication_offers() -> None:
    plans = {plan.code: plan for plan in public_billing_plans()}

    assert set(plans) == {
        "starter_intro_15d",
        "starter_monthly",
        "starter_quarterly",
    }
    assert (plans["starter_intro_15d"].amount.amount_minor, plans["starter_intro_15d"].period_days) == (50_000, 15)
    assert plans["starter_intro_15d"].renewal_plan_code == "starter_monthly"
    assert (plans["starter_monthly"].amount.amount_minor, plans["starter_monthly"].period_days) == (200_000, 30)
    assert (plans["starter_quarterly"].amount.amount_minor, plans["starter_quarterly"].period_days) == (500_000, 90)
    assert PLAN_CATALOG["pro_monthly"].public is False
