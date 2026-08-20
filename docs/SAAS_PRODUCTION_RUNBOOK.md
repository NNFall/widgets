# Kaigo SaaS production runbook

This is a release procedure, not authorization to deploy. **No live rollout**
is performed by this repository task.

## Before the maintenance window

1. Prefer images built and pushed by CI from the reviewed repository revision.
   On the production host, write their immutable registry references (never
   mutable tags) to `/etc/kaigo/release.env`:

   ```bash
   KAIGO_APP_IMAGE=registry.example/kaigo/app@sha256:<app-digest>
   KAIGO_BUILDER_WORKER_IMAGE=registry.example/kaigo/builder-worker@sha256:<worker-digest>
   KAIGO_BUILDER_WORKER_IMAGE_IDENTITY=registry.example/kaigo/builder-worker@sha256:<worker-digest>
   KAIGO_RELEASE_ID=<40-character-reviewed-commit-sha>
   KAIGO_MARKETING_RELEASE_SHA=<same-40-character-reviewed-commit-sha>
   KAIGO_MARKETING_ARCHIVE=/srv/kaigo/releases/kaigo-marketing-<same-40-character-reviewed-commit-sha>.tar.gz
   KAIGO_MARKETING_ARCHIVE_SHA256=<static-archive-sha256>
   KAIGO_BUILDER_WORKER_BOOT_ID=<new-random-uuid-for-this-rollout>
   KAIGO_DATABASE_OPS_IMAGE=postgres@sha256:<postgres-15-alpine-digest>
   ```

   A release that changes the publication request or response contract is one
   indivisible app/migration/static release. CI must build the app image and
   marketing archive from the same clean full commit SHA, put that SHA in
   `.kaigo-release-sha` inside the archive, and publish the archive digest with
   the image references. Never combine a newer Studio archive with an older API
   image, even as a temporary hotfix.

   The current server may instead use immutable local Docker image IDs when no
   registry is available. Build exactly once from the pinned, clean commit,
   capture each `.Id`, and write those exact `sha256:<64-hex>` values to the
   same release file:

   ```bash
   set -euo pipefail
   set +x
   umask 077
   REVIEWED_RELEASE_SHA='<40-character-reviewed-commit-sha>'
   RELEASE_COMMIT="$(git rev-parse --verify HEAD^{commit})"
   test -z "$(git status --porcelain)"
   [[ "$REVIEWED_RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]
   [[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]]
   test "$RELEASE_COMMIT" = "$REVIEWED_RELEASE_SHA"
   docker build --pull --tag "kaigo-app-build:$RELEASE_COMMIT" .
   docker build --pull --file Dockerfile.builder-lab --tag "kaigo-worker-build:$RELEASE_COMMIT" .
   npm --prefix frontend ci
   npm --prefix frontend run build
   test -f frontend/dist/index.html
   test -d frontend/dist/assets
   install -d -m 0755 /srv/kaigo/releases
   STATIC_ARCHIVE_STAGING="$(mktemp -d /srv/kaigo/releases/.kaigo-marketing-build.XXXXXX)"
   trap 'rm -rf -- "$STATIC_ARCHIVE_STAGING"' EXIT
   cp -a -- frontend/dist/. "$STATIC_ARCHIVE_STAGING/"
   printf '%s\n' "$RELEASE_COMMIT" > "$STATIC_ARCHIVE_STAGING/.kaigo-release-sha"
   KAIGO_MARKETING_ARCHIVE="/srv/kaigo/releases/kaigo-marketing-$RELEASE_COMMIT.tar.gz"
   test ! -e "$KAIGO_MARKETING_ARCHIVE"
   tar --directory "$STATIC_ARCHIVE_STAGING" --create --gzip \
     --file "$KAIGO_MARKETING_ARCHIVE" .
   KAIGO_MARKETING_ARCHIVE_SHA256="$(sha256sum "$KAIGO_MARKETING_ARCHIVE" | awk '{print $1}')"
   [[ "$KAIGO_MARKETING_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]]
   rm -rf -- "$STATIC_ARCHIVE_STAGING"
   trap - EXIT
   docker pull postgres:15-alpine
   KAIGO_APP_IMAGE="$(docker image inspect --format '{{.Id}}' "kaigo-app-build:$RELEASE_COMMIT")"
   KAIGO_BUILDER_WORKER_IMAGE="$(docker image inspect --format '{{.Id}}' "kaigo-worker-build:$RELEASE_COMMIT")"
   KAIGO_BUILDER_WORKER_IMAGE_IDENTITY="$KAIGO_BUILDER_WORKER_IMAGE"
   KAIGO_RELEASE_ID="$RELEASE_COMMIT"
   KAIGO_MARKETING_RELEASE_SHA="$RELEASE_COMMIT"
   KAIGO_BUILDER_WORKER_BOOT_ID="$(cat /proc/sys/kernel/random/uuid)"
   KAIGO_DATABASE_OPS_IMAGE="$(docker image inspect --format '{{.Id}}' postgres:15-alpine)"
   printf '%s\n' \
     "KAIGO_APP_IMAGE=$KAIGO_APP_IMAGE" \
     "KAIGO_BUILDER_WORKER_IMAGE=$KAIGO_BUILDER_WORKER_IMAGE" \
     "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY=$KAIGO_BUILDER_WORKER_IMAGE_IDENTITY" \
     "KAIGO_RELEASE_ID=$KAIGO_RELEASE_ID" \
     "KAIGO_MARKETING_RELEASE_SHA=$KAIGO_MARKETING_RELEASE_SHA" \
     "KAIGO_MARKETING_ARCHIVE=$KAIGO_MARKETING_ARCHIVE" \
     "KAIGO_MARKETING_ARCHIVE_SHA256=$KAIGO_MARKETING_ARCHIVE_SHA256" \
     "KAIGO_BUILDER_WORKER_BOOT_ID=$KAIGO_BUILDER_WORKER_BOOT_ID" \
     "KAIGO_DATABASE_OPS_IMAGE=$KAIGO_DATABASE_OPS_IMAGE" \
     > /etc/kaigo/release.env
   ```

   Local image IDs cannot be pulled. After recording them, never rebuild during
   preflight, migration, application start, worker start, or rollback. Load the
   selected release values, accept only a registry RepoDigest or a direct local
   image ID, pull only registry-backed references, then inspect every selected
   image before any database operation:

   ```bash
   set -a
   . /etc/kaigo/release.env
   set +a
   [[ "$KAIGO_APP_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
   [[ "$KAIGO_BUILDER_WORKER_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
   [[ "$KAIGO_DATABASE_OPS_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
   if [[ "$KAIGO_APP_IMAGE" == *@sha256:* && "$KAIGO_BUILDER_WORKER_IMAGE" == *@sha256:* ]]; then
     COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml pull app builder-worker migration
   else
     [[ "$KAIGO_APP_IMAGE" == *@sha256:* ]] && COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml pull app migration
     [[ "$KAIGO_BUILDER_WORKER_IMAGE" == *@sha256:* ]] && COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml pull builder-worker
   fi
   if [[ "$KAIGO_DATABASE_OPS_IMAGE" == *@sha256:* ]]; then
     COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile operations pull database-ops
   fi
   docker image inspect "$KAIGO_APP_IMAGE" "$KAIGO_BUILDER_WORKER_IMAGE" \
     "$KAIGO_DATABASE_OPS_IMAGE" \
     > "/srv/kaigo/releases/$(date -u +%Y%m%dT%H%M%SZ)-images.json"
   COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml config --quiet
   ```

   Copy the same worker reference into
   `/etc/kaigo/builder-worker-image.env`; the systemd unit refuses a mutable or
   absent worker image and refuses to build during startup.

