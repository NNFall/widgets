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
   KAIGO_RELEASE_ID=<reviewed-commit-sha>
   KAIGO_BUILDER_WORKER_BOOT_ID=<new-random-uuid-for-this-rollout>
   KAIGO_DATABASE_OPS_IMAGE=postgres@sha256:<postgres-15-alpine-digest>
   ```

   The current server may instead use immutable local Docker image IDs when no
   registry is available. Build exactly once from the pinned, clean commit,
   capture each `.Id`, and write those exact `sha256:<64-hex>` values to the
   same release file:

   ```bash
   RELEASE_COMMIT="$(git rev-parse HEAD)"
   test -z "$(git status --porcelain)"
   test "$RELEASE_COMMIT" = '<reviewed-commit-sha>'
   docker build --pull --tag "kaigo-app-build:$RELEASE_COMMIT" .
   docker build --pull --file Dockerfile.builder-lab --tag "kaigo-worker-build:$RELEASE_COMMIT" .
   docker pull postgres:15-alpine
   KAIGO_APP_IMAGE="$(docker image inspect --format '{{.Id}}' "kaigo-app-build:$RELEASE_COMMIT")"
   KAIGO_BUILDER_WORKER_IMAGE="$(docker image inspect --format '{{.Id}}' "kaigo-worker-build:$RELEASE_COMMIT")"
   KAIGO_BUILDER_WORKER_IMAGE_IDENTITY="$KAIGO_BUILDER_WORKER_IMAGE"
   KAIGO_RELEASE_ID="$RELEASE_COMMIT"
   KAIGO_BUILDER_WORKER_BOOT_ID="$(cat /proc/sys/kernel/random/uuid)"
   KAIGO_DATABASE_OPS_IMAGE="$(docker image inspect --format '{{.Id}}' postgres:15-alpine)"
   printf '%s\n' \
     "KAIGO_APP_IMAGE=$KAIGO_APP_IMAGE" \
     "KAIGO_BUILDER_WORKER_IMAGE=$KAIGO_BUILDER_WORKER_IMAGE" \
     "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY=$KAIGO_BUILDER_WORKER_IMAGE_IDENTITY" \
     "KAIGO_RELEASE_ID=$KAIGO_RELEASE_ID" \
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
     COMPOSE_PROJECT_NAME=kaigo docker compose pull app builder-worker migration
   else
     [[ "$KAIGO_APP_IMAGE" == *@sha256:* ]] && COMPOSE_PROJECT_NAME=kaigo docker compose pull app migration
     [[ "$KAIGO_BUILDER_WORKER_IMAGE" == *@sha256:* ]] && COMPOSE_PROJECT_NAME=kaigo docker compose pull builder-worker
   fi
   if [[ "$KAIGO_DATABASE_OPS_IMAGE" == *@sha256:* ]]; then
     COMPOSE_PROJECT_NAME=kaigo docker compose --profile operations pull database-ops
   fi
   docker image inspect "$KAIGO_APP_IMAGE" "$KAIGO_BUILDER_WORKER_IMAGE" \
     "$KAIGO_DATABASE_OPS_IMAGE" \
     > "/srv/kaigo/releases/$(date -u +%Y%m%dT%H%M%SZ)-images.json"
   COMPOSE_PROJECT_NAME=kaigo docker compose config --quiet
   ```

   Copy the same worker reference into
   `/etc/kaigo/builder-worker-image.env`; the systemd unit refuses a mutable or
   absent worker image and refuses to build during startup.

2. Back up PostgreSQL and record the current Alembic revision. Both commands
   run in disposable Compose containers attached only to `kaigo_app_db`, where
   the database hostname is `db`; no host Python or `pg_dump` is used:

   ```bash
   COMPOSE_PROJECT_NAME=kaigo docker compose --profile operations run --rm database-ops pg_dump --format=custom --file=/backups/pre-saas.dump
   COMPOSE_PROJECT_NAME=kaigo docker compose --profile operations run --rm database-ops psql -Atc 'select version_num from alembic_version'
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
   APP_CONTAINER="$(COMPOSE_PROJECT_NAME=kaigo docker compose ps -q app)"
   WORKER_CONTAINER="$(COMPOSE_PROJECT_NAME=kaigo docker compose --profile saas-worker ps -q builder-worker)"
   test -n "$APP_CONTAINER"
   test -n "$WORKER_CONTAINER"
   ROLLBACK_APP_IMAGE="$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")"
   ROLLBACK_WORKER_IMAGE="$(docker inspect --format '{{.Image}}' "$WORKER_CONTAINER")"
   ROLLBACK_RELEASE_ID="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$APP_CONTAINER" | sed -n 's/^KAIGO_RELEASE_ID=//p')"
   ROLLBACK_WORKER_IMAGE_IDENTITY="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$WORKER_CONTAINER" | sed -n 's/^KAIGO_BUILDER_WORKER_IMAGE_IDENTITY=//p')"
   ROLLBACK_PREVIOUS_ALEMBIC_REVISION="$(COMPOSE_PROJECT_NAME=kaigo docker compose --profile operations run --rm database-ops psql -Atc 'select version_num from alembic_version')"
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
COMPOSE_PROJECT_NAME=kaigo docker compose --profile operations run --rm migration python scripts/preflight_saas_schema.py
```

Review its proposed action and the database backup. Mutation requires the
explicit mode:

```bash
COMPOSE_PROJECT_NAME=kaigo docker compose --profile operations run --rm migration python scripts/preflight_saas_schema.py --apply
```

For the exact unversioned legacy schema only, `--apply` stamps
`0002_widget_settings` and then runs `alembic upgrade head`. It never stamps an
unknown schema.

## Start order and smoke

Schema migration and the atomic rollback snapshot finish before either runtime
is switched. Stop the old worker first so it drains its current claim under the
systemd stop timeout and cannot claim new work during the switch:

```bash
systemctl stop kaigo-builder-worker
COMPOSE_PROJECT_NAME=kaigo docker compose up -d --no-build app
cp /etc/kaigo/release.env /etc/kaigo/builder-worker-image.env
systemctl daemon-reload
systemctl restart kaigo-builder-worker
nginx -t
systemctl reload nginx
```

Do not replace the restart with a plain `systemctl start kaigo-builder-worker`:
an already-running unit could otherwise keep the previous worker container.

Verify the images of the actual running containers, not only the locally
available image references. Both comparisons must succeed:

```bash
APP_CONTAINER="$(COMPOSE_PROJECT_NAME=kaigo docker compose ps -q app)"
WORKER_CONTAINER="$(COMPOSE_PROJECT_NAME=kaigo docker compose --profile saas-worker ps -q builder-worker)"
test "$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")" = \
  "$(docker image inspect --format '{{.Id}}' "$KAIGO_APP_IMAGE")"
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
  COMPOSE_PROJECT_NAME=kaigo docker compose pull app builder-worker
else
  [[ "$KAIGO_APP_IMAGE" == *@sha256:* ]] && COMPOSE_PROJECT_NAME=kaigo docker compose pull app
  [[ "$KAIGO_BUILDER_WORKER_IMAGE" == *@sha256:* ]] && COMPOSE_PROJECT_NAME=kaigo docker compose pull builder-worker
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
COMPOSE_PROJECT_NAME=kaigo docker compose up -d --no-build app
APP_CONTAINER="$(COMPOSE_PROJECT_NAME=kaigo docker compose ps -q app)"
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
WORKER_CONTAINER="$(COMPOSE_PROJECT_NAME=kaigo docker compose --profile saas-worker ps -q builder-worker)"
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
