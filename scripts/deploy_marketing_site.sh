#!/usr/bin/env bash
set -Eeuo pipefail

repository_root="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1
  pwd -P
)"
frontend_dir="${repository_root}/frontend"
dist_dir="${KAIGO_MARKETING_DIST_DIR:-${frontend_dir}/dist}"
deploy_root="${KAIGO_MARKETING_DEPLOY_ROOT:-/var/www/kaigo-marketing}"
releases_dir="${deploy_root}/releases"
current_link="${deploy_root}/current"
nginx_bin="${KAIGO_NGINX_BIN:-nginx}"
systemctl_bin="${KAIGO_SYSTEMCTL_BIN:-systemctl}"

release_id="${1:-${KAIGO_MARKETING_RELEASE_ID:-}}"
if [[ -z "${release_id}" ]]; then
  release_id="$(git -C "${repository_root}" rev-parse --verify HEAD)"
fi
if [[ ! "${release_id}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "invalid release id: use 1-128 ASCII letters, digits, dots, underscores or dashes" >&2
  exit 2
fi

install -d -m 0755 -- "${deploy_root}"
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required for exclusive deploy locking" >&2
  exit 1
fi
exec {deploy_lock_fd}> "${deploy_root}/.deploy.lock"
if ! flock -n "${deploy_lock_fd}"; then
  echo "another marketing deploy is already running" >&2
  exit 1
fi

release_dir="${releases_dir}/${release_id}"
staging_dir=""
next_link="${deploy_root}/.current.${release_id}.$$"
previous_target=""
switched=0

rollback_on_error() {
  status=$?
  trap - ERR
  set +e

  rm -f -- "${next_link}"
  if [[ -n "${staging_dir}" && -d "${staging_dir}" ]]; then
    rm -rf -- "${staging_dir}"
  fi

  if [[ "${switched}" -eq 1 ]]; then
    if [[ -n "${previous_target}" ]]; then
      rollback_link="${deploy_root}/.current.rollback.$$"
      ln -s -- "${previous_target}" "${rollback_link}" &&
        mv -Tf -- "${rollback_link}" "${current_link}"
      rm -f -- "${rollback_link}"

      if "${nginx_bin}" -t >/dev/null 2>&1 &&
        "${systemctl_bin}" reload nginx >/dev/null 2>&1; then
        echo "deploy failed; restored previous release" >&2
      else
        echo "deploy failed; previous symlink was restored but nginx reload also failed" >&2
      fi
    else
      echo "deploy failed; no previous release; kept new current for availability" >&2
    fi
  fi

  exit "${status}"
}
trap rollback_on_error ERR

if [[ "${KAIGO_MARKETING_SKIP_BUILD:-0}" != "1" ]]; then
  npm --prefix "${frontend_dir}" ci
  npm --prefix "${frontend_dir}" run build
fi

if [[ ! -f "${dist_dir}/index.html" || ! -d "${dist_dir}/assets" ]]; then
  echo "frontend package is incomplete: expected index.html and assets/" >&2
  exit 1
fi
if ! grep -Eq '/assets/[^"[:space:]]+-[A-Za-z0-9_-]{8}\.(js|css)' \
  "${dist_dir}/index.html"; then
  echo "frontend index does not reference a hashed JS/CSS asset" >&2
  exit 1
fi

install -d -m 0755 -- "${releases_dir}"
if [[ -e "${release_dir}" || -L "${release_dir}" ]]; then
  echo "immutable release already exists: ${release_dir}" >&2
  exit 1
fi
if [[ -e "${current_link}" && ! -L "${current_link}" ]]; then
  echo "refusing to replace non-symlink current path: ${current_link}" >&2
  exit 1
fi
if [[ -L "${current_link}" ]]; then
  previous_target="$(readlink -- "${current_link}")"
  if [[ -z "${previous_target}" || ! -d "${previous_target}" ]]; then
    echo "current symlink does not point to an existing release" >&2
    exit 1
  fi
fi

staging_dir="$(mktemp -d "${releases_dir}/.${release_id}.staging.XXXXXX")"
cp -a -- "${dist_dir}/." "${staging_dir}/"
chmod -R u=rwX,go=rX -- "${staging_dir}"

# Validate the currently loaded nginx configuration before any routing switch.
"${nginx_bin}" -t

mv -- "${staging_dir}" "${release_dir}"
staging_dir=""
ln -s -- "${release_dir}" "${next_link}"
mv -Tf -- "${next_link}" "${current_link}"
switched=1

# Validate again with the new static target, then reload. ERR triggers rollback.
"${nginx_bin}" -t
"${systemctl_bin}" reload nginx

switched=0
trap - ERR
echo "activated Kaigo marketing release ${release_id}: ${release_dir}"
