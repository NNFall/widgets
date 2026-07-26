# Kaigo: UX-ресерч AI-конструкторов

Дата: 26 июля 2026 года

Статус: продуктовый ресерч, не утверждённый дизайн

## Зачем проводился ресерч

Нужно определить не цвета и декоративный стиль Kaigo, а удобную конструкцию
продукта:

- как человек впервые понимает предложение;
- как вводит сайт и пожелание;
- что видит во время генерации;
- как переходит к редактированию;
- как проверяет результат и публикует его.

Главное ограничение: Kaigo создаёт не полноценный сайт с нуля. Он анализирует
существующий сайт и добавляет на него индивидуального AI-сотрудника. Поэтому
копирование интерфейса обычного AI-site-builder целиком будет ошибкой.

## Короткий вывод

Лучший базовый путь для Kaigo:

> **Публичный лендинг с одним URL-first вводом → сразу созданный проект →
> понятная фоновая генерация → тот же экран постепенно становится редактором →
> проверка → отдельная публикация.**

Главное отличие от Lovable, Base44, Bolt, v0 и Google AI Studio должно быть
видно уже в первом экране: пользователь начинает не с пустой идеи, а со своего
реального сайта. В редакторе справа показывается не абстрактный canvas, а этот
же сайт с работающим Kaigo-виджетом поверх него.

## Что делают основные продукты

