#!/usr/bin/env bash
set -euo pipefail

cleanup_on_error() {
  docker compose --profile builder-lab stop builder-lab >/dev/null 2>&1 || true
}
trap cleanup_on_error ERR

docker compose --profile builder-lab build builder-lab

builder_image="$(
  docker compose --profile builder-lab config --format json |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["builder-lab"]["image"])'
)"
if [[ -z "$builder_image" ]]; then
  echo "builder image name is missing from the resolved compose config" >&2
  exit 1
fi
docker image inspect "$builder_image" >/dev/null
builder_uid="$(docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --entrypoint id "$builder_image" -u)"
builder_gid="$(docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --entrypoint id "$builder_image" -g)"
if [[ ! "$builder_uid" =~ ^[0-9]+$ || ! "$builder_gid" =~ ^[0-9]+$ ]]; then
  echo "builder image returned an invalid runtime identity" >&2
  exit 1
fi

demo_path="$(pwd -P)/data/builder-demo"
[[ ! -L "$(pwd -P)/data" ]]
[[ ! -L "$demo_path" ]]
install -d -m 0700 "$demo_path"
chown "$builder_uid:$builder_gid" "$demo_path"
for demo_file in "$demo_path/latest.json" "$demo_path/.latest.json.smoke.lock"; do
  if [[ -e "$demo_file" || -L "$demo_file" ]]; then
    [[ -f "$demo_file" && ! -L "$demo_file" ]]
    chown "$builder_uid:$builder_gid" "$demo_file"
    chmod 0600 "$demo_file"
  fi
done
unset builder_image builder_uid builder_gid demo_file demo_path

docker compose --profile builder-lab up --no-start --force-recreate --no-deps builder-lab

# The container remains stopped until both host safety controls are installed
# and positively verified. There is no post-start policy installation window.
sudo bash scripts/apply_builder_egress_guard.sh
sudo bash scripts/install_reference_cleanup_timer.sh

docker compose --profile builder-lab start builder-lab

trap - ERR
docker compose --profile builder-lab ps builder-lab
