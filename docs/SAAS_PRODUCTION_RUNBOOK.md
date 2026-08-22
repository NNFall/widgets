# Kaigo SaaS production runbook

This is a release procedure, not authorization to deploy. **No live rollout**
is performed by this repository task.

## Before the maintenance window

Open one dedicated root Bash release session before preparing either the CI
references or the local fallback artifacts. Run every numbered preparation
step, both schema-preflight commands, and the app/static identity gate in that
same root Bash release session. Do not copy the later blocks into fresh shells:
the session owns the rollout lock until static activation has succeeded.

Acquire the lock before the first shared release file or database mutation.
The single cleanup trap removes any in-progress temporary artifact and releases
the descriptor on every failure or signal. `release_session_finish` is called
explicitly by the identity gate only after the deploy command has returned
successfully:

```bash
set -euo pipefail
set +x
umask 077
PUBLICATION_RELEASE_LOCK="/run/kaigo-publication-release.lock"
PUBLICATION_RELEASE_LOCK_FD=9
PUBLICATION_RELEASE_LOCK_HELD=0
STATIC_ARCHIVE_STAGING=""
EGRESS_ENV_TMP=""
ANTIGRAVITY_ENV_TMP=""
ROLLBACK_TMP=""
PUBLICATION_CONTRACT_GATE=""

release_session_remove_temps() {
  if [[ -n "$STATIC_ARCHIVE_STAGING" ]]; then
    rm -rf -- "$STATIC_ARCHIVE_STAGING"
  fi
  for release_temp in \
    "$EGRESS_ENV_TMP" \
    "$ANTIGRAVITY_ENV_TMP" \
    "$ROLLBACK_TMP" \
    "$PUBLICATION_CONTRACT_GATE"
  do
    if [[ -n "$release_temp" ]]; then
      rm -f -- "$release_temp"
    fi
  done
}

release_session_cleanup() {
  release_status=$?
  trap - EXIT HUP INT TERM
  set +e
  release_session_remove_temps
  if [[ "$PUBLICATION_RELEASE_LOCK_HELD" = 1 ]]; then
    flock -u "$PUBLICATION_RELEASE_LOCK_FD"
    exec 9>&-
    PUBLICATION_RELEASE_LOCK_HELD=0
  fi
  exit "$release_status"
}

release_session_finish() {
  test "$PUBLICATION_RELEASE_LOCK_HELD" = 1
  release_session_remove_temps
  flock -u "$PUBLICATION_RELEASE_LOCK_FD"
  exec 9>&-
  PUBLICATION_RELEASE_LOCK_HELD=0
  trap - EXIT HUP INT TERM
}

trap release_session_cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
exec 9> "$PUBLICATION_RELEASE_LOCK"
flock -n "$PUBLICATION_RELEASE_LOCK_FD"
PUBLICATION_RELEASE_LOCK_HELD=1
```

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
   cp -a -- frontend/dist/. "$STATIC_ARCHIVE_STAGING/"
   printf '%s\n' "$RELEASE_COMMIT" > "$STATIC_ARCHIVE_STAGING/.kaigo-release-sha"
   KAIGO_MARKETING_ARCHIVE="/srv/kaigo/releases/kaigo-marketing-$RELEASE_COMMIT.tar.gz"
   test ! -e "$KAIGO_MARKETING_ARCHIVE"
   tar --directory "$STATIC_ARCHIVE_STAGING" --create --gzip \
     --file "$KAIGO_MARKETING_ARCHIVE" .
   KAIGO_MARKETING_ARCHIVE_SHA256="$(sha256sum "$KAIGO_MARKETING_ARCHIVE" | awk '{print $1}')"
   [[ "$KAIGO_MARKETING_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]]
   rm -rf -- "$STATIC_ARCHIVE_STAGING"
   STATIC_ARCHIVE_STAGING=""
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
   If automation needs the aggregate operator reports, optionally generate a
   separate random 32+ byte `KAIGO_OPERATOR_READ_TOKEN` directly in the
   root-only application secret store. This token is accepted only on the JSON
   read routes for the funnel and sanitized generation runs; it is not a user,
   admin, billing, or mutation credential. Never put it in Git, a URL, shell
   arguments, logs, or chat. The CLI can read the same value from a one-line
   root-only file through `KAIGO_OPERATOR_READ_TOKEN_FILE`:

   ```bash
   set -euo pipefail
   umask 077
   OPERATOR_TOKEN_FILE="$(mktemp /run/kaigo-operator-read-token.XXXXXX)"
   trap 'rm -f "$OPERATOR_TOKEN_FILE"' EXIT
   openssl rand -base64 48 | tr -d '\n' > "$OPERATOR_TOKEN_FILE"
   chmod 600 "$OPERATOR_TOKEN_FILE"
   test "$(stat -c '%a' "$OPERATOR_TOKEN_FILE")" = 600
   # Install the value from this file into KAIGO_OPERATOR_READ_TOKEN in the
   # root-only app EnvironmentFile without printing it or passing it in argv.
   export KAIGO_OPERATOR_READ_TOKEN_FILE="$OPERATOR_TOKEN_FILE"
   python scripts/operator_metrics.py snapshot \
     --from 2026-08-22 --to 2026-08-23 --source yandex
   unset KAIGO_OPERATOR_READ_TOKEN_FILE
   trap - EXIT
   rm -f "$OPERATOR_TOKEN_FILE"
   ```

   Rotate by replacing the environment value and restarting the application;
   the previous value immediately stops working. Revoke by removing the
   variable and restarting. A missing value leaves the existing OAuth-only
   operator access unchanged.
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
   printf '%s\n' \
     'KAIGO_WORKER_PUBLIC_HTTPS_HOST=5.129.236.90/32' \
     'KAIGO_WORKER_PUBLIC_HTTPS_PORT=443' \
     > "$EGRESS_ENV_TMP"
   install -o root -g root -m 600 "$EGRESS_ENV_TMP" \
     /etc/kaigo/builder-worker-egress.env
   rm -f "$EGRESS_ENV_TMP"
   EGRESS_ENV_TMP=""
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
   ANTIGRAVITY_ENV_TMP=""
   unset ANTIGRAVITY_KEY_LINES
   test "$(stat -c %u:%g:%a /etc/kaigo/builder-worker-antigravity.env)" = "0:0:600"
   ```

   Then install `deploy/systemd/kaigo-builder-worker.service`, adjusting only
   the immutable checkout path if production uses a different path. Docker
   restart remains disabled for `builder-worker`; systemd is the sole restart
   owner.

5. Before any runtime switch, atomically capture the complete prior release
   identity. The rollback tuple contains the images actually running now, the
   prior app release and worker-image identities, the exact current Alembic
   revision, the validated current marketing symlink target, a fresh boot ID
   reserved for a rollback worker, and the selected current release image that
   is trusted to run rollback migrations. The snapshot is renamed only after
   every required value, image, and static directory is verified:

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
   ROLLBACK_MARKETING_CURRENT_LINK="/var/www/kaigo-marketing/current"
   test -L "$ROLLBACK_MARKETING_CURRENT_LINK"
   ROLLBACK_MARKETING_RELEASE_DIR="$(readlink -f -- "$ROLLBACK_MARKETING_CURRENT_LINK")"
   test -d "$ROLLBACK_MARKETING_RELEASE_DIR"
   ROLLBACK_MARKETING_RELEASE_ID="$(basename -- "$ROLLBACK_MARKETING_RELEASE_DIR")"
   [[ "$ROLLBACK_MARKETING_RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]
   test "$ROLLBACK_MARKETING_RELEASE_DIR" = "/var/www/kaigo-marketing/releases/$ROLLBACK_MARKETING_RELEASE_ID"
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
   printf '%s\n' \
     "KAIGO_APP_IMAGE=$ROLLBACK_APP_IMAGE" \
     "KAIGO_BUILDER_WORKER_IMAGE=$ROLLBACK_WORKER_IMAGE" \
     "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY=$ROLLBACK_WORKER_IMAGE_IDENTITY" \
     "KAIGO_RELEASE_ID=$ROLLBACK_RELEASE_ID" \
     "KAIGO_BUILDER_WORKER_BOOT_ID=$ROLLBACK_WORKER_BOOT_ID" \
     "KAIGO_PREVIOUS_ALEMBIC_REVISION=$ROLLBACK_PREVIOUS_ALEMBIC_REVISION" \
     "KAIGO_ROLLBACK_MIGRATION_IMAGE=$ROLLBACK_MIGRATION_IMAGE" \
     "KAIGO_ROLLBACK_MARKETING_RELEASE_ID=$ROLLBACK_MARKETING_RELEASE_ID" \
     "KAIGO_ROLLBACK_MARKETING_RELEASE_DIR=$ROLLBACK_MARKETING_RELEASE_DIR" \
     "KAIGO_DATABASE_OPS_IMAGE=$KAIGO_DATABASE_OPS_IMAGE" \
     > "$ROLLBACK_TMP"
   chmod 600 "$ROLLBACK_TMP"
   mv -f "$ROLLBACK_TMP" /srv/kaigo/releases/rollback.env
   ROLLBACK_TMP=""
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
inside the locked identity gate below; do not start the new worker or activate
the Studio archive separately.

For every release that changes the publication contract, run the following
identity gate before switching `/var/www/kaigo-marketing/current`. It also
applies to ordinary combined app/static releases, so the stricter path can be
used consistently. The gate verifies the selected app and migration services
resolve to the same immutable image, verifies the archive digest and embedded
full SHA, and reads only the single `KAIGO_RELEASE_ID` value from the live app.
Shell tracing stays disabled; the command never dumps the container environment
or `/etc/kaigo/release.env`.

The dedicated release shell already holds
`/run/kaigo-publication-release.lock`, acquired before release-file changes,
the rollback snapshot, and schema preflight. It retains that descriptor across
the app identity and loopback checks and releases it only after static
activation completes. Every app/static release operator must use the full
session; a second rollout therefore fails before either rollout can mutate
shared release state or the database.

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
test "${PUBLICATION_RELEASE_LOCK_HELD:-0}" = 1
systemctl stop kaigo-builder-worker
COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml up -d --no-build app
PUBLICATION_CONTRACT_COMPOSE_PROJECT=ai_project
PUBLICATION_CONTRACT_APP_CONFIG_IMAGE="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml config --format json | jq -er '.services.app.image')"
PUBLICATION_CONTRACT_MIGRATION_CONFIG_IMAGE="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml --profile operations config --format json | jq -er '.services.migration.image')"
PUBLICATION_CONTRACT_SELECTED_APP_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$KAIGO_APP_IMAGE")"
APP_CONTAINER="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml ps -q app)"
test -n "$APP_CONTAINER"
PUBLICATION_CONTRACT_LIVE_APP_IMAGE_ID="$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")"
PUBLICATION_CONTRACT_LIVE_APP_SHA="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$APP_CONTAINER" | sed -n 's/^KAIGO_RELEASE_ID=//p')"
PUBLICATION_CONTRACT_HEALTH_URL=http://127.0.0.1:8080/api/health
PUBLICATION_CONTRACT_AUTH_SESSION_URL=http://127.0.0.1:8080/api/auth/session
KAIGO_MARKETING_RELEASES_ROOT=/srv/kaigo/releases
KAIGO_MARKETING_DEPLOY_INTERPRETER=/bin/bash
KAIGO_MARKETING_DEPLOY_SCRIPT=/opt/kaigo/current/scripts/deploy_marketing_site.sh
export PUBLICATION_CONTRACT_COMPOSE_PROJECT
export PUBLICATION_CONTRACT_APP_CONFIG_IMAGE
export PUBLICATION_CONTRACT_MIGRATION_CONFIG_IMAGE
export PUBLICATION_CONTRACT_SELECTED_APP_IMAGE_ID
export PUBLICATION_CONTRACT_LIVE_APP_IMAGE_ID
export PUBLICATION_CONTRACT_LIVE_APP_SHA
export PUBLICATION_CONTRACT_HEALTH_URL
export PUBLICATION_CONTRACT_AUTH_SESSION_URL
export KAIGO_MARKETING_RELEASES_ROOT
export KAIGO_MARKETING_DEPLOY_INTERPRETER
export KAIGO_MARKETING_DEPLOY_SCRIPT
PUBLICATION_CONTRACT_GATE="$(mktemp /run/kaigo-publication-contract-gate.XXXXXX.py)"
cat > "$PUBLICATION_CONTRACT_GATE" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
IMAGE = re.compile(r"^(?:[^\s]+@sha256:|sha256:)[0-9a-fA-F]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


def reject() -> None:
    print("publication release identity gate failed", file=sys.stderr)
    raise SystemExit(1)


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        reject()
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def probe_json(kind: str) -> dict[str, object]:
    variable = (
        "PUBLICATION_CONTRACT_HEALTH_URL"
        if kind == "health"
        else "PUBLICATION_CONTRACT_AUTH_SESSION_URL"
    )
    expected_path = "/api/health" if kind == "health" else "/api/auth/session"
    url = required(variable)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
    ):
        reject()
    opener = build_opener(NoRedirect())
    request = Request(url, headers={"Accept": "application/json"})
    with opener.open(request, timeout=5) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            reject()
        body = response.read(65_537)
    if len(body) > 65_536:
        reject()
    payload = json.loads(body)
    if not isinstance(payload, dict):
        reject()
    return payload


staging: Path | None = None
try:
    app_image = required("KAIGO_APP_IMAGE")
    release_sha = required("KAIGO_RELEASE_ID")
    static_sha = required("KAIGO_MARKETING_RELEASE_SHA")
    archive_digest = required("KAIGO_MARKETING_ARCHIVE_SHA256")
    compose_project = required("PUBLICATION_CONTRACT_COMPOSE_PROJECT")
    app_config_image = required("PUBLICATION_CONTRACT_APP_CONFIG_IMAGE")
    migration_config_image = required("PUBLICATION_CONTRACT_MIGRATION_CONFIG_IMAGE")
    selected_app_image_id = required("PUBLICATION_CONTRACT_SELECTED_APP_IMAGE_ID")
    live_app_image_id = required("PUBLICATION_CONTRACT_LIVE_APP_IMAGE_ID")
    live_app_sha = required("PUBLICATION_CONTRACT_LIVE_APP_SHA")
    releases_root = Path(required("KAIGO_MARKETING_RELEASES_ROOT")).resolve()
    archive_path = Path(required("KAIGO_MARKETING_ARCHIVE")).resolve()
    deploy_interpreter = Path(required("KAIGO_MARKETING_DEPLOY_INTERPRETER"))
    deploy_script = Path(required("KAIGO_MARKETING_DEPLOY_SCRIPT"))

    if (
        compose_project != "ai_project"
        or not IMAGE.fullmatch(app_image)
        or not FULL_SHA.fullmatch(release_sha)
        or not FULL_SHA.fullmatch(static_sha)
        or not DIGEST.fullmatch(archive_digest)
        or not IMAGE_ID.fullmatch(selected_app_image_id)
        or not IMAGE_ID.fullmatch(live_app_image_id)
        or not FULL_SHA.fullmatch(live_app_sha)
        or app_config_image != app_image
        or migration_config_image != app_image
        or selected_app_image_id != live_app_image_id
        or release_sha != static_sha
        or release_sha != live_app_sha
        or not deploy_interpreter.is_file()
        or not deploy_script.is_file()
    ):
        reject()
    if (
        archive_path.parent != releases_root
        or archive_path.name != f"kaigo-marketing-{release_sha}.tar.gz"
        or not archive_path.is_file()
    ):
        reject()

    digest = hashlib.sha256()
    with archive_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != archive_digest:
        reject()

    with tarfile.open(archive_path, "r:gz") as archive:
        members: dict[str, tarfile.TarInfo] = {}
        total_size = 0
        for member in archive.getmembers():
            if member.name in (".", "./"):
                if not member.isdir():
                    reject()
                continue
            path = PurePosixPath(member.name)
            parts = tuple(part for part in path.parts if part not in ("", "."))
            if path.is_absolute() or not parts or ".." in parts:
                reject()
            name = "/".join(parts)
            if name in members or not (member.isfile() or member.isdir()):
                reject()
            total_size += member.size
            if len(members) >= 10_000 or total_size > 512 * 1024 * 1024:
                reject()
            members[name] = member

        marker_member = members.get(".kaigo-release-sha")
        marker_stream = archive.extractfile(marker_member) if marker_member else None
        if marker_member is None or not marker_member.isfile() or marker_stream is None:
            reject()
        marker = marker_stream.read(128).decode("ascii", errors="strict").strip()
        if marker != release_sha or not FULL_SHA.fullmatch(marker):
            reject()
        if "index.html" not in members or not any(
            name.startswith("assets/") for name in members
        ):
            reject()

        health_payload = probe_json("health")
        if health_payload != {"status": "ok"}:
            reject()
        auth_payload = probe_json("auth")
        providers = auth_payload.get("providers")
        if (
            auth_payload.get("enabled") is not True
            or auth_payload.get("authenticated") is not False
            or not isinstance(providers, list)
            or not providers
            or not all(isinstance(provider, str) and provider for provider in providers)
        ):
            reject()

        staging = Path(
            tempfile.mkdtemp(prefix=".kaigo-marketing-deploy.", dir=releases_root)
        )
        for name, member in members.items():
            target = staging / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                reject()
            with source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)

    if not (staging / "index.html").is_file() or not (staging / "assets").is_dir():
        reject()
    deploy_environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "KAIGO_MARKETING_DIST_DIR": str(staging),
        "KAIGO_MARKETING_SKIP_BUILD": "1",
        "KAIGO_MARKETING_DEPLOY_ROOT": "/var/www/kaigo-marketing",
        "KAIGO_NGINX_BIN": "/usr/sbin/nginx",
        "KAIGO_SYSTEMCTL_BIN": "/usr/bin/systemctl",
    }
    for system_name in ("SYSTEMROOT", "WINDIR"):
        system_value = os.environ.get(system_name)
        if system_value:
            deploy_environment[system_name] = system_value
    subprocess.run(
        [str(deploy_interpreter), str(deploy_script), release_sha],
        env=deploy_environment,
        check=True,
    )
except SystemExit:
    raise
except Exception:
    reject()
finally:
    if staging is not None:
        shutil.rmtree(staging, ignore_errors=True)
PY
python "$PUBLICATION_CONTRACT_GATE"
rm -f -- "$PUBLICATION_CONTRACT_GATE"
PUBLICATION_CONTRACT_GATE=""
release_session_finish
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

The release gate has already required the loopback health and unauthenticated
session contracts before static activation. Repeat them after worker startup as
a diagnostic; do not request `/studio/` on the app port because nginx owns that
static route:

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

The read-only operator report client is deliberately separate from the browser
cookie smoke. When `KAIGO_OPERATOR_READ_TOKEN_FILE` is present, it can fetch
only the aggregate JSON reports:

```bash
python scripts/operator_metrics.py snapshot \
  --from 2026-08-22 --to 2026-08-23 --source yandex
```

Do not put the token in a query string or export it through shell tracing. The
service token cannot authorize HTML pages, admin actions, billing, or writes.

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
: "${KAIGO_ROLLBACK_MARKETING_RELEASE_ID:?rollback marketing release ID is missing}"
: "${KAIGO_ROLLBACK_MARKETING_RELEASE_DIR:?rollback marketing release directory is missing}"
: "${KAIGO_DATABASE_OPS_IMAGE:?database ops image is missing}"
[[ "$KAIGO_APP_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_BUILDER_WORKER_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_BUILDER_WORKER_IMAGE_IDENTITY" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_ROLLBACK_MIGRATION_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_DATABASE_OPS_IMAGE" =~ ^(.+@sha256:|sha256:)[0-9a-fA-F]{64}$ ]]
[[ "$KAIGO_RELEASE_ID" =~ ^[A-Za-z0-9._:-]+$ ]]
[[ "$KAIGO_BUILDER_WORKER_BOOT_ID" =~ ^[0-9a-fA-F-]{36}$ ]]
[[ "$KAIGO_PREVIOUS_ALEMBIC_REVISION" =~ ^[0-9a-z_]+$ ]]
[[ "$KAIGO_ROLLBACK_MARKETING_RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]
test "$KAIGO_ROLLBACK_MARKETING_RELEASE_DIR" = "/var/www/kaigo-marketing/releases/$KAIGO_ROLLBACK_MARKETING_RELEASE_ID"
test -d "$KAIGO_ROLLBACK_MARKETING_RELEASE_DIR"
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

Start and verify only the application, restore the captured marketing release
with an atomic symlink replacement, then validate nginx and the critical
application/public routes. This prevents a later worker or smoke failure from
leaving the rolled-back app behind the newer publication UI. `/api/ready` is
intentionally not used here because the worker must remain stopped until all
schema and route decisions are complete:

```bash
COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml up -d --no-build app
APP_CONTAINER="$(COMPOSE_PROJECT_NAME=ai_project docker compose --file /opt/kaigo/current/docker-compose.yml --file /etc/kaigo/docker-compose.pattern-selection.yml ps -q app)"
test "$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")" = \
  "$(docker image inspect --format '{{.Id}}' "$KAIGO_APP_IMAGE")"
ROLLBACK_MARKETING_NEXT_LINK="/var/www/kaigo-marketing/.current.rollback.$$"
trap 'rm -f -- "$ROLLBACK_MARKETING_NEXT_LINK"' EXIT
test ! -e "$ROLLBACK_MARKETING_NEXT_LINK"
ln -s -- "$KAIGO_ROLLBACK_MARKETING_RELEASE_DIR" "$ROLLBACK_MARKETING_NEXT_LINK"
mv -Tf -- "$ROLLBACK_MARKETING_NEXT_LINK" "/var/www/kaigo-marketing/current"
trap - EXIT
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
