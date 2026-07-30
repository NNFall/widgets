from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _service(compose: str, name: str, next_name: str) -> str:
    return compose.split(f"  {name}:", 1)[1].split(f"\n  {next_name}:", 1)[0]


def test_forensics_volume_is_private_to_worker_and_cleanup() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    app = _service(compose, "app", "builder-worker")
    worker = _service(compose, "builder-worker", "migration")
    cleanup = _service(compose, "generation-forensics-cleanup", "database-ops")
    mount = (
        "${KAIGO_GENERATION_FORENSICS_HOST_DIR:-./data/generation-forensics}:"
        "/var/lib/kaigo/generation-forensics:rw"
    )

    assert "KAIGO_GENERATION_FORENSICS_ENABLED" in app
    assert "/var/lib/kaigo/generation-forensics" not in app.split("volumes:", 1)[-1]
    assert "read_only: true" in worker
    assert mount in worker
    assert "KAIGO_GENERATION_FORENSICS_ROOT: /var/lib/kaigo/generation-forensics" in worker
    assert "profiles:\n      - operations" in cleanup
    assert "user: \"10001:10001\"" in cleanup
    assert "read_only: true" in cleanup
    assert mount in cleanup
    assert 'command: ["python", "scripts/cleanup_generation_forensics.py"]' in cleanup
    assert "kaigo_app_db:" in cleanup
    assert "ports:" not in cleanup


def test_forensics_cleanup_timer_and_installer_are_bounded() -> None:
    service = (
        ROOT / "deploy/systemd/kaigo-generation-forensics-cleanup.service"
    ).read_text(encoding="utf-8")
    timer = (
        ROOT / "deploy/systemd/kaigo-generation-forensics-cleanup.timer"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT / "scripts/install_generation_forensics_cleanup_timer.sh"
    ).read_text(encoding="utf-8")

    assert "WorkingDirectory=/opt/kaigo/current" in service
    assert "EnvironmentFile=/etc/kaigo/release.env" in service
    assert "--profile operations run --rm --no-deps generation-forensics-cleanup" in service
    assert "OnUnitActiveSec=1h" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=" in timer
    assert "kaigo-generation-forensics-cleanup.service" in timer
    assert '[[ "${EUID}" -eq 0 ]]' in installer
    assert "install -d -o 10001 -g 10001 -m 0700" in installer
    assert "cleanup_generation_forensics.py --dry-run" in installer
    assert "systemctl enable --now kaigo-generation-forensics-cleanup.timer" in installer


def test_forensics_environment_example_is_disabled_and_exactly_five_days() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "KAIGO_GENERATION_FORENSICS_ENABLED=false" in example
    assert "KAIGO_GENERATION_FORENSICS_TTL_HOURS=120" in example
    assert "KAIGO_GENERATION_FORENSICS_HOST_DIR=./data/generation-forensics" in example
    assert "KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS=" in example


def test_forensics_persistence_smoke_survives_a_fresh_process(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "smoke_generation_forensics.py"
    root = tmp_path / "private-evidence"

    seeded = subprocess.run(
        [sys.executable, str(script), "seed", "--root", str(root)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    verified = subprocess.run(
        [sys.executable, str(script), "verify", "--root", str(root)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert seeded.returncode == verified.returncode == 0
    payload = json.loads(verified.stdout)
    assert payload == {
        "checksum_valid": True,
        "entry_count": 1,
        "run_id": "7f000000-0000-4000-8000-000000000001",
        "status": "succeeded",
    }
