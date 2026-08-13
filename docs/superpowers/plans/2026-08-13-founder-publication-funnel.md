# Founder Publication Funnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать founder-пилот и прозрачные платные варианты публикации в существующем backend/frontend Kaigo.

**Architecture:** Публичные платные планы остаются в серверном каталоге; вводный план содержит ссылку на следующий месячный план, которую использует renewal worker. Бесплатный доступ хранится отдельным грантом и активной неавтопродлеваемой подпиской, а UI получает единый offer snapshot и не вычисляет коммерческие условия самостоятельно.

**Tech Stack:** Python 3.12, aiohttp, SQLAlchemy/Alembic, PostgreSQL/SQLite tests, React 19, TypeScript, Vitest/Testing Library, YooKassa.

---

### Task 1: Публичный каталог и переходный тариф

**Files:**
- Modify: `app/billing/catalog.py`
- Modify: `app/billing/renewals.py`
- Test: `tests/saas_cases/test_billing_service.py`
- Test: `tests/saas_cases/test_billing_renewals.py`

- [x] Добавить падающие тесты на суммы 500/2 000/5 000 ₽ и скрытый legacy Pro.
- [ ] Добавить падающий тест: первое продление `starter_intro_15d` создаётся как `starter_monthly`.
- [ ] Расширить совместимый снимок плана полями `public` и `renewal_plan_code`.
- [ ] Реализовать выбор следующего плана в renewal worker и прогнать тесты.

### Task 2: Founder-грант и токены

**Files:**
- Modify: `app/saas/models.py`
- Create: `migrations/versions/0019_founder_publication_funnel.py`
- Create: `app/billing/offers.py`
- Modify: `app/billing/service.py`
- Test: `tests/saas_cases/test_founder_offer.py`
- Test: `tests/saas_cases/test_billing_migration.py`

- [ ] Написать падающие тесты eligibility, повторной выдачи, доменного дубля и квоты 20.
- [ ] Добавить `FounderAccessGrant`, `CustomerContactRequest` и lineage гранта в подписку/ledger.
- [ ] Реализовать атомарную выдачу гранта, 14-дневную подписку и 1 500 000 токенов.
- [ ] Научить generation ledger резервировать, учитывать и возвращать токены гранта.
- [ ] Прогнать founder, billing service и migration тесты.

### Task 3: Offer, claim и feedback API

**Files:**
- Modify: `app/billing/routes.py`
- Modify: `app/analytics/service.py`
- Test: `tests/saas_cases/test_billing_routes.py`

- [ ] Написать падающие route-тесты для `GET /api/billing/offer`, founder claim и support request.
- [ ] Вернуть server-priced планы, eligibility, точные renewal terms и active subscription metadata.
- [ ] Добавить CSRF-защищённые idempotent founder/support endpoints.
- [ ] Записать funnel events без клиентских email и цен.
- [ ] Прогнать billing route/funnel тесты.

### Task 4: Окно публикации Студии

**Files:**
- Create: `frontend/src/studio/PublicationOfferDialog.tsx`
- Create: `frontend/src/studio/PublicationOfferDialog.test.tsx`
- Create: `frontend/src/studio/SupportDialog.tsx`
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/studio/api.ts`
- Modify: `frontend/src/studio/types.ts`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/studio/UpgradeGate.test.tsx`
- Test: `frontend/src/studio/api.test.ts`

- [ ] Написать падающие component/API-тесты founder, трёх планов и явного consent.
- [ ] Реализовать типы/API и диалог с loading/error/unavailable states.
- [ ] Связать founder claim с немедленной публикацией готовой версии.
- [ ] Показать точную дату/сумму продления и сохранить существующее отключение.
- [ ] Добавить форму поддержки/отзыва и responsive CSS в текущих токенах Студии.
- [ ] Прогнать Vitest, typecheck и lint.

### Task 5: Проверка и редакторский контур

**Files:**
- Modify: `docs/product-journal/2026-08.md`
- Create: `docs/telegram/release-packets/2026-08-13-founder-publication-funnel.md`
- Modify: `docs/telegram/content-backlog.md`

- [ ] Прогнать целевые backend suites и Alembic upgrade/downgrade contract tests.
- [ ] Прогнать полный frontend Vitest/typecheck/build.
- [ ] Проверить `git diff --check` и убедиться, что unrelated dirty files не затронуты.
- [ ] Записать только подтверждённые результаты, ограничения и команды проверки.
