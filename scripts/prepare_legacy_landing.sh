#!/usr/bin/env bash
set -euo pipefail

source_dir="${1:?usage: prepare_legacy_landing.sh SOURCE_DIR TARGET_DIR}"
target_dir="${2:?usage: prepare_legacy_landing.sh SOURCE_DIR TARGET_DIR}"

if [[ ! -f "${source_dir}/index.html" || ! -d "${source_dir}/assets" ]]; then
  echo "legacy landing source is incomplete: ${source_dir}" >&2
  exit 1
fi

if [[ -e "${target_dir}" || -L "${target_dir}" ]]; then
  echo "legacy landing target already exists: ${target_dir}" >&2
  exit 1
fi

install -d -m 0755 -- "$(dirname -- "${target_dir}")"
staging_dir="$(mktemp -d "$(dirname -- "${target_dir}")/.landing-old.staging.XXXXXX")"

cleanup() {
  if [[ -d "${staging_dir}" ]]; then
    rm -rf -- "${staging_dir}"
  fi
}
trap cleanup EXIT

cp -a -- "${source_dir}/." "${staging_dir}/"

# The archived Vite bundle was built for the domain root. Keep all real
# assets isolated from the current release by moving only those root paths
# into the stable archive namespace.
find "${staging_dir}" -type f \( -name '*.html' -o -name '*.js' -o -name '*.css' \) \
  -exec sed -i \
    -e 's#href="/favicon\.svg"#href="/landing-old/favicon.svg"#g' \
    -e 's#\(["'"'"'(]\)/assets/#\1/landing-old/assets/#g' \
    {} +

grep -q '/landing-old/assets/' "${staging_dir}/index.html"
if grep -R -n -E '(["'"'"'(])/assets/' "${staging_dir}" \
  --include='*.html' --include='*.js' --include='*.css'; then
  echo "unscoped root asset reference remains in legacy landing" >&2
  exit 1
fi

mv -- "${staging_dir}" "${target_dir}"
trap - EXIT
echo "prepared immutable legacy landing: ${target_dir}"
