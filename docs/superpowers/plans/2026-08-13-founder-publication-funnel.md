# План реализации founder-воронки публикации

> **Для исполнителей:** выполнять план по задачам и отмечать шаги флажками (`- [ ]`).

**Цель:** Реализовать founder-пилот и три платных варианта публикации в существующем backend/frontend Kaigo.

**Архитектура:** Публичные платные планы остаются в серверном каталоге. Платный
intro — это одноразовые 500 ₽/15 дней; только при отдельном явном согласии
renewal worker создаёт скрытый шаг 1 500 ₽/15 дней, затем обычный месячный план
2 000 ₽/30 дней. Без согласия доступ заканчивается после первых 15 дней без
следующего списания. Founder хранится отдельным грантом и активной подпиской без
способа оплаты и автопродления. Founder и paid intro взаимоисключающие, а UI
получает единый offer snapshot и не вычисляет коммерческие условия сам.

**Контракт:** Первые 20 подходящих founder-клиентов получают 14 дней бесплатно,
без карты, 1 500 000 токенов и оставляют обратную связь. Один пользователь и
один исходный HTTPS-origin могут получить только один founder-грант. Платный
intro доступен один раз и несовместим с founder. Обычный месяц стоит 2 000 ₽ за
30 дней, квартал — 5 000 ₽ за 90 дней.

**Серверные инварианты:** Цены, периоды, токены и цепочка продлений берутся
только из каталога. `auto_renew` принимается только как явный boolean; при
`false` способ оплаты и consent-поля не сохраняются. Квота founder и
взаимоисключение founder/intro проверяются атомарно. У renewal-попытки
неизменяемы snapshot тарифа, цена, fingerprint и исходные даты периода; при
компенсации меняется только effective entitlement period, а остаток токенов
переносится один раз.

**Идемпотентность:** Checkout требует авторизацию, CSRF и `Idempotency-Key`.
Повтор с тем же ключом и параметрами возвращает тот же checkout; другой тариф,
проект или intent с тем же ключом дают `409`. Ключ YooKassa выводится из UUID
платёжной попытки и сохраняется при восстановлении после сетевого сбоя. Запрос
`POST /api/billing/contact` также требует `Idempotency-Key`: повтор того же
ключа и данных не создаёт второй запрос или event, конфликт данных даёт `409`.

**Стек:** Python 3.12, aiohttp, SQLAlchemy/Alembic, PostgreSQL/SQLite tests,
React 19, TypeScript, Vitest/Testing Library, YooKassa.

---

### Задача 1: публичный каталог и переходный тариф

**Files:**
- Modify: `app/billing/catalog.py`
- Modify: `app/billing/renewals.py`
- Test: `tests/saas_cases/test_billing_service.py`
- Test: `tests/saas_cases/test_billing_renewals.py`

- [x] Добавить падающие тесты на 500/1 500/2 000/5 000 ₽ и скрытый legacy Pro.
- [x] Проверить одноразовость платного intro и его взаимоисключение с founder.
- [x] Добавить падающие тесты: первое продление `starter_intro_15d` создаётся как `starter_intro_balance_15d`, второе — как `starter_monthly`.
- [x] Расширить совместимый снимок плана полями `public` и `renewal_plan_code`.
- [x] Реализовать выбор следующего плана в renewal worker.

### Задача 2: founder-грант и токены

**Files:**
- Modify: `app/saas/models.py`
- Create: `migrations/versions/0019_founder_publication_funnel.py`
- Create: `app/billing/offers.py`
- Modify: `app/billing/service.py`
- Test: `tests/saas_cases/test_founder_offer.py`
- Test: `tests/saas_cases/test_billing_migration.py`

- [x] Написать тесты eligibility, повторной выдачи, взаимоисключения с paid
  intro и квоты 20.
- [x] Добавить отдельный тест на дубль founder для одного нормализованного
  домена у разных пользователей.
- [x] Добавить `FounderAccessGrant`, `CustomerContactRequest` и lineage гранта
  в подписку/ledger.
- [x] Реализовать атомарную выдачу гранта, 14-дневную подписку без карты и
  автопродления и 1 500 000 токенов.
- [x] Научить generation ledger резервировать, учитывать и возвращать токены
  гранта.
- [x] Прогнать billing service тесты: 54 passed.

### Задача 3: offer, claim и feedback API

**Files:**
- Modify: `app/billing/routes.py`
- Modify: `app/analytics/service.py`
- Test: `tests/saas_cases/test_billing_routes.py`

- [x] Написать route-тесты для `GET /api/billing/offer`, founder claim и support
  request.
- [x] Вернуть server-priced планы, eligibility, точные renewal terms и active
  subscription metadata.
- [x] Добавить CSRF-защищённые founder/support endpoints и boolean-согласие
  `auto_renew` для checkout.
- [x] Добавить идемпотентность для повторной отправки support request; founder
  claim также идемпотентен.
- [x] Записать funnel events без клиентских email и цен.
- [x] Прогнать founder/routes/migration: 26 passed, 3 skipped.

### Задача 4: окно публикации Студии

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

- [x] Написать component/API-тесты founder, трёх планов, одноразового intro и
  явного consent.
- [x] Реализовать типы/API и диалог с loading/error/unavailable states.
- [x] Связать founder claim с немедленной публикацией готовой версии.
- [x] Показать точную дату/сумму продления и сохранить существующее отключение.
- [x] Добавить форму поддержки/отзыва и responsive CSS в текущих токенах
  Студии.
- [x] Прогнать frontend: API 13 passed, UpgradeGate 27 passed; 7 начальных
  сбоев были только таймаутами по 5 секунд, повторный single-worker прогон
  дал 99/99 passed; полный набор ранее дал 256 passed, typecheck/lint/build
  зелёные.

### Задача 5: проверка и редакторский контур

**Files:**
- Modify: `docs/product-journal/2026-08.md`
- Modify: `docs/telegram/release-packets/2026-08-14-founder-publication-funnel.md`
- Modify: `docs/telegram/content-backlog.md`

- [x] Прогнать backend suites: billing_service 54 passed; founder/routes/migration
  26 passed, 3 skipped; renewals 15 passed, 2 skipped.
- [x] Прогнать PostgreSQL contract round-trip `0018 -> 0019 -> 0018`: 6 passed.
- [x] Прогнать полный frontend Vitest/typecheck/build: API 13, UpgradeGate 27;
  повторный single-worker прогон 99/99 passed после 7 таймаутов по 5 секунд;
  полный набор ранее 256 passed; production deploy не заявлять без фактического
  выпуска.
- [x] Проверить `git diff --check` по owned docs; чужие dirty-файлы не менялись.
- [x] Записать подтверждённые non-prod результаты и ограничения: независимый
  review GO, P0/P1/P2 нет.
- [x] Выпустить изменения в production и выполнить production smoke-check:
  release `cd3353f1f45d73fdf37c7aa0ca251094b9d09b76`, Alembic
  `0019_founder_publication_funnel`, app/billing/worker на точных immutable
  image IDs; `/api/health`, token-authenticated `/api/ready`, Studio, auth
  session, `/landing-old/`, embed/runtime и один живой публичный chat canary
  прошли. Реальное списание и sandbox webhook намеренно не запускались без
  платёжной fixture/карты.
