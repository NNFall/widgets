from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.billing.contracts import Money


@dataclass(frozen=True, slots=True)
class BillingPlan:
    code: str
    title: str
    amount: Money
    period_days: int
    generation_tokens: int
    public: bool = True
    renewal_plan_code: str | None = None

    def snapshot(self) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "code": self.code,
            "title": self.title,
            "amount_minor": self.amount.amount_minor,
            "currency": self.amount.currency,
            "period_days": self.period_days,
            "generation_tokens": self.generation_tokens,
        }
        # Keep existing paid-period fingerprints stable. New optional terms are
        # persisted only when they differ from the legacy defaults.
        if not self.public:
            snapshot["public"] = False
        if self.renewal_plan_code is not None:
            snapshot["renewal_plan_code"] = self.renewal_plan_code
        return snapshot

    def fingerprint(self) -> str:
        return snapshot_fingerprint(self.snapshot())

    @classmethod
    def from_snapshot(cls, snapshot: object) -> BillingPlan:
        required = {
            "code",
            "title",
            "amount_minor",
            "currency",
            "period_days",
            "generation_tokens",
        }
        optional = {"public", "renewal_plan_code"}
        if (
            not isinstance(snapshot, dict)
            or not required.issubset(snapshot)
            or not set(snapshot).issubset(required | optional)
        ):
            raise ValueError("billing plan snapshot is invalid")
        code, title = snapshot["code"], snapshot["title"]
        amount_minor, currency = snapshot["amount_minor"], snapshot["currency"]
        period_days, generation_tokens = (
            snapshot["period_days"],
            snapshot["generation_tokens"],
        )
        public = snapshot.get("public", True)
        renewal_plan_code = snapshot.get("renewal_plan_code")
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(title, str)
            or not title
            or isinstance(amount_minor, bool)
            or not isinstance(amount_minor, int)
            or amount_minor <= 0
            or not isinstance(currency, str)
            or isinstance(period_days, bool)
            or not isinstance(period_days, int)
            or not 1 <= period_days <= 3_660
            or isinstance(generation_tokens, bool)
            or not isinstance(generation_tokens, int)
            or generation_tokens <= 0
            or not isinstance(public, bool)
            or (
                renewal_plan_code is not None
                and (not isinstance(renewal_plan_code, str) or not renewal_plan_code)
            )
        ):
            raise ValueError("billing plan snapshot is invalid")
        return cls(
            code=code,
            title=title,
            amount=Money(amount_minor, currency),
            period_days=period_days,
            generation_tokens=generation_tokens,
            public=public,
            renewal_plan_code=renewal_plan_code,
        )


def snapshot_fingerprint(snapshot: object) -> str:
    if not isinstance(snapshot, dict):
        raise ValueError("billing plan snapshot is invalid")
    return hashlib.sha256(
        json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


PLAN_CATALOG = {
    "starter_intro_15d": BillingPlan(
        code="starter_intro_15d",
        title="Kaigo Starter, первые 15 дней",
        amount=Money(50_000, "RUB"),
        period_days=15,
        generation_tokens=500_000,
        renewal_plan_code="starter_intro_balance_15d",
    ),
    "starter_intro_balance_15d": BillingPlan(
        code="starter_intro_balance_15d",
        title="Kaigo Starter, вторая половина первого месяца",
        amount=Money(150_000, "RUB"),
        period_days=15,
        generation_tokens=500_000,
        public=False,
        renewal_plan_code="starter_monthly",
    ),
    "starter_monthly": BillingPlan(
        code="starter_monthly",
        title="Kaigo Starter, 1 месяц",
        amount=Money(200_000, "RUB"),
        period_days=30,
        generation_tokens=1_000_000,
    ),
    "starter_quarterly": BillingPlan(
        code="starter_quarterly",
        title="Kaigo Starter, 3 месяца",
        amount=Money(500_000, "RUB"),
        period_days=90,
        generation_tokens=3_000_000,
    ),
    "pro_monthly": BillingPlan(
        code="pro_monthly",
        title="Kaigo Pro, 1 месяц",
        amount=Money(499_000, "RUB"),
        period_days=30,
        generation_tokens=3_000_000,
        public=False,
    ),
}

PUBLIC_PLAN_ORDER = (
    "starter_intro_15d",
    "starter_monthly",
    "starter_quarterly",
)


def public_billing_plans() -> tuple[BillingPlan, ...]:
    # The UI order is part of the commercial offer. Do not rely on mutable
    # dictionary insertion order: tests and operational tools can temporarily
    # replace an entry while preserving the same catalogue contract.
    return tuple(
        PLAN_CATALOG[code]
        for code in PUBLIC_PLAN_ORDER
        if code in PLAN_CATALOG and PLAN_CATALOG[code].public
    )
