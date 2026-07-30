# YooKassa Recurring Subscriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Довести существующий redirect-биллинг Kaigo до безопасной повторной
оплаты через сохранённый способ ЮKassa: с явным согласием, восстановлением
потерянных webhook, одним renewal на расчётный период, отключаемым
автопродлением и доказательством на тестовом магазине.

**Architecture:** Первый redirect-платёж остаётся server-priced и exactly-once,
но запрашивает сохранение способа оплаты только после явного согласия. Opaque
`payment_method_id` сохраняется только из повторно проверенного `succeeded`
объекта с `payment_method.saved=true`; webhook и 60-секундный reconciler входят
в один transactional fulfillment. Отдельный billing worker всегда может
сверять известные pending payment IDs, а создание renewal включается отдельным
feature flag только в тестовом контуре до прохождения E2E gate.

**Tech Stack:** Python 3.12, aiohttp, SQLAlchemy 2 async, Alembic, PostgreSQL,
SQLite test doubles, httpx `MockTransport`, pytest, React 19, TypeScript,
Vitest, Playwright.

---

## Обязательные границы

- Миграция называется ровно
  `migrations/versions/0015_yookassa_recurring_foundation.py` и имеет
  `down_revision = "0014_generation_forensics"`. Номер `0014` не занимать и не
  переподключать `0015` напрямую к `0013_pattern_registry`.
- До начала Task 1 файл `0014_generation_forensics.py` должен уже существовать
  и быть единственной предыдущей revision. Если его ещё нет, остановить
  исполнение этого плана до завершения forensic-плана.
- Схема только расширяется: не удалять старые колонки, таблицы и billing rows.
  Data backfill допустим только для безопасных defaults и нормализации
  `subscription.credit` из `generation_tokens` в существующий `tokens` bucket.
- В репозиторий, документацию, логи, exception text, JSON evidence и frontend
  не попадают значения merchant credentials и opaque
  `provider_payment_method_id`. Не читать и не менять `.env`-файлы.
- Существующие подписки после миграции получают только `auto_renew=false`, без
  придуманного payment method и без изменения оплаченного периода.
- Первый новый checkout требует явного consent и отправляет
  `save_payment_method=true`. Если verified payment не содержит одновременно
  `status=succeeded`, `paid=true` и `payment_method.saved=true`, оплата всё равно
  активирует уже оплаченный период, но автопродление остаётся выключенным.
- Frontend читает только локальный Kaigo status раз в 3 секунды и прекращает
  polling после 400 запросов (20 минут). Он не вызывает ЮKassa напрямую.
- Worker должен подобрать due reconciliation не позднее 60 секунд после
  появления известного provider payment ID и не делать `GET` одного attempt
  чаще одного раза за 60 секунд, даже при нескольких процессах.
- Неизвестный результат `POST /payments` сразу сохраняется как
  `dispatch_unknown`. Такой attempt можно повторять только с теми же body,
  merchant fingerprint и provider idempotency key и только до 24 часов от
  первого dispatch. На границе окна и после неё новый POST для него или новый
  renewal того же периода запрещён; истечение видно по
  `provider_idempotency_expires_at`, status не маскируется как definitive failure.
- Один billing period образует один logical renewal cycle. В нём допустимы
  primary attempt и максимум один новый provider payment через 24 часа после
  подтверждённой временной отмены. Retry получает отдельный local attempt и
  новый idempotency key; ambiguous/unknown POST никогда не создаёт retry.
  Permanent cancellation или неуспех единственного retry выключает дальнейшее
  автопродление и оставляет текущий оплаченный период неизменным.
- Если фискализация включена server environment, initial и renewal body получают
  один immutable `receipt`: `User.email`, подтверждённый совпадающим
  `UserIdentity(email_verified=true)`, server-owned plan title и amount, а также
  проверенные `vat_code`, `payment_mode`, `payment_subject` и optional
  `tax_system_code`. Эти значения не задаются frontend и не hard-code-ятся без
  решения бухгалтера/владельца.
- Live renewal не входит в критерий готовности этого плана. Gate — реальный
  test-shop E2E; после него разрешается включить flag только в тестовом
  окружении. Live rollout требует отдельного решения владельца.
- Первый безопасный vertical slice заканчивается на initial redirect,
  verified saved-method persistence, exactly-once fulfillment,
  reconciliation и owner controls. Billing worker разворачивается с
  `renewals_enabled=false`. Task 6 запрещено включать, пока его финансовые
  race-контракты не доказаны.
- Initial checkout и renewal используют один User-lock/CAS boundary. Новый
  checkout запрещён при активной автопродлеваемой подписке или nonterminal
  renewal cycle; API показывает `past_due`/`renewal_in_progress`, а не
  предлагает второй платёж за тот же период.
- Отключение автопродления и право worker-а начать POST имеют один
  point-of-no-return. До durable dispatch CAS пользователь отменяет все
  недиспетчеризованные attempts; после начала POST API честно сообщает
  `renewal_in_progress`. Поздний success продлевает оплаченный период, но не
  включает `auto_renew` обратно.

