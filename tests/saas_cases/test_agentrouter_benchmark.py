from __future__ import annotations

from app.models.contracts import ModelUsage
from scripts.run_agentrouter_widget_benchmark import add_usage, estimate_cost_usd


def test_benchmark_pricing_uses_measured_input_output_and_thinking_tokens() -> None:
    usage = ModelUsage(input_tokens=100_000, output_tokens=20_000, thinking_tokens=5_000)

    # Qwen reports thinking as a subset of output_tokens. It remains useful for
    # diagnostics but must not be billed a second time.
    assert estimate_cost_usd("glm-5.2", usage) == 0.72
    assert estimate_cost_usd("gpt-5.5", usage) == 0.84


def test_usage_is_accumulated_across_generation_and_repair_attempts() -> None:
    total = add_usage(
        ModelUsage(input_tokens=100, output_tokens=20, thinking_tokens=15),
        ModelUsage(input_tokens=140, output_tokens=30, thinking_tokens=10),
    )

    assert total == ModelUsage(input_tokens=240, output_tokens=50, thinking_tokens=25)