2. Back up PostgreSQL and record the current Alembic revision. Both commands
   run in disposable Compose containers attached only to `kaigo_app_db`, where
   the database hostname is `db`; no host Python or `pg_dump` is used:

   ```bash
   COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile operations run --rm database-ops pg_dump --format=custom --file=/backups/pre-saas.dump
   COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile operations run --rm database-ops psql -Atc 'select version_num from alembic_version'
   ```

3. Fill the production environment from `.env.example` and set
   `KAIGO_ENVIRONMENT=production` exactly; missing values and spelling mistakes
   stop both application startup and schema preflight. Production must set
   `KAIGO_PUBLIC_AUTH_ENABLED=true` and at least one OAuth provider. Google and
   Yandex require complete credential pairs; VK ID uses only a positive public
   `VK_OAUTH_APP_ID` with PKCE. Protected/service VK keys are not application
   configuration and must not be copied to the host. An incomplete Google or
   Yandex pair stops application startup.
   Configure the dedicated entry-endpoint trusted-proxy CIDR setting supplied
   by the auth hardening change (`KAIGO_ENTRY_TRUSTED_PROXY_CIDRS`) with only
   the immediate production nginx/proxy address or subnet. Do not trust all
   private networks, and do not reuse a broad CDN client range: untrusted peers
   must not be able to select their rate-limit identity with
   `X-Forwarded-For`.
   Secrets remain only in
   the host secret store or `.env`, never in Git. Both generation price
   variables must be positive integers; the worker refuses missing or zero
   prices. Generate a random 32+ byte `KAIGO_READINESS_TOKEN` in the host
   secret store. The application returns 404 for `/api/ready` without it,
   before opening a database session. Rotate `KAIGO_BUILDER_WORKER_BOOT_ID`
   for every rollout and keep the release, immutable worker-image, and boot
   identities identical in the app and worker environments.
4. Enable bridge netfilter before installing the worker unit. The unit fails
   closed when `/proc/sys/net/bridge/bridge-nf-call-iptables` is absent or not
   set to `1`, because its database and egress containment rules would
   otherwise be bypassed:

   ```bash
   printf 'br_netfilter\n' > /etc/modules-load.d/kaigo-builder.conf
   modprobe br_netfilter
   printf 'net.bridge.bridge-nf-call-iptables = 1\n' \
     > /etc/sysctl.d/99-kaigo-builder.conf
   sysctl --system
   test "$(cat /proc/sys/net/bridge/bridge-nf-call-iptables)" = 1
   ```

   Install the single reviewed public HTTPS destination for the builder worker
   in a separate root-owned file. The systemd unit uses the non-optional
   `EnvironmentFile=/etc/kaigo/builder-worker-egress.env` directive and refuses
   to start if the file is absent or is not owned by `root:root` with mode 600.
   The host must be an exact IPv4 `/32`; the port must be a canonical number
   between 1 and 65535. This release permits only the self-hosted Kaigo HTTPS
   endpoint and does not open the database bridge or the rest of the build
   subnet:

   ```bash
   set -euo pipefail
   set +x
   EGRESS_ENV_TMP="$(mktemp)"
   trap 'rm -f "$EGRESS_ENV_TMP"' EXIT
   printf '%s\n' \
     'KAIGO_WORKER_PUBLIC_HTTPS_HOST=5.129.236.90/32' \
     'KAIGO_WORKER_PUBLIC_HTTPS_PORT=443' \
     > "$EGRESS_ENV_TMP"
   install -o root -g root -m 600 "$EGRESS_ENV_TMP" \
     /etc/kaigo/builder-worker-egress.env
   rm -f "$EGRESS_ENV_TMP"
   trap - EXIT
   test "$(stat -c %u:%g:%a /etc/kaigo/builder-worker-egress.env)" = "0:0:600"
   ```

   `KAIGO_WORKER_BUILD_CLIENT_ADDRESS=172.30.240.2` remains pinned in the unit
   and must match the fixed `builder-worker` address on
   `kaigo_builder_research`. The G5 host rule permits port 443 only from this
   one source, so `builder-lab` on the same bridge does not inherit the allow.

   Install the AntiGravity builder configuration separately from the shared
   Kaigo environment. Do not put the AntiGravity bearer key in
   `/etc/kaigo/kaigo.env`: that file is consumed by services which do not need
   this credential. The systemd unit passes the dedicated file only to the
   builder-worker Compose invocation through
   `EnvironmentFile=/etc/kaigo/builder-worker-antigravity.env` and requires
   `root:root` mode 600:

   ```bash
   set -euo pipefail
   set +x
   test -f "$ANTIGRAVITY_KEY_FILE"
   mapfile -t ANTIGRAVITY_KEY_LINES < "$ANTIGRAVITY_KEY_FILE"
   test "${#ANTIGRAVITY_KEY_LINES[@]}" -eq 1
   [[ "${ANTIGRAVITY_KEY_LINES[0]}" =~ ^[A-Za-z0-9._~-]{32,256}$ ]]
   ANTIGRAVITY_ENV_TMP="$(mktemp)"
   trap 'rm -f "$ANTIGRAVITY_ENV_TMP"' EXIT
   {
     printf '%s\n' \
       'KAIGO_ANTIGRAVITY_API_ENABLED=false' \
       'KAIGO_ANTIGRAVITY_API_BASE_URL=https://kaigo.space/antigravity-api' \
       'KAIGO_ANTIGRAVITY_API_TIMEOUT_SECONDS=180' \
       'KAIGO_ANTIGRAVITY_API_MODEL=gemini-3.7-flash-high' \
       'KAIGO_ANTIGRAVITY_API_REASONING_EFFORT=high'
     printf 'KAIGO_ANTIGRAVITY_API_KEY=%s\n' "${ANTIGRAVITY_KEY_LINES[0]}"
   } > "$ANTIGRAVITY_ENV_TMP"
   install -o root -g root -m 600 "$ANTIGRAVITY_ENV_TMP" \
     /etc/kaigo/builder-worker-antigravity.env
   rm -f "$ANTIGRAVITY_ENV_TMP"
   unset ANTIGRAVITY_KEY_LINES
   trap - EXIT
   test "$(stat -c %u:%g:%a /etc/kaigo/builder-worker-antigravity.env)" = "0:0:600"
   ```

   Then install `deploy/systemd/kaigo-builder-worker.service`, adjusting only
   the immutable checkout path if production uses a different path. Docker
   restart remains disabled for `builder-worker`; systemd is the sole restart
   owner.

