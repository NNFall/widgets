#!/usr/bin/env bash
set -euo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "run as root" >&2; exit 1; }
for command in iptables iptables-save iptables-restore; do
  command -v "${command}" >/dev/null || { echo "${command} is required" >&2; exit 1; }
done

BRIDGE="br-kaigo-build"
SUBNET="172.30.240.0/28"
RULESET_GENERATION="G3"
FORWARD_CHAIN="KAIGO-BLD-FWD-${RULESET_GENERATION}"
HOST_CHAIN="KAIGO-BLD-HOST-${RULESET_GENERATION}"
LEGACY_FORWARD_CHAIN="KAIGO-BUILDER-EGRESS"
LEGACY_HOST_CHAIN="KAIGO-BUILDER-HOST"
IPT=(iptables -w 10)
RESTORE=(iptables-restore --wait 10 --noflush)

PRIVATE_DESTINATIONS=(
  0.0.0.0/8
  10.0.0.0/8
  100.64.0.0/10
  127.0.0.0/8
  169.254.0.0/16
  172.16.0.0/12
  192.0.0.0/24
  192.0.2.0/24
  192.88.99.0/24
  192.168.0.0/16
  198.18.0.0/15
  198.51.100.0/24
  203.0.113.0/24
  224.0.0.0/4
  240.0.0.0/4
)
LEGACY_DESTINATIONS=(
  0.0.0.0/8
  10.0.0.0/8
  100.64.0.0/10
  127.0.0.0/8
  169.254.0.0/16
  172.16.0.0/12
  192.168.0.0/16
  224.0.0.0/4
  240.0.0.0/4
)

expected_forward_chain() {
  echo "-N ${FORWARD_CHAIN}"
  for network in "${PRIVATE_DESTINATIONS[@]}"; do
    echo "-A ${FORWARD_CHAIN} -s ${SUBNET} -d ${network} -m conntrack --ctstate NEW -j REJECT --reject-with icmp-port-unreachable"
  done
  echo "-A ${FORWARD_CHAIN} -j RETURN"
}

expected_host_chain() {
  echo "-N ${HOST_CHAIN}"
  echo "-A ${HOST_CHAIN} -s ${SUBNET} -m conntrack --ctstate NEW -j REJECT --reject-with icmp-port-unreachable"
  echo "-A ${HOST_CHAIN} -j RETURN"
}

verify_exact_chain() {
  local chain="$1"
  local expected_function="$2"
  local index
  local -a actual=()
  local -a expected=()
  mapfile -t actual < <("${IPT[@]}" -S "${chain}")
  mapfile -t expected < <("${expected_function}")
  if [[ "${#actual[@]}" -ne "${#expected[@]}" ]]; then
    echo "immutable egress generation does not exactly match ${chain}" >&2
    return 1
  fi
  for index in "${!expected[@]}"; do
    if [[ "${actual[${index}]}" != "${expected[${index}]}" ]]; then
      echo "immutable egress generation does not exactly match ${chain}" >&2
      return 1
    fi
  done
}

forward_exists=false
host_exists=false
"${IPT[@]}" -nL "${FORWARD_CHAIN}" >/dev/null 2>&1 && forward_exists=true
"${IPT[@]}" -nL "${HOST_CHAIN}" >/dev/null 2>&1 && host_exists=true
if [[ "${forward_exists}" != "${host_exists}" ]]; then
  echo "incomplete immutable egress generation; refusing in-place repair" >&2
  exit 1
fi

# Build the immutable generation while it is unreachable. iptables-restore
# commits the complete generation atomically or leaves the active policy alone.
if [[ "${forward_exists}" == false ]]; then
  {
    echo '*filter'
    echo ":${FORWARD_CHAIN} - [0:0]"
    echo ":${HOST_CHAIN} - [0:0]"
    for network in "${PRIVATE_DESTINATIONS[@]}"; do
      echo "-A ${FORWARD_CHAIN} -s ${SUBNET} -d ${network} -m conntrack --ctstate NEW -j REJECT --reject-with icmp-port-unreachable"
    done
    echo "-A ${FORWARD_CHAIN} -j RETURN"
    echo "-A ${HOST_CHAIN} -s ${SUBNET} -m conntrack --ctstate NEW -j REJECT --reject-with icmp-port-unreachable"
    echo "-A ${HOST_CHAIN} -j RETURN"
    echo 'COMMIT'
  } | "${RESTORE[@]}"
fi

# Never mutate an installed generation. Presence checks are insufficient: one
# leading RETURN or ACCEPT would disable the policy while every expected rule
# still existed. Require exact normalized order, count, and content instead.
verify_exact_chain "${FORWARD_CHAIN}" expected_forward_chain
verify_exact_chain "${HOST_CHAIN}" expected_host_chain

