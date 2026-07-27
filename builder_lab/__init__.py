"""Isolated Gemini-first widget builder laboratory."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import BuilderLabConfig

__all__ = ["BuilderLabConfig"]


def __getattr__(name: str) -> Any:
    if name == "BuilderLabConfig":
        from .config import BuilderLabConfig

        return BuilderLabConfig
    raise AttributeError(name)
