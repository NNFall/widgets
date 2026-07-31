#!/usr/bin/env bash
set -euo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "run as root" >&2; exit 1; }

PROJECT_ROOT="/opt/kaigo/current"
UNIT_SOURCE="${PROJECT_ROOT}/deploy/systemd"
FORENSIC_ROOT="/var/lib/kaigo/generation-forensics"

[[ -f "${UNIT_SOURCE}/kaigo-generation-forensics-cleanup.service" ]]
[[ -f "${UNIT_SOURCE}/kaigo-generation-forensics-cleanup.timer" ]]
[[ -f "${PROJECT_ROOT}/scripts/cleanup_generation_forensics.py" ]]
[[ -f "/etc/kaigo/release.env" ]]

install -d -o 10001 -g 10001 -m 0700 "${FORENSIC_ROOT}"
install -m 0644 "${UNIT_SOURCE}/kaigo-generation-forensics-cleanup.service" \
  /etc/systemd/system/kaigo-generation-forensics-cleanup.service
install -m 0644 "${UNIT_SOURCE}/kaigo-generation-forensics-cleanup.timer" \
  /etc/systemd/system/kaigo-generation-forensics-cleanup.timer

cd "${PROJECT_ROOT}"
export COMPOSE_PROJECT_NAME=kaigo
export KAIGO_GENERATION_FORENSICS_HOST_DIR="${FORENSIC_ROOT}"
docker compose --profile operations config --quiet
docker compose --profile operations run --rm --no-deps generation-forensics-cleanup \
  python -c 'import os; from builder_lab.forensics.config import GenerationForensicsConfig; from builder_lab.forensics.storage import GenerationForensicStorage; config = GenerationForensicsConfig.from_env(environment=os.environ.get("KAIGO_ENVIRONMENT", "production")); GenerationForensicStorage.open(config, writable=True); print("Kaigo forensic storage initialized")'
docker compose --profile operations run --rm --no-deps generation-forensics-cleanup \
  python scripts/cleanup_generation_forensics.py --dry-run

systemctl daemon-reload
systemctl enable --now kaigo-generation-forensics-cleanup.timer
systemctl is-enabled --quiet kaigo-generation-forensics-cleanup.timer
systemctl is-active --quiet kaigo-generation-forensics-cleanup.timer

echo "Kaigo five-day generation forensic cleanup timer is installed and active"