Текущие provider-инварианты сверять с официальными страницами ЮKassa:
[24-часовая идемпотентность](https://yookassa.ru/developers/using-api/interaction-format),
[сохранение способа во время платежа](https://yookassa.ru/developers/payment-acceptance/scenario-extensions/recurring-payments/save-payment-method/save-during-payment)
и [платёж сохранённым способом](https://yookassa.ru/developers/payment-acceptance/scenario-extensions/recurring-payments/pay-with-saved),
[чеки при платежах](https://yookassa.ru/developers/payment-acceptance/receipts/54fz/yoomoney/payments),
[справочник параметров чека](https://yookassa.ru/developers/payment-acceptance/receipts/54fz/yoomoney/parameters-values)
и [официальные тестовые сценарии/карты](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing).

## Целевая файловая структура

### Создать

- `migrations/versions/0015_yookassa_recurring_foundation.py` — additive
  recurring schema, legacy defaults и `tokens` bucket backfill.
- `tests/saas_cases/test_billing_recurring_migration.py` — revision chain,
  backfill, constraints и downgrade coverage.
- `app/billing/receipts.py` — единый verified-email lookup и immutable receipt
  builder для initial и renewal payment.
- `tests/saas_cases/test_billing_receipts.py` — disabled mode, verified email,
  balanced item и fail-before-POST tests.
- `app/billing/reconciliation.py` — DB lease и provider GET для известных
  pending payments.
- `tests/saas_cases/test_billing_reconciliation.py` — lost-webhook,
  60-second cadence и webhook/reconciler race.
- `app/billing/renewals.py` — due-cycle claim, snapshot-based attempt creation и
  stored-method dispatch.
- `tests/saas_cases/test_billing_renewals.py` — one logical cycle per period,
  primary attempt, one-day temporary retry, merchant binding, immediate
  success, pending и permanent failure cases.
- `app/billing/worker.py` — stop-aware loop, в котором reconciliation всегда
  доступен, а renewal dispatch проверяет feature flag.
- `scripts/run_billing_worker.py` — отдельная process entrypoint без вывода
  конфигурации или provider identifiers.
- `tests/saas_cases/test_billing_worker.py` — feature-flag и lifecycle tests.
- `deploy/systemd/kaigo-billing-worker.service` — отдельный restartable worker.
- `docs/release-evidence/2026-07-30-yookassa-test-shop.md` — создавать только
  после фактического test-shop прогона; хранить только redacted результаты.

### Изменить

- `app/saas/models.py` — `BillingPaymentMethod` и additive recurring columns.
- `app/billing/contracts.py` — saved-method и recurring provider contracts.
- `app/billing/yookassa.py` — save flag, stored-method POST и direct GET.
- `app/billing/payments.py` — consent snapshot, 24-hour dispatch guard и общий
  verified fulfillment.
- `app/billing/service.py` — единый `tokens` balance query.
- `app/billing/routes.py` — server-owned plan response, explicit consent,
  redacted status и idempotent auto-renew off action.
- `app/billing/runtime.py` — единая provider/service factory для web и worker.
- `app/billing/__init__.py` — публичные billing service exports.
- `app/config.py` — disabled-by-default renewal flag, bounded worker cadence и
  validated receipt/54-ФЗ settings from server environment.
- `tests/saas_cases/test_billing_provider.py` — provider payload/parse tests.
- `tests/saas_cases/test_billing_service.py` — consent, method persistence,
  24-hour guard, fulfillment и unified bucket tests.
- `tests/saas_cases/test_billing_routes.py` — owner/CSRF/serialization tests.
- `tests/saas_cases/test_config.py` — safe defaults и validation.
- `frontend/src/studio/types.ts` — recurring-safe response types.
- `frontend/src/studio/api.ts` — plan, consent и disable-auto-renew calls.
- `frontend/src/studio/api.test.ts` — exact HTTP contracts.
- `frontend/src/studio/UpgradeGate.tsx` — consent UI, 3s/20m polling и off UX.
- `frontend/src/studio/UpgradeGate.test.tsx` — fake-timer and consent tests.
- `frontend/e2e/fixtures/builder.ts` — safe recurring response fixtures.
- `frontend/e2e/studio.spec.ts` — visible consent/off acceptance.
- `docker-compose.yml` — billing-worker service, без secret values в файле.
- `tests/deployment_cases/test_saas_production_contract.py` — worker deployment
  и renewals-off default.
- `docs/BILLING_FOUNDATION.md` — новый проверенный контракт и rollout gate.
- `docs/SAAS_PRODUCTION_RUNBOOK.md` — schema-first, worker и kill-switch order.
- `docs/product-journal/2026-07.md` и подходящий release packet — только после
  фактического test-shop доказательства, согласно `AGENTS.md`.

## Целевые состояния и публичный контракт

`PaymentAttempt.status` использует `scheduled`, `creating`, `pending`,
`succeeded`, `cancelled`, `failed`, `dispatch_unknown`. Последнее означает: POST мог быть
принят, provider ID неизвестен, повтор после deadline запрещён. Browser получает
этот status и безопасное сообщение, но не получает provider payment/method ID.

Новые model types:

```python
@dataclass(frozen=True, slots=True)
class ProviderPaymentMethod:
    provider_payment_method_id: str
    saved: bool
    method_type: str | None = None


@dataclass(frozen=True, slots=True)
class ReceiptItem:
    description: str
    quantity: str
    amount: Money
    vat_code: int
    payment_mode: str
    payment_subject: str


@dataclass(frozen=True, slots=True)
class PaymentReceipt:
    customer_email: str
    items: tuple[ReceiptItem, ...]
    tax_system_code: int | None = None


@dataclass(frozen=True, slots=True)
class BillingReceiptSettings:
    enabled: bool = False
    vat_code: int | None = None
    payment_mode: str = "full_payment"
    payment_subject: str = "service"
    tax_system_code: int | None = None


@dataclass(frozen=True, slots=True)
class RecurringPaymentCommand:
    idempotency_key: str
    payment_method_id: str
    amount: Money
    description: str
    metadata: Mapping[str, str]
    receipt: PaymentReceipt | None
```

Новые server responses не содержат opaque ID:

```json
{
  "subscription": {
    "id": "local-subscription-uuid",
    "plan_code": "starter_monthly",
    "status": "active",
    "current_period_start": "2026-07-30T10:00:00Z",
    "current_period_end": "2026-08-29T10:00:00Z",
    "auto_renew": true,
    "payment_method_saved": true,
    "next_renewal_at": "2026-08-29T10:00:00Z"
  },
  "token_balance": 1000000
}
```

## Task 0: Preflight и baseline без изменений

**Files:**

- Read: `AGENTS.md`
- Read: `docs/BILLING_FOUNDATION.md`
- Read: `docs/superpowers/specs/2026-07-30-kaigo-verifiable-mvp-design.md`
- Verify: `migrations/versions/0014_generation_forensics.py`

- [ ] **Step 1: Зафиксировать существующий dirty worktree**

Run:

```powershell
git status --short --branch
```

Expected: текущие пользовательские изменения видны и не очищаются. Отметить их
для себя; не включать в billing commits.

- [ ] **Step 2: Проверить migration dependency**

Run:

```powershell
Test-Path -LiteralPath migrations/versions/0014_generation_forensics.py
python -c "import importlib; m=importlib.import_module('migrations.versions.0014_generation_forensics'); print(m.revision)"
```

Expected: `True`, затем `0014_generation_forensics`. Иное значение — stop, а не
повод переименовать `0015`.

- [ ] **Step 3: Запустить текущий billing baseline**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_migration.py tests/saas_cases/test_billing_provider.py tests/saas_cases/test_billing_service.py tests/saas_cases/test_billing_routes.py tests/saas_cases/test_config.py -q
npm --prefix frontend test -- --run src/studio/api.test.ts src/studio/UpgradeGate.test.tsx
```

Expected: PASS с уже документированными environment skips. Любой реальный FAIL
фиксируется отдельно до recurring-изменений.

## Task 1: Additive recurring migration и ORM models

**Files:**

- Create: `migrations/versions/0015_yookassa_recurring_foundation.py`
- Create: `tests/saas_cases/test_billing_recurring_migration.py`
- Modify: `app/saas/models.py:434-555`

- [ ] **Step 1: RED — revision, legacy defaults и constraints**

Добавить tests с точными именами:

```python
def test_recurring_migration_follows_reserved_forensics_revision() -> None:
    migration = importlib.import_module(
        "migrations.versions.0015_yookassa_recurring_foundation"
    )
    assert migration.revision == "0015_yookassa_recurring_foundation"
    assert migration.down_revision == "0014_generation_forensics"


@pytest.mark.asyncio
async def test_recurring_migration_defaults_legacy_subscriptions_off(
    migrated_database,
) -> None:
    subscription = await migrated_database.get(Subscription, LEGACY_SUBSCRIPTION_ID)
    assert subscription.auto_renew is False
    assert subscription.payment_method_id is None
    assert subscription.next_renewal_at is None


@pytest.mark.asyncio
async def test_recurring_migration_normalizes_only_paid_token_credit(
    migrated_database,
) -> None:
    rows = list((await migrated_database.scalars(
        select(UsageLedger).order_by(UsageLedger.idempotency_key)
    )))
    assert [(row.entry_type, row.bucket) for row in rows] == [
        ("model.usage", "tokens"),
        ("subscription.credit", "tokens"),
        ("trial.reserve", "trial_reserved"),
    ]
```

Добавить database constraint cases:

```python
@pytest.mark.parametrize("case", [
    "renewal_without_subscription",
    "renewal_without_period",
    "renewal_attempt_number_three",
    "retry_without_primary_attempt",
    "auto_renew_without_method",
    "initial_auto_renew_without_consent",
    "renewal_without_consent_lineage",
    "period_end_before_start",
])
@pytest.mark.asyncio
async def test_recurring_constraints_reject_incomplete_state(
    recurring_database,
    case: str,
) -> None:
    with pytest.raises(IntegrityError):
        await insert_invalid_recurring_case(recurring_database, case)
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_recurring_migration.py -q
```

Expected: FAIL because revision `0015` and recurring columns do not exist.

- [ ] **Step 3: GREEN — create the additive migration**

Implement these exact schema additions. For SQLite tests, add constraints
inside `create_table` or use Alembic `batch_alter_table`; do not rely on
unsupported standalone `ALTER TABLE ADD CONSTRAINT` operations.

```python
revision = "0015_yookassa_recurring_foundation"
down_revision = "0014_generation_forensics"

# billing_payment_methods
# id UUID PK
# user_id -> users.id ON DELETE CASCADE
# provider VARCHAR(32) NOT NULL
# merchant_account_fingerprint VARCHAR(64) NOT NULL
# provider_payment_method_id VARCHAR(255) NOT NULL
# source_payment_attempt_id -> payment_attempts.id ON DELETE SET NULL
# status VARCHAR(32) NOT NULL
# consent_version VARCHAR(64) NOT NULL
# consented_at TIMESTAMPTZ NOT NULL
# saved_at TIMESTAMPTZ NOT NULL
# disabled_at TIMESTAMPTZ NULL
# created_at/updated_at TIMESTAMPTZ NOT NULL

op.create_unique_constraint(
    "uq_billing_payment_method_provider_identity",
    "billing_payment_methods",
    ["provider", "merchant_account_fingerprint", "provider_payment_method_id"],
)
op.create_check_constraint(
    "ck_billing_payment_method_status",
    "billing_payment_methods",
    "status IN ('active', 'disabled', 'invalid')",
)

# subscriptions
# merchant_account_fingerprint VARCHAR(64) NULL
# payment_method_id UUID NULL -> billing_payment_methods.id ON DELETE SET NULL
# auto_renew BOOLEAN NOT NULL DEFAULT FALSE
# next_renewal_at TIMESTAMPTZ NULL
# auto_renew_enabled_at/auto_renew_disabled_at TIMESTAMPTZ NULL
op.create_check_constraint(
    "ck_subscription_auto_renew_ready",
    "subscriptions",
    "NOT auto_renew OR (payment_method_id IS NOT NULL "
    "AND merchant_account_fingerprint IS NOT NULL "
    "AND next_renewal_at IS NOT NULL)",
)

# payment_attempts
# purpose VARCHAR(16) NOT NULL DEFAULT 'initial'
# subscription_id UUID NULL -> subscriptions.id ON DELETE SET NULL
# payment_method_id UUID NULL -> billing_payment_methods.id ON DELETE SET NULL
# billing_period_start/billing_period_end TIMESTAMPTZ NULL
# renewal_attempt_number SMALLINT NULL; renewal only, allowed values 1 or 2
# retry_of_payment_attempt_id UUID NULL -> payment_attempts.id ON DELETE SET NULL
# next_dispatch_at TIMESTAMPTZ NULL; used only by scheduled renewal retry
# auto_renew_requested BOOLEAN NOT NULL DEFAULT FALSE
# consent_version VARCHAR(64) NULL
# consented_at TIMESTAMPTZ NULL
# save_payment_method_requested BOOLEAN NULL; NULL means legacy field omitted
# request_fingerprint VARCHAR(64) NULL; NULL is allowed only for legacy rows
# first_dispatched_at/provider_idempotency_expires_at TIMESTAMPTZ NULL
# last_reconciled_at/next_reconcile_at TIMESTAMPTZ NULL
# reconcile_lease_token VARCHAR(64) NULL
# reconcile_lease_expires_at TIMESTAMPTZ NULL
op.create_check_constraint(
    "ck_payment_attempt_purpose",
    "payment_attempts",
    "purpose IN ('initial', 'renewal')",
)
op.create_check_constraint(
    "ck_payment_attempt_renewal_period",
    "payment_attempts",
    "(purpose = 'initial' AND renewal_attempt_number IS NULL "
    "AND retry_of_payment_attempt_id IS NULL AND next_dispatch_at IS NULL) "
    "OR (purpose = 'renewal' AND subscription_id IS NOT NULL "
    "AND payment_method_id IS NOT NULL AND renewal_attempt_number IN (1, 2) "
    "AND billing_period_start IS NOT NULL "
    "AND billing_period_end > billing_period_start "
    "AND ((renewal_attempt_number = 1 AND retry_of_payment_attempt_id IS NULL "
    "AND next_dispatch_at IS NULL) "
    "OR (renewal_attempt_number = 2 AND retry_of_payment_attempt_id IS NOT NULL "
    "AND next_dispatch_at IS NOT NULL)))",
)
op.create_check_constraint(
    "ck_payment_attempt_auto_renew_consent",
    "payment_attempts",
    "NOT auto_renew_requested OR (consent_version IS NOT NULL "
    "AND consented_at IS NOT NULL AND ((purpose = 'initial' "
    "AND save_payment_method_requested IS TRUE) OR (purpose = 'renewal' "
    "AND save_payment_method_requested IS FALSE)))",
)
op.create_index(
    "uq_payment_attempt_renewal_sequence",
    "payment_attempts",
    ["subscription_id", "billing_period_start", "renewal_attempt_number"],
    unique=True,
    postgresql_where=sa.text("purpose = 'renewal'"),
    sqlite_where=sa.text("purpose = 'renewal'"),
)

# payment_webhook_events
# merchant_account_fingerprint VARCHAR(64) NOT NULL, backfilled from attempt or
# the existing legacy-unknown sentinel. Keep uq_payment_event for single-merchant MVP.
```

Backfill rules are exact:

```sql
UPDATE subscriptions
SET auto_renew = false,
    payment_method_id = NULL,
    next_renewal_at = NULL,
    auto_renew_enabled_at = NULL,
    auto_renew_disabled_at = NULL;

UPDATE payment_attempts
SET purpose = 'initial',
    auto_renew_requested = false,
    save_payment_method_requested = NULL,
    first_dispatched_at = CASE
        WHEN provider_payment_id IS NULL AND status IN ('creating', 'failed')
        THEN created_at ELSE first_dispatched_at END,
    provider_idempotency_expires_at = CASE
        WHEN provider_payment_id IS NULL AND status IN ('creating', 'failed')
        THEN created_at + interval '24 hours'
        ELSE provider_idempotency_expires_at END;

UPDATE usage_ledger
SET bucket = 'tokens'
WHERE entry_type = 'subscription.credit'
  AND bucket = 'generation_tokens';
```

Для SQLite выполнить equivalent update через SQLAlchemy/Python-safe SQL без
PostgreSQL-only interval syntax. Downgrade удаляет новые constraints/FKs/indexes
перед columns и возвращает только legacy `subscription.credit` rows из
`tokens` в `generation_tokens`; остальные `tokens` entries не менять.

- [ ] **Step 4: GREEN — mirror schema in ORM**

Добавить `BillingPaymentMethod` и перечисленные поля в `Subscription`,
`PaymentAttempt`, `PaymentWebhookEvent`. ORM constraints/index names обязаны
совпасть с миграцией:

```python
class BillingPaymentMethod(Base):
    __tablename__ = "billing_payment_methods"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "merchant_account_fingerprint",
            "provider_payment_method_id",
            name="uq_billing_payment_method_provider_identity",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'invalid')",
            name="ck_billing_payment_method_status",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    merchant_account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_payment_method_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 5: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_recurring_migration.py tests/saas_cases/test_schema.py -q
```

Expected: PASS. PostgreSQL-only test may SKIP only when the disposable database
is not configured; the SQLite upgrade/downgrade test must PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/saas/models.py migrations/versions/0015_yookassa_recurring_foundation.py tests/saas_cases/test_billing_recurring_migration.py
git commit -m "feat: add recurring billing schema"
```

## Task 2: Provider contract for saved methods and direct reconciliation

**Files:**

- Modify: `app/billing/contracts.py`
- Modify: `app/billing/yookassa.py`
- Modify: `tests/saas_cases/test_billing_provider.py`

- [ ] **Step 1: RED — save flag, verified method, GET and recurring POST**

Добавить tests:

```python
@pytest.mark.asyncio
async def test_redirect_payment_requests_method_save(mock_yookassa) -> None:
    result, request = await mock_yookassa.create_redirect(save_payment_method=True)
    body = json.loads(request.content)
    assert body["save_payment_method"] is True
    assert body["confirmation"]["type"] == "redirect"
    assert result.status is PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_succeeded_payment_exposes_only_verified_saved_method(mock_yookassa) -> None:
    payment = await mock_yookassa.get_succeeded(saved=True)
    assert payment.payment_method == ProviderPaymentMethod(
        provider_payment_method_id="opaque-method-id",
        saved=True,
        method_type="bank_card",
    )


@pytest.mark.asyncio
async def test_unsaved_method_is_not_accepted_for_recurring(mock_yookassa) -> None:
    payment = await mock_yookassa.get_succeeded(saved=False)
    assert payment.payment_method is None


@pytest.mark.asyncio
async def test_recurring_payment_has_no_confirmation(mock_yookassa) -> None:
    payment, request = await mock_yookassa.create_recurring()
    body = json.loads(request.content)
    assert body["payment_method_id"] == "opaque-method-id"
    assert "confirmation" not in body
    assert "save_payment_method" not in body
    assert payment.status in {PaymentStatus.PENDING, PaymentStatus.SUCCEEDED}


@pytest.mark.asyncio
async def test_get_payment_uses_exact_provider_id(mock_yookassa) -> None:
    payment, request = await mock_yookassa.get_payment("provider-payment-id")
    assert request.method == "GET"
    assert request.url.path == "/v3/payments/provider-payment-id"
    assert payment.provider_payment_id == "provider-payment-id"


@pytest.mark.asyncio
async def test_enabled_fiscalization_adds_one_balanced_service_receipt(
    mock_yookassa,
) -> None:
    _, request = await mock_yookassa.create_redirect(with_receipt=True)
    body = json.loads(request.content)
    item = body["receipt"]["items"][0]
    assert item["amount"] == body["amount"]
    assert item["quantity"] == "1.00"
    assert item["payment_mode"] == "full_payment"
    assert item["payment_subject"] == "service"
    assert "receipt" not in mock_yookassa.captured_logs


@pytest.mark.asyncio
async def test_cancellation_reason_is_allowlisted_not_raw_provider_payload(
    mock_yookassa,
) -> None:
    payment = await mock_yookassa.get_cancelled("insufficient_funds")
    assert payment.cancellation_reason == "insufficient_funds"
```

`mock_yookassa` должен использовать только `httpx.MockTransport`; он не делает
network calls и не содержит реальные merchant values.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_provider.py -k "method_save or saved_method or recurring_payment or get_payment or receipt or cancellation_reason" -q
```

Expected: FAIL because the protocol lacks saved-method, receipt,
cancellation-reason and direct-GET support.

- [ ] **Step 3: GREEN — extend provider-neutral contracts**

Использовать эти signatures:

```python
@dataclass(frozen=True, slots=True)
class CheckoutCommand:
    idempotency_key: str
    amount: Money
    description: str
    metadata: Mapping[str, str]
    save_payment_method: bool | None
    receipt: PaymentReceipt | None


@dataclass(frozen=True, slots=True)
class ProviderPaymentMethod:
    provider_payment_method_id: str
    saved: bool
    method_type: str | None = None


@dataclass(frozen=True, slots=True)
class RecurringPaymentCommand:
    idempotency_key: str
    payment_method_id: str
    amount: Money
    description: str
    metadata: Mapping[str, str]
    receipt: PaymentReceipt | None


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    provider_payment_id: str
    status: PaymentStatus
    amount: Money
    paid: bool
    metadata: Mapping[str, str]
    test_mode: bool
    payment_method: ProviderPaymentMethod | None = None
    cancellation_reason: str | None = None


class PaymentProvider(Protocol):
    async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout: ...
    async def create_recurring_payment(
        self, command: RecurringPaymentCommand
    ) -> ProviderPayment: ...
    async def get_payment(self, provider_payment_id: str) -> ProviderPayment: ...
```

`ProviderCheckout` также получает
`payment_method: ProviderPaymentMethod | None = None`.
`ReceiptItem`, `PaymentReceipt` и `BillingReceiptSettings` используют
definitions из раздела целевого контракта; receipt передаётся как typed
immutable value, а не произвольный frontend JSON.

- [ ] **Step 4: GREEN — implement YooKassa payloads and parsing**

Правила тела запроса:

```python
body = {
    "amount": _provider_amount(command.amount),
    "capture": True,
    "confirmation": {"type": "redirect", "return_url": self._return_url},
    "description": command.description,
    "metadata": dict(command.metadata),
}
if command.save_payment_method is not None:
    body["save_payment_method"] = command.save_payment_method
if command.receipt is not None:
    body["receipt"] = _receipt_payload(command.receipt)
```

Recurring body:

```python
body = {
    "amount": _provider_amount(command.amount),
    "capture": True,
    "payment_method_id": command.payment_method_id,
    "description": command.description,
    "metadata": dict(command.metadata),
}
if command.receipt is not None:
    body["receipt"] = _receipt_payload(command.receipt)
```

Parser принимает method только при строгом `saved is True` и непустом safe ID:

```python
def _payment_method(payload: object) -> ProviderPaymentMethod | None:
    if not isinstance(payload, dict) or payload.get("saved") is not True:
        return None
    method_id = payload.get("id")
    method_type = payload.get("type")
    if not isinstance(method_id, str) or not _PAYMENT_ID_PATTERN.fullmatch(method_id):
        raise YooKassaVerificationError("provider payment method is invalid")
    if method_type is not None and not isinstance(method_type, str):
        raise YooKassaVerificationError("provider payment method type is invalid")
    return ProviderPaymentMethod(method_id, True, method_type)
```

`_receipt_payload()` возвращает ровно `customer.email`, optional
`tax_system_code` и `items`; сумма item при quantity `1.00` обязана равняться
payment amount. `vat_code`, `payment_mode` и `payment_subject` уже прошли
AppConfig validation. Parser cancellation извлекает только строковый
`cancellation_details.reason`, допускает известный provider code pattern и не
сохраняет полный `cancellation_details`.

`verify_notification()` должен делегировать GET в новый `get_payment()`, затем
проверять event-specific status. Никакой provider response или exception не
логирует request body, auth headers или payment method ID.

- [ ] **Step 5: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_provider.py -q
```

Expected: PASS, включая прежние amount/metadata/test-mode проверки.

- [ ] **Step 6: Commit**

```powershell
git add app/billing/contracts.py app/billing/yookassa.py tests/saas_cases/test_billing_provider.py
git commit -m "feat: support yookassa saved payment methods"
```

## Task 3: Explicit consent and the 24-hour ambiguous-dispatch stop

**Files:**

- Create: `app/billing/receipts.py`
- Create: `tests/saas_cases/test_billing_receipts.py`
- Modify: `app/billing/payments.py:46-450`
- Modify: `tests/saas_cases/test_billing_service.py`

- [ ] **Step 1: RED — consent snapshot and exact request replay**

Добавить a mutable fake clock и tests:

```python
@pytest.mark.asyncio
async def test_initial_checkout_snapshots_explicit_auto_renew_consent(
    billing_db,
    fake_clock,
) -> None:
    provider = FakeProvider()
    service = BillingService(billing_db.factory, provider, clock=fake_clock)
    result = await service.create_checkout(
        10,
        "starter_monthly",
        "consented-checkout",
        auto_renew=True,
    )
    attempt = await billing_db.get(PaymentAttempt, result.payment_id)
    assert attempt.auto_renew_requested is True
    assert attempt.consent_version == AUTO_RENEW_CONSENT_VERSION
    assert attempt.consented_at == fake_clock.now
    assert attempt.save_payment_method_requested is True
    assert provider.checkout_calls[0].save_payment_method is True


@pytest.mark.asyncio
async def test_ambiguous_post_replays_exactly_inside_24_hours(
    billing_db,
    fake_clock,
) -> None:
    provider = LostResponseThenPendingProvider()
    service = BillingService(billing_db.factory, provider, clock=fake_clock)
    with pytest.raises(BillingError):
        await service.create_checkout(
            10, "starter_monthly", "ambiguous-inside-window", auto_renew=True
        )
    fake_clock.advance(timedelta(hours=23, minutes=59))
    await service.create_checkout(
        10, "starter_monthly", "ambiguous-inside-window", auto_renew=True
    )
    assert provider.checkout_calls[0] == provider.checkout_calls[1]


@pytest.mark.asyncio
async def test_ambiguous_post_is_never_replayed_at_or_after_24_hours(
    billing_db,
    fake_clock,
) -> None:
    provider = AlwaysLosesResponseProvider()
    service = BillingService(billing_db.factory, provider, clock=fake_clock)
    with pytest.raises(BillingError):
        await service.create_checkout(
            10, "starter_monthly", "ambiguous-expired", auto_renew=True
        )
    fake_clock.advance(timedelta(hours=24))
    with pytest.raises(PaymentDispatchExpired):
        await service.create_checkout(
            10, "starter_monthly", "ambiguous-expired", auto_renew=True
        )
    assert len(provider.checkout_calls) == 1


@pytest.mark.asyncio
async def test_receipts_enabled_rejects_unverified_email_before_payment_attempt(
    billing_db,
    enabled_receipt_settings,
) -> None:
    await mark_all_identities_unverified(billing_db, user_id=10)
    provider = FakeProvider()
    service = BillingService(
        billing_db.factory,
        provider,
        receipt_settings=enabled_receipt_settings,
    )
    with pytest.raises(ReceiptCustomerUnavailable):
        await service.create_checkout(
            10, "starter_monthly", "receipt-no-verified-email", auto_renew=True
        )
    assert await billing_db.scalar(select(func.count()).select_from(PaymentAttempt)) == 0
    assert provider.checkout_calls == []
```

Добавить отдельный regression для migrated legacy attempt: `NULL`
`save_payment_method_requested` должен повторить старое тело без этого поля, а
не изменить его на `false` или `true`.

Добавить `test_receipt_change_blocks_ambiguous_replay`: при включённой
фискализации изменение verified account email меняет canonical body, поэтому
replay останавливается до provider POST вместо отправки другого чека с тем же
ключом.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_receipts.py tests/saas_cases/test_billing_service.py -k "receipt or consent or ambiguous_post or legacy_request_shape" -q
```

Expected: FAIL because consent fields, clock and 24-hour deadline are absent.

- [ ] **Step 3: GREEN — store immutable command shape before dispatch**

Ввести:

```python
AUTO_RENEW_CONSENT_VERSION = "kaigo-recurring-v1"
AUTO_RENEW_CONSENT_TEMPLATE_RU = (
    "Сохраняем способ оплаты в ЮKassa. После первого платежа {amount_rub} ₽ "
    "тариф будет автоматически продлеваться каждые {period_days} дней, и эта "
    "сумма будет списываться без повторного подтверждения. Автопродление "
    "можно отключить в Kaigo в любой момент; оплаченный период сохранится."
)
PROVIDER_IDEMPOTENCY_WINDOW = timedelta(hours=24)


class PaymentDispatchExpired(BillingError):
    pass


def _request_fingerprint(command: CheckoutCommand | RecurringPaymentCommand) -> str:
    document = _canonical_provider_request(command)
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
```

Canonical document включает amount, capture semantics, confirmation presence,
description, metadata, save flag/payment method field и полный receipt.
`app/billing/receipts.py` выбирает `User.email` только при наличии для того же
user совпадающего normalized `UserIdentity.email` с `email_verified=true`, затем
строит receipt из plan snapshot и validated settings. При включённых чеках
отсутствие такого адреса даёт `ReceiptCustomerUnavailable` до создания attempt и
до provider POST. Email не копируется в `PaymentAttempt.payload`; при replay
изменившийся verified email даёт fingerprint mismatch и безопасный stop.

Единый builder имеет этот surface:

```python
async def verified_receipt_email(
    database: AsyncSession,
    *,
    user_id: int,
) -> str | None:
    return await database.scalar(
        select(User.email)
        .join(
            UserIdentity,
            and_(
                UserIdentity.user_id == User.id,
                UserIdentity.email_verified.is_(True),
                func.lower(UserIdentity.email) == func.lower(User.email),
            ),
        )
        .where(User.id == user_id)
        .limit(1)
    )


def build_payment_receipt(
    *,
    customer_email: str,
    plan: BillingPlan,
    settings: BillingReceiptSettings,
) -> PaymentReceipt:
    return PaymentReceipt(
        customer_email=customer_email,
        tax_system_code=settings.tax_system_code,
        items=(ReceiptItem(
            description=validated_receipt_description(plan.title),
            quantity="1.00",
            amount=plan.amount,
            vat_code=required_vat_code(settings),
            payment_mode=settings.payment_mode,
            payment_subject=settings.payment_subject,
        ),),
    )
```

`test_billing_receipts.py` фиксирует disabled mode без email lookup, normalized
verified match, отказ при несовпадающем/неподтверждённом identity, один item и
равенство item/payment amount. Title проверяется по текущему provider limit до
attempt creation; не обрезать его молча.

Изменить constructor так, чтобы web checkout и worker получали один и тот же
immutable receipt policy и управляемые часы. Frozen default безопасен для
существующих unit tests, но production factory из Task 9 всегда передаёт
настроенный instance явно:

```python
class BillingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
        *,
        receipt_settings: BillingReceiptSettings = BillingReceiptSettings(),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        self._receipt_settings = receipt_settings
        self._clock = clock
        self._merchant_account_fingerprint = _validate_merchant_fingerprint(
            provider.merchant_account_fingerprint
        )
```

Изменить public method на обязательный keyword:

```python
async def create_checkout(
    self,
    user_id: int,
    plan_code: str,
    idempotency_key: str,
    *,
    auto_renew: bool,
) -> CheckoutResult:
```

Для нового public checkout `auto_renew` обязан быть literal `True`; сервис
сохраняет current server consent version/time и `save_payment_method_requested
= True`. Перед первым POST в одной DB transaction:

```python
now = self._clock()
attempt.first_dispatched_at = now
attempt.provider_idempotency_expires_at = now + PROVIDER_IDEMPOTENCY_WINDOW
command = self._checkout_command(attempt, plan)
attempt.request_fingerprint = _request_fingerprint(command)
```

Перед каждым replay:

```python
if attempt.provider_payment_id is None:
    if now >= attempt.provider_idempotency_expires_at:
        attempt.status = "dispatch_unknown"
        raise PaymentDispatchExpired("provider dispatch result is unknown")
    command = self._checkout_command(attempt, self._stored_plan(attempt))
    if _request_fingerprint(command) != attempt.request_fingerprint:
        raise BillingError("stored provider request was modified")
```

Transport/HTTP failure после начала POST переводит attempt в
`dispatch_unknown`, а не в definitive `failed`. Известный
`provider_payment_id` никогда не POST-ится повторно: он передаётся reconciler.

В `pending_payment` merchant-drain query включить `scheduled` и
`dispatch_unknown`, чтобы новый checkout не обходил ожидающий retry или возможное
списание.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_service.py -q
```

Expected: PASS; обновить старые direct service calls, передав
`auto_renew=True`, не ослабляя existing idempotency/merchant tests.

- [ ] **Step 5: Commit**

```powershell
git add app/billing/receipts.py app/billing/payments.py tests/saas_cases/test_billing_receipts.py tests/saas_cases/test_billing_service.py
git commit -m "feat: guard recurring checkout consent and retries"
```

## Task 4: One verified fulfillment path, saved method persistence and tokens

**Files:**

- Modify: `app/billing/payments.py:452-649`
- Modify: `app/billing/service.py:594-676`
- Modify: `app/billing/__init__.py`
- Modify: `tests/saas_cases/test_billing_service.py`
- Modify: `tests/saas_cases/test_trial_service.py`

- [ ] **Step 1: RED — verified method only and unified token ledger**

Добавить tests:

```python
@pytest.mark.asyncio
async def test_verified_saved_method_enables_auto_renew(billing_db) -> None:
    provider = FakeProvider(saved_method=True)
    result = await paid_checkout(billing_db, provider, auto_renew=True)
    method = await billing_db.scalar(select(BillingPaymentMethod))
    subscription = await billing_db.get(Subscription, result.subscription_id)
    assert method.status == "active"
    assert method.source_payment_attempt_id == result.payment_id
    assert subscription.payment_method_id == method.id
    assert subscription.auto_renew is True
    assert subscription.next_renewal_at == subscription.current_period_end


@pytest.mark.asyncio
async def test_unsaved_method_fulfills_period_but_keeps_auto_renew_off(
    billing_db,
) -> None:
    provider = FakeProvider(saved_method=False)
    result = await paid_checkout(billing_db, provider, auto_renew=True)
    subscription = await billing_db.get(Subscription, result.subscription_id)
    assert subscription.status == "active"
    assert subscription.auto_renew is False
    assert subscription.payment_method_id is None
    assert await billing_db.scalar(select(func.count()).select_from(
        BillingPaymentMethod
    )) == 0


@pytest.mark.asyncio
async def test_new_verified_consent_reactivates_same_owner_disabled_method(
    billing_db,
) -> None:
    method = await disabled_method_for_user(billing_db, user_id=10)
    provider = FakeProvider(saved_method_id=method.provider_payment_method_id)
    result = await paid_checkout(billing_db, provider, auto_renew=True)
    reloaded = await billing_db.get(BillingPaymentMethod, method.id)
    subscription = await billing_db.get(Subscription, result.subscription_id)
    assert reloaded.status == "active"
    assert reloaded.disabled_at is None
    assert reloaded.source_payment_attempt_id == result.payment_id
    assert subscription.payment_method_id == reloaded.id


@pytest.mark.asyncio
async def test_payment_method_id_never_enters_webhook_payload(billing_db) -> None:
    provider = FakeProvider(saved_method=True)
    await paid_checkout(billing_db, provider, auto_renew=True)
    event = await billing_db.scalar(select(PaymentWebhookEvent))
    assert "payment_method" not in json.dumps(event.payload)


@pytest.mark.asyncio
async def test_paid_credit_offsets_model_usage_in_tokens_bucket(billing_db) -> None:
    await fulfill_credit(billing_db, amount=1_000_000)
    await record_model_debit(billing_db, amount=125_000)
    assert await UsageBalanceService(billing_db.factory).token_balance(10) == 875_000
    buckets = set(await billing_db.scalars(select(UsageLedger.bucket)))
    assert "generation_tokens" not in buckets
    assert "tokens" in buckets
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_service.py tests/saas_cases/test_trial_service.py -k "saved_method or reactivates or token_balance or paid_credit" -q
```

Expected: FAIL because fulfillment writes `generation_tokens` and cannot persist
saved methods.

- [ ] **Step 3: GREEN — extract shared verified application**

Сделать webhook и будущий reconciler thin callers одного метода:

```python
async def apply_verified_payment(
    self,
    attempt_id: UUID,
    payment: ProviderPayment,
    *,
    source: Literal["webhook", "reconciliation", "dispatch"],
) -> FulfillmentResult:
```

Перед transaction validate provider id, amount, currency, full metadata и test
mode. В transaction сохранить фиксированный lock order:

```text
User -> PaymentAttempt -> active Subscription -> existing BillingPaymentMethod
-> PaymentWebhookEvent -> UsageLedger
```

Canonical event key остаётся одинаковым для всех entry paths:

```python
event_key = f"payment.{payment.status.value}:{payment.provider_payment_id}"
```

`PaymentWebhookEvent.payload` содержит только status, paid, amount, currency,
test mode и source. Provider method ID туда не входит.

При successful initial payment:

```python
saved = payment.payment_method
if (
    attempt.auto_renew_requested
    and saved is not None
    and saved.saved is True
    and attempt.consent_version
    and attempt.consented_at
):
    method = await self._activate_verified_method(database, attempt, saved)
    subscription.payment_method_id = method.id
    subscription.merchant_account_fingerprint = attempt.merchant_account_fingerprint
    subscription.auto_renew = True
    subscription.auto_renew_enabled_at = now
    subscription.auto_renew_disabled_at = None
    subscription.next_renewal_at = subscription.current_period_end
else:
    subscription.auto_renew = False
    subscription.payment_method_id = None
    subscription.next_renewal_at = None
```

При collision opaque method с другим user не перепривязывать его: paid period и
credit применить, но оставить `auto_renew=false` и записать только безопасный
операционный error code без provider ID.

Collision handling must run inside a SAVEPOINT/nested transaction or an
equivalent atomic claim. A unique-key `IntegrityError` from a concurrent method
insert must not roll back the already verified paid-period fulfillment or
token credit. Add a true concurrent two-user collision test.

Для того же user/provider/merchant identity `_activate_verified_method` делает
locked upsert: новая verified consented initial payment может реактивировать
`disabled` или `invalid` row, обновить consent/source/saved timestamps и очистить
`disabled_at`. Ни reconciliation старого платежа, ни renewal не имеют права
реактивировать отключённый method.

Credit становится:

```python
credit_key = (
    f"renewal:{attempt.subscription_id}:{attempt.billing_period_start.isoformat()}:tokens"
    if attempt.purpose == "renewal"
    else f"payment:{attempt.id}:tokens"
)
UsageLedger(
    user_id=user_id,
    payment_attempt_id=attempt.id,
    bucket="tokens",
    entry_type="subscription.credit",
    amount=plan.generation_tokens,
    idempotency_key=credit_key,
    payload={
        "plan_code": plan.code,
        "plan_fingerprint": plan.fingerprint(),
        "purpose": attempt.purpose,
    },
)
```

Для renewal `billing_period_start/end` являются immutable target period.
Fulfillment сначала проверяет, что subscription ещё не достиг
`billing_period_end`, и выставляет границы ровно из attempt; он не прибавляет
период повторно от текущего значения. Unique cycle-level ledger key остаётся
последним DB guard против двойного credit.

Добавить read-only balance service:

```python
class UsageBalanceService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def token_balance(self, user_id: int) -> int:
        async with self._sessions() as database:
            value = await database.scalar(
                select(func.coalesce(func.sum(UsageLedger.amount), 0)).where(
                    UsageLedger.user_id == user_id,
                    UsageLedger.bucket == "tokens",
                )
            )
        return int(value or 0)
```

Этот task унифицирует accounting bucket; он не вводит новый приблизительный
pre-charge или скрытый token limit.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_service.py tests/saas_cases/test_trial_service.py -q
```

Expected: PASS. Existing exactly-once webhook and trial settlement tests остаются
зелёными.

- [ ] **Step 5: Commit**

```powershell
git add app/billing/payments.py app/billing/service.py app/billing/__init__.py tests/saas_cases/test_billing_service.py tests/saas_cases/test_trial_service.py
git commit -m "feat: persist verified recurring payment methods"
```

## Task 5: Lost-webhook reconciliation with a 60-second DB lease

**Files:**

- Create: `app/billing/reconciliation.py`
- Create: `tests/saas_cases/test_billing_reconciliation.py`
- Modify: `app/billing/payments.py`

- [ ] **Step 1: RED — recovery, cadence and race**

Добавить tests:

```python
@pytest.mark.asyncio
async def test_lost_webhook_is_reconciled_and_fulfilled_once(
    recurring_db,
    fake_clock,
) -> None:
    attempt = await pending_attempt(recurring_db, next_reconcile_at=fake_clock.now)
    provider = FakeProvider(payment_status=PaymentStatus.SUCCEEDED)
    service = BillingService(recurring_db.factory, provider, clock=fake_clock)
    results = await PaymentReconciler(
        recurring_db.factory,
        provider,
        payment_service=service,
        clock=fake_clock,
    ).run_once()
    assert results == {attempt.id: "succeeded"}
    assert await count(UsageLedger) == 1
    assert await count(PaymentWebhookEvent) == 1


@pytest.mark.asyncio
async def test_same_attempt_is_not_fetched_more_than_once_per_60_seconds(
    recurring_db,
    fake_clock,
) -> None:
    await pending_attempt(recurring_db, next_reconcile_at=fake_clock.now)
    provider = FakeProvider(payment_status=PaymentStatus.PENDING)
    service = BillingService(recurring_db.factory, provider, clock=fake_clock)
    first = PaymentReconciler(
        recurring_db.factory,
        provider,
        payment_service=service,
        clock=fake_clock,
    )
    second = PaymentReconciler(
        recurring_db.factory,
        provider,
        payment_service=service,
        clock=fake_clock,
    )
    await asyncio.gather(first.run_once(), second.run_once())
    fake_clock.advance(timedelta(seconds=59))
    await first.run_once()
    assert len(provider.get_calls) == 1
    fake_clock.advance(timedelta(seconds=1))
    await second.run_once()
    assert len(provider.get_calls) == 2


@pytest.mark.asyncio
async def test_webhook_and_reconciler_race_share_one_fulfillment(
    recurring_db,
    fake_clock,
) -> None:
    attempt = await pending_attempt(recurring_db, next_reconcile_at=fake_clock.now)
    provider = GatedSucceededProvider()
    service = BillingService(recurring_db.factory, provider, clock=fake_clock)
    reconciler = PaymentReconciler(
        recurring_db.factory,
        provider,
        payment_service=service,
        clock=fake_clock,
    )
    results = await asyncio.gather(
        reconciler.run_once(),
        service.handle_notification(provider.notification_for(attempt)),
    )
    assert await count(UsageLedger) == 1
    assert await count(PaymentWebhookEvent) == 1
    assert sum(result_processed(results)) == 1
```

Также проверить: unknown/mismatched merchant не делает provider GET;
`dispatch_unknown` без provider ID не выбирается; cancellation проходит через
тот же canonical event path.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_reconciliation.py -q
```

Expected: FAIL because `PaymentReconciler` does not exist.

- [ ] **Step 3: GREEN — claim due attempts atomically**

Use constants:

```python
RECONCILE_INTERVAL = timedelta(seconds=60)
RECONCILE_LEASE = timedelta(seconds=30)
```

Алгоритм `run_once(limit=100)`:

1. Select IDs where provider payment ID is known, status is `pending` or
   `creating`, merchant fingerprint matches, `next_reconcile_at <= now`, and
   lease is absent/expired.
2. Для каждого candidate выполнить conditional `UPDATE ... WHERE` с новым
   random lease token и `RETURNING id`; это fencing point для PostgreSQL и
   SQLite tests.
3. В claim transaction сразу поставить `last_reconciled_at=now` и
   `next_reconcile_at=now+60s`; поэтому другой worker не делает второй GET даже
   после transport failure.
4. Вне transaction вызвать `provider.get_payment(provider_payment_id)`.
5. Для terminal state вызвать `BillingService.apply_verified_payment(...)`.
   Для pending оставить следующий due time. Всегда снять только собственный
   lease token.

Public surface:

```python
class PaymentReconciler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
        *,
        payment_service: BillingService,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        self._payments = payment_service
        self._clock = clock

    async def run_once(self, *, limit: int = 100) -> dict[UUID, str]:
        return await self._claim_fetch_and_apply(limit=limit)
```

`payment_service` обязан использовать тот же provider instance и session factory;
Task 9 собирает их один раз. Reconciler не создаёт второй `BillingService` и не
дублирует fulfillment logic.

Provider failures записываются безопасным status code через logger без response
body и IDs способа оплаты; attempt остаётся recoverable на следующий 60-second
slot.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_reconciliation.py tests/saas_cases/test_billing_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/billing/reconciliation.py app/billing/payments.py tests/saas_cases/test_billing_reconciliation.py
git commit -m "feat: reconcile pending yookassa payments"
```

## Task 6: One logical renewal cycle with one temporary +1-day retry

**Phase gate — recurring POST remains disabled until all cases below are
GREEN on PostgreSQL:**

1. Persist and commit the immutable attempt, exact request fingerprint,
   provider key, `first_dispatched_at` and 24-hour deadline before any network
   call. Then acquire a conditional lease/CAS
   `scheduled|creating -> dispatching`, commit it, execute POST outside the
   transaction, and persist the result in a separate transaction. A crash after
   provider acceptance must recover the same attempt and same provider key.
2. Before POST, fail closed unless
   `method.user_id == subscription.user_id`,
   `method.provider == subscription.provider == runtime.provider.name`,
   all three merchant fingerprints match, and
   `subscription.payment_method_id == method.id`.
3. Serialize initial checkout and renewal creation under the same User lock and
   reject checkout when any nonterminal renewal cycle exists.
4. Serialize disable and dispatch CAS under the same locks. Disable cancels
   every not-yet-dispatched primary/retry attempt; if dispatch already began,
   return `renewal_in_progress` without claiming it was cancelled.

Required RED tests: crash between provider success and result commit,
cross-owner/cross-provider/cross-merchant/cross-subscription method binding,
concurrent initial checkout versus renewal scheduler, disable versus dispatch,
and late success after disable.

**Files:**

- Create: `app/billing/renewals.py`
- Create: `tests/saas_cases/test_billing_renewals.py`
- Modify: `app/billing/payments.py`

- [ ] **Step 1: RED — due cycle, concurrency and snapshot**

Добавить tests:

```python
def renewal_scheduler(
    recurring_subscription,
    provider: PaymentProvider,
    fake_clock,
    *,
    receipt_settings: BillingReceiptSettings = BillingReceiptSettings(),
) -> RenewalScheduler:
    service = BillingService(
        recurring_subscription.factory,
        provider,
        receipt_settings=receipt_settings,
        clock=fake_clock,
    )
    return RenewalScheduler(
        recurring_subscription.factory,
        provider,
        payment_service=service,
        receipt_settings=receipt_settings,
        clock=fake_clock,
    )


@pytest.mark.asyncio
async def test_due_renewal_is_created_once_across_workers(
    recurring_subscription,
    fake_clock,
) -> None:
    provider = GatedRecurringProvider(status=PaymentStatus.PENDING)
    first = renewal_scheduler(recurring_subscription, provider, fake_clock)
    second = renewal_scheduler(recurring_subscription, provider, fake_clock)
    await asyncio.gather(first.run_once(), second.run_once())
    attempts = await renewal_attempts(recurring_subscription.factory)
    assert len(attempts) == 1
    assert len(provider.recurring_calls) == 1


@pytest.mark.asyncio
async def test_renewal_uses_subscription_snapshot_not_catalog(
    recurring_subscription,
    fake_clock,
    monkeypatch,
) -> None:
    original = recurring_subscription.plan_snapshot.copy()
    monkeypatch.setitem(PLAN_CATALOG, "starter_monthly", changed_catalog_plan())
    provider = FakeProvider(recurring_status=PaymentStatus.SUCCEEDED)
    await renewal_scheduler(recurring_subscription, provider, fake_clock).run_once()
    call = provider.recurring_calls[0]
    assert call.amount.amount_minor == original["amount_minor"]


@pytest.mark.asyncio
async def test_renewal_success_extends_exact_stored_period_and_credits_once(
    recurring_subscription,
    fake_clock,
) -> None:
    old_end = recurring_subscription.current_period_end
    provider = FakeProvider(recurring_status=PaymentStatus.SUCCEEDED)
    await renewal_scheduler(recurring_subscription, provider, fake_clock).run_once()
    row = await reload_subscription(recurring_subscription)
    assert row.current_period_start == old_end
    assert row.current_period_end == old_end + timedelta(days=30)
    assert row.next_renewal_at == row.current_period_end
    assert await count_subscription_credits(row.id) == 1


@pytest.mark.asyncio
async def test_disabled_auto_renew_never_calls_provider_or_shortens_period(
    recurring_subscription,
    fake_clock,
) -> None:
    await disable_auto_renew(recurring_subscription.id, at=fake_clock.now)
    paid_through = recurring_subscription.current_period_end
    provider = FakeProvider()
    await renewal_scheduler(recurring_subscription, provider, fake_clock).run_once()
    assert provider.recurring_calls == []
    assert (await reload_subscription(recurring_subscription)).current_period_end == paid_through


@pytest.mark.asyncio
async def test_required_receipt_without_verified_email_never_calls_provider(
    recurring_subscription,
    fake_clock,
    enabled_receipt_settings,
) -> None:
    await mark_all_identities_unverified(
        recurring_subscription.factory,
        user_id=recurring_subscription.user_id,
    )
    paid_through = recurring_subscription.current_period_end
    provider = FakeProvider()
    await renewal_scheduler(
        recurring_subscription,
        provider,
        fake_clock,
        receipt_settings=enabled_receipt_settings,
    ).run_once()
    row = await reload_subscription(recurring_subscription)
    assert provider.recurring_calls == []
    assert row.auto_renew is False
    assert row.current_period_end == paid_through


@pytest.mark.asyncio
async def test_temporary_cancellation_schedules_one_new_attempt_for_next_day(
    recurring_subscription,
    fake_clock,
) -> None:
    provider = FakeProvider(
        recurring_status=PaymentStatus.CANCELLED,
        cancellation_reason="insufficient_funds",
    )
    scheduler = renewal_scheduler(recurring_subscription, provider, fake_clock)
    await scheduler.run_once()
    attempts = await renewal_attempts(recurring_subscription.factory)
    assert [(row.renewal_attempt_number, row.status) for row in attempts] == [
        (1, "cancelled"),
        (2, "scheduled"),
    ]
    assert attempts[1].retry_of_payment_attempt_id == attempts[0].id
    assert attempts[1].next_dispatch_at == fake_clock.now + timedelta(days=1)
    assert attempts[1].idempotency_key != attempts[0].idempotency_key
    await scheduler.run_once()
    assert len(provider.recurring_calls) == 1
    fake_clock.advance(timedelta(days=1))
    provider.recurring_status = PaymentStatus.SUCCEEDED
    await scheduler.run_once()
    assert len(provider.recurring_calls) == 2


@pytest.mark.asyncio
async def test_permanent_or_second_cancellation_never_schedules_third_attempt(
    recurring_subscription,
    fake_clock,
) -> None:
    provider = FakeProvider(
        recurring_status=PaymentStatus.CANCELLED,
        cancellation_reason="permission_revoked",
    )
    await renewal_scheduler(recurring_subscription, provider, fake_clock).run_once()
    attempts = await renewal_attempts(recurring_subscription.factory)
    assert len(attempts) == 1
    assert (await reload_subscription(recurring_subscription)).auto_renew is False


@pytest.mark.asyncio
async def test_ambiguous_renewal_never_creates_plus_one_day_retry(
    recurring_subscription,
    fake_clock,
) -> None:
    provider = AlwaysLosesResponseProvider()
    scheduler = renewal_scheduler(recurring_subscription, provider, fake_clock)
    await scheduler.run_once()
    fake_clock.advance(timedelta(days=1))
    await scheduler.run_once()
    attempts = await renewal_attempts(recurring_subscription.factory)
    assert len(attempts) == 1
    assert attempts[0].status == "dispatch_unknown"
    assert len(provider.recurring_calls) == 1
```

Добавить cases: inactive/disabled method, cross-merchant method, pending response,
immediate success, temporary cancellation, second retry cancellation и ambiguous
POST before/after 24h.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_renewals.py -q
```

Expected: FAIL because renewal scheduler does not exist.

- [ ] **Step 3: GREEN — claim the cycle and persist before POST**

Public surface:

```python
class RenewalScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
        *,
        payment_service: BillingService,
        receipt_settings: BillingReceiptSettings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        self._payments = payment_service
        self._receipt_settings = receipt_settings
        self._clock = clock

    async def run_once(self, *, limit: int = 100) -> dict[UUID, str]:
        return await self._claim_dispatch_and_apply(limit=limit)
```

Как и reconciler, scheduler получает уже собранный `BillingService`; provider,
service, session factory и `BillingReceiptSettings` должны происходить из одного
runtime bundle. Это исключает расхождение merchant fingerprint, receipt body и
fulfillment между web, reconciliation и renewal.

В transaction lock `User -> Subscription -> BillingPaymentMethod`, затем:

```python
period_start = subscription.current_period_end
plan = BillingPlan.from_snapshot(subscription.plan_snapshot)
period_end = period_start + timedelta(days=plan.period_days)
attempt = PaymentAttempt(
    user_id=subscription.user_id,
    provider=subscription.provider,
    merchant_account_fingerprint=subscription.merchant_account_fingerprint,
    idempotency_key=renewal_idempotency_key(subscription.id, period_start, 1),
    purpose="renewal",
    subscription_id=subscription.id,
    payment_method_id=method.id,
    billing_period_start=period_start,
    billing_period_end=period_end,
    renewal_attempt_number=1,
    retry_of_payment_attempt_id=None,
    next_dispatch_at=None,
    auto_renew_requested=True,
    save_payment_method_requested=False,
    consent_version=method.consent_version,
    consented_at=method.consented_at,
    plan_code=subscription.plan_code,
    plan_snapshot=subscription.plan_snapshot,
    plan_fingerprint=subscription.plan_fingerprint,
    amount_minor=plan.amount.amount_minor,
    currency=plan.amount.currency,
    status="creating",
    payload={},
)
```

Flush до provider call; partial unique index на period + attempt number является
cross-worker final guard. Primary success означает ровно один attempt. Новый
attempt number 2 разрешён только из verified `cancelled` primary с allowlisted
temporary reason; он не является replay первого POST и получает новый local UUID
и provider idempotency key.
Metadata включает local attempt ID, user ID, plan code/fingerprint, purpose,
subscription ID и UTC period boundaries. Не включать payment method ID.

До POST проверить:

```python
if method.status != "active":
    raise BillingError("saved payment method is inactive")
if method.merchant_account_fingerprint != self._merchant_account_fingerprint:
    raise MerchantAccountMismatch("saved method merchant account mismatch")
```

Recurring command также получает immutable receipt через тот же
`verified_receipt_email()` + `build_payment_receipt()` из Task 3, stored plan
snapshot и server receipt settings. Если включённый receipt нельзя построить,
provider POST не выполняется, auto-renew выключается с безопасным local reason,
а оплаченный период не меняется. Immediate `succeeded`/`cancelled` передать в
`apply_verified_payment(source="dispatch")`; `pending` сохранить с
`next_reconcile_at=now`. Transport ambiguity использует тот же fingerprint и
24-hour rule из Task 3.

Temporary cancellation policy is fail-closed and explicit:

```python
RETRYABLE_RENEWAL_CANCELLATIONS = frozenset(
    {
        "insufficient_funds",
        "general_decline",
        "issuer_unavailable",
        "payment_method_limit_exceeded",
    }
)
RENEWAL_RETRY_DELAY = timedelta(days=1)
```

For primary attempt number 1 with an allowlisted reason, insert attempt number 2
with the same subscription/period/plan/method snapshot, `status="scheduled"`,
`retry_of_payment_attempt_id=primary.id` and
`next_dispatch_at=now+RENEWAL_RETRY_DELAY`. Do not call provider before that
timestamp; keep the method active and set
`subscription.next_renewal_at=retry.next_dispatch_at`. Unknown reason,
`permission_revoked`, inactive method, second-attempt cancellation, or any
`dispatch_unknown` state creates no further attempt and sets `auto_renew=false`,
`auto_renew_disabled_at=now`, `next_renewal_at=NULL` without changing
`current_period_end`. Mark the method `invalid` for `permission_revoked`; for
other final failures mark it `disabled`, always with `disabled_at=now`.

Fulfillment uses a cycle-level ledger idempotency key derived from
subscription + `billing_period_start`, not attempt number, so primary/retry
cannot produce two credits for one period.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_renewals.py tests/saas_cases/test_billing_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/billing/renewals.py app/billing/payments.py tests/saas_cases/test_billing_renewals.py
git commit -m "feat: schedule idempotent subscription renewals"
```

## Task 7: Owner API, explicit consent and auto-renew off

**Files:**

- Modify: `app/billing/routes.py`
- Modify: `tests/saas_cases/test_billing_routes.py`

- [ ] **Step 1: RED — request shape, redaction and off semantics**

Добавить tests:

```python
@pytest.mark.asyncio
async def test_checkout_requires_explicit_auto_renew_consent(billing_client) -> None:
    missing = await billing_client.post(
        "/api/billing/checkout",
        json={"plan_code": "starter_monthly"},
        headers=csrf_and_idempotency("consent-missing"),
    )
    declined = await billing_client.post(
        "/api/billing/checkout",
        json={"plan_code": "starter_monthly", "auto_renew": False},
        headers=csrf_and_idempotency("consent-declined"),
    )
    assert missing.status == 400
    assert declined.status == 400
    assert (await missing.json())["error"]["code"] == "auto_renew_consent_required"


@pytest.mark.asyncio
async def test_subscription_response_never_exposes_provider_method_id(
    billing_client,
) -> None:
    response = await billing_client.get("/api/billing/subscription")
    serialized = json.dumps(await response.json())
    assert "provider_payment_method_id" not in serialized
    assert "opaque-method-id" not in serialized


@pytest.mark.asyncio
async def test_disable_auto_renew_is_csrf_owner_scoped_and_idempotent(
    billing_client,
    active_recurring_subscription,
) -> None:
    first = await billing_client.post(
        "/api/billing/subscription/auto-renew/disable",
        headers={"X-CSRF-Token": "csrf"},
    )
    first_disabled_at = (
        await reload_subscription(active_recurring_subscription.id)
    ).auto_renew_disabled_at
    second = await billing_client.post(
        "/api/billing/subscription/auto-renew/disable",
        headers={"X-CSRF-Token": "csrf"},
    )
    assert first.status == second.status == 200
    first_body = await first.json()
    second_body = await second.json()
    assert first_body["subscription"]["auto_renew"] is False
    assert second_body["subscription"]["auto_renew"] is False
    assert first_body["subscription"]["current_period_end"] == second_body["subscription"]["current_period_end"]
    assert (
        await reload_subscription(active_recurring_subscription.id)
    ).auto_renew_disabled_at == first_disabled_at


@pytest.mark.asyncio
async def test_disable_auto_renew_cancels_undispatched_retry(
    billing_client,
    active_recurring_subscription,
) -> None:
    retry = await scheduled_retry(active_recurring_subscription, due_in=timedelta(days=1))
    await billing_client.post(
        "/api/billing/subscription/auto-renew/disable",
        headers={"X-CSRF-Token": "csrf"},
    )
    assert (await reload_attempt(retry.id)).status == "cancelled"
    await run_renewals_after(timedelta(days=1))
    assert recurring_provider_calls() == []


@pytest.mark.asyncio
async def test_plans_and_token_balance_are_server_owned(billing_client) -> None:
    plans = await (await billing_client.get("/api/billing/plans")).json()
    subscription = await (await billing_client.get("/api/billing/subscription")).json()
    assert plans["plans"][0]["amount_minor"] == PLAN_CATALOG["starter_monthly"].amount.amount_minor
    assert isinstance(subscription["token_balance"], int)
```

Также сохранить existing auth/CSRF/idempotency/merchant-cutover tests.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_routes.py -k "consent or auto_renew or provider_method or server_owned" -q
```

Expected: FAIL because routes and response fields are absent.

- [ ] **Step 3: GREEN — implement exact routes**

`POST /api/billing/checkout` принимает только:

```python
if (
    not isinstance(payload, dict)
    or set(payload) != {"plan_code", "auto_renew"}
    or payload.get("auto_renew") is not True
):
    return web.json_response(
        _error(
            "auto_renew_consent_required",
            "Подтвердите условия автопродления перед оплатой",
        ),
        status=400,
    )
```

Добавить:

```text
GET  /api/billing/plans
POST /api/billing/subscription/auto-renew/disable
```

Plan serializer возвращает `code`, `title`, `amount_minor`, `currency`,
`period_days`, `generation_tokens`; checkout принимает только `plan_code` и не
доверяет browser price. Disable action требует auth + CSRF, блокирует owned
active subscription и method, затем выставляет:

```python
subscription.auto_renew = False
if subscription.auto_renew_disabled_at is None:
    subscription.auto_renew_disabled_at = now
subscription.next_renewal_at = None
method.status = "disabled"
if method.disabled_at is None:
    method.disabled_at = now
```

`current_period_start/end`, plan snapshot и usage credit не изменять.
В той же locked transaction перевести ещё не отправленные renewal rows со
`status IN ("scheduled", "creating")` в local `cancelled` только если durable
dispatch CAS ещё не совершён, с безопасным reason code
`auto_renew_disabled_before_dispatch`. Scheduler перед любым scheduled retry
повторно блокирует subscription+method и проверяет `auto_renew=true` и
`method.status="active"`; выключенный retry никогда не доходит до provider POST.

Subscription serializer добавляет только:

```python
{
    "auto_renew": row.auto_renew,
    "payment_method_saved": bool(
        row.auto_renew and row.payment_method_id is not None
    ),
    "next_renewal_at": _time(row.next_renewal_at),
}
```

Payment serializer добавляет `purpose`; для `dispatch_unknown` response
возвращает safe recovery status и никогда не предлагает новый checkout URL.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_routes.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/billing/routes.py tests/saas_cases/test_billing_routes.py
git commit -m "feat: expose recurring subscription controls"
```

## Task 8: Frontend consent, local 3s/20m polling and off UX

**Files:**

- Modify: `frontend/src/studio/types.ts:96-130`
- Modify: `frontend/src/studio/api.ts:134-178`
- Modify: `frontend/src/studio/api.test.ts`
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/studio/UpgradeGate.test.tsx`
- Modify: `frontend/e2e/fixtures/builder.ts`
- Modify: `frontend/e2e/studio.spec.ts`

- [ ] **Step 1: RED — exact HTTP contracts and timers**

API tests:

```typescript
it('sends explicit recurring consent without client price', async () => {
  await createBillingCheckout(
    'starter_monthly',
    true,
    'csrf-billing',
    'checkout-stable-key',
  );
  const [, init] = vi.mocked(fetch).mock.calls[0];
  expect(JSON.parse(String(init?.body))).toEqual({
    plan_code: 'starter_monthly',
    auto_renew: true,
  });
});

it('disables auto renewal with csrf and no payment method data', async () => {
  await disableBillingAutoRenew('csrf-billing');
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe('/api/billing/subscription/auto-renew/disable');
  expect(init?.method).toBe('POST');
  expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('csrf-billing');
});
```

Component tests with fake timers:

```typescript
it('requires unchecked-by-default recurring consent', async () => {
  render(<UpgradeGate csrfToken="csrf-billing" />);
  const consent = await screen.findByRole('checkbox', { name: /автопродлен/i });
  const pay = screen.getByRole('button', { name: /опубликовать и подключить/i });
  expect(consent).not.toBeChecked();
  expect(pay).toBeDisabled();
  await userEvent.click(consent);
  expect(pay).toBeEnabled();
});

it('polls local status every 3 seconds for at most 20 minutes', async () => {
  vi.useFakeTimers();
  vi.mocked(api.getBillingPayment).mockResolvedValue({
    payment: { ...payment, status: 'pending' },
  });
  render(<UpgradeGate csrfToken="csrf-billing" />);
  await startConsentedCheckout();
  await vi.advanceTimersByTimeAsync(3_000 * 400);
  expect(api.getBillingPayment).toHaveBeenCalledTimes(400);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(api.getBillingPayment).toHaveBeenCalledTimes(400);
  expect(screen.getByText(/платёж ещё проверяется/i)).toBeInTheDocument();
});

it('turns auto renewal off without hiding the paid period', async () => {
  render(<UpgradeGate csrfToken="csrf-billing" />);
  await userEvent.click(await screen.findByRole('button', {
    name: /отключить автопродление/i,
  }));
  expect(api.disableBillingAutoRenew).toHaveBeenCalledWith('csrf-billing');
  expect(screen.getByText(/доступ до 29 августа/i)).toBeInTheDocument();
});
```

The component test must additionally assert the complete rendered
`kaigo-recurring-v1` consent text, with the server-owned price and period
interpolated exactly. The immutable version must be bumped whenever that text
changes; a regex containing only “автопродление” is insufficient.

- [ ] **Step 2: Verify RED**

Run:

```powershell
npm --prefix frontend test -- --run src/studio/api.test.ts src/studio/UpgradeGate.test.tsx
```

Expected: FAIL because current defaults are 2 seconds/150 polls and no consent
or off control exists.

- [ ] **Step 3: GREEN — update types and API**

Use exact safe types:

```typescript
export type BillingPaymentStatus =
  | 'creating'
  | 'pending'
  | 'succeeded'
  | 'cancelled'
  | 'failed'
  | 'dispatch_unknown';

export interface BillingPlan {
  code: string;
  title: string;
  amount_minor: number;
  currency: string;
  period_days: number;
  generation_tokens: number;
}

export interface BillingSubscription {
  id: string;
  plan_code: string;
  status: 'pending' | 'active' | 'past_due' | 'cancelled' | 'expired';
  current_period_start: string;
  current_period_end: string;
  auto_renew: boolean;
  payment_method_saved: boolean;
  next_renewal_at: string | null;
}
```

API signatures:

```typescript
export function getBillingPlans(signal?: AbortSignal) {
  return saasRequestJson<{ plans: BillingPlan[] }>('/api/billing/plans', { signal });
}

export function createBillingCheckout(
  planCode: string,
  autoRenew: true,
  csrfToken: string,
  idempotencyKey: string,
) {
  return saasRequestJson<BillingCheckout>('/api/billing/checkout', {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({ plan_code: planCode, auto_renew: autoRenew }),
  });
}

export function disableBillingAutoRenew(csrfToken: string) {
  return saasRequestJson<{ subscription: BillingSubscription }>(
    '/api/billing/subscription/auto-renew/disable',
    { method: 'POST', headers: { 'X-CSRF-Token': csrfToken } },
  );
}
```

- [ ] **Step 4: GREEN — update component behavior**

Set:

```typescript
const DEFAULT_POLL_INTERVAL_MS = 3_000;
const DEFAULT_MAX_POLL_ATTEMPTS = 400;
```

Fetch the `starter_monthly` server plan before rendering consent. Checkbox
starts unchecked and its label interpolates server amount/currency and
`period_days`. Do not make price, period or token amount editable in browser.
On 400th pending response enter `pending_timeout`, clear timer, keep local
payment ID for a later page reload, and show a neutral message rather than
suggesting another payment.

Active state renders current paid-through date, auto-renew state and an
idempotent off button. Clicking off updates only the returned subscription in
state; publication stays available until backend period expiry.

For `dispatch_unknown`, hide checkout CTA and show: payment status is being
verified; do not repeat payment. Page reload continues local polling if the
attempt remains recoverable.

- [ ] **Step 5: GREEN — e2e-visible contract**

Update safe fixtures with `auto_renew`, `payment_method_saved`,
`next_renewal_at` and server plan. Add Playwright assertions:

```typescript
await expect(page.getByRole('checkbox', { name: /автопродлен/i })).not.toBeChecked();
await expect(page.getByRole('button', { name: /опубликовать и подключить/i })).toBeDisabled();
await page.getByRole('checkbox', { name: /автопродлен/i }).check();
await expect(page.getByRole('button', { name: /опубликовать и подключить/i })).toBeEnabled();
```

For active fixture, click `Отключить автопродление` and assert paid-through
copy remains visible.

- [ ] **Step 6: Verify GREEN**

Run:

```powershell
npm --prefix frontend test -- --run src/studio/api.test.ts src/studio/UpgradeGate.test.tsx
npm --prefix frontend run typecheck
npm --prefix frontend run lint
```

Expected: PASS with zero type/lint errors.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/studio/types.ts frontend/src/studio/api.ts frontend/src/studio/api.test.ts frontend/src/studio/UpgradeGate.tsx frontend/src/studio/UpgradeGate.test.tsx frontend/e2e/fixtures/builder.ts frontend/e2e/studio.spec.ts
git commit -m "feat: add recurring consent and renewal controls"
```

## Task 9: Feature-flagged billing worker and deployment contract

**Files:**

- Create: `app/billing/worker.py`
- Create: `scripts/run_billing_worker.py`
- Create: `tests/saas_cases/test_billing_worker.py`
- Create: `deploy/systemd/kaigo-billing-worker.service`
- Modify: `app/billing/runtime.py`
- Modify: `app/config.py`
- Modify: `tests/saas_cases/test_config.py`
- Modify: `docker-compose.yml`
- Modify: `tests/deployment_cases/test_saas_production_contract.py`

- [ ] **Step 1: RED — renewals are off, reconciliation still runs**

Добавить tests:

```python
@pytest.mark.asyncio
async def test_worker_reconciles_when_renewals_are_disabled() -> None:
    reconciler = AsyncMock()
    renewals = AsyncMock()
    worker = BillingWorker(
        reconciler=reconciler,
        renewals=renewals,
        renewals_enabled=False,
        poll_seconds=5,
    )
    await worker.run_once()
    reconciler.run_once.assert_awaited_once()
    renewals.run_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_dispatches_renewals_only_with_explicit_flag() -> None:
    reconciler = AsyncMock()
    renewals = AsyncMock()
    worker = BillingWorker(
        reconciler=reconciler,
        renewals=renewals,
        renewals_enabled=True,
        poll_seconds=5,
    )
    await worker.run_once()
    reconciler.run_once.assert_awaited_once()
    renewals.run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_log_does_not_include_provider_exception_text(caplog) -> None:
    reconciler = AsyncMock()
    reconciler.run_once.side_effect = RuntimeError("sentinel-provider-body")
    worker = BillingWorker(
        reconciler=reconciler,
        renewals=AsyncMock(),
        renewals_enabled=False,
        poll_seconds=5,
    )
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0)
    worker.stop()
    await asyncio.wait_for(task, timeout=1)
    assert "sentinel-provider-body" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_billing_worker_config_is_safe_by_default(monkeypatch) -> None:
    monkeypatch.delenv("KAIGO_BILLING_RENEWALS_ENABLED", raising=False)
    config = load_test_config(monkeypatch)
    assert config.billing_renewals_enabled is False
    assert 1 <= config.billing_worker_poll_seconds <= 60


def test_receipt_config_requires_complete_valid_54fz_fields(monkeypatch) -> None:
    monkeypatch.setenv("YOOKASSA_RECEIPTS_ENABLED", "true")
    monkeypatch.delenv("YOOKASSA_RECEIPT_VAT_CODE", raising=False)
    with pytest.raises(RuntimeError, match="receipt vat code"):
        load_config()


def test_receipt_config_is_server_owned(monkeypatch) -> None:
    configure_test_receipt_environment(monkeypatch)
    config = load_config()
    assert config.yookassa_receipts_enabled is True
    assert 1 <= config.yookassa_receipt_vat_code <= 12
    assert config.yookassa_receipt_payment_mode == "full_payment"
    assert config.yookassa_receipt_payment_subject == "service"
```

Deployment test checks:

```python
assert 'command: ["python", "scripts/run_billing_worker.py"]' in billing_worker
assert "restart:" in billing_worker
assert "KAIGO_BILLING_RENEWALS_ENABLED" in billing_worker
assert "true" not in renewal_flag_default.lower()
assert "EnvironmentFile=" in systemd_unit
assert "ExecStart=" in systemd_unit
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_worker.py tests/saas_cases/test_config.py tests/deployment_cases/test_saas_production_contract.py -k "billing_worker or billing_renewal or receipt" -q
```

Expected: FAIL because worker/config/deployment are absent.

- [ ] **Step 3: GREEN — implement stop-aware loop**

```python
class BillingWorker:
    def __init__(
        self,
        *,
        reconciler: PaymentReconciler,
        renewals: RenewalScheduler,
        renewals_enabled: bool,
        poll_seconds: float,
    ) -> None:
        self._reconciler = reconciler
        self._renewals = renewals
        self._renewals_enabled = renewals_enabled
        self._poll_seconds = poll_seconds
        self._stopped = asyncio.Event()

    async def run_once(self) -> None:
        await self._reconciler.run_once()
        if self._renewals_enabled:
            await self._renewals.run_once()

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.run_once()
            except Exception as error:
                logger.error(
                    "billing worker iteration failed",
                    extra={"error_type": type(error).__name__},
                )
            try:
                await asyncio.wait_for(self._stopped.wait(), self._poll_seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()
```

Config additions:

```python
billing_renewals_enabled: bool = False
billing_worker_poll_seconds: float = 5
yookassa_receipts_enabled: bool = False
yookassa_receipt_vat_code: int | None = None
yookassa_receipt_payment_mode: str = "full_payment"
yookassa_receipt_payment_subject: str = "service"
yookassa_receipt_tax_system_code: int | None = None
```

Validate `1 <= billing_worker_poll_seconds <= 60`. Renewal flag default false in
all environments; no production auto-enable. Load receipt settings only from
`YOOKASSA_RECEIPTS_ENABLED`, `YOOKASSA_RECEIPT_VAT_CODE`,
`YOOKASSA_RECEIPT_PAYMENT_MODE`, `YOOKASSA_RECEIPT_PAYMENT_SUBJECT` and optional
`YOOKASSA_RECEIPT_TAX_SYSTEM_CODE`. When enabled, require VAT code 1..12,
optional tax system 1..6 and provider-documented enum values; invalid or missing
settings fail startup before a payment. Do not add guessed tax defaults.

Reuse one provider/service factory from `app/billing/runtime.py` so web and
worker calculate the same merchant fingerprint and receipt body. Preserve the
existing `create_billing_service(...)` entry point as a compatibility wrapper,
but build its value and the worker from one explicit bundle:

```python
@dataclass(slots=True, repr=False)
class BillingRuntime:
    provider: PaymentProvider
    payments: BillingService
    receipt_settings: BillingReceiptSettings


def create_billing_runtime(
    config: AppConfig,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_factory: ProviderFactory = YooKassaProvider,
) -> BillingRuntime | None:
    receipt_settings = BillingReceiptSettings(
        enabled=config.yookassa_receipts_enabled,
        vat_code=config.yookassa_receipt_vat_code,
        payment_mode=config.yookassa_receipt_payment_mode,
        payment_subject=config.yookassa_receipt_payment_subject,
        tax_system_code=config.yookassa_receipt_tax_system_code,
    )
    provider = _create_provider_without_logging_config(config, provider_factory)
    if provider is None:
        return None
    payments = BillingService(
        session_factory,
        provider,
        receipt_settings=receipt_settings,
    )
    return BillingRuntime(provider, payments, receipt_settings)
```

`_create_provider_without_logging_config` is the extraction of the current
guarded provider construction: it receives credentials from `AppConfig`, passes
them only to the provider constructor and never serializes, logs or exposes them
as runtime fields. `create_billing_service` returns `runtime.payments`; web
cleanup and worker cleanup each close that service exactly once.

Worker wiring is exact and uses the same instances:

```python
reconciler = PaymentReconciler(
    session_factory,
    runtime.provider,
    payment_service=runtime.payments,
)
renewals = RenewalScheduler(
    session_factory,
    runtime.provider,
    payment_service=runtime.payments,
    receipt_settings=runtime.receipt_settings,
)
```

`scripts/run_billing_worker.py` builds async engine/session factory, provider,
`PaymentReconciler`, `RenewalScheduler`, `BillingWorker`, installs SIGINT/SIGTERM
handlers, runs forever, closes provider and engine. Logging includes local
attempt UUID only at debug level and never logs provider method ID, auth header,
config repr, exception message or HTTP body. Provider boundaries convert failures
to safe local error codes; the outer worker logs only the exception class name.

- [ ] **Step 4: GREEN — deploy as a separate process**

Add a `billing-worker` compose service using the application image and command:

```yaml
command: ["python", "scripts/run_billing_worker.py"]
restart: unless-stopped
```

Pass only the renewal flag and existing externally supplied server environment;
do not add secret values or `.env` files to Git. Systemd unit mirrors existing
builder-worker hardening, starts after migration/database, and has restart-on-
failure. Rollback order: set renewal flag off, restart billing worker, keep
reconciliation/webhook running, then roll back application code if required.

- [ ] **Step 5: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_worker.py tests/saas_cases/test_config.py tests/deployment_cases/test_saas_production_contract.py -k "billing_worker or billing_renewal or receipt" -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/billing/worker.py app/billing/runtime.py app/config.py scripts/run_billing_worker.py tests/saas_cases/test_billing_worker.py tests/saas_cases/test_config.py docker-compose.yml deploy/systemd/kaigo-billing-worker.service tests/deployment_cases/test_saas_production_contract.py
git commit -m "feat: add feature flagged billing worker"
```

## Task 10: Full regression, test-shop E2E gate and verified documentation

**Files:**

- Modify: `docs/BILLING_FOUNDATION.md`
- Modify: `docs/SAAS_PRODUCTION_RUNBOOK.md`
- Create after external proof: `docs/release-evidence/2026-07-30-yookassa-test-shop.md`
- Modify after external proof: `docs/product-journal/2026-07.md`
- Create or modify after external proof: one file under
  `docs/telegram/release-packets/`

- [ ] **Step 1: Run backend focused suite**

Run:

```powershell
python -m pytest tests/saas_cases/test_billing_migration.py tests/saas_cases/test_billing_recurring_migration.py tests/saas_cases/test_billing_provider.py tests/saas_cases/test_billing_service.py tests/saas_cases/test_billing_reconciliation.py tests/saas_cases/test_billing_renewals.py tests/saas_cases/test_billing_routes.py tests/saas_cases/test_billing_worker.py tests/saas_cases/test_trial_service.py tests/saas_cases/test_config.py -q
```

Expected: PASS; only an explicitly unconfigured disposable-PostgreSQL test may
SKIP. No external YooKassa call occurs in this suite.

- [ ] **Step 2: Run frontend and broad SaaS regression**

Run:

```powershell
npm --prefix frontend test -- --run
npm --prefix frontend run build
python -m pytest tests/saas_cases tests/deployment_cases/test_saas_production_contract.py -q
git diff --check
```

Expected: PASS, build success, no whitespace errors. Investigate every new
failure; do not label a broad-suite timeout as success.

- [ ] **Step 3: Verify schema-first rollout with renewals off**

Run the migration against the disposable PostgreSQL target before application
code, then start application and billing worker with renewal flag false.

Acceptance:

```text
alembic current -> 0015_yookassa_recurring_foundation
legacy subscription.auto_renew -> false
legacy current_period_end -> unchanged
billing worker reconciliation loop -> healthy
renewal provider POST count -> 0
```

Do not proceed if the PostgreSQL migration test was skipped and no disposable
schema-first run has replaced it.

- [ ] **Step 4: Run real test-shop scenario A — saved method and lost webhook**

Use a disposable test user and a test-only HTTPS deployment. Merchant values
come from the deployment secret store and are never pasted into commands,
notes or evidence.

Sequence:

1. Confirm renewal flag is false.
2. Enable receipt handling only with owner/accountant-approved server values;
   confirm startup validation passes. Do not expose those values to frontend.
3. In browser, verify consent is unchecked and checkout is disabled.
4. Check consent, create one redirect payment and complete it with a current
   official YooKassa test-card scenario selected from the linked documentation.
   Do not copy card details into Git or evidence.
5. In the controlled test ingress, suppress delivery of that payment's webhook.
6. Confirm local frontend polls only every 3 seconds.
7. Confirm worker performs a provider GET within 60 seconds, never twice inside
   one 60-second window.
8. Confirm local payment becomes `succeeded`, one subscription period and one
   `tokens` credit exist, and a method row exists only because verified
   `payment_method.saved=true`.
9. Confirm the test-shop payment accepted the configured receipt and that the
   receipt describes one service item whose amount equals the payment amount.
10. Restore webhook delivery and confirm its retry is accepted with no second
   period, method row or credit.

Failure at any step leaves renewal flag false.

- [ ] **Step 5: Run real test-shop scenario B — stored-method renewal and off**

Use three independent disposable test subscriptions so success, temporary retry
and off cases cannot invalidate each other.

Subscription 1:

1. Complete consented test redirect and verify saved method.
2. In the test database only, move `next_renewal_at` to current time while
   preserving the stored period boundary.
3. Enable renewal flag only in the isolated test deployment.
4. Confirm exactly one provider POST for the period, with no redirect, followed
   by one period extension and one `tokens` credit.
5. Run two worker instances through the same due window and confirm attempt and
   provider POST counts remain one.

Subscription 2:

1. Use the current official YooKassa test-card scenario that saves a method but
   makes the later recurring payment fail with a documented temporary reason.
2. Make the renewal due and confirm primary attempt number 1 is `cancelled`.
3. Confirm attempt number 2 is `scheduled` for exactly +1 day, has a new local
   and provider idempotency key, and no second provider POST occurs before due.
4. Record the exact 24-hour delta, then only in this disposable test database
   move attempt 2 `next_dispatch_at` to current time; do not add a production
   clock override or wait a real day. Confirm exactly one retry POST.
5. After the retry's definitive failure, confirm `auto_renew=false`, no third
   attempt exists and the already paid period was not shortened.

Subscription 3:

1. Complete consented test redirect and record paid-through timestamp.
2. Disable auto-renew through the owner/CSRF route.
3. Move test clock/due time beyond the original renewal boundary.
4. Confirm provider POST count stays zero for this subscription and the stored
   paid-through timestamp was not shortened.

Also exercise one permanent cancellation and one simulated ambiguous transport
response. Permanent cancellation must create no +1-day retry. For the ambiguous
case prove same-body replay before the deadline, no separate retry attempt and
zero POST attempts at/after 24 hours.

- [ ] **Step 6: Write redacted evidence only after success**

Create `docs/release-evidence/2026-07-30-yookassa-test-shop.md` with this exact
shape and only boolean/count/timing evidence:

```markdown
# YooKassa recurring test-shop evidence

- Provider mode verified as test: yes
- Current official YooKassa test-card scenarios used: yes
- Receipt settings loaded from server environment and accepted: pass
- Receipt item amount equals payment amount: pass
- Consent required before redirect: pass
- Verified saved method persisted: pass
- Lost webhook recovered in seconds: <integer from observed run, at most 60>
- Maximum GETs for one attempt in any 60-second window: 1
- Webhook/reconcile fulfillment credits: 1
- Successful renewal-cycle attempts/provider POSTs: 1/1
- Temporary-failure cycle attempts/provider POSTs: 2/2
- Temporary retry delay in hours: 24
- Attempts after second or permanent failure: 0
- Auto-renew off provider POSTs: 0
- Paid period shortened by off action: no
- POSTs at or after ambiguous 24-hour deadline: 0
- Renewal flag during run: isolated test environment only
- Live renewal enabled: no
```

Replace the angle-bracket timing field with the observed integer. Do not include
emails, local/provider IDs, checkout URLs, payment method IDs, headers, database
URLs, screenshots containing personal data or credentials.

- [ ] **Step 7: Update verified docs and editorial contour**

`docs/BILLING_FOUNDATION.md` documents:

- consent and saved-method verification rule;
- local 3s/20m polling versus server 60-second reconciliation;
- shared fulfillment and one-logical-cycle-per-period invariant;
- one logical renewal cycle with at most one +1-day temporary retry;
- 24-hour stop and `dispatch_unknown` operator meaning;
- unified `tokens` bucket;
- auto-renew off semantics;
- receipt/54-ФЗ server-environment validation and request fields;
- official test-card page as the only source for current test scenarios;
- renewal flag and test-only gate.

`docs/SAAS_PRODUCTION_RUNBOOK.md` documents schema-first deploy, worker health,
flag-off default and kill-switch-first rollback. After external proof, add one
short Russian product-journal entry and one release packet; separate verified
facts from later live-rollout plans.

- [ ] **Step 8: Confirm scope and secret hygiene**

Run:

```powershell
git status --short
git diff --name-only
git diff --name-only | Select-String -Pattern '(^|[\\/])\.env($|\.)'
```

Expected: only files named in this plan are changed; final command emits no
matches. Inspect response/evidence tests to confirm the opaque method field name
and value never occur in serialized output.

- [ ] **Step 9: Commit documentation and evidence**

If test-shop E2E passed:

```powershell
git add docs/BILLING_FOUNDATION.md docs/SAAS_PRODUCTION_RUNBOOK.md docs/release-evidence/2026-07-30-yookassa-test-shop.md docs/product-journal/2026-07.md docs/telegram/release-packets
git commit -m "docs: record yookassa recurring test proof"
```

If E2E did not pass, commit only the implementation contract/runbook, do not
create success evidence, do not add a success journal entry, and leave renewal
flag false:

```powershell
git add docs/BILLING_FOUNDATION.md docs/SAAS_PRODUCTION_RUNBOOK.md
git commit -m "docs: document recurring billing rollout gate"
```

## Финальный definition of done

- `0015_yookassa_recurring_foundation` follows real
  `0014_generation_forensics`, upgrades/downgrades on SQLite and upgrades on
  disposable PostgreSQL.
- Legacy subscriptions remain entitled for the same period and have
  `auto_renew=false` with no fabricated method.
- New redirect checkout is impossible without explicit consent and sends
  `save_payment_method=true` from the server adapter.
- Opaque method ID persists only after verified succeeded+paid+saved and never
  appears in browser, logs, event payload or committed evidence.
- Local polling is exactly 3 seconds, at most 400 requests; provider GET cadence
  is cross-worker fenced to once per attempt per 60 seconds.
- Webhook, reconciliation and immediate renewal response create one canonical
  fulfillment, one period transition and one `tokens` credit.
- Ambiguous POST is exact-body replayable only before 24 hours and is never
  submitted at/after the deadline.
- One logical renewal cycle exists per subscription period. It has one primary
  attempt and, only after a verified temporary cancellation, at most one new
  attempt with a new key exactly one day later; ambiguous POSTs and permanent or
  second failures never create another attempt.
- Auto-renew off is authenticated, CSRF-protected, idempotent and never shortens
  the paid period.
- Reconciliation can run while renewals are off; renewal dispatch remains
  disabled by default.
- When receipt handling is enabled, initial and renewal requests use only
  validated server-environment fiscal settings and a balanced service item;
  frontend cannot alter fiscal fields.
- Mocked local suites pass, then real test-shop redirect, lost-webhook recovery,
  stored-method renewal, +1-day retry, receipt and off scenarios pass with the
  current official test cards. Until that external evidence exists, the feature
  is not complete and renewal flag stays off.
