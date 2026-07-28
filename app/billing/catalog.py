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

    def snapshot(self) -> dict[str, object]:
        return {
            "code": self.code,
            "title": self.title,
            "amount_minor": self.amount.amount_minor,
            "currency": self.amount.currency,
            "period_days": self.period_days,
            "generation_tokens": self.generation_tokens,
        }

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
        if not isinstance(snapshot, dict) or set(snapshot) != required:
            raise ValueError("billing plan snapshot is invalid")
        code, title = snapshot["code"], snapshot["title"]
        amount_minor, currency = snapshot["amount_minor"], snapshot["currency"]
        period_days, generation_tokens = (
            snapshot["period_days"],
            snapshot["generation_tokens"],
        )
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
        ):
            raise ValueError("billing plan snapshot is invalid")
        return cls(
            code=code,
            title=title,
            amount=Money(amount_minor, currency),
            period_days=period_days,
            generation_tokens=generation_tokens,
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
    "starter_monthly": BillingPlan(
        code="starter_monthly",
        title="Kaigo Starter, 1 месяц",
        amount=Money(199_000, "RUB"),
        period_days=30,
        generation_tokens=1_000_000,
    ),
    "pro_monthly": BillingPlan(
        code="pro_monthly",
        title="Kaigo Pro, 1 месяц",
        amount=Money(499_000, "RUB"),
        period_days=30,
        generation_tokens=3_000_000,
    ),
}
