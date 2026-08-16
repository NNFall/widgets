#!/usr/bin/env bash
set -Eeuo pipefail

repository_root="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1
  pwd -P
)"
manifest="${repository_root}/deploy/frontend-history/versions.json"
transformer="${repository_root}/scripts/frontend_history_transform.mjs"
bootstrap="${repository_root}/deploy/frontend-history/archive-bootstrap.js"
deploy_root="${KAIGO_FRONTEND_HISTORY_ROOT:-/var/www/kaigo-frontend-history}"
releases_dir="${deploy_root}/releases"
current_link="${deploy_root}/current"
worktree_root="${KAIGO_FRONTEND_HISTORY_WORKTREES:-/opt/kaigo-previews/frontend-history-worktrees}"
release_id="${1:-$(date -u +%Y%m%dT%H%M%SZ)-$(git -C "${repository_root}" rev-parse --short=12 HEAD)}"

if [[ ! "${release_id}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "invalid frontend history release id" >&2
  exit 2
fi
for required in "${manifest}" "${transformer}" "${bootstrap}"; do
  if [[ ! -f "${required}" ]]; then
    echo "missing frontend history input: ${required}" >&2
    exit 1
  fi
done

install -d -m 0755 -- "${releases_dir}" "${worktree_root}"
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required for frontend history deployment" >&2
  exit 1
fi
exec {deploy_lock_fd}> "${deploy_root}/.deploy.lock"
if ! flock -n "${deploy_lock_fd}"; then
  echo "another frontend history deployment is already running" >&2
  exit 1
fi

release_dir="${releases_dir}/${release_id}"
if [[ -e "${release_dir}" || -L "${release_dir}" ]]; then
  echo "immutable frontend history release already exists: ${release_dir}" >&2
  exit 1
fi

staging_dir=""
active_worktree=""
next_link="${deploy_root}/.current.${release_id}.$$"
deployment_succeeded=0
release_owned=0
worktree_owned=0

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  rm -f -- "${next_link}"
  if [[ "${worktree_owned}" -eq 1 && -n "${active_worktree}" && "${active_worktree}" == "${worktree_root}/"* ]]; then
    git -C "${repository_root}" worktree remove --force "${active_worktree}" >/dev/null 2>&1 || true
    if [[ -d "${active_worktree}" ]]; then
      rm -rf -- "${active_worktree}"
    fi
  fi
  if [[ "${deployment_succeeded}" -eq 0 && -n "${staging_dir}" && "${staging_dir}" == "${releases_dir}/."* && -d "${staging_dir}" ]]; then
    rm -rf -- "${staging_dir}"
  fi
  if [[ "${deployment_succeeded}" -eq 0 && "${release_owned}" -eq 1 && "${release_dir}" == "${releases_dir}/"* && -d "${release_dir}" ]]; then
    rm -rf -- "${release_dir}"
  fi
  exit "${status}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

staging_dir="$(mktemp -d "${releases_dir}/.${release_id}.staging.XXXXXX")"

cp -- "${bootstrap}" "${staging_dir}/archive-bootstrap.js"
node "${transformer}" index "${manifest}" "${staging_dir}/index.html"

while IFS=$'\t' read -r version_id commit_hash; do
  if [[ ! "${version_id}" =~ ^v[1-9][0-9]*$ || ! "${commit_hash}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "invalid version entry: ${version_id} ${commit_hash}" >&2
    exit 1
  fi

  active_worktree="${worktree_root}/${version_id}-${release_id}-$$"
  if [[ -e "${active_worktree}" ]]; then
    echo "refusing to reuse worktree path: ${active_worktree}" >&2
    exit 1
  fi
  if git -C "${repository_root}" worktree list --porcelain | grep -Fqx "worktree ${active_worktree}"; then
    echo "refusing to reuse registered worktree path: ${active_worktree}" >&2
    exit 1
  fi

  git -C "${repository_root}" cat-file -e "${commit_hash}^{commit}"
  worktree_owned=1
  git -C "${repository_root}" worktree add --detach "${active_worktree}" "${commit_hash}"
  npm --prefix "${active_worktree}/frontend" ci --no-audit --no-fund
  VITE_BUILDER_BASE_URL=/frontend-preview-api/ npm --prefix "${active_worktree}/frontend" run build -- --base="/frontend/${version_id}/" --assetsInlineLimit=0
  node "${transformer}" transform "${manifest}" "${active_worktree}/frontend/dist" "${version_id}"

  if [[ ! -f "${active_worktree}/frontend/dist/index.html" || ! -d "${active_worktree}/frontend/dist/assets" ]]; then
    echo "incomplete archive build for ${version_id}" >&2
    exit 1
  fi
  cp -a -- "${active_worktree}/frontend/dist" "${staging_dir}/${version_id}"
  git -C "${repository_root}" worktree remove --force "${active_worktree}"
  worktree_owned=0
  active_worktree=""
done < <(node -e '
  const versions = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
  for (const version of versions) console.log(`${version.id}\t${version.commit}`);
' "${manifest}")

chmod -R u=rwX,go=rX -- "${staging_dir}"
mv -- "${staging_dir}" "${release_dir}"
staging_dir=""
release_owned=1
ln -s -- "${release_dir}" "${next_link}"
mv -Tf -- "${next_link}" "${current_link}"
deployment_succeeded=1
release_owned=0

echo "activated Kaigo frontend history release ${release_id}: ${release_dir}"
