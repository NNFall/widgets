#!/usr/bin/env bash
set -euo pipefail

cleanup_on_error() {
  docker compose --profile builder-lab stop builder-lab >/dev/null 2>&1 || true
}
trap cleanup_on_error ERR

docker compose --profile builder-lab build builder-lab
docker compose --profile builder-lab create --force-recreate --no-deps builder-lab
sudo bash scripts/apply_builder_egress_guard.sh
docker compose --profile builder-lab start builder-lab
sudo bash scripts/apply_builder_egress_guard.sh

trap - ERR
docker compose --profile builder-lab ps builder-lab
