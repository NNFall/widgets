# VK ID One Tap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить безопасный официальный VK ID One Tap в существующее окно входа Kaigo, сохранив Google, Яндекс, draft binding и серверное владение OAuth-сессией.

**Architecture:** Frontend получает одноразовую PKCE-транзакцию от нового server bootstrap endpoint и передаёт её официальному `@vkid/sdk`. VK возвращает authorization code в существующий callback-контур; сервер проверяет state/browser binding, обменивает код, читает профиль и создаёт обычную Kaigo-сессию. Секреты VK не используются.

**Tech Stack:** Python 3.12, aiohttp, SQLAlchemy, httpx, React 19, TypeScript, `@vkid/sdk` 2.6.1, Vitest, pytest, Nginx CSP.

---

## Task 1: Серверный VK OAuth provider

**Files:**
- Modify: `app/auth/oauth.py`
- Test: `tests/saas_cases/test_oauth.py`

- [ ] Написать RED-тесты для VK authorization URL, обязательного `device_id`, PKCE token exchange, state response и user-info profile.
- [ ] Запустить `python -m pytest -q tests/saas_cases/test_oauth.py -k vk` и подтвердить RED.
- [ ] Добавить `device_id: str | None` в `OAuthCallback` без изменения Google/Яндекс.
- [ ] Реализовать `VKOAuthProvider` с `id.vk.ru`, scope `email`, строгой валидацией state/id/email и безопасным профилем.
- [ ] Запустить focused и полный `test_oauth.py` до GREEN.

## Task 2: Конфигурация и provider registry

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `app/auth/routes.py`
- Test: `tests/saas_cases/test_config.py`
- Test: `tests/saas_cases/test_auth_routes.py`

- [ ] Написать RED-тесты на положительный integer `VK_OAUTH_APP_ID`, provider registry и список `/api/auth/session`.
- [ ] Реализовать `vk_oauth_app_id: int | None`; не добавлять secret/service key.
- [ ] Зарегистрировать `VKOAuthProvider` только при валидном App ID.
- [ ] Подтвердить, что Google/Яндекс по-прежнему требуют полную пару id+secret и их тесты зелёные.

## Task 3: Одноразовый VK bootstrap и callback

**Files:**
- Modify: `app/auth/routes.py`
- Test: `tests/saas_cases/test_auth_routes.py`

- [ ] Вынести создание OAuth state/PKCE/browser binding из `auth_start` в общий приватный helper.
- [ ] Написать RED-тесты `POST /api/auth/vk/bootstrap`: rate limit, bound draft, journey, no-store, безопасный JSON и отсутствие не-VK provider.
- [ ] Реализовать bootstrap route и зарегистрировать его до generic provider route.
- [ ] Написать RED-тест callback с `device_id`, invalid/missing device id и повторным state.
- [ ] Передавать `device_id` в `OAuthCallback`, сохранив существующий consume-before-exchange порядок.
- [ ] Прогнать `test_auth_routes.py` и `test_oauth.py` до GREEN.

## Task 4: Официальный One Tap во frontend

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Create: `frontend/src/auth/VkOneTap.tsx`
- Create: `frontend/src/auth/VkOneTap.test.tsx`
- Modify: `frontend/src/auth/AuthGate.tsx`
- Modify: `frontend/src/auth/AuthGate.test.tsx`

- [ ] Установить exact `@vkid/sdk@2.6.1`.
- [ ] Написать RED-тесты bootstrap request и `Config.init` с app/redirect/state/codeVerifier/scope/mode.
- [ ] Написать RED-тест render с `fastAuthEnabled:false`, `showAlternativeLogin:false`, light/primary, 44 px, radius 8, full width.
- [ ] Написать RED-тест fallback-ссылки при bootstrap/SDK/render error и draft suffix.
- [ ] Реализовать изолированный `VkOneTap`, не сохраняя токены и очищая container/widget при unmount.
- [ ] Вставить его в `AuthGate` только когда provider list содержит `vk`; сохранить Google/Яндекс.
- [ ] Прогнать targeted Vitest, затем весь `src/auth`.

## Task 5: CSP без стороннего tracker

**Files:**
- Modify: `deploy/nginx/kaigo-marketing-site.conf`
- Modify: `tests/deployment_cases/test_marketing_site_package.py`

- [ ] Написать RED-контракт: Studio/SPA CSP содержит `connect-src 'self' https://id.vk.ru` и `frame-src 'self' https://id.vk.ru`.
- [ ] Зафиксировать, что `script-src` остаётся только `'self'`, без `unsafe-inline`, unpkg и MyTracker.
- [ ] Обновить только CSP браузерного приложения; embed/archive политики не расширять.
- [ ] Прогнать deployment contract tests.

## Task 6: Полная локальная проверка и журнал продукта

**Files:**
- Modify: `docs/product-journal/2026-08.md`
- Create: `docs/telegram/release-packets/2026-08-15-vk-id-one-tap.md`
- Modify: `docs/telegram/content-backlog.md`

- [ ] Выполнить backend auth/config suites.
- [ ] Выполнить frontend targeted/full Vitest, typecheck, lint и build.
- [ ] Выполнить CSP/deployment tests, Ruff, compileall и `git diff --check`.
- [ ] Запустить локальный браузерный smoke: окно входа, One Tap iframe/fallback, сохранение Google/Яндекс, отсутствие overflow.
- [ ] Записать только подтверждённые результаты в journal/release packet/backlog, без ключей и персональных данных.

## Task 7: Безопасный production-релиз

**Files:**
- Runtime config only: `/etc/kaigo/kaigo.env`
- Deployment artifacts from committed release

- [ ] Зафиксировать чистый commit и push release SHA.
- [ ] Снять read-only preflight production: active release, DB revision, Compose project, image identities, systemd paths, marketing symlink.
- [ ] Добавить только `VK_OAUTH_APP_ID` в защищённый runtime env; не добавлять protected/service key.
- [ ] Собрать immutable app/frontend artifacts, выполнить обычный backup/migration preflight при необходимости и recreate в безопасном порядке.
- [ ] Применить Nginx config через `nginx -t` и атомарный marketing deploy.
- [ ] Проверить `/api/health`, `/api/auth/session`, start/bootstrap routes, Google/Яндекс regression и VK One Tap в реальном браузере.
- [ ] Не объявлять end-to-end VK login подтверждённым до ручного входа владельца VK-аккаунта.
- [ ] Рекомендовать владельцу перевыпустить оба ранее отправленных VK-ключа, так как они были раскрыты в чате и для этого потока не нужны.
