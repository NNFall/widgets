#!/usr/bin/env bash
set -euo pipefail

# Re-apply after every container recreate because its bridge address can change.
# These rules mitigate private egress; they do not claim to pin DNS answers.
container_id="$(docker compose --profile builder-lab ps -q builder-lab)"
if [[ -z "${container_id}" ]]; then
  echo "builder-lab container is not running" >&2
  exit 1
fi

builder_ip="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${container_id}")"
if [[ -z "${builder_ip}" ]]; then
  echo "builder-lab container has no bridge IPv4 address" >&2
  exit 1
fi

ensure_reject() {
  local destination="$1"
  if ! iptables -C DOCKER-USER -s "${builder_ip}" -d "${destination}" -j REJECT 2>/dev/null; then
    iptables -I DOCKER-USER 1 -s "${builder_ip}" -d "${destination}" -j REJECT
  fi
}

for network in \
  0.0.0.0/8 \
  10.0.0.0/8 \
  100.64.0.0/10 \
  127.0.0.0/8 \
  169.254.0.0/16 \
  172.16.0.0/12 \
  192.168.0.0/16 \
  224.0.0.0/4 \
  240.0.0.0/4
do
  ensure_reject "${network}"
done

echo "DOCKER-USER private-egress guard installed for ${builder_ip}"
