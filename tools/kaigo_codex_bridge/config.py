from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_REASONING_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True, slots=True)
class CodexBridgeConfig:
    executable: str
    model: str
    reasoning_effort: str
    max_concurrency: int
    timeout_seconds: float
    state_root: Path
    work_root: Path
    socket_path: Path
    socket_gid: int = 10001
    max_images: int = 9
    max_image_bytes: int = 10 * 1024 * 1024
    max_request_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValueError("Codex executable must be non-empty")
        if not self.model.strip():
            raise ValueError("Codex model must be non-empty")
        if self.reasoning_effort not in _REASONING_LEVELS:
            raise ValueError("Codex reasoning effort is unsupported")
        if isinstance(self.max_concurrency, bool) or not (
            1 <= self.max_concurrency <= 3
        ):
            raise ValueError("Codex concurrency must be between 1 and 3")
        if isinstance(self.timeout_seconds, bool) or not (
            0 < self.timeout_seconds <= 3600
        ):
            raise ValueError("Codex timeout must be between 0 and 3600 seconds")
        for name in ("state_root", "work_root", "socket_path"):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{name} must be an absolute path")
        for name in ("max_images", "max_image_bytes", "max_request_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.socket_gid, bool) or not isinstance(self.socket_gid, int):
            raise ValueError("socket_gid must be an integer")
        if self.socket_gid < 0:
            raise ValueError("socket_gid cannot be negative")

    @classmethod
    def from_env(cls) -> "CodexBridgeConfig":
        state_root = Path(
            os.getenv("KAIGO_CODEX_BRIDGE_STATE_ROOT", "/var/lib/kaigo/codex-bridge")
        )
        return cls(
            executable=os.getenv("KAIGO_CODEX_EXECUTABLE", "codex").strip(),
            model=os.getenv("KAIGO_CODEX_BRIDGE_MODEL", "gpt-5.6-luna").strip(),
            reasoning_effort=os.getenv(
                "KAIGO_CODEX_BRIDGE_REASONING_EFFORT", "medium"
            ).strip(),
            max_concurrency=_env_int(
                "KAIGO_CODEX_BRIDGE_MAX_CONCURRENCY", 3
            ),
            timeout_seconds=_env_float(
                "KAIGO_CODEX_BRIDGE_TIMEOUT_SECONDS", 900.0
            ),
            state_root=state_root,
            work_root=Path(
                os.getenv(
                    "KAIGO_CODEX_BRIDGE_WORK_ROOT",
                    str(state_root / "work"),
                )
            ),
            socket_path=Path(
                os.getenv(
                    "KAIGO_CODEX_BRIDGE_SOCKET_PATH",
                    "/run/kaigo-codex/bridge.sock",
                )
            ),
            socket_gid=_env_int("KAIGO_CODEX_BRIDGE_SOCKET_GID", 10001),
            max_images=_env_int("KAIGO_CODEX_BRIDGE_MAX_IMAGES", 9),
            max_image_bytes=_env_int(
                "KAIGO_CODEX_BRIDGE_MAX_IMAGE_BYTES", 10 * 1024 * 1024
            ),
            max_request_bytes=_env_int(
                "KAIGO_CODEX_BRIDGE_MAX_REQUEST_BYTES", 64 * 1024 * 1024
            ),
        )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