5. Before any runtime switch, atomically capture the complete prior release
   identity. The rollback tuple contains the images actually running now, the
   prior app release and worker-image identities, the exact current Alembic
   revision, a fresh boot ID reserved for a rollback worker, and the selected
   current release image that is trusted to run rollback migrations. The
   snapshot is renamed only after every required value and image is verified:

   ```bash
   set -euo pipefail
   umask 077
   set -a
   . /etc/kaigo/release.env
   set +a
   : "${KAIGO_APP_IMAGE:?selected current app/migration image is required}"
   : "${KAIGO_DATABASE_OPS_IMAGE:?selected database ops image is required}"
   APP_CONTAINER="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml ps -q app)"
   WORKER_CONTAINER="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile saas-worker ps -q builder-worker)"
   test -n "$APP_CONTAINER"
   test -n "$WORKER_CONTAINER"
   ROLLBACK_APP_IMAGE="$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")"
   ROLLBACK_WORKER_IMAGE="$(docker inspect --format '{{.Image}}' "$WORKER_CONTAINER")"
   ROLLBACK_RELEASE_ID="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$APP_CONTAINER" | sed -n 's/^KAIGO_RELEASE_ID=//p')"
   ROLLBACK_WORKER_IMAGE_IDENTITY="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$WORKER_CONTAINER" | sed -n 's/^KAIGO_BUILDER_WORKER_IMAGE_IDENTITY=//p')"
   ROLLBACK_PREVIOUS_ALEMBIC_REVISION="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile operations run --rm database-ops psql -Atc 'select version_num from alembic_version')"
   ROLLBACK_WORKER_BOOT_ID="$(cat /proc/sys/kernel/random/uuid)"
   ROLLBACK_MIGRATION_IMAGE="$KAIGO_APP_IMAGE"
   [[ "$ROLLBACK_APP_IMAGE" =~ ^sha256:[0-9a-fA-F]{64}$ ]]
   [[ "$ROLLBACK_WORKER_IMAGE" =~ ^sha256:[0-9a-fA-F]{64}$ ]]
   [[ "$ROLLBACK_MIGRATION_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
   [[ "$KAIGO_DATABASE_OPS_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
   [[ "$ROLLBACK_RELEASE_ID" =~ ^[A-Za-z0-9._:-]+$ ]]
   [[ "$ROLLBACK_WORKER_IMAGE_IDENTITY" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
   [[ "$ROLLBACK_PREVIOUS_ALEMBIC_REVISION" =~ ^[0-9a-z_]+$ ]]
   [[ "$ROLLBACK_WORKER_BOOT_ID" =~ ^[0-9a-fA-F-]{36}$ ]]
   docker image inspect "$ROLLBACK_APP_IMAGE" "$ROLLBACK_WORKER_IMAGE" \
     "$ROLLBACK_MIGRATION_IMAGE" "$KAIGO_DATABASE_OPS_IMAGE" >/dev/null
   ROLLBACK_TMP="$(mktemp /srv/kaigo/releases/rollback.env.tmp.XXXXXX)"
   trap 'rm -f "$ROLLBACK_TMP"' EXIT
   printf '%s\n' \
     "KAIGO_APP_IMAGE=$ROLLBACK_APP_IMAGE" \
     "KAIGO_BUILDER_WORKER_IMAGE=$ROLLBACK_WORKER_IMAGE" \
     "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY=$ROLLBACK_WORKER_IMAGE_IDENTITY" \
     "KAIGO_RELEASE_ID=$ROLLBACK_RELEASE_ID" \
     "KAIGO_BUILDER_WORKER_BOOT_ID=$ROLLBACK_WORKER_BOOT_ID" \
     "KAIGO_PREVIOUS_ALEMBIC_REVISION=$ROLLBACK_PREVIOUS_ALEMBIC_REVISION" \
     "KAIGO_ROLLBACK_MIGRATION_IMAGE=$ROLLBACK_MIGRATION_IMAGE" \
     "KAIGO_DATABASE_OPS_IMAGE=$KAIGO_DATABASE_OPS_IMAGE" \
     > "$ROLLBACK_TMP"
   chmod 600 "$ROLLBACK_TMP"
   mv -f "$ROLLBACK_TMP" /srv/kaigo/releases/rollback.env
   trap - EXIT
   ```

## Fail-closed schema preflight

The first command is read-only. It accepts an empty database, a database with a
known Alembic revision, or the exact five-table legacy schema corresponding to
`0002_widget_settings`. Extra or altered tables, columns, constraints, or
indexes stop the release.

```bash
COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile operations run --rm migration python scripts/preflight_saas_schema.py
```

Review its proposed action and the database backup. Mutation requires the
explicit mode:

