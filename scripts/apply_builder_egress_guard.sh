#!/usr/bin/env bash
set -euo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "run as root" >&2; exit 1; }
command -v iptables >/dev/null || { echo "iptables is required" >&2; exit 1; }

BRIDGE="br-kaigo-build"
SUBNET="172.30.240.0/28"
FORWARD_CHAIN="KAIGO-BUILDER-EGRESS"
HOST_CHAIN="KAIGO-BUILDER-HOST"
IPT=(iptables -w 10)

ensure_chain() {
  local chain="$1"
  "${IPT[@]}" -N "${chain}" 2>/dev/null || true
  "${IPT[@]}" -F "${chain}"
}

replace_hook() {
  local parent="$1" bridge="$2" target="$3"
  while "${IPT[@]}" -C "${parent}" -i "${bridge}" -j "${target}" 2>/dev/null; do
    "${IPT[@]}" -D "${parent}" -i "${bridge}" -j "${target}"
  done
  "${IPT[@]}" -I "${parent}" 1 -i "${bridge}" -j "${target}"
}

ensure_chain "${FORWARD_CHAIN}"
ensure_chain "${HOST_CHAIN}"

for network in \
  0.0.0.0/8 \
  10.0.0.0/8 \
  100.64.0.0/10 \
  127.0.0.0/8 \
  169.254.0.0/16 \
  172.16.0.0/12 \
  192.0.0.0/24 \
  192.0.2.0/24 \
  192.88.99.0/24 \
  192.168.0.0/16 \
  198.18.0.0/15 \
  198.51.100.0/24 \
  203.0.113.0/24 \
  224.0.0.0/4 \
  240.0.0.0/4
do
  "${IPT[@]}" -A "${FORWARD_CHAIN}" -s "${SUBNET}" -d "${network}" \
    -m conntrack --ctstate NEW -j REJECT
done
"${IPT[@]}" -A "${FORWARD_CHAIN}" -j RETURN

# A new connection from the research bridge to any host-owned address is denied.
# Replies for established nginx -> builder traffic do not match ctstate NEW.
"${IPT[@]}" -A "${HOST_CHAIN}" -s "${SUBNET}" \
  -m conntrack --ctstate NEW -j REJECT
"${IPT[@]}" -A "${HOST_CHAIN}" -j RETURN

replace_hook DOCKER-USER "${BRIDGE}" "${FORWARD_CHAIN}"
replace_hook INPUT "${BRIDGE}" "${HOST_CHAIN}"

"${IPT[@]}" -C DOCKER-USER -i "${BRIDGE}" -j "${FORWARD_CHAIN}"
"${IPT[@]}" -C INPUT -i "${BRIDGE}" -j "${HOST_CHAIN}"
"${IPT[@]}" -C "${FORWARD_CHAIN}" -s "${SUBNET}" -d 169.254.0.0/16 \
  -m conntrack --ctstate NEW -j REJECT

echo "stable Kaigo builder egress guard installed for ${BRIDGE} (${SUBNET})"
