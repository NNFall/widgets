# Kaigo Agent Kernel и Pattern Registry — утверждённая архитектура

**Дата:** 2026-07-29  
**Статус:** утверждено пользователем  
**Ветка:** `codex/saas-foundation`

## 1. Цель

Превратить уже существующий Builder Lab из набора специализированных стадий в
формализованный `Kaigo Agent Kernel`, не переписывая работающую durable-очередь,
визуальный комитет, проверки и provider router.

Главное новое преимущество — версионируемая библиотека проверенных частей
виджета. Модель выбирает подходящую композицию, настраивает разрешённые параметры
и собирает индивидуальный результат, но не переписывает внутреннюю механику
проверенных паттернов.

## 2. Что сохраняется

- пять стадий `art_direction -> foundation -> identity -> conversation -> motion_polish`;
- три независимых предложения направления и blind judge;
- три визуальных критика и независимый visual judge;
- локальные HTML/CSS/JS, browser и accessibility проверки;
- PostgreSQL-backed worker с lease, heartbeat и checkpoint;
- provider-agnostic model router, usage и cost ledger;
- последняя принятая ревизия даже при исчерпании repair-цикла.

Новый слой расширяет эту систему, а не заменяет её LangGraph, OpenHands или
другим оркестратором.

## 3. Канонический конвейер

```text
URL + пожелание
  -> reference evidence
  -> три visual direction
  -> direction judge
  -> composition planner
  -> deterministic pattern resolver
  -> staged generation
  -> deterministic validation
  -> visual committee
  -> targeted repair
  -> preview / publish / embed
```

`composition planner` запускается после выбора visual direction и до
`foundation`. Он получает только компактные manifest-описания активных
паттернов. Полный код передаётся генератору только для выбранных версий.

## 4. Pattern Registry

Начальные категории:

- `launcher` — закрытое состояние и привлечение внимания;
- `shell` — геометрия и каркас открытого чата;
- `messages` — сообщения AI и посетителя;
- `composer` — поле ввода и отправка;
- `motion` — открытие, закрытие и idle motion.

Каждая версия паттерна имеет:

- стабильные `pattern_id`, `category`, `version`;
- статус `draft | active | deprecated`;
- совместимые слоты и несовместимости;
- JSON Schema разрешённых параметров;
- короткое описание для planner;
- HTML/CSS/JS implementation assets;
- human-readable evidence и screenshot reference;
- обязательные contract tests;
- SHA-256 implementation hash.

Source of truth для implementation assets — Git. PostgreSQL хранит каталог
доступных версий, выбранные composition plans и outcomes. Это сохраняет code
review, воспроизводимость и rollback, но позволяет включать и исключать паттерны
без изменения исторических запусков.

## 5. Первая библиотека

Первая версия содержит не менее десяти небольших элементов:

- launcher: `orb-pulse`, `peek-tab`;
- shell: `compact-chat`, `floating-card`;
- messages: `paired-bubbles`, `advisor-cards`;
- composer: `single-line-pill`, `multiline-soft`;
- motion: `spring-reveal`, `soft-scale`.

Это не десять готовых одинаковых виджетов. Цвета, типографика, тексты,
скорость, радиусы и безопасные диапазоны motion остаются параметрами выбранной
композиции.

## 6. Composition Plan

Planner возвращает строго типизированный объект:

```json
{
  "schema_version": 1,
  "direction_id": "candidate-2",
  "selections": [
    {
      "slot": "launcher",
      "pattern_id": "orb-pulse",
      "version": 1,
      "parameters": {"attention_delay_ms": 15000},
      "reason": "Поддерживает мягкую динамику выбранного направления"
    }
  ],
  "custom_escape": null,
  "summary": "Компактный брендовый консультант"
}
```

Локальная валидация отклоняет:

- неизвестную или неактивную версию;
- отсутствие обязательной категории;
- несовместимые слоты;
- параметры вне JSON Schema;
- повтор одного слота;
- произвольный код внутри ответа planner.

Resolver загружает выбранные assets, проверяет их hashes и формирует bounded
prompt bundle.

## 7. Контролируемый custom escape

Если библиотека объективно не покрывает выбранное направление, planner может
вернуть `custom_escape` с категорией и короткой причиной. Он не содержит код.
Генератор может создать custom-реализацию только для указанного слота.

Custom-слот:

- проходит все обычные проверки;
- явно помечается в provenance;
- не становится паттерном автоматически;
- может быть вручную отобран и добавлен в библиотеку новой версией.

## 8. PostgreSQL

Добавляются таблицы:

- `widget_pattern_versions` — immutable manifest snapshot и hash;
- `composition_plans` — run, artifact/direction, schema version, summary,
  custom escape, planner model call;
- `composition_plan_items` — slot, exact pattern version, parameters, reason;
- `pattern_outcomes` — validation/visual result, repairs, tokens, cost, latency,
  publication and adoption signals.

Исторический plan всегда ссылается на точную версию. Удаление версии запрещено;
вместо этого используется `deprecated`.

## 9. Наблюдаемость и продуктовый moat

Для каждой выбранной версии фиксируются:

- технический pass/fail;
- visual committee score;
- число repair-циклов;
- input/output/thinking tokens;
- стоимость и latency;
- публикация, rollback и последующая доработка.

На этой статистике позднее строится ranking паттернов. Ценность Kaigo возникает
не из названия фреймворка, а из накопленной библиотеки, воспроизводимых проверок
и данных о том, какие композиции реально проходят QA и используются клиентами.

## 10. Граница memory layer

В первой версии temporal knowledge graph не вводится. Для pattern selection
достаточно нормализованного PostgreSQL. `pgvector` допускается позднее для
семантического поиска по большим каталогам, knowledge сайта и успешным кейсам.

Mem0, Graphiti или Zep рассматриваются только для будущей памяти AI-сотрудника,
когда появятся долгоживущие изменяемые отношения между клиентами, заказами,
событиями и действиями.

## 11. Сравнение моделей

GLM-5.2 и GPT-5.5 сравниваются на одном frozen evidence bundle, одном выбранном
direction и одном composition plan. Новый crawl между сравниваемыми запусками
не выполняется.

Отчёт содержит provider/model, prompt versions, tokens, стоимость USD/RUB,
latency, retries, repair cycles, validator result, visual score и publishability.
Секреты и полный приватный prompt в публичный отчёт не попадают.

## 12. Проверка и выпуск

- Контрактные и интеграционные тесты выполняются локально и на PostgreSQL 15.
- Автоматические regression E2E могут использовать проектный Playwright.
- Ручная финальная приёмка выполняется только через встроенный Codex Browser.
- Реальные Google/Яндекс OAuth и YooKassa-платежи не считаются проверенными без
  пользовательских credentials и отдельного пользовательского прогона.
- SaaS-файлы коммитятся точечно; Telegram bridge, drafts и published log не
  входят в этот release.

## 13. Критерии готовности первой итерации

1. PostgreSQL 15 migration/upgrade/downgrade и unified backend suite проходят.
2. Registry загружает только валидные immutable manifests.
3. Planner выдаёт и валидирует plan без произвольного кода.
4. Worker сохраняет plan до первого model generation dispatch и восстанавливает
   его после restart без повторного выбора.
5. Generator получает только выбранные implementations.
6. Outcome записывает качество, стоимость и provenance.
7. Одинаковый benchmark GLM-5.2/GPT-5.5 формирует сравнимый русский отчёт.
8. Production-кандидат проверен через встроенный Browser и может быть безопасно
   откатан к предыдущему release.
