#!/usr/bin/env bash
set -euo pipefail

cleanup_on_error() {
  docker compose --profile builder-lab stop builder-lab >/dev/null 2>&1 || true
}
trap cleanup_on_error ERR

docker compose --profile builder-lab build builder-lab
docker compose --profile builder-lab up --no-start --force-recreate --no-deps builder-lab

# The container remains stopped until both host safety controls are installed
# and positively verified. There is no post-start policy installation window.
sudo bash scripts/apply_builder_egress_guard.sh
sudo bash scripts/install_reference_cleanup_timer.sh

docker compose --profile builder-lab start builder-lab

trap - ERR
docker compose --profile builder-lab ps builder-lab
