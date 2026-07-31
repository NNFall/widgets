from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_billing_worker_is_separate_hardened_and_renewals_default_off() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker = compose.split("  billing-worker:", 1)[1].split(
        "  builder-worker:", 1
    )[0]

    assert 'command: ["python", "scripts/run_billing_worker.py"]' in worker
    assert "restart: unless-stopped" in worker
    assert "read_only: true" in worker
    assert "no-new-privileges:true" in worker
    assert (
        "KAIGO_BILLING_RENEWALS_ENABLED: "
        "${KAIGO_BILLING_RENEWALS_ENABLED:-false}"
    ) in worker
    assert "${KAIGO_BILLING_RENEWALS_ENABLED:-true}" not in worker

    unit = (ROOT / "deploy/systemd/kaigo-billing-worker.service").read_text(
        encoding="utf-8"
    )
    assert "EnvironmentFile=/etc/kaigo/billing-worker.env" in unit
    assert "ExecStart=/usr/bin/docker compose" in unit
    assert "Restart=on-failure" in unit
