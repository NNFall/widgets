# Stored feedback frontend: evidence and handoff

Дата: 17 августа 2026 года
Ветка: `codex/product-ui`
Статус: frontend готов к backend-интеграции; production endpoint и таблица ещё
не реализованы.

## Что проверено

В интерфейсе Kaigo реализованы:

- единая форма обратной связи на лендинге и в Studio;
- четыре понятные темы: вопрос, ошибка, идея по улучшению и сотрудничество;
- отправка без имени, email, телефона и других контактных полей;
- consent-only payload с версией документа `feedback-v2`;
- `GET /api/feedback/session` для landing и `POST /api/feedback` с CSRF и
  `Idempotency-Key`;
- успешный UI только для валидного ответа `200/201` со статусом `stored`;
- сохранение текста и повтор с тем же ключом/телом после временного `500`;
- компактный footer без неподтверждённого почтового адреса и `mailto:`;
- мобильная проверка без горизонтального overflow и с touch-safe controls;
- сохранение фокуса, Escape и focus trap контактного drawer в Studio.

## Граница доказательства

Playwright E2E используют локальный контрактный mock для:

```text
GET  /api/feedback/session -> 200
POST /api/feedback         -> 201 { receipt_id, status: "stored", received_at }
```

Они проверяют реальные request headers/body и пользовательские состояния, но не
доказывают наличие backend-хранилища. На момент этого frontend-commit в
production нет подтверждённых `GET /api/feedback/session`, `POST /api/feedback`
и PostgreSQL `feedback_submissions`. Поэтому live success, реальная история,
email- или Telegram-доставка не заявляются.

Четыре реальные локальные captures (375×811 для трёх landing-кадров и 390×843
для Studio, in-app browser, explicit contract mock) сохранены в папке assets и перечислены в
[`assets/2026-08-17-product-ui-stored-feedback/README.md`](assets/2026-08-17-product-ui-stored-feedback/README.md).
Они показывают только frontend UX и не являются production-доказательством;
desktop-captures в этой подборке нет.

## Проверочная команда

Точный targeted-прогон из `frontend/`:

```powershell
npx playwright test e2e/landing.spec.ts e2e/studio.spec.ts --project=desktop-1920 --project=mobile-390 --grep "feedback"
```

Контракт требует ноль вызовов feedback-session в авторизованной Studio и
серверный auth CSRF. После исправления hydration для Studio home целевой прогон
даёт `8 passed`: landing использует анонимную feedback-session, Studio — токен
авторизованной сессии, а повтор после неоднозначной ошибки сохраняет точное тело
и idempotency key.

Более широкий регрессионный прогон без `--grep`:

```powershell
npx playwright test e2e/landing.spec.ts e2e/studio.spec.ts --project=desktop-1920 --project=mobile-390
```

Текущий повтор дал `25 passed` за `3.3m`. В прогон входят desktop 1920 и mobile
390, landing, Studio home, Studio drawer, keyboard/focus, overflow и feedback
контракты. Это проверка frontend с mock API, а не доказательство live backend
storage.

Проверенный baseline этой задачи — commit `a948372`
(`fix(frontend): hydrate Studio feedback csrf`). Backend-чат обязан
интегрировать этот commit или reviewed descendant и записать фактический SHA в
свой deployment record:

```text
fix(frontend): hydrate Studio feedback csrf
Minimum verified baseline: a948372
```

## Что не выполнялось

Этот frontend-commit не изменяет `app/`, миграции, PostgreSQL, nginx, systemd,
production checkout или production deployment. Не выполнялись реальные
обращения к пользователям, платежи, Telegram и почтовые отправки.

До backend-интеграции и отдельного согласования production deployment остаётся
обязательным.

См. подробный API/DB/backend запрос в
[`docs/CONTACT_AND_LEGAL_HANDOFF.md`](../CONTACT_AND_LEGAL_HANDOFF.md).
