#!/usr/bin/env bash
set -euo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "run as root" >&2; exit 1; }

PROJECT_ROOT="/root/ai_project"
UNIT_SOURCE="${PROJECT_ROOT}/deploy/systemd"
EVIDENCE_ROOT="${PROJECT_ROOT}/data/reference-evidence"

[[ -f "${UNIT_SOURCE}/kaigo-reference-cleanup.service" ]]
[[ -f "${UNIT_SOURCE}/kaigo-reference-cleanup.timer" ]]
[[ -f "${PROJECT_ROOT}/scripts/cleanup_reference_evidence.py" ]]

install -d -m 0700 "${EVIDENCE_ROOT}"
install -m 0644 "${UNIT_SOURCE}/kaigo-reference-cleanup.service" \
  /etc/systemd/system/kaigo-reference-cleanup.service
install -m 0644 "${UNIT_SOURCE}/kaigo-reference-cleanup.timer" \
  /etc/systemd/system/kaigo-reference-cleanup.timer

systemctl daemon-reload
systemctl start kaigo-reference-cleanup.service
systemctl enable --now kaigo-reference-cleanup.timer
systemctl is-enabled --quiet kaigo-reference-cleanup.timer
systemctl is-active --quiet kaigo-reference-cleanup.timer

echo "Kaigo reference evidence TTL cleanup timer is installed and active"
