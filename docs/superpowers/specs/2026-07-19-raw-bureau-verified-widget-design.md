# RAW BUREAU: проверяемый AI-виджет Kaigo

Дата: 2026-07-19  
Статус: утверждено пользователем выбором RAW BUREAU и командой приступить

## 1. Цель текущего этапа

Собрать первый честный пример Kaigo для сайта RAW BUREAU и одновременно
добавить в Builder Lab недостающую доказательную петлю:

1. нейросеть получает содержательный и визуальный контекст сайта;
2. несколько независимых направлений критикуются, отдельный судья выбирает одно;
3. Gemini генерирует безопасный HTML/CSS-артефакт;
4. доверенный runtime, а не сгенерированный код, выполняет реальный чат;
5. браузер нажимает элементы, проводит два хода, измеряет layout и делает снимки;
6. Gemini получает снимки как изображения, возвращает структурированные дефекты;
7. не более двух визуальных repair-циклов исправляют финальный артефакт;
8. итог разворачивается на `kaigo.space`, а desktop/mobile и реальный Gemini-чат
   повторно проверяются во встроенном браузере Codex.

Это не попытка сразу построить весь SaaS. Текущий вертикальный срез должен
доказать качество генерации, реальность общения и пригодность контура проверки.

## 2. Источник и факты бренда

Первый источник: `https://rawbureau.ru/`.

Наблюдения из видимой страницы во встроенном браузере:

- фон `rgb(245, 245, 245)`, основной цвет — чёрный;
- основные заголовки используют `Inter, Arial, sans-serif`;
- первый заголовок `RAW BUREAU` имеет размер около 232 px, вес 900 и плотный
  отрицательный tracking;
- остальная система строится на малой служебной типографике, крупных заголовках,
  тонких линиях, больших паузах и тёплых интерьерных фотографиях;
- студия проектирует квартиры и загородные дома, работает в Москве и по России;
- визуальный язык плоский, редакционный и почти монохромный.

Виджет не имеет права выдумывать услуги, цены, сроки, географию, действия или
статусы. Ответы должны опираться только на зафиксированный публичный контекст.
Если факта нет, AI прямо говорит, что требуется уточнение у бюро.

### 2.1 Visual Site Research Agent

Один `fullPage` screenshot не считается анализом сайта. Сайты используют
lazy-loaded изображения, scroll reveal, sticky-секции, client-side rendering и
анимации, поэтому reference collector является отдельным bounded browser-agent.

Основа, которую не нужно писать заново:

