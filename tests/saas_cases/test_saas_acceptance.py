from __future__ import annotations

import pytest

from scripts.smoke_saas_foundation import run_acceptance_journey


pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web_app.NotAppKeyWarning")


@pytest.mark.asyncio
async def test_composed_saas_journey_uses_real_routes_and_durable_worker(
    tmp_path,
) -> None:
    evidence = await run_acceptance_journey(tmp_path / "acceptance.db")

    assert evidence["oauth"] == {
        "claimed_fresh_draft": True,
        "stale_draft_unclaimed": True,
    }
    assert evidence["projects"]["repeat_creates_distinct_ids"] is True
    assert evidence["trial"] == {
        "accepted_requests": 1,
        "rejected_requests": 1,
        "settlement": "consumed",
    }
    assert evidence["generation"]["state"] == "completed"
    assert evidence["generation"]["preview_revision"] >= 1
    assert evidence["generation"]["sse_terminal"] is True
    assert evidence["billing"] == {
        "provider_checkout_calls": 1,
        "checkout_replayed": True,
        "webhook_first_processed": True,
        "webhook_replay_processed": False,
        "subscriptions": 1,
        "generation_token_credits": 1,
    }
    assert evidence["publication"]["stable_key_preserved"] is True
    assert evidence["publication"]["embed_served"] is True
    assert evidence["publication"]["runtime_served"] is True
    assert evidence["publication"]["rollback_restored_first_release"] is True