```bash
COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile operations run --rm migration python scripts/preflight_saas_schema.py --apply
```

For the exact unversioned legacy schema only, `--apply` stamps
`0002_widget_settings` and then runs `alembic upgrade head`. It never stamps an
unknown schema.

## Start order and smoke

Schema migration and the atomic rollback snapshot finish before either runtime
is switched. Stop the old worker first so it drains its current claim under the
systemd stop timeout and cannot claim new work during the switch. Start the app
first, but do not start the new worker or activate the Studio archive yet:

```bash
systemctl stop kaigo-builder-worker
COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml up -d --no-build app
```

For every release that changes the publication contract, run the following
identity gate before switching `/var/www/kaigo-marketing/current`. It also
applies to ordinary combined app/static releases, so the stricter path can be
used consistently. The gate verifies the selected app and migration services
resolve to the same immutable image, verifies the archive digest and embedded
full SHA, and reads only the single `KAIGO_RELEASE_ID` value from the live app.
Shell tracing stays disabled; the command never dumps the container environment
or `/etc/kaigo/release.env`.

Every equality check occurs before the deploy script can change the static
symlink. A missing, malformed, or mismatched identity exits nonzero and leaves
the previous static release active. Do not bypass the gate: stop and roll back
the just-started app before reopening the release if it fails.

```bash
set -euo pipefail
set +x
umask 077
set -a
. /etc/kaigo/release.env
set +a
: "${KAIGO_APP_IMAGE:?selected app/migration image is required}"
: "${KAIGO_RELEASE_ID:?selected app release SHA is required}"
: "${KAIGO_MARKETING_RELEASE_SHA:?selected static release SHA is required}"
: "${KAIGO_MARKETING_ARCHIVE:?selected static archive is required}"
: "${KAIGO_MARKETING_ARCHIVE_SHA256:?selected static archive digest is required}"
[[ "$KAIGO_RELEASE_ID" =~ ^[0-9a-f]{40}$ ]]
[[ "$KAIGO_MARKETING_RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$KAIGO_MARKETING_ARCHIVE" =~ ^/srv/kaigo/releases/kaigo-marketing-[0-9a-f]{40}\.tar\.gz$ ]]
[[ "$KAIGO_MARKETING_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]]
test "$KAIGO_MARKETING_RELEASE_SHA" = "$KAIGO_RELEASE_ID"
PUBLICATION_CONTRACT_ARCHIVE_SHA256="$(sha256sum "$KAIGO_MARKETING_ARCHIVE" | awk '{print $1}')"
test "$PUBLICATION_CONTRACT_ARCHIVE_SHA256" = "$KAIGO_MARKETING_ARCHIVE_SHA256"
APP_CONFIG_IMAGE="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml config --format json | jq -er '.services.app.image')"
MIGRATION_CONFIG_IMAGE="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml config --format json | jq -er '.services.migration.image')"
test "$APP_CONFIG_IMAGE" = "$KAIGO_APP_IMAGE"
test "$MIGRATION_CONFIG_IMAGE" = "$KAIGO_APP_IMAGE"
APP_CONTAINER="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml ps -q app)"
test -n "$APP_CONTAINER"
test "$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")" = \
  "$(docker image inspect --format '{{.Id}}' "$KAIGO_APP_IMAGE")"
PUBLICATION_CONTRACT_LIVE_APP_SHA="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$APP_CONTAINER" | sed -n 's/^KAIGO_RELEASE_ID=//p')"
PUBLICATION_CONTRACT_STATIC_SHA="$(tar -xOf "$KAIGO_MARKETING_ARCHIVE" ./.kaigo-release-sha)"
[[ "$PUBLICATION_CONTRACT_LIVE_APP_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$PUBLICATION_CONTRACT_STATIC_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$PUBLICATION_CONTRACT_LIVE_APP_SHA" = "$KAIGO_RELEASE_ID"
test "$PUBLICATION_CONTRACT_STATIC_SHA" = "$KAIGO_MARKETING_RELEASE_SHA"
test "$PUBLICATION_CONTRACT_STATIC_SHA" = "$KAIGO_RELEASE_ID"
test "$PUBLICATION_CONTRACT_LIVE_APP_SHA" = "$PUBLICATION_CONTRACT_STATIC_SHA"
PUBLICATION_CONTRACT_STATIC_DIR="$(mktemp -d /srv/kaigo/releases/.kaigo-marketing-deploy.XXXXXX)"
trap 'rm -rf -- "$PUBLICATION_CONTRACT_STATIC_DIR"' EXIT
tar --extract --gzip --file "$KAIGO_MARKETING_ARCHIVE" \
  --directory "$PUBLICATION_CONTRACT_STATIC_DIR" --no-same-owner --no-same-permissions
test -f "$PUBLICATION_CONTRACT_STATIC_DIR/index.html"
test -d "$PUBLICATION_CONTRACT_STATIC_DIR/assets"
KAIGO_MARKETING_DIST_DIR="$PUBLICATION_CONTRACT_STATIC_DIR" KAIGO_MARKETING_SKIP_BUILD=1 ./scripts/deploy_marketing_site.sh "$PUBLICATION_CONTRACT_STATIC_SHA"
rm -rf -- "$PUBLICATION_CONTRACT_STATIC_DIR"
trap - EXIT
```

Only after the matching static release is active may the worker start. Do not
replace the restart with a plain `systemctl start kaigo-builder-worker`: an
already-running unit could otherwise keep the previous worker container.
Verify the image of the actual running worker, not only the locally available
image reference:

```bash
cp /etc/kaigo/release.env /etc/kaigo/builder-worker-image.env
systemctl daemon-reload
systemctl restart kaigo-builder-worker
WORKER_CONTAINER="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile saas-worker ps -q builder-worker)"
test "$(docker inspect --format '{{.Image}}' "$WORKER_CONTAINER")" = \
  "$(docker image inspect --format '{{.Id}}' "$KAIGO_BUILDER_WORKER_IMAGE")"
systemctl is-active --quiet kaigo-builder-worker
```

The application-only checks use the loopback app port and do not request
`/studio/`, because nginx owns that static route:

```bash
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/api/auth/session
```

The worker starts with AntiGravity disabled and therefore retains the reviewed
Codex fallback. Before changing that flag, prove the provider contract directly.
Keep the bearer value out of shell tracing, argv, and output by passing it in a
root-only curl config file; the response is also temporary and root-only:

