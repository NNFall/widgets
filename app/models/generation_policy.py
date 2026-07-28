from __future__ import annotations

from dataclasses import dataclass

from google.genai import types


SUPPORTED_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})


@dataclass(frozen=True)
class GenerationPolicy:
    thinking_config: types.ThinkingConfig | None
    sampling_kwargs: dict[str, float]


def normalize_thinking_level(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_THINKING_LEVELS:
        allowed = ", ".join(sorted(SUPPORTED_THINKING_LEVELS))
        raise ValueError(f"thinking level must be one of: {allowed}")
    return normalized


def _thinking_config(
    model: str,
    level: str,
    *,
    include_thoughts: bool | None,
) -> types.ThinkingConfig | None:
    normalized_model = model.strip().lower().removeprefix("models/")
    values: dict[str, object] = {}
    if normalized_model.startswith("gemini-2.5-flash"):
        values["thinking_budget"] = 0
    elif normalized_model.startswith("gemini-2.5-"):
        return None
    else:
        normalized_level = normalize_thinking_level(level)
        values["thinking_level"] = getattr(
            types.ThinkingLevel,
            normalized_level.upper(),
        )
    if include_thoughts is not None:
        values["include_thoughts"] = include_thoughts
    return types.ThinkingConfig(**values)


def _supports_sampling(model: str) -> bool:
    normalized = model.strip().lower().removeprefix("models/")
    return not (
        normalized.startswith("gemini-3.6-")
        or normalized.startswith("gemini-3.5-flash-lite")
    )


def generation_policy(
    model: str,
    level: str,
    *,
    temperature: float | None = None,
    include_thoughts: bool | None = None,
) -> GenerationPolicy:
    normalized_level = normalize_thinking_level(level)
    sampling_kwargs: dict[str, float] = {}
    if temperature is not None and _supports_sampling(model):
        sampling_kwargs = {"temperature": temperature, "top_p": 1.0}
    return GenerationPolicy(
        thinking_config=_thinking_config(
            model,
            normalized_level,
            include_thoughts=include_thoughts,
        ),
        sampling_kwargs=sampling_kwargs,
    )