- [Crawlee for Python](https://github.com/apify/crawlee-python) — очередь URL,
  retries, sessions, robots, link discovery и `PlaywrightCrawler` с
  `infinite_scroll`; проект развивается и подходит существующему Python-стеку;
- [Playwright](https://playwright.dev/docs/screenshots) — точные viewport,
  screenshot, browser events, computed DOM и воспроизводимый Chromium;
- [axe-core](https://github.com/dequelabs/axe-core) — дополнительный
  deterministic accessibility scan;
- [Stagehand](https://github.com/browserbase/stagehand) или
  [Browser Use](https://github.com/browser-use/browser-use) — только opt-in
  fallback для незнакомых cookie/reveal/accordion interactions, а не основной
  screenshot oracle;
- [BackstopJS](https://github.com/garris/BackstopJS) — полезен позже для visual
  regression уже опубликованных версий, но не заменяет brand research;
- [SiteOne Crawler](https://github.com/janreges/siteone-crawler) — возможный
  companion для SEO/security/site-wide отчётов, но не источник visual grammar.

Firecrawl умеет wait/scroll/screenshot и полезен для быстрого text/markdown
контекста, однако его core AGPL-3.0 и отдельная инфраструктура не нужны первому
Kaigo slice. Для управляемости и изоляции выбирается self-hosted
`Crawlee Python + Playwright`.

Обязательный deterministic lifecycle одной страницы:

1. проверить URL, DNS и каждый redirect; разрешены только публичные `http/https`
   адреса и порты 80/443;
2. открыть страницу и дождаться `domcontentloaded`, затем `load` в пределах
   timeout, `document.fonts.ready` и загрузки видимых изображений;
3. выдержать configurable post-load warm-up, по умолчанию 5 секунд;
4. прокручивать вниз шагами около 70% viewport с configurable паузой через
   реальные wheel/touch-equivalent browser actions, а не одним `scrollTo`,
   по умолчанию 750 ms (допустимый профиль 600–1200 ms), чтобы сработали
   lazy-load и reveal; остановиться после двух неизменившихся
   `scrollHeight` или по жёсткому лимиту steps/time/height;
5. обнаружить фактический scrolling element, вложенные overflow-контейнеры и
   virtual-scroll через transform; если `window.scrollY` расходится с видимым
   содержимым, продолжить проход по реальному контейнеру и пометить стратегию в
   evidence;
6. по пути зафиксировать top/middle/bottom viewport tiles и наблюдаемые
   animation/transition properties; stitched `fullPage` остаётся только
   best-effort приложением и никогда не является screenshot oracle;
7. перед эталонным top tile вернуть реальный scrolling element наверх; если
   custom/virtual scroll не сбрасывается надёжно, перезагрузить страницу, снова
   выполнить warm-up и отдельно зафиксировать reset strategy; дождаться 1.5
   секунды стабильного layout; для reproducible QA finite CSS motion
   fast-forward-ится, но только после scroll warm-up;
8. извлечь не весь DOM, а bounded semantic/style sample: headings, body, nav,
   links/buttons, surfaces, borders, radii, shadows, spacing, dominant colors,
   fonts, images/aspect ratios и fixed/sticky controls;
9. сохранить screenshot hashes, console/network failures, final URL, timings и
   причины пропуска элементов; Playwright trace хранить только для failed run с
   коротким TTL.

На MVP crawler выбирает не более пяти ключевых страниц через sitemap и видимую
навигацию: главная, услуги/цены, портфолио/каталог, FAQ и контакты. Desktop
снимается для всех, mobile — минимум для главной и одной содержательной страницы.
Длинные страницы режутся на viewport tiles: отправлять Gemini один сверхдлинный
PNG нельзя, потому что мелкие детали станут неразборчивыми.

Безопасность обязательна: robots-by-default, понятный user-agent, concurrency 1
на домен, page/byte/time limits, блокировка private/link-local/metadata IP,
повторная DNS-проверка после redirect, отсутствие пользовательских cookies и
запрет произвольных кликов/форм. Agent fallback получает allowlist безопасных
действий (`scroll`, `dismiss`, `expand`) и никогда не логинится, не отправляет
формы и не обходит CAPTCHA.

Firecrawl Branding Format v2 отдельно прогоняется как benchmark над RAW BUREAU.
Сравниваются logo/color/typography/spacing/component tokens, но cloud-ответ не
становится единственным source of truth и не входит в обязательный runtime.

## 3. Выбранное направление

Независимый судья выбрал направление **«плавающая проектная заметка»**.

Виджет воспринимается как небольшой редакционный блок RAW BUREAU, а не как
обычный мессенджер. Он использует острые углы, одну внешнюю рамку, тонкие линии,
чёрно-белую палитру, крупную типографику и служебные метки. Запрещены пузыри,
аватары, карточки внутри карточки, стекло, glow, градиенты, HUD, координаты,
фиктивная телеметрия и «AI-магия» как декор.

### 3.1 Размеры и состояния

Состояние по умолчанию — `closed`.

Desktop (`width >= 900px`):

- закрытый launcher: `216×46px`, отступы справа и снизу `20px`;
- первый open: ширина `372px`, высота `304px`;
- затем высота растёт по содержимому до `min(536px, 68dvh)`;
- при `1440×900` размер не превышает `372×536px` и 30% площади viewport.

Mobile/tablet (`width < 900px`):

- боковые и нижний отступы `12px` с учётом `safe-area`;
- ширина `calc(100vw - 24px)`;
- при `390×844` первый open — `320px` высотой;
- предел — `min(70dvh, calc(100dvh - 24px))`;
- fullscreen, backdrop и блокирующее страницу модальное поведение запрещены.

Первое открытие полностью помещается без overflow. Scroll появляется только у
истории после достижения предельной высоты. Native scrollbar визуально скрыт,
но wheel, touch, Page Up/Down, Home/End и screen reader остаются рабочими.
Edge fade и единственная команда `РАНЕЕ`/`К ПОСЛЕДНЕМУ` показываются только при
реальном скрытом содержимом. Декоративный custom-scrollbar запрещён.

### 3.2 Состав интерфейса

- Launcher: `AI-КОНСУЛЬТАНТ / RAW BUREAU` и понятный признак открытия.
- Header: `RAW BUREAU / AI`, текстовая кнопка `ЗАКРЫТЬ`.
- Transcript: редакционные блоки `RAW AI` и `ВЫ`, разделённые пространством или
  hairline, без bubble-формы.
- First open: короткое приветствие до 220 символов и не более двух честных
  suggested prompts.
- Suggestions: нумерованные текстовые строки; выбранная строка отправляет именно
  этот вопрос реальному AI, а не только копирует текст в поле.
- Composer: семантический auto-growing `textarea` от одной до четырёх строк,
  визуально без стандартной рамки, resize-handle и inset-эффектов; остаётся label,
  собственный focus state и текстовая команда `ОТПРАВИТЬ`.
- Enter отправляет, Shift+Enter добавляет строку, IME composition не вызывает
  случайную отправку.
- Loading: один редакционный status-row и конечное движение линии, без spinner,
  bouncing dots или бесконечной анимации.
- Error: inline-состояние и настоящая кнопка `ПОВТОРИТЬ`; текст пользователя не
  удаляется и duplicate send не создаётся.

Одновременно видимы не более трёх действий. Все интерактивные области не меньше
`44×44px`.

### 3.3 Поведение диалога

Обязательный сценарий:

1. пользователь открывает виджет;
2. отправляет первый вопрос;
3. немедленно появляется ровно один user turn и `aria-busy=true`;
4. реальный Gemini-ответ добавляет ровно один assistant turn;
5. пользователь отправляет уточнение;
6. второй ответ учитывает первый ход;
7. после закрытия/открытия transcript, draft и session сохраняются;
8. ошибка сети не изображается как успех.

История имеет `role=log`, `aria-live=polite`. Escape закрывает виджет и возвращает
focus launcher-у. Открытие фокусирует composer только на desktop; на touch-устройстве
оно не должно самопроизвольно поднимать клавиатуру. Motion использует только
opacity/transform, длится 150–250 ms и отключается через `prefers-reduced-motion`.

## 4. Граница доверия

Gemini возвращает только `body_html`, `css`, токены и текстовую art-direction.
Сгенерированному артефакту запрещены JavaScript, внешние URL, `form`, iframe и
сеть. Его CSP сохраняет `connect-src 'none'`.

Весь функционал выполняет версионированный доверенный runtime Kaigo:

- open/close, focus и keyboard;
- безопасная вставка transcript только через `textContent`;
- pending/error/retry;
- autosize и bounded widget resize;
- вызов серверного chat endpoint;
- сохранение одной server-side conversation;
- history и session continuity.

Preview iframe передаёт parent-среде только сообщения протокола версии 2 с
`channel_id`, `request_id`, `revision` и ограниченным текстом. Parent проверяет
`event.source`, версию, channel, request, revision и длину. API-ключ никогда не
попадает в браузер.

## 5. Контур независимых предложений

Перед стадиями HTML/CSS формируются три независимых направления:

1. brand archaeologist — извлекает визуальную грамматику сайта;
2. interaction inventor — предлагает небанальную, но выполнимую механику;
3. hostile conversion/accessibility critic — ищет ложные действия, крупные размеры,
   generic chat UI и барьеры.

Отдельный judge получает направления без сведений об их авторах и выбирает одно
по фиксированной матрице: site fit, второстепенность к странице, функциональная
правдивость, responsive integrity, accessibility и реализуемость runtime.

Для первого RAW BUREAU среза это решение уже принято: «плавающая проектная
заметка». В коде текущего этапа фиксируются контракты и возможность подключить
эту bounded-схему; параллельная генерация не должна превращаться в бесконечный
agent loop.

## 6. Браузерная доказательная петля

Полный visual gate запускается только для финальной стадии `motion_polish`, после
детерминированного validator и до commit финальной revision.

Pinned Chromium, DPR 1:

- desktop `1440×900`;
- mobile `390×844`.

Шесть обязательных screenshot states:

- `desktop.closed`;
- `desktop.open_initial`;
- `desktop.after_turn_2`;
- `mobile.closed`;
- `mobile.open_initial`;
- `mobile.after_turn_2`.

Для layout также измеряется состояние после первого хода. Для каждого состояния
сохраняются ограниченные metrics:

- rect обязательных `data-region`;
- `clientWidth/scrollWidth`, `clientHeight/scrollHeight`;
- видимость, clipping, overlap, horizontal overflow;
- focus, aria-состояния, count/order ролей transcript;
- console/page/request failures;
- SHA-256 изображения.

Детерминированные release gates:

- panel полностью внутри viewport;
- horizontal overflow <= 1px;
- first-open transcript не scrollable;
- launcher, close, send, retry и actions >= 44×44px;
- composer не перекрывает сообщения;
- закрытие/открытие сохраняет оба хода;
- ноль pageerror/console error/unexpected network;
- mobile не fullscreen и не выше 70dvh.

## 7. Gemini visual critic

Используется официальный `generateContent` через `google-genai` и
`gemini-3.5-flash`. Screenshot передаются как реальные image parts:

```python
types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
```

Arbitrary URL не передаётся в `generateContent` как изображение. Приложение само
получает или снимает изображение, проверяет тип/размер и отправляет bytes inline.
Для шести временных JPEG Files API не нужен. Общий payload ограничен 8 MB, каждый
снимок — 1.5 MB; это ниже 20 MB inline-limit.

Critic получает brief, выбранную art-direction, шесть подписанных image parts и
compact layout metrics. Ответ — строгий JSON со следующими полями:

- `verdict: pass|repair`;
- `summary`;
- до 12 findings;
- для finding: severity, category, screenshot/state, evidence, нормализованный
  region, artifact fields, repair instruction и confidence.

Модель обязана в summary перечислить конкретные уникальные визуальные маркеры из
каждого state и сослаться на соответствующий screenshot id. Это вместе с
контрольным seed-маркером на одном тестовом изображении доказывает, что она
получила изображения, а не только текстовые metrics. Синтаксическая structured
output не заменяет прикладную семантическую валидацию.

Repair trigger: blocker или major с confidence >= 0.75. Minor сохраняется в
отчёте, но не запускает repair.

Жёсткие пределы:

- максимум 3 critic calls: initial + после двух repairs;
- максимум 2 visual repair calls;
- repeated fingerprint останавливает цикл;
- любой repair снова проходит deterministic validator и browser gate;
- после исчерпания — `visual_quality_failed`, а не ложный `completed`;
- synthetic two-turn transcript создаётся один раз и переиспользуется при
  повторных снимках, чтобы repair не умножал chat cost.

## 8. Реальный чат и визуальный oracle

Browser visual loop использует deterministic fixture replies, потому что геометрия
не должна зависеть от случайной формулировки модели. Отдельный release smoke
обязательно проводит два реальных Gemini-хода с одной session и проверяет:

- два успешных сетевых ответа;
- непустые различающиеся assistant messages;
- сохранённый первый ход;
- loading/error recovery;
- server-side request/log correlation;
- отсутствие fake `setTimeout`/keyword ответов.

Только совместное прохождение deterministic browser gate, Gemini image critic и
real Gemini chat smoke считается доказательством.

## 9. Публикация и доказательства

Итоговый срез публикуется отдельным RAW BUREAU widget route на `kaigo.space`.
Старый `/builder-demo/` нельзя называть рабочим AI-виджетом, пока его Send только
очищает поле; либо он получает trusted real-chat bridge, либо явно маркируется как
visual preview и ведёт на настоящий widget route.

Перед отчётом пользователю обязательно:

1. тесты и lint/compile;
2. реальный Gemini generation;
3. real two-turn chat;
4. desktop/mobile проверка через `browser:control-in-app-browser`;
5. visible screenshots и Gemini critic report;
6. deploy, nginx/container health и live URL;
7. commit/push GitHub;
8. отсутствие регрессий `kaigo.space/`, `/w/demka` и соседних сервисов.

## 10. Явные критерии отказа

Результат отклоняется при любом из условий:

- desktop шире 420px, выше 30% viewport area или фиксированный 740/760/100vh;
- mobile fullscreen либо full-height rail;
- visible native scrollbar, decorative scrollbar или overflow на первом open;
- пузырьки, аватары, тяжёлая тень, большие radius, boxed textarea;
- latest-only/folio paging, скрывающее последовательность разговора;
- fake response, fake status, пустая кнопка или действие, только очищающее input;
- более трёх одновременных actions или hit target меньше 44px;
- потеря первого хода, draft или session после close/open;
- выдуманные RAW BUREAU факты или функции;
- screenshot без полного surrounding viewport, скрывающий collision/размер;
- заявление «Gemini проанализировал изображения» без реального image-part request
  и структурированного отчёта с image-specific evidence.