```bash
set -euo pipefail
set +x
umask 077
ANTIGRAVITY_PROVIDER_SMOKE_CONFIG="$(mktemp /run/kaigo-antigravity-curl.XXXXXX)"
ANTIGRAVITY_PROVIDER_SMOKE_RESPONSE="$(mktemp /run/kaigo-antigravity-response.XXXXXX)"
trap 'rm -f "$ANTIGRAVITY_PROVIDER_SMOKE_CONFIG" "$ANTIGRAVITY_PROVIDER_SMOKE_RESPONSE"' EXIT
. /etc/kaigo/builder-worker-antigravity.env
: "${KAIGO_ANTIGRAVITY_API_KEY:?AntiGravity bearer key is required}"
[[ "$KAIGO_ANTIGRAVITY_API_KEY" =~ ^[A-Za-z0-9._~-]{32,256}$ ]]
{
  printf '%s\n' \
    'silent' \
    'show-error' \
    'fail-with-body' \
    'request = "POST"' \
    'url = "https://kaigo.space/antigravity-api/v1/respond"' \
    'header = "Content-Type: application/json"'
  printf 'header = "Authorization: Bearer %s"\n' "$KAIGO_ANTIGRAVITY_API_KEY"
} > "$ANTIGRAVITY_PROVIDER_SMOKE_CONFIG"
chmod 600 "$ANTIGRAVITY_PROVIDER_SMOKE_CONFIG" "$ANTIGRAVITY_PROVIDER_SMOKE_RESPONSE"
curl --config "$ANTIGRAVITY_PROVIDER_SMOKE_CONFIG" \
  --data-binary '{"prompt":"Return a JSON object with ready set to true.","model":"gemini-3.7-flash-high","reasoning_effort":"high","native_tools":"none","response_format":{"type":"json_schema","json_schema":{"name":"kaigo_provider_smoke","schema":{"type":"object","properties":{"ready":{"type":"boolean"}},"required":["ready"],"additionalProperties":false},"strict":true}}}' \
  --output "$ANTIGRAVITY_PROVIDER_SMOKE_RESPONSE"
jq -e '
  .conversation_deleted == true
  and .finish_reason == "stop"
  and .model == "gemini-3.7-flash-high"
  and .reasoning_effort == "high"
  and .actual_provider == "gemini"
  and (.actual_model | type == "string" and length > 0)
  and (.request_id | type == "string" and length > 0)
  and (.duration_ms | type == "number" and . >= 0 and floor == .)
  and (.usage | type == "object")
  and ([
    .usage.input_tokens,
    .usage.output_tokens,
    .usage.thinking_tokens,
    .usage.cache_read_tokens,
    .usage.total_tokens
  ] | all(.[]; type == "number" and . >= 0 and floor == .))
  and (.output_text | type == "string" and length > 0)
  and ((.output_text | fromjson) == {"ready":true})
  and (.tool_calls | length == 0)
  and (.native_tool_events | length == 0)
' "$ANTIGRAVITY_PROVIDER_SMOKE_RESPONSE" >/dev/null
unset KAIGO_ANTIGRAVITY_API_KEY
rm -f "$ANTIGRAVITY_PROVIDER_SMOKE_CONFIG" "$ANTIGRAVITY_PROVIDER_SMOKE_RESPONSE"
trap - EXIT
```

Only after that direct smoke passes, atomically enable AntiGravity and restart
the worker. The next action after this block is the authenticated canary
generation; do not leave the provider enabled without running it:

```bash
set -euo pipefail
set +x
ANTIGRAVITY_ENV=/etc/kaigo/builder-worker-antigravity.env
test "$(stat -c %u:%g:%a "$ANTIGRAVITY_ENV")" = "0:0:600"
test "$(grep -c '^KAIGO_ANTIGRAVITY_API_ENABLED=false$' "$ANTIGRAVITY_ENV")" = 1
ANTIGRAVITY_ENABLE_TMP="$(mktemp /etc/kaigo/builder-worker-antigravity.env.tmp.XXXXXX)"
trap 'rm -f "$ANTIGRAVITY_ENABLE_TMP"' EXIT
sed 's/^KAIGO_ANTIGRAVITY_API_ENABLED=false$/KAIGO_ANTIGRAVITY_API_ENABLED=true/' \
  "$ANTIGRAVITY_ENV" > "$ANTIGRAVITY_ENABLE_TMP"
test "$(grep -c '^KAIGO_ANTIGRAVITY_API_ENABLED=true$' "$ANTIGRAVITY_ENABLE_TMP")" = 1
test "$(grep -c '^KAIGO_ANTIGRAVITY_API_ENABLED=false$' "$ANTIGRAVITY_ENABLE_TMP" || true)" = 0
chown root:root "$ANTIGRAVITY_ENABLE_TMP"
chmod 600 "$ANTIGRAVITY_ENABLE_TMP"
mv -f "$ANTIGRAVITY_ENABLE_TMP" "$ANTIGRAVITY_ENV"
trap - EXIT
test "$(stat -c %u:%g:%a "$ANTIGRAVITY_ENV")" = "0:0:600"
systemctl restart kaigo-builder-worker
systemctl is-active --quiet kaigo-builder-worker
```

Before the release smoke, use a real browser OAuth session through the public
edge to create a new canary generation **after the worker restart**. Wait for
that run to complete and record its owner project UUID. This queue round trip
remains functional evidence, while `/api/ready` reads one singleton database
row and requires a fresh heartbeat with this rollout's release, immutable
worker image, and boot identities. Old generation rows and old worker boots
cannot satisfy the deploy gate. Merely seeing the process or container as
running is not sufficient.

Run the routing diagnostic against the nginx/public edge where `/studio/`
exists. Pass the operational readiness secret through a root-only file:

```bash
umask 077
KAIGO_SMOKE_READINESS_TOKEN_FILE="$(mktemp /run/kaigo-readiness-token.XXXXXX)"
trap 'rm -f "$KAIGO_SMOKE_READINESS_TOKEN_FILE"' EXIT
printf '%s' "$KAIGO_READINESS_TOKEN" > "$KAIGO_SMOKE_READINESS_TOKEN_FILE"
chmod 600 "$KAIGO_SMOKE_READINESS_TOKEN_FILE"
export KAIGO_SMOKE_READINESS_TOKEN_FILE
python scripts/smoke_saas_foundation.py --base-url https://kaigo.space
rm -f "$KAIGO_SMOKE_READINESS_TOKEN_FILE"
unset KAIGO_SMOKE_READINESS_TOKEN_FILE
trap - EXIT
```