| Продукт | Первый экран | После запуска | Сильный паттерн | Что Kaigo не стоит копировать |
|---|---|---|---|---|
| [Lovable](https://lovable.dev/) | Короткий эмоциональный заголовок и большой prompt-композер | Chat слева, live preview справа, история и Publish сверху | Минимум решений до первого действия; прозрачная работа агента; versions и Draft/Published | Абстрактное «создайте что угодно» и визуальные признаки бренда Lovable |
| [Base44](https://base44.com/) | Большой prompt, Build/Plan, примеры типов продукта | Chat + preview; Discuss/Edit; история версий | Бесплатный план до сборки, безопасное обсуждение без изменений, публикация старой версии без уничтожения нового draft | Большой marketplace и сложность полноценной app-платформы |
| [Google AI Studio Build](https://ai.google.dev/gemini-api/docs/aistudio-build-mode) | Prompt, capability chips, voice, Lucky и Gallery | Chat слева, preview/code справа, annotation mode, Share/Publish | Capability chips, стабильная география workspace, аннотации прямо на preview | Слишком много равноправных входов и вдохновляющий loader без честных стадий |
| [Bolt](https://bolt.new/) | Один composer и понятные типы результата | Chat + preview; plan, code и выбор элемента | Самый короткий путь «идея → работающий preview» | IDE и технические инструменты как часть основного опыта новичка |
| [v0](https://v0.app/) | Prompt, примеры и визуальная галерея | Подробный action log, browser screenshots, preview, design mode и версии | Лучшее объяснение действий агента и связь visual edit с версиями | Сырые tool-calls в основном пользовательском слое |
| [Replit Agent](https://docs.replit.com/build/your-first-app) | Prompt, Plan и выбор типа артефакта | Chat + preview; задачи, checkpoints, review, apply, rollback | Лучшая модель длинной фоновой работы и безопасного восстановления | Перегруженный набор артефактов, IDE, задач и deployment-вариантов |
| [Embeddable](https://embeddable.co/) | Prompt, тип результата, Website Reference, примеры и templates | AI-редактор, preview, версии, publish и embed | Самый близкий к Kaigo путь: describe → generate → refine → embed | Нельзя считать саму генерацию виджетов или Website Reference уникальностью Kaigo |
| [Chatbase](https://www.chatbase.co/docs/user-guides/chatbot/deploy), [Elfsight](https://help.elfsight.com/article/190-step-4-create-your-first-widget), [Common Ninja](https://www.commoninja.com/widgets/ai-chatbot) | Настройка источников, поведения и внешнего вида | Preview, embed-код, стандартный редактор | Понятный путь знания → поведение → внешний вид → embed | Типовой чат-пузырь, который слабо связан с визуальным языком сайта |

## Визуальные референсы

Это ссылки для просмотра структуры и состояний интерфейса, а не образцы для
буквального копирования.

1. [Lovable — первый экран](https://lovable.dev/): максимально короткая
   иерархия, крупный prompt и демонстрация результата ниже первого экрана.
2. [Base44 — первый экран](https://base44.com/): composer, Build/Plan и
   продуктовые примеры, выглядывающие снизу.
3. [Bolt — первый экран](https://bolt.new/): один доминирующий ввод и несколько
   понятных типов результата.
4. [Embeddable — первый экран](https://embeddable.co/): наиболее близкий
   конкурент; Website Reference, типы виджетов и prompt-примеры.
5. [Google AI Studio — официальный первый экран](https://storage.googleapis.com/gweb-uniblog-publish-prod/images/Main.width-1200.format-webp.webp).
6. [Google AI Studio — официальный экран ожидания](https://storage.googleapis.com/gweb-uniblog-publish-prod/images/Loading-screen.width-1200.format-webp.webp).
7. [Google AI Studio — официальная галерея](https://storage.googleapis.com/gweb-uniblog-publish-prod/images/Gallery_DJGCHva.width-1200.format-webp.webp).
8. [Google AI Studio — annotation mode](https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/annotation.gif).
9. [v0 — Design Mode](https://api2.v0.dev/docs/design-mode): выбор элемента,
   визуальная правка, before/after и создание новой версии.
10. [Replit — Project Editor](https://docs.replit.com/learn/projects-and-artifacts/project-editor)
    и [Task System](https://docs.replit.com/core-concepts/agent/task-system):
    удачные примеры длинной фоновой работы.
11. [Embeddable — prompt и Website Reference](https://embeddable.co/_next/image?q=75&url=%2Fimages%2Fblog%2Fhow-to-build-chatbot-prompt.png&w=3840)
    и [сгенерированный preview с Publish/Embed](https://embeddable.co/_next/image?q=75&url=%2Fimages%2Fblog%2Fhow-to-build-chatbot-editor.png&w=3840).
12. [Elfsight — Agent Profile](https://stash.elfsightcdn.com/iTUC7kVN-image.png),
    [Knowledge](https://stash.elfsightcdn.com/eGIHComE-know.png) и
    [Theme editor](https://stash.elfsightcdn.com/s0rcqfFv-theme.png).
13. [Common Ninja — URL и инструкции](https://website-assets.commoninja.com/distribution/how-to-add-an-ai-chatbot-to-your-website-step-1-instructions.jpg),
    [behavior и live preview](https://website-assets.commoninja.com/distribution/how-to-add-an-ai-chatbot-to-your-website-step-3-behavior.jpg),
    [проверка разговора](https://website-assets.commoninja.com/distribution/how-to-add-an-ai-chatbot-to-your-website-step-4-test.jpg).

## Устойчивые UX-паттерны

### 1. Один сильный первый ввод

Lovable, Bolt, Base44, v0 и Replit начинают с крупного composer. Это снижает
порог входа, но пустое поле без рамок плохо помогает новичку.

Для Kaigo composer должен быть более предметным:

- обязательный или главный ввод — ссылка на сайт;
- необязательное пожелание обычным языком;
- 3–5 быстрых сценариев: «Консультирует», «Отвечает по услугам», «Собирает
  заявки», «Помогает выбрать», «Квалифицирует клиента»;
- одна основная кнопка;
- редкие параметры скрыты под «Уточнить настройки».

Модель, temperature, thinking, бюджеты проверок и технический движок не должны
быть видны на первом пользовательском экране.

### 2. Генерация сразу становится проектом

После отправки формы должен появляться постоянный URL проекта. Не нужен
отдельный экран «подождите», который исчезает после перезагрузки. Тот же экран
постепенно меняется:

```text
URL + пожелание + пустой preview
              ↓
этапы анализа + частичный preview
              ↓
диалог редактирования + готовый интерактивный preview
```

Это объединяет сильные стороны Lovable, Replit и текущей возобновляемой логики
Kaigo.

### 3. Два уровня статусов

Пользовательский уровень:

- «Изучаю сайт»;
- «Определяю роль AI-сотрудника»;
- «Собираю внешний вид»;
- «Проверяю разговор»;
- «Проверяю desktop и mobile».

Технический уровень открывается по кнопке «Детали» и содержит вызовы моделей,
снимки, тесты и ошибки. Поток tool-calls не должен быть главным интерфейсом.
Нужны Stop, повтор только неудачного этапа и последняя рабочая версия.

### 4. Постоянный workspace после первого результата

Рекомендуемая desktop-компоновка:

- слева: диалог с генератором, человеческие статусы и история пожеланий;
- справа: реальный сайт пользователя с виджетом поверх него;
- сверху: название проекта, сохранение, Desktop/Mobile, история версий и
  Publish;
- технические настройки и код — вторичный слой, не default view.

На мобильном экране нужны переключаемые вкладки «Диалог» и «Preview», а не две
сжатые колонки.

### 5. Версии и публикация — разные вещи

Каждое осмысленное изменение должно создавать revision. История должна
показывать prompt, миниатюру, понятное название изменения и результат проверки.
Нужны bookmark/restore и возможность просмотреть старую версию.

Состояния интерфейса:

`Draft → Проверено → Готово к публикации → Live`

Publish — обычное подтверждаемое действие, а не очередной свободный prompt.
Пользователь должен понимать, что сейчас опубликовано и какие изменения ещё
остаются только в draft.

### 6. Точечные изменения важнее полной регенерации

После первой версии пользователь должен выбрать элемент виджета или область
preview и написать изменение. Хороший ориентир — Annotation Mode AI Studio и
Design Mode v0: выделение → короткая команда → новая revision → Apply/Undo.

Полная регенерация должна быть вторичным и подтверждаемым действием, потому что
она может уничтожить удачные ручные решения.

## Где находится настоящее отличие Kaigo

Embeddable уже предлагает Website Reference, генерацию по prompt, preview,
редактирование и embed. Поэтому слоган «AI создаёт виджеты» недостаточно
отличается.

Защищаемая продуктовая метафора Kaigo:

> **AI-сотрудник уже работает внутри вашего сайта.**

Это должно проявляться не только в тексте:

1. Вход начинается с реального URL.
2. Kaigo показывает, какие признаки сайта он понял: содержание, тон и визуальный
   характер.
3. Preview всегда демонстрирует виджет в контексте исходной страницы.
4. Проверяется не только внешний вид, но и реальный диалог.
5. В будущем сотрудник понимает, на какой странице и рядом с каким блоком
   находится посетитель.

Рыночное окно можно сформулировать ещё точнее: Embeddable хорошо генерирует
индивидуальный интерфейс, а Chatbase, SiteGPT и Elfsight сильнее проработали
knowledge base, persona, actions и production-эксплуатацию. Возможность Kaigo —
соединить **уникальный визуальный frontend и зрелого grounded AI-сотрудника в
одном автоматическом URL-first пути**.

## Зафиксированная конструкция первого экрана

После пользовательского уточнения выбран ещё более простой вариант, чем
исходная рекомендация ресерча:

- почти пустой viewport;
- небольшой логотип и вход в верхней панели;
- один длинный composer строго по центру;
- composer принимает ссылку либо обычное описание;
- одна кнопка немедленного запуска;
- без hero-заголовка, подзаголовка, chips, карточек и preview на первом экране;
- текст, объяснение, примеры и before/after начинаются ниже.

Это делает первый экран не рассказом о Kaigo, а самим продуктовым действием.
Различаться могут палитра, типографика, фон, геометрия composer и motion, но не
иерархия.

Пять первых визуальных направлений сохранены в
`docs/research/assets/2026-07-26-kaigo-landing-references/`.

Риск решения: без заголовка назначение поля должно быть предельно понятно из
placeholder, поведения ввода и следующего состояния после отправки.

## Что обязательно в первой продуктовой версии интерфейса

- URL и необязательное пожелание на первом экране;
- один главный CTA;
- проект создаётся до начала длинной генерации;
- работа продолжается после обновления страницы или ухода пользователя;
- честные этапы вместо фальшивого процента;
- частичный или последний рабочий preview;
- desktop/mobile preview;
- история осмысленных revisions;
- точечная команда изменения;
- явное различие Draft и Live;
- понятная ошибка: что случилось, что сохранено и что повторится.

## Что пока не нужно

- полноценная IDE по умолчанию;
- marketplace из сотен шаблонов;
- drag-and-drop canvas уровня Figma;
- многошаговая анкета до первого результата;
- точный ETA без реальной телеметрии;
- отдельный tablet-редактор;
- командная совместная работа и branching;
- десятки интеграций на первом экране;
- визуальная фиксация конкретной палитры, градиента или стиля до выбора
  референсов.

## Анти-паттерны

1. Пустой prompt без URL, примеров и границ возможностей.
2. Регистрация или тариф до первой видимой ценности.
3. Один красивый spinner на 5–15 минут.
4. Фальшивые мысли агента вместо проверяемых этапов.
5. Сырые технические логи как основной экран.
6. Случайный прыжок из простого лендинга в перегруженную IDE.
7. Кнопка «Сгенерировать заново» как основной способ редактирования.
8. Mobile toggle, который только сужает iframe, но не проверяет интерактивность.
9. Смешивание Share, Preview и Publish.
10. Буквальное копирование чужой геометрии, иллюстраций, текста и brand style.

## Источники

Основные официальные материалы:

- [Google AI Studio Build mode](https://ai.google.dev/gemini-api/docs/aistudio-build-mode)
- [Google AI Studio deployment](https://ai.google.dev/gemini-api/docs/aistudio-deploying)
- [Lovable editor](https://docs.lovable.dev/features/projects/editor)
- [Lovable chat and activity](https://docs.lovable.dev/features/projects/chat)
- [Lovable history](https://docs.lovable.dev/features/projects/history)
- [Base44 quick start](https://docs.base44.com/Getting-Started/Quick-start-guide)
- [Base44 AI chat modes](https://docs.base44.com/Building-your-app/AI-chat-modes)
- [Bolt quick start](https://support.bolt.new/building/quickstart)
- [Bolt plan mode](https://support.bolt.new/best-practices/plan-mode)
- [v0 agentic features](https://api2.v0.dev/docs/agentic-features)
- [v0 versions](https://api2.v0.dev/docs/versions)
- [Replit task system](https://docs.replit.com/core-concepts/agent/task-system)
- [Replit checkpoints](https://docs.replit.com/references/version-control/checkpoints-and-rollbacks)
- [Embeddable: what is an AI widget builder](https://embeddable.co/blog/what-is-an-ai-widget-builder)
- [Embeddable widgets API](https://embeddable.co/docs/developer-api/widgets)
- [Elfsight AI Chatbot](https://elfsight.com/ai-chatbot-widget/)
- [Chatbase quick start](https://www.chatbase.co/docs/user-guides/quick-start/your-first-agent)
- [SiteGPT quick start](https://sitegpt.ai/docs/setup/quickstart)
- [Common Ninja AI Chatbot setup](https://help.commoninja.com/hc/en-us/articles/25895477798941-Getting-Started-Setting-Up-Your-AI-Chatbot-for-the-First-Time)
- [NN/G: progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/)
- [NN/G: progress indicators](https://www.nngroup.com/articles/progress-indicators/)