saved_rules="$(iptables-save -t filter)"

legacy_rule_count() {
  local canonical="$1"
  printf '%s\n' "${saved_rules}" | grep -Fxc -- "${canonical}" || true
}

complete_legacy_signature() {
  local source="$1"
  local network canonical
  for network in "${LEGACY_DESTINATIONS[@]}"; do
    canonical="-A DOCKER-USER -s ${source} -d ${network} -j REJECT --reject-with icmp-port-unreachable"
    [[ "$(legacy_rule_count "${canonical}")" -eq 1 ]] || return 1
  done
}

# The obsolete pre-fixed-bridge script emitted exactly nine plain /32 source
# rules. Only a source with that complete canonical signature is unambiguous;
# partial or decorated lookalikes belong to the host and are retained.
declare -A legacy_sources=()
while read -r append chain source_flag source destination_flag destination jump_flag target reject_flag reject_value extra; do
  [[ "${append}" == "-A" && "${chain}" == "DOCKER-USER" ]] || continue
  [[ "${source_flag}" == "-s" && "${source}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/32$ ]] || continue
  [[ "${destination_flag}" == "-d" && "${jump_flag}" == "-j" && "${target}" == "REJECT" ]] || continue
  [[ "${reject_flag}" == "--reject-with" && "${reject_value}" == "icmp-port-unreachable" ]] || continue
  [[ -z "${extra}" ]] || continue
  for network in "${LEGACY_DESTINATIONS[@]}"; do
    if [[ "${destination}" == "${network}" ]]; then
      legacy_sources["${source}"]=1
      break
    fi
  done
done <<< "${saved_rules}"

transaction="$(mktemp)"
trap 'rm -f "${transaction}"' EXIT
{
  echo '*filter'

  # Remove every previous Kaigo bridge hook, including duplicate current hooks.
  while IFS= read -r rule; do
    if [[ "${rule}" == "-A DOCKER-USER -i ${BRIDGE} -j KAIGO-"* ]] || \
       [[ "${rule}" == "-A INPUT -i ${BRIDGE} -j KAIGO-"* ]]; then
      echo "${rule/-A /-D }"
    fi
  done <<< "${saved_rules}"

  # Migration from the pre-fixed-bridge version. Never delete individual rules
  # merely because they resemble Kaigo; require the full historical signature.
  for source in "${!legacy_sources[@]}"; do
    complete_legacy_signature "${source}" || continue
    for network in "${LEGACY_DESTINATIONS[@]}"; do
      canonical="-A DOCKER-USER -s ${source} -d ${network} -j REJECT --reject-with icmp-port-unreachable"
      echo "${canonical/-A /-D }"
    done
  done

  echo "-I DOCKER-USER 1 -i ${BRIDGE} -j ${FORWARD_CHAIN}"
  echo "-I INPUT 1 -i ${BRIDGE} -j ${HOST_CHAIN}"

  # Old generations are unreachable after the two hook replacements above and
  # are deleted in the same atomic filter-table transaction.
  while IFS= read -r declaration; do
    chain="${declaration%% *}"
    chain="${chain#:}"
    case "${chain}" in
      KAIGO-BLD-FWD-*|KAIGO-BLD-HOST-*|${LEGACY_FORWARD_CHAIN}|${LEGACY_HOST_CHAIN})
        if [[ "${chain}" != "${FORWARD_CHAIN}" && "${chain}" != "${HOST_CHAIN}" ]]; then
          echo "-F ${chain}"
          echo "-X ${chain}"
        fi
        ;;
    esac
  done < <(printf '%s\n' "${saved_rules}" | grep '^:KAIGO-' || true)
  echo 'COMMIT'
} > "${transaction}"

"${RESTORE[@]}" < "${transaction}"

"${IPT[@]}" -C DOCKER-USER -i "${BRIDGE}" -j "${FORWARD_CHAIN}"
"${IPT[@]}" -C INPUT -i "${BRIDGE}" -j "${HOST_CHAIN}"
[[ "$(iptables-save -t filter | grep -Fxc -- "-A DOCKER-USER -i ${BRIDGE} -j ${FORWARD_CHAIN}")" -eq 1 ]]
[[ "$(iptables-save -t filter | grep -Fxc -- "-A INPUT -i ${BRIDGE} -j ${HOST_CHAIN}")" -eq 1 ]]

echo "atomic Kaigo builder egress generation ${RULESET_GENERATION} installed for ${BRIDGE} (${SUBNET})"