The command emits one compact JSON evidence object and checks `/api/health`,
DB-backed `/api/ready`, the static `/studio/` application, and the public
`/api/auth/session` contract. Without owner, publication, and sandbox webhook
evidence it returns `release_ready: false` and `release_status: "incomplete"`.
This diagnostic is useful, but it is not a completed release gate.

For the full gate, put the real browser cookie in a temporary root-owned file.
Masked `read` keeps it out of shell history and `ps`; the trap removes the file
on success, failure, or interruption:

```bash
umask 077
KAIGO_SMOKE_COOKIE_FILE="$(mktemp /run/kaigo-smoke-cookie.XXXXXX)"
KAIGO_SMOKE_READINESS_TOKEN_FILE="$(mktemp /run/kaigo-readiness-token.XXXXXX)"
trap 'rm -f "$KAIGO_SMOKE_COOKIE_FILE" "$KAIGO_SMOKE_READINESS_TOKEN_FILE"' EXIT
chmod 600 "$KAIGO_SMOKE_COOKIE_FILE"
chmod 600 "$KAIGO_SMOKE_READINESS_TOKEN_FILE"
test "$(stat -c '%u:%a' "$KAIGO_SMOKE_COOKIE_FILE")" = "0:600"
test "$(stat -c '%u:%a' "$KAIGO_SMOKE_READINESS_TOKEN_FILE")" = "0:600"
read -rsp 'Paste the real browser Cookie header: ' KAIGO_SMOKE_COOKIE_INPUT
printf '\n'
printf '%s' "$KAIGO_SMOKE_COOKIE_INPUT" > "$KAIGO_SMOKE_COOKIE_FILE"
unset KAIGO_SMOKE_COOKIE_INPUT
printf '%s' "$KAIGO_READINESS_TOKEN" > "$KAIGO_SMOKE_READINESS_TOKEN_FILE"
export KAIGO_SMOKE_COOKIE_FILE
export KAIGO_SMOKE_READINESS_TOKEN_FILE
export KAIGO_SMOKE_PROJECT_ID='<owner project UUID with a completed active run>'
export KAIGO_SMOKE_PUBLIC_KEY='<published stable key>'
export KAIGO_SMOKE_EMBED_ORIGIN='https://approved-customer.example'
export KAIGO_SMOKE_WEBHOOK_FIXTURE=/run/secrets/yookassa-canary-event.json
python scripts/smoke_saas_foundation.py \
  --base-url https://kaigo.space \
  --require-release-ready
rm -f "$KAIGO_SMOKE_COOKIE_FILE" "$KAIGO_SMOKE_READINESS_TOKEN_FILE"
unset KAIGO_SMOKE_COOKIE_FILE KAIGO_SMOKE_READINESS_TOKEN_FILE
trap - EXIT
```

These inputs check owner project/run refresh, terminal SSE replay, the JSON
preview and trusted preview document, plus the public embed loader and runtime.
The sandbox fixture is posted twice to the exact YooKassa webhook. The first
response must be `processed: true` and the replay must be `processed: false`;
`false/false` means the fixture never exercised processing and fails the gate.
Any missing release check, requested critical route returning non-2xx,
malformed contract data, unauthenticated supplied cookie, or incorrect webhook
pair exits nonzero with JSON error evidence on stderr. The existing no-argument
mode remains a deterministic, self-contained SQLite acceptance journey and
never calls external OAuth or payment APIs.

Confirm:

- `/studio` and `/studio/` return the static application without Basic Auth;
- `/builder/` still returns `401` without its legacy credentials;

## External HTTPS publication canary

Local, loopback, mocked, HTTP, self-signed, skipped, or blocked runs are useful
diagnostics but are **not** publication acceptance. The real gate uses two
credential-free static origins and a dedicated non-customer owner account:

- `https://canary.kaigo.space` is the only origin added to the publication
  allowlist;
- `https://denied-canary.kaigo.space` serves the identical static shell and
  must not render a usable widget.

Deploy `deploy/publication-canary/index.html` and `canary.js` to
`/var/www/kaigo-publication-canary`, provision both certificates outside Git,
install `deploy/nginx/kaigo-publication-canary.conf`, then run `nginx -t`
before reloading nginx. The static hosts contain no owner API, Cookie, CSRF,
OAuth, payment, prompt, customer, or generated-artifact data.

Use this schema-first order:

1. Back up PostgreSQL and record the running release plus Alembic revision.
2. Apply migrations through `0016_project_versions` while
   `KAIGO_PROJECT_VERSIONS_ENABLED=false`.
3. Deploy the application and worker, then pass readiness and the existing
   production smoke.
4. Provision both static HTTPS canary hosts and verify their certificates,
   redirects, CSP, `no-store`, `no-referrer`, `nosniff`, and permissions
   headers.
5. Set `KAIGO_PROJECT_VERSIONS_ENABLED=true`, restart the application, and
   repeat readiness.
6. Use a dedicated non-customer canary account with an active, unexpired test
   subscription and a project containing two separately reviewed versions.
7. Put the real browser Cookie header in an owner-only temporary file without
   echoing it or passing it on argv.
8. Run the external acceptance from the builder-lab/browser-tools environment:

   ```bash
   umask 077
   export KAIGO_CANARY_APP_ORIGIN='https://kaigo.space'
   export KAIGO_CANARY_ALLOWED_ORIGIN='https://canary.kaigo.space'
   export KAIGO_CANARY_DENIED_ORIGIN='https://denied-canary.kaigo.space'
   export KAIGO_CANARY_PROJECT_ID='<dedicated canary project UUID>'
   export KAIGO_CANARY_BASELINE_VERSION_ID='<reviewed baseline UUID>'
   export KAIGO_CANARY_CANDIDATE_VERSION_ID='<reviewed candidate UUID>'
   export KAIGO_CANARY_COOKIE_FILE="$(mktemp /run/kaigo-canary-cookie.XXXXXX)"
   export KAIGO_CANARY_EVIDENCE_FILE="/srv/kaigo/releases/$(date -u +%Y%m%dT%H%M%SZ)-publication-canary.json"
   trap 'rm -f "$KAIGO_CANARY_COOKIE_FILE"' EXIT
   chmod 600 "$KAIGO_CANARY_COOKIE_FILE"
   read -rsp 'Paste the dedicated canary browser Cookie header: ' KAIGO_CANARY_COOKIE_INPUT
   printf '\n'
   printf '%s' "$KAIGO_CANARY_COOKIE_INPUT" > "$KAIGO_CANARY_COOKIE_FILE"
   unset KAIGO_CANARY_COOKIE_INPUT
   python scripts/run_publication_https_canary.py
   rm -f "$KAIGO_CANARY_COOKIE_FILE"
   unset KAIGO_CANARY_COOKIE_FILE
   trap - EXIT
   ```

