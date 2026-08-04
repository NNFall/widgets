from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_builder_worker_receives_only_private_codex_socket() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    worker = compose["services"]["builder-worker"]
    environment = worker["environment"]
    volumes = worker["volumes"]

    assert environment["KAIGO_CODEX_BRIDGE_ENABLED"] == (
        "${KAIGO_CODEX_BRIDGE_ENABLED:-false}"
    )
    assert environment["KAIGO_CODEX_BRIDGE_SOCKET_PATH"] == (
        "${KAIGO_CODEX_BRIDGE_SOCKET_PATH:-/run/kaigo-codex/bridge.sock}"
    )
    assert environment["KAIGO_CODEX_BRIDGE_TIMEOUT_SECONDS"] == (
        "${KAIGO_CODEX_BRIDGE_TIMEOUT_SECONDS:-900}"
    )
    assert environment["KAIGO_CODEX_BRIDGE_MODEL"] == (
        "${KAIGO_CODEX_BRIDGE_MODEL:-gpt-5.6-luna}"
    )
    assert any(
        volume.endswith(":/run/kaigo-codex:rw") for volume in volumes
    )
    assert all("codex-bridge/state" not in volume for volume in volumes)
    assert all("/root/.codex" not in volume for volume in volumes)


def test_systemd_unit_runs_host_bridge_with_bounded_state_paths() -> None:
    unit = (
        ROOT / "deploy/systemd/kaigo-codex-bridge.service"
    ).read_text(encoding="utf-8")

    assert "scripts/run_codex_bridge.py" in unit
    assert "KAIGO_CODEX_BRIDGE_MAX_CONCURRENCY=3" in unit
    assert "KAIGO_CODEX_BRIDGE_REASONING_EFFORT=max" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "RuntimeDirectory=kaigo-codex" in unit
    assert "RuntimeDirectoryMode=0770" in unit
    assert "StateDirectory=kaigo-codex-bridge" in unit
    assert "StateDirectoryMode=0700" in unit
    assert "ExecStartPre=/usr/bin/install" not in unit
    assert "Group=10001" not in unit
    assert "ReadWritePaths=/run/kaigo-codex" in unit
    assert "/var/lib/kaigo-codex-bridge" in unit
    assert "0.0.0.0" not in unit


def test_builder_worker_drop_in_enables_and_orders_private_bridge() -> None:
    drop_in = (
        ROOT / "deploy/systemd/kaigo-builder-worker-codex-bridge.conf"
    ).read_text(encoding="utf-8")

    assert "Requires=kaigo-codex-bridge.service" in drop_in
    assert "After=kaigo-codex-bridge.service" in drop_in
    assert "KAIGO_CODEX_BRIDGE_ENABLED=true" in drop_in
    assert "KAIGO_CODEX_BRIDGE_SOCKET_PATH=/run/kaigo-codex/bridge.sock" in drop_in
    assert "KAIGO_CODEX_BRIDGE_SOCKET_DIR=/run/kaigo-codex" in drop_in
    assert "KAIGO_CODEX_BRIDGE_MODEL=gpt-5.6-luna" in drop_in
