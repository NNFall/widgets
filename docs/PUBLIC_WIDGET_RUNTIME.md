# Публичный runtime виджета

Публичный loader создаёт sandboxed iframe без `allow-same-origin`. Внутренний
trusted runtime Kaigo отображает принятые HTML/CSS артефакта, но намеренно не
исполняет сгенерированный JavaScript. Открытие, закрытие, чат и изменение
геометрии принадлежат фиксированному runtime.

## Защита visitor chat

При загрузке `/runtime/{stable_key}` сервер проверяет origin страницы по
`Referer` и allowlist публикации. Для разрешённого origin в runtime встраивается
HMAC-capability сроком на пять минут. Capability связан со stable key, конкретным
immutable release, revision и нормализованным origin. `/runtime/{key}/chat`
принимает только запрос из sandbox (`Origin: null`) с действующим capability.

Дополнительно действуют независимые скользящие лимиты на публикацию и IP, а
общий chat service ограничивает сессию, client scope, число одновременных
запросов и хранит идемпотентные ответы. В production обязательно задать один и
тот же `KAIGO_PUBLICATION_CHAT_SIGNING_SECRET` длиной не менее 32 байт на всех
workers. Параметры:

- `KAIGO_PUBLICATION_CHAT_CAPABILITY_TTL_SECONDS` — 30–3600, по умолчанию 300;
- `KAIGO_PUBLICATION_CHAT_KEY_RATE_LIMIT_REQUESTS` — лимит публикации в общем
  окне `KAIGO_CHAT_RATE_LIMIT_WINDOW_SECONDS`;
- `KAIGO_PUBLICATION_CHAT_IP_RATE_LIMIT_REQUESTS` — лимит удалённого IP в том же
  окне;
- `KAIGO_PUBLICATION_CHAT_TRUSTED_PROXY_CIDRS` — список CIDR доверенных reverse
  proxies через запятую. Только от этих peer-адресов принимается
  `X-Forwarded-For`; произвольный клиент не может подставить этот заголовок.

Если production-приложение видит private/loopback peer, но доверенные proxy CIDR
не настроены, IP-bucket отключается, чтобы один адрес nginx не заблокировал всех
посетителей. Лимиты публикации и сессии продолжают действовать. Для текущего
nginx следует явно указать его peer CIDR и проверить реальный client IP.

## Остаточный риск

Публичный виджет принципиально доступен посетителям сайта. Серверный клиент
может подделать `Referer`, получить runtime с capability и воспроизвести обычный
поток посетителя. Capability предотвращает слепую подстановку домена и
долгоживущий replay, но не является аутентификацией конечного посетителя.
Текущие лимиты находятся в памяти процесса; при нескольких workers production
gateway должен добавить общий IP/key rate limit (или лимитер следует перенести
в Redis). Метрики расходов и аномалий по stable key остаются обязательным
операционным контролем.