9. Retain only the sanitized JSON evidence. It contains public IDs, checksums,
   timestamps, statuses, origins, and booleans—never Cookie, CSRF, capability,
   Authorization, raw HTML, prompts, or chat text.
10. If the gate fails, disable the flag or roll back application code using
    the existing schema-forward procedure. Do not weaken the subscription gate
    and do not publish a customer project to diagnose the canary.

The runner proves baseline publish, public launcher/close/reopen/chat,
candidate update with compare-and-swap, rollback with compare-and-swap,
stable-key reuse, exact release markers, empty browser storage, absence of
credential headers, and rejection at the denied origin. Any missing input or
unproven assertion exits nonzero with `status="blocked"`.
- `/api/health`, an SSE generation stream, `/embed/<key>.js`,
  `/runtime/<key>`, and `/billing/success` reach `127.0.0.1:8080`;
- a YooKassa sandbox notification reaches the exact webhook and remains
  idempotent;
- the worker reaches PostgreSQL and a public reference website, while loopback,
  RFC1918, link-local, and `169.254.169.254` fail;
- the app can reach OAuth/YooKassa and a published widget still loads and chats.

## YooKassa merchant cutover

Do not change `YOOKASSA_SHOP_ID` while any `creating`, `pending`, or `failed`
payment attempt exists. The current MVP has one live provider instance and no
registry capable of verifying payments across multiple merchant accounts.

Before a planned cutover, stop new checkout creation and drain every
nonterminal attempt under the old merchant: let it succeed or be verified as
cancelled while the old credentials are still active. Only then rotate the shop
configuration and resume checkout creation.

If the shop was changed too early, restore the exact previous `shop_id` and its
credentials. During this condition the pending-payment API deliberately hides
all checkout URLs, new checkouts fail with `merchant_cutover_required`, and the
webhook returns `503` with `Retry-After: 300` so YooKassa can retry after the
operator restores the old account. Do not bypass the fingerprint check or
manually accept the webhook under the new merchant.

## Rollback

Stop new work first:

```bash
systemctl stop kaigo-builder-worker
```

Disable AntiGravity atomically before preparing the rollback. This preserves
the existing bearer key and provider settings without exposing them; the worker
remains stopped until the schema and route checks below are complete:

```bash
set -euo pipefail
set +x
ANTIGRAVITY_ENV=/etc/kaigo/builder-worker-antigravity.env
test "$(stat -c %u:%g:%a "$ANTIGRAVITY_ENV")" = "0:0:600"
test "$(grep -Ec '^KAIGO_ANTIGRAVITY_API_ENABLED=(true|false)$' "$ANTIGRAVITY_ENV")" = 1
ANTIGRAVITY_DISABLE_TMP="$(mktemp /etc/kaigo/builder-worker-antigravity.env.tmp.XXXXXX)"
trap 'rm -f "$ANTIGRAVITY_DISABLE_TMP"' EXIT
sed 's/^KAIGO_ANTIGRAVITY_API_ENABLED=true$/KAIGO_ANTIGRAVITY_API_ENABLED=false/' \
  "$ANTIGRAVITY_ENV" > "$ANTIGRAVITY_DISABLE_TMP"
test "$(grep -c '^KAIGO_ANTIGRAVITY_API_ENABLED=false$' "$ANTIGRAVITY_DISABLE_TMP")" = 1
test "$(grep -c '^KAIGO_ANTIGRAVITY_API_ENABLED=true$' "$ANTIGRAVITY_DISABLE_TMP" || true)" = 0
chown root:root "$ANTIGRAVITY_DISABLE_TMP"
chmod 600 "$ANTIGRAVITY_DISABLE_TMP"
mv -f "$ANTIGRAVITY_DISABLE_TMP" "$ANTIGRAVITY_ENV"
trap - EXIT
test "$(stat -c %u:%g:%a "$ANTIGRAVITY_ENV")" = "0:0:600"
```

Load the snapshot fail-closed: every identity and revision field is mandatory.
Validate and inspect both prior runtime images and the **current reviewed**
migration image. The latter must retain the current preflight/Alembic tooling;
never run rollback schema commands from the previous app image:

