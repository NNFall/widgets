#!/usr/bin/env bash
set -euo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "run as root" >&2; exit 1; }
for command in iptables iptables-save iptables-restore; do
  command -v "${command}" >/dev/null || { echo "${command} is required" >&2; exit 1; }
done

BRIDGE="br-kaigo-build"
SUBNET="172.30.240.0/28"
TUNNEL_HOST="172.19.0.1/32"
TUNNEL_PORT="8787"
RULESET_GENERATION="G5"
FORWARD_CHAIN="KAIGO-BLD-FWD-${RULESET_GENERATION}"
HOST_CHAIN="KAIGO-BLD-HOST-${RULESET_GENERATION}"
LEGACY_FORWARD_CHAIN="KAIGO-BUILDER-EGRESS"
LEGACY_HOST_CHAIN="KAIGO-BUILDER-HOST"
IPT=(iptables -w 10)
RESTORE=(iptables-restore --wait 10 --noflush)

validate_ipv4() {
  local address="$1"
  local octet
  local -a octets=()
  [[ "${address}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS='.' read -r -a octets <<< "${address}"
  [[ "${#octets[@]}" -eq 4 ]] || return 1
  for octet in "${octets[@]}"; do
    [[ "${octet}" == "0" || "${octet}" != 0* ]] || return 1
    ((10#${octet} <= 255)) || return 1
  done
}

validate_public_https_destination() {
  : "${KAIGO_WORKER_BUILD_CLIENT_ADDRESS:?worker build client address is required}"
  : "${KAIGO_WORKER_PUBLIC_HTTPS_HOST:?worker public HTTPS host is required}"
  : "${KAIGO_WORKER_PUBLIC_HTTPS_PORT:?worker public HTTPS port is required}"

  [[ "${KAIGO_WORKER_PUBLIC_HTTPS_HOST}" =~ /32$ ]] || {
    echo "worker public HTTPS host must be an exact IPv4 /32" >&2
    return 1
  }
  local public_host_address="${KAIGO_WORKER_PUBLIC_HTTPS_HOST%/32}"
  validate_ipv4 "${public_host_address}" || {
    echo "worker public HTTPS host must be an exact IPv4 /32" >&2
    return 1
  }
  validate_ipv4 "${KAIGO_WORKER_BUILD_CLIENT_ADDRESS}" || {
    echo "worker build client address must be an exact IPv4 address" >&2
    return 1
  }
  [[ "${KAIGO_WORKER_PUBLIC_HTTPS_PORT}" =~ ^[0-9]+$ ]] || {
    echo "worker public HTTPS port must be numeric" >&2
    return 1
  }
  [[ "${KAIGO_WORKER_PUBLIC_HTTPS_PORT}" =~ ^[1-9][0-9]{0,4}$ ]] || {
    echo "worker public HTTPS port must be canonical and between 1 and 65535" >&2
    return 1
  }
  local public_https_port=$((10#${KAIGO_WORKER_PUBLIC_HTTPS_PORT}))
  ((public_https_port >= 1 && public_https_port <= 65535)) || {
    echo "worker public HTTPS port must be between 1 and 65535" >&2
    return 1
  }

  BUILD_CLIENT="${KAIGO_WORKER_BUILD_CLIENT_ADDRESS}"
  PUBLIC_HTTPS_HOST="${KAIGO_WORKER_PUBLIC_HTTPS_HOST}"
  PUBLIC_HTTPS_PORT="${public_https_port}"
}

validate_public_https_destination

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
  # iptables -S canonicalizes base selectors as source, destination, interface.
  echo "-A ${HOST_CHAIN} -s ${SUBNET} -d ${TUNNEL_HOST} -i ${BRIDGE} -p tcp -m tcp --dport ${TUNNEL_PORT} -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT"
  echo "-A ${HOST_CHAIN} -s ${BUILD_CLIENT}/32 -d ${PUBLIC_HTTPS_HOST} -i ${BRIDGE} -p tcp -m tcp --dport ${PUBLIC_HTTPS_PORT} -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT"
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

legacy_rule_count() {
  local rules="$1"
  local canonical="$2"
  printf '%s\n' "${rules}" | grep -Fxc -- "${canonical}" || true
}

complete_legacy_signature() {
  local source="$1"
  local rules="$2"
  local network canonical
  for network in "${LEGACY_DESTINATIONS[@]}"; do
    canonical="-A DOCKER-USER -s ${source} -d ${network} -j REJECT --reject-with icmp-port-unreachable"
    [[ "$(legacy_rule_count "${rules}" "${canonical}")" -eq 1 ]] || return 1
  done
}

contains_complete_legacy_signature() {
  local rules="$1"
  local append chain source_flag source destination_flag destination
  local jump_flag target reject_flag reject_value extra
  while read -r append chain source_flag source destination_flag destination jump_flag target reject_flag reject_value extra; do
    [[ "${append}" == "-A" && "${chain}" == "DOCKER-USER" ]] || continue
    [[ "${source_flag}" == "-s" && "${source}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/32$ ]] || continue
    [[ "${destination_flag}" == "-d" && "${jump_flag}" == "-j" && "${target}" == "REJECT" ]] || continue
    [[ "${reject_flag}" == "--reject-with" && "${reject_value}" == "icmp-port-unreachable" ]] || continue
    [[ -z "${extra}" ]] || continue
    complete_legacy_signature "${source}" "${rules}" && return 0
  done <<< "${rules}"
  return 1
}

is_kaigo_egress_hook() {
  local rule="$1"
  local expected_chain="$2"
  local jump=""
  local index
  local -a tokens=()
  read -r -a tokens <<< "${rule}"
  [[ "${tokens[0]:-}" == "-A" && "${tokens[1]:-}" == "${expected_chain}" ]] || return 1
  for ((index = 2; index + 1 < ${#tokens[@]}; index++)); do
    case "${tokens[${index}]}" in
      -j|-g) jump="${tokens[$((index + 1))]}" ;;
    esac
  done
  case "${jump}" in
    KAIGO-BLD-FWD-*|KAIGO-BLD-HOST-*|${LEGACY_FORWARD_CHAIN}|${LEGACY_HOST_CHAIN}) return 0 ;;
    *) return 1 ;;
  esac
}

verify_post_state() {
  local post_rules
  local rule chain rule_chain
  local expected_forward_hook="-A DOCKER-USER -i ${BRIDGE} -j ${FORWARD_CHAIN}"
  local expected_host_hook="-A INPUT -i ${BRIDGE} -j ${HOST_CHAIN}"
  local -a docker_user_rules=()
  local -a input_rules=()

  post_rules="$(iptables-save -t filter)"
  if contains_complete_legacy_signature "${post_rules}"; then
    echo "stale Kaigo source-IP legacy policy remains" >&2
    return 1
  fi
  mapfile -t docker_user_rules < <(printf '%s\n' "${post_rules}" | grep '^-A DOCKER-USER ' || true)
  mapfile -t input_rules < <(printf '%s\n' "${post_rules}" | grep '^-A INPUT ' || true)
  if [[ "${docker_user_rules[0]:-}" != "${expected_forward_hook}" || \
        "${input_rules[0]:-}" != "${expected_host_hook}" ]]; then
    echo "current Kaigo bridge hook is not first" >&2
    return 1
  fi

  while IFS= read -r rule; do
    [[ "${rule}" == "-A "* ]] || continue
    read -r _ rule_chain _ <<< "${rule}"
    if is_kaigo_egress_hook "${rule}" "${rule_chain}"; then
      if [[ "${rule}" != "${expected_forward_hook}" && \
            "${rule}" != "${expected_host_hook}" ]]; then
        echo "unexpected Kaigo egress reference" >&2
        return 1
      fi
    fi
  done <<< "${post_rules}"

  [[ "$(printf '%s\n' "${post_rules}" | grep -Fxc -- "${expected_forward_hook}" || true)" -eq 1 ]] || {
    echo "unexpected Kaigo bridge hook" >&2
    return 1
  }
  [[ "$(printf '%s\n' "${post_rules}" | grep -Fxc -- "${expected_host_hook}" || true)" -eq 1 ]] || {
    echo "unexpected Kaigo bridge hook" >&2
    return 1
  }

  while IFS= read -r declaration; do
    chain="${declaration%% *}"
    chain="${chain#:}"
    case "${chain}" in
      KAIGO-BLD-FWD-*|KAIGO-BLD-HOST-*|${LEGACY_FORWARD_CHAIN}|${LEGACY_HOST_CHAIN})
        if [[ "${chain}" != "${FORWARD_CHAIN}" && "${chain}" != "${HOST_CHAIN}" ]]; then
          echo "stale Kaigo egress generation remains" >&2
          return 1
        fi
        ;;
    esac
  done < <(printf '%s\n' "${post_rules}" | grep '^:KAIGO-' || true)
}

# Build the immutable generation only from the saved pre-cutover snapshot below;
# the actual creation and hook switch share one restore transaction.
saved_rules="$(iptables-save -t filter)"

forward_exists=false
host_exists=false
"${IPT[@]}" -nL "${FORWARD_CHAIN}" >/dev/null 2>&1 && forward_exists=true
"${IPT[@]}" -nL "${HOST_CHAIN}" >/dev/null 2>&1 && host_exists=true
if [[ "${forward_exists}" != "${host_exists}" ]]; then
  echo "incomplete immutable egress generation; refusing in-place repair" >&2
  exit 1
fi

# An installed immutable generation is never rebuilt or re-hooked. It must be
# exact in both content and reachability, otherwise the next reviewed
# generation is required. This prevents a checked generation from being
# changed in the gap before a later hook-only cutover.
if [[ "${forward_exists}" == true ]]; then
  verify_exact_chain "${FORWARD_CHAIN}" expected_forward_chain
  verify_exact_chain "${HOST_CHAIN}" expected_host_chain
  verify_post_state || {
    echo "installed immutable generation is not exact; refusing in-place hook repair" >&2
    exit 1
  }
  echo "atomic Kaigo builder egress generation ${RULESET_GENERATION} already installed for ${BRIDGE} (${SUBNET})"
  exit 0
fi

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

  # Create and populate the new immutable generation in the same atomic
  # transaction that makes it reachable. A failed restore leaves the previous
  # generation and its hooks untouched.
  echo "-N ${FORWARD_CHAIN}"
  echo "-N ${HOST_CHAIN}"
  for network in "${PRIVATE_DESTINATIONS[@]}"; do
    echo "-A ${FORWARD_CHAIN} -s ${SUBNET} -d ${network} -m conntrack --ctstate NEW -j REJECT --reject-with icmp-port-unreachable"
  done
  echo "-A ${FORWARD_CHAIN} -j RETURN"
  echo "-A ${HOST_CHAIN} -i ${BRIDGE} -s ${SUBNET} -d ${TUNNEL_HOST} -p tcp -m tcp --dport ${TUNNEL_PORT} -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT"
  echo "-A ${HOST_CHAIN} -i ${BRIDGE} -s ${BUILD_CLIENT}/32 -d ${PUBLIC_HTTPS_HOST} -p tcp -m tcp --dport ${PUBLIC_HTTPS_PORT} -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT"
  echo "-A ${HOST_CHAIN} -s ${SUBNET} -m conntrack --ctstate NEW -j REJECT --reject-with icmp-port-unreachable"
  echo "-A ${HOST_CHAIN} -j RETURN"

  # Remove every previous Kaigo egress hook, including wrong-interface and
  # duplicate hooks to a current or old generation.
  while IFS= read -r rule; do
    if is_kaigo_egress_hook "${rule}" DOCKER-USER || \
       is_kaigo_egress_hook "${rule}" INPUT; then
      echo "${rule/-A /-D }"
    fi
  done <<< "${saved_rules}"

  # Migration from the pre-fixed-bridge version. Never delete individual rules
  # merely because they resemble Kaigo; require the full historical signature.
  for source in "${!legacy_sources[@]}"; do
    complete_legacy_signature "${source}" "${saved_rules}" || continue
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

verify_exact_chain "${FORWARD_CHAIN}" expected_forward_chain
verify_exact_chain "${HOST_CHAIN}" expected_host_chain
verify_post_state

echo "atomic Kaigo builder egress generation ${RULESET_GENERATION} installed for ${BRIDGE} (${SUBNET})"
