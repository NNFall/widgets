# Kaigo contact and legal experience design

Date: 2026-08-15
Status: implementation contract for the isolated frontend preview

## Outcome

Kaigo must give an early customer a clear, human route to ask a question, report a bug, suggest an improvement, or discuss cooperation from both the landing page and Studio. The current frontend has no feedback backend, so the first release must remain truthful: it prepares an email in the customer's mail application and never claims that Kaigo has received anything automatically.

The same release adds readable Russian-language legal pages and a real footer information architecture. Unknown owner details, INN, postal address, Telegram, document dates, and production mailbox status must remain visibly marked placeholders until the owner and legal reviewer supply them. No personal or business requisites may be invented.

## Product principles

- Keep the established Living Fold identity and the white, cold metallic graphite, and Kaigo orange palette.
- Use ordinary Russian language, not legal or technical jargon in action labels.
- Prefer one clear contact surface over floating widgets and duplicated popups.
- Keep every action truthful. A `mailto:` link says it opens the mail application and sends nothing until the user confirms there.
- Make support reachable even when Studio authentication fails.
- Preserve 44 px touch targets, keyboard operation, visible focus, reduced motion, mobile reading order, and no horizontal page overflow.
- Do not add fake Telegram links, fake success messages, or fake operator details.

## Landing experience

Add a dedicated `#contact` section near the bottom of the landing page, before the final conversion section. It is a calm full-width editorial band rather than a grid of nested cards.

The section contains:

1. A human promise: questions and ideas are read by the Kaigo team.
2. Four mutually exclusive topics: question, bug, improvement, cooperation.
3. A message field with useful placeholder copy.
4. A separate personal-data consent checkbox with a link to `/personal-data-consent/`.
5. A mail action disabled until both a message and consent are present.
6. The visible support address and an explicit note about how `mailto:` works.
7. A Telegram row marked “Добавим после подтверждения контакта”, without an anchor.

Replace the FAQ's dead secondary hash link with `#contact`. The footer becomes a reusable component with product navigation, contact access, legal links, and a compact operator block. Placeholder requisites are explicitly marked as preview data.

## Studio experience

Do not add a fifth icon to the project header. Add “Помощь и обратная связь” inside the existing Account drawer and open a dedicated Contact drawer using the established drawer state machine.

The Contact drawer reuses the same topics and transparent email behavior. It can include Studio context in the prepared message, but it must not expose secrets or silently submit data. The authentication error state also exposes the support email so a blocked customer can still ask for help.

## Contact configuration

Centralize public contact values and mail composition in one shared module:

- provisional support address: `support@kaigo.space`;
- founder Telegram: `null` until the owner provides the exact public URL;
- subject prefixes and topic labels;
- URL-safe mail body generation;
- current consent document version.

The address is a frontend preview assumption, not proof that the mailbox exists. Production release is blocked until the owner confirms that it receives mail.

## Legal routes

Add exact SPA routes with and without a trailing slash:

- `/privacy/` — personal-data processing and privacy policy;
- `/personal-data-consent/` — separate consent text;
- `/terms/` — service use terms;
- `/offer/` — preliminary public-offer template for paid service activation.

Each page uses one shared legal layout: compact Living Fold header, back link, readable 65–72 character text column, revision line, contents navigation, contact block, shared footer, and a prominent “Тестовая редакция” notice.

The copy must distinguish what the frontend currently does from what production will do. It must not claim completed Roskomnadzor notification, Russian data localization, payment acceptance, receipt delivery, a retention period, or a production operator identity until the backend owner verifies those facts.

## Legal content scope

### Privacy policy

Describe likely categories only: contact details supplied by the user, request text, account/project identifiers when applicable, and technical/security logs. Explain purposes, processing actions, possible processors, security approach, user rights, withdrawal/deletion channel, and unknown operator fields. Mark backend data-flow and retention details for confirmation.

### Separate consent

Keep consent separate from other documents. State the placeholder operator, listed data, purposes, actions, duration rule, and withdrawal channel. The feedback control references this document and records no fake server-side consent in the mail-only release.

### Terms

Cover free preview, account use, customer-provided content, AI limitations, acceptable use, intellectual property, availability, publication responsibility, and support.

### Preliminary offer

Explain that free generation is not paid acceptance; a paid agreement can be accepted only where the actual price, service scope, term, cancellation/refund rules, and operator requisites are shown before payment. State that an NPD receipt must accompany a paid service once backend billing is connected. Keep all unknown requisites as blockers.

## Backend handoff

Document a future `POST /api/feedback` contract. It should accept category, name/contact, message, page, safe Studio context, consent document version, and consent timestamp. The server must validate, rate-limit, protect against CSRF/spam, create a receipt identifier, and deliver to email/Telegram without exposing tokens in the frontend.

Before enabling production submission, the backend owner must confirm:

- operator identity, INN, address, and support contacts;
- mailbox and Telegram destinations;
- personal-data notification status and applicable exceptions;
- Russian database location and processor list;
- retention/deletion periods;
- payment provider, prices, refunds, cancellation, receipts, and offer acceptance evidence;
- separate consent versioning and proof;
- legal review of all four documents.

Only after a real endpoint exists may the UI show progress, success, retry, or delivery identifiers.

## Deployment and preview

Production nginx keeps narrow route ownership, so add exact legal route blocks rather than a broad SPA fallback. Extend the isolated frontend-history transformer so legal pages work under `/frontend/v11/<route>/`. The archive remains noindex and uses the isolated preview API prefix.

No production deployment, service restart, nginx reload, migration, or production checkout change is authorized by this work.

## Verification

- Unit tests for mail composition, consent gating, routes, legal copy, footer links, Studio drawer, and authentication error support.
- Deployment tests for exact nginx route blocks.
- Archive transformer tests for legal wrappers and runtime paths.
- Playwright at desktop plus 320, 360, 390, and 430 px for reading order, touch targets, no overflow, keyboard navigation, and axe-critical accessibility.
- Full typecheck, lint, build, unit suite, deployment suite, and isolated-preview smoke checks.