```bash
set -euo pipefail
umask 077
set -a
. /srv/kaigo/releases/rollback.env
set +a
: "${KAIGO_APP_IMAGE:?rollback app image is missing}"
: "${KAIGO_BUILDER_WORKER_IMAGE:?rollback worker image is missing}"
: "${KAIGO_BUILDER_WORKER_IMAGE_IDENTITY:?rollback worker identity is missing}"
: "${KAIGO_RELEASE_ID:?rollback release ID is missing}"
: "${KAIGO_BUILDER_WORKER_BOOT_ID:?fresh rollback boot ID is missing}"
: "${KAIGO_PREVIOUS_ALEMBIC_REVISION:?previous Alembic revision is missing}"
: "${KAIGO_ROLLBACK_MIGRATION_IMAGE:?trusted rollback migration image is missing}"
: "${KAIGO_DATABASE_OPS_IMAGE:?database ops image is missing}"
[[ "$KAIGO_APP_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_BUILDER_WORKER_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_BUILDER_WORKER_IMAGE_IDENTITY" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_ROLLBACK_MIGRATION_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_DATABASE_OPS_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_RELEASE_ID" =~ ^[A-Za-z0-9._:-]+$ ]]
[[ "$KAIGO_BUILDER_WORKER_BOOT_ID" =~ ^[0-9a-fA-F-]{36}$ ]]
[[ "$KAIGO_PREVIOUS_ALEMBIC_REVISION" =~ ^[0-9a-z_]+$ ]]
if [[ "$KAIGO_APP_IMAGE" == *@sha256:* && "$KAIGO_BUILDER_WORKER_IMAGE" == *@sha256:* ]]; then
  COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml pull app builder-worker
else
  [[ "$KAIGO_APP_IMAGE" == *@sha256:* ]] && COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml pull app
  [[ "$KAIGO_BUILDER_WORKER_IMAGE" == *@sha256:* ]] && COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml pull builder-worker
fi
[[ "$KAIGO_ROLLBACK_MIGRATION_IMAGE" == *@sha256:* ]] && docker pull "$KAIGO_ROLLBACK_MIGRATION_IMAGE"
docker image inspect "$KAIGO_ROLLBACK_MIGRATION_IMAGE" >/dev/null
docker image inspect "$KAIGO_APP_IMAGE" "$KAIGO_BUILDER_WORKER_IMAGE" \
  "$KAIGO_DATABASE_OPS_IMAGE" >/dev/null
APP_ENV_TMP="$(mktemp /etc/kaigo/release.env.tmp.XXXXXX)"
WORKER_ENV_TMP="$(mktemp /etc/kaigo/builder-worker-image.env.tmp.XXXXXX)"
trap 'rm -f "$APP_ENV_TMP" "$WORKER_ENV_TMP"' EXIT
cp /srv/kaigo/releases/rollback.env "$APP_ENV_TMP"
cp /srv/kaigo/releases/rollback.env "$WORKER_ENV_TMP"
chmod 600 "$APP_ENV_TMP"
chmod 600 "$WORKER_ENV_TMP"
mv -f "$APP_ENV_TMP" /etc/kaigo/release.env
mv -f "$WORKER_ENV_TMP" /etc/kaigo/builder-worker-image.env
trap - EXIT
```

Keep the worker stopped while deciding schema compatibility. Run read-only
preflight from the pinned current migration image, never the previous app:

```bash
docker run --rm --network kaigo_app_db --env-file .env "$KAIGO_ROLLBACK_MIGRATION_IMAGE" python scripts/preflight_saas_schema.py
```

The operator must select exactly one reviewed decision. The unset/unknown case
exits before any runtime starts. `keep-forward-compatible` requires an explicit
compatibility review; `downgrade-to-snapshot` requires a reviewed downgrade and
targets the recorded revision rather than an inferred `-1`:

```bash
case "${KAIGO_ROLLBACK_SCHEMA_DECISION:-}" in
  keep-forward-compatible)
    test "${KAIGO_ROLLBACK_COMPATIBILITY_REVIEWED:-}" = "yes"
    ;;
  downgrade-to-snapshot)
    test "${KAIGO_ROLLBACK_DOWNGRADE_REVIEWED:-}" = "yes"
    docker run --rm --network kaigo_app_db --env-file .env "$KAIGO_ROLLBACK_MIGRATION_IMAGE" alembic downgrade "$KAIGO_PREVIOUS_ALEMBIC_REVISION"
    ;;
  *)
    echo "Set an explicit reviewed KAIGO_ROLLBACK_SCHEMA_DECISION" >&2
    exit 1
    ;;
esac
```

If any downgrade would discard production data, restore the pre-release dump
instead of improvising a multi-revision downgrade. After either decision,
re-run the read-only preflight. This final preflight must pass before the app is
started:

```bash
docker run --rm --network kaigo_app_db --env-file .env "$KAIGO_ROLLBACK_MIGRATION_IMAGE" python scripts/preflight_saas_schema.py
```

Start and verify only the application, then validate nginx and the critical
application/public routes. `/api/ready` is intentionally not used here because
the worker must remain stopped until all schema and route decisions are complete:

```bash
COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml up -d --no-build app
APP_CONTAINER="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml ps -q app)"
test "$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")" = \
  "$(docker image inspect --format '{{.Id}}' "$KAIGO_APP_IMAGE")"
nginx -t
systemctl reload nginx
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/api/auth/session
curl -fsS https://kaigo.space/api/health
curl -fsS https://kaigo.space/studio/
```

Only after the final preflight and every route check has passed may the worker
be re-enabled. It is the last runtime started during rollback. After verifying
its actual image, wait for token-authenticated readiness and run the edge canary:

```bash
systemctl daemon-reload
systemctl restart kaigo-builder-worker
WORKER_CONTAINER="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile saas-worker ps -q builder-worker)"
test "$(docker inspect --format '{{.Image}}' "$WORKER_CONTAINER")" = \
  "$(docker image inspect --format '{{.Id}}' "$KAIGO_BUILDER_WORKER_IMAGE")"
systemctl is-active --quiet kaigo-builder-worker
umask 077
KAIGO_SMOKE_READINESS_TOKEN_FILE="$(mktemp /run/kaigo-readiness-token.XXXXXX)"
KAIGO_READINESS_HEADER_FILE="$(mktemp /run/kaigo-readiness-header.XXXXXX)"
trap 'rm -f "$KAIGO_SMOKE_READINESS_TOKEN_FILE" "$KAIGO_READINESS_HEADER_FILE"' EXIT
printf '%s' "$KAIGO_READINESS_TOKEN" > "$KAIGO_SMOKE_READINESS_TOKEN_FILE"
printf 'X-Kaigo-Readiness-Token: %s\n' "$KAIGO_READINESS_TOKEN" > "$KAIGO_READINESS_HEADER_FILE"
chmod 600 "$KAIGO_SMOKE_READINESS_TOKEN_FILE"
chmod 600 "$KAIGO_READINESS_HEADER_FILE"
READY=0
for attempt in $(seq 1 60); do
  if curl -fsS -H @"$KAIGO_READINESS_HEADER_FILE" https://kaigo.space/api/ready >/dev/null; then
    READY=1
    break
  fi
  sleep 2
done
test "$READY" = 1
export KAIGO_SMOKE_READINESS_TOKEN_FILE
python scripts/smoke_saas_foundation.py --base-url https://kaigo.space
rm -f "$KAIGO_SMOKE_READINESS_TOKEN_FILE" "$KAIGO_READINESS_HEADER_FILE"
unset KAIGO_SMOKE_READINESS_TOKEN_FILE
trap - EXIT
```
