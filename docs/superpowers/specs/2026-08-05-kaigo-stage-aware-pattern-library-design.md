# Kaigo Stage-Aware Pattern Library — согласованная архитектура

**Дата:** 2026-08-05  
**Статус:** согласовано в диалоге, ожидает проверки письменной спецификации  
**Ветка:** `codex/saas-foundation`

## 1. Цель

Развить существующий Kaigo Pattern Registry из каталога пяти крупных слотов в
версионируемую библиотеку небольших UI- и motion-паттернов. Отдельная модель
выбирает для каждой релевантной категории от двух до пяти кандидатов, видя не
только название, но и полное поле `ai_description`. Генерирующая модель получает
код только выбранных кандидатов и только на том этапе, где эти паттерны нужны.

Паттерн является проверенной основой и источником идей, а не неизменяемым
готовым виджетом. Генератор вправе выбрать один вариант, объединить несколько
или заметно адаптировать их под сайт, сохраняя минимальный технический контракт
рабочего чата.

`pgvector`, отдельная embedding-модель, Qdrant, Redis и temporal knowledge graph
в эту итерацию не входят. До существенного роста каталога кандидатов выбирает
сама LLM по полным текстовым описаниям.

## 2. Границы первой технической итерации

Входит:

- расширяемая таксономия небольших паттернов;
- обязательное полное `ai_description` в manifest каждого нового паттерна;
- schema v2 для shortlist из 2–5 кандидатов на категорию;
- выбор кандидатов одной моделью после blind-judge visual direction;
- сохранение shortlist и причин выбора в PostgreSQL;
- stage-aware загрузка кода только релевантных кандидатов;
- provenance того, какие кандидаты были показаны модели и какие она заявила
  как использованные;
- простая admin-only Pattern Lab для просмотра описания, manifest и
  sandboxed-preview;
- технические и контрактные тесты;
- совместимость с историческими composition plans и текущими генерациями.

Не входит:

- наполнение каждой категории двадцатью художественно завершёнными эффектами;
- визуальный редизайн Studio или публичного лендинга;
- автоматический crawl чужих виджетов;
- semantic/vector search;
- автоматическое ранжирование по outcome;
- полноценное визуальное одобрение всех паттернов в рамках backend-итерации.

Технический контур должен позволить отдельной визуальной задаче безопасно
добавлять и дорабатывать паттерны без изменений selector, worker и БД.

## 3. Таксономия

Первая версия поддерживает следующие категории:

1. `launcher_shape` — форма и содержимое закрытого launcher;
2. `launcher_idle` — спокойное зацикленное движение launcher;
3. `launcher_attention` — заметное привлечение внимания;
4. `shell_layout` — геометрия открытого окна;
5. `widget_open` — появление и раскрытие виджета;
6. `widget_close` — закрытие и сворачивание;
7. `background_effect` — частицы, свет, декоративная среда;
8. `assistant_message_enter` — появление сообщения AI;
9. `user_message_enter` — появление сообщения пользователя;
10. `typing_indicator` — состояние набора ответа;
11. `message_send` — отправка сообщения;
12. `composer_focus` — фокус и активное состояние поля;
13. `control_hover` — hover/focus микровзаимодействия;
14. `responsive_transition` — переход между desktop/mobile состояниями.

Таксономия расширяется добавлением категории и её stage mapping. Она не должна
зависеть от конкретной модели или provider.

## 4. Manifest паттерна

Source of truth для кода остаётся Git. Существующие manifest schema v1/v2
остаются неизменными; для новой атомарной библиотеки вводится schema v3:

```json
{
  "schema_version": 3,
  "pattern_id": "wave-reveal",
  "version": 1,
  "category": "widget_open",
  "status": "active",
  "title": "Долгое появление через волну",
  "summary": "Панель раскрывается плавной волной снизу вверх.",
  "ai_description": "Выразительное, но плавное появление: сначала волна формирует внешний контур панели, затем каскадом проявляется содержимое. Модель может ускорить эффект, сделать его спокойнее, усилить свечением, изменить направление или объединить с лёгкими частицами.",
  "technical_contract": "chat-shell-motion-v1",
  "adaptation_policy": "creative",
  "incompatible_with": [],
  "provenance": {
    "origin": "kaigo-owned",
    "review_state": "approved"
  }
}
```

Обязательные свойства:

- `title` — короткое понятное человеку название;
- `summary` — одна короткая строка для списков;
- `ai_description` — полное естественно-языковое описание замысла, допустимых
  преобразований и характера эффекта;
- `technical_contract` — минимальный runtime-контракт;
- `adaptation_policy` — `strict | adaptive | creative`;
- immutable `pattern_id + version` и SHA-256 implementation assets.

`status` сохраняет существующую lifecycle-семантику `draft | active |
deprecated`. Review-состояние `ready_for_review | approved | rejected`
хранится отдельно в PostgreSQL; первоначальное значение может импортироваться
из `provenance.review_state`. Selector видит только active-версию с effective
review state `approved`.

В `ai_description` не вводятся обязательные числовые значения длительности,
амплитуды или количества частиц. Такие значения допустимы внутри демонстрации,
но генератор может их менять согласно `adaptation_policy`.

## 5. Выбор кандидатов

Selector получает:

- пользовательский brief;
- reference context сайта;
- выбранное blind-judge visual direction;
- grouped catalog активных паттернов;
- для каждого паттерна: `pattern_id`, `version`, `category`, `title`, `summary`,
  полное `ai_description`, `technical_contract`, `adaptation_policy`,
  incompatibilities и provenance;
- implementation assets на этом этапе не передаются.

Selector возвращает schema v2:

```json
{
  "schema_version": 2,
  "direction_id": "candidate-2",
  "groups": [
    {
      "category": "widget_open",
      "candidates": [
        {
          "pattern_id": "wave-reveal",
          "version": 1,
          "rank": 1,
          "reason": "Поддерживает плавную технологичность выбранного направления"
        },
        {
          "pattern_id": "scan-build",
          "version": 1,
          "rank": 2,
          "reason": "Связывает появление виджета с анализом сайта"
        }
      ]
    }
  ],
  "summary": "Кандидаты подобраны для спокойной технологичной композиции"
}
```

Правила:

- при наличии двух и более active/approved-паттернов выбираются 2–5 уникальных
  кандидатов;
- если approved-паттерн один, выбирается один и это не считается ошибкой;
- категория без active/approved-паттернов сохраняется как пустая и не блокирует run,
  если она не обязательна для текущего runtime contract;
- selector не возвращает HTML, CSS, JavaScript или произвольный код;
- сервер проверяет version, category, status, incompatibilities и отсутствие
  дубликатов;
- первый некорректный ответ получает один correction retry;
- повторная ошибка включает детерминированный fallback из approved-кандидатов,
  чтобы генерация не завершалась из-за selector.

## 6. Stage-aware injection

Один общий shortlist создаётся после выбора visual direction и сохраняется до
первого generation dispatch. Полный код не передаётся всем стадиям сразу.

Начальное отображение категорий на этапы:

```text
foundation:
  launcher_shape, shell_layout, background_effect

identity:
  launcher_attention, launcher_idle

conversation:
  assistant_message_enter, user_message_enter,
  typing_indicator, message_send, composer_focus

motion_polish:
  widget_open, widget_close, control_hover,
  responsive_transition, background_effect
```

Для этапа сервер формирует bounded reference pack. Для каждого кандидата он
содержит manifest metadata, полный `ai_description`, точную версию, hash и
implementation assets. Генератору явно разрешено:

- выбрать один паттерн;
- объединить несколько кандидатов одной категории;
- использовать отдельную механику как вдохновение;
- менять визуальное выражение согласно `adaptation_policy`.

Генератор не вправе ломать `technical_contract`, подменять runtime API или
использовать паттерн, которого не было в shortlist. Технические действия open,
close, submit и runtime message insertion остаются собственностью trusted
runtime.

## 7. Provenance и PostgreSQL

Исторические `composition_plans` schema v1 остаются неизменными. Новый контур
добавляется отдельными таблицами, чтобы не переписывать прошлые run:

- `pattern_candidate_plans` — run, direction, schema version, summary,
  selector model call, registry digest;
- `pattern_candidate_groups` — plan, category и stage mapping;
- `pattern_candidate_items` — точная pattern version, rank и reason;
- `pattern_stage_exposures` — какие кандидаты реально попали в prompt этапа;
- `pattern_stage_usage_claims` — какие кандидаты генератор назвал primary,
  combined или inspiration;
- `pattern_reviews` — reviewer, status, comment и timestamp.

Источники паттернов и hashes синхронизируются с существующим
`widget_pattern_versions`. Outcome не приписывается автоматически всем
кандидатам shortlist. В БД отдельно различаются:

- `selected` — selector включил кандидата;
- `exposed` — код кандидата был передан конкретной стадии;
- `claimed_used` — генератор заявил использование;
- `verified_effect` — отдельная будущая проверка подтвердила эффект.

Это предотвращает ложное обучение статистики на вариантах, которые модель
видела, но не использовала.

## 8. Pattern Lab

Первая Pattern Lab — техническая admin-only HTML-страница, а не новый дизайн
Studio. Она предоставляет:

- фильтр по category/status;
- название, summary и точный `ai_description`, который видит selector;
- pattern id, version, contract, adaptation policy и implementation hash;
- нейтральную демонстрационную fixture;
- sandboxed iframe без `allow-same-origin` для запуска pattern JavaScript;
- команды `Запустить`, `Повторить`, `Открыть`, `Закрыть`, `Сообщение AI`,
  `Сообщение пользователя`, `Typing` в зависимости от категории;
- desktop/mobile viewport;
- approve/reject и комментарий администратора;
- понятное пустое состояние для ещё не наполненных категорий.

Pattern Lab не изменяет immutable version. Любое исправление implementation
создаёт новую версию, после чего она отдельно проходит review.

## 9. Совместимость и включение

- Manifest v1/v2 и исторический exact-one composition planner продолжают
  работать без преобразования сохранённых запусков.
- Новые атомарные паттерны создаются только как manifest v3.
- Для каждой новой категории добавляется нейтральная техническая fixture,
  достаточная для контрактных тестов Pattern Lab; художественное наполнение
  выполняется отдельной визуальной задачей.
- Новый selector включается feature flag по окружению. Production сохраняет
  текущий planner, пока обязательные категории не имеют минимум двух
  active/approved вариантов и не прошли review в Pattern Lab.
- После включения новый candidate plan используется для новых run; refinements
  и replay старых run продолжают читать сохранённую схему своего запуска.

## 10. Ошибки и восстановление

- повреждённый manifest или hash блокирует только конкретную версию при
  загрузке registry;
- invalid selector output проходит один correction retry и fallback;
- worker сохраняет candidate plan до generation dispatch и восстанавливает его
  после restart без повторного AI-выбора;
- недоступный необязательный category pack не останавливает другие стадии;
- несовместимые кандидаты отклоняются до передачи кода модели;
- stage pack строится только из exact persisted versions, поэтому обновление
  registry не меняет уже начатый run;
- Pattern Lab исполняет assets только в sandboxed preview.

## 11. Проверки

Обязательные тесты первой итерации:

1. loader принимает только полное валидное `ai_description` и immutable hash;
2. selector prompt действительно содержит полное `ai_description`, но не
   implementation assets;
3. schema v2 принимает 2–5 уникальных кандидатов и корректно обрабатывает
   каталог из одного элемента;
4. неизвестная версия, неверная категория, deprecated/rejected status и
   incompatibility отклоняются сервером;
5. repeated invalid selector response активирует fallback;
6. persisted candidate plan переживает restart и не выбирается повторно;
7. каждая generation stage получает только категории из stage mapping;
8. stage prompt содержит код только exact shortlisted versions;
9. usage claim не может ссылаться на невыданный кандидату pattern;
10. legacy composition plans продолжают загружаться;
11. PostgreSQL migration проходит upgrade/downgrade;
12. Pattern Lab API закрыт admin-проверкой, а preview использует sandbox;
13. текущие builder worker и provider-router suites не регрессируют.

Полный дорогой live-прогон модели и ручная визуальная приёмка не являются
обязательными для технического merge этой итерации. Они выполняются отдельной
задачей после наполнения каталога визуальными паттернами.

## 12. Критерии готовности

1. Новые категории и manifests загружаются без `pgvector` и дополнительного
   инфраструктурного сервиса.
2. Selector видит полное `ai_description` каждого доступного кандидата.
3. Для наполненной категории сохраняется shortlist из 2–5 exact versions.
4. Worker передаёт implementation assets stage-aware, а не одним общим пакетом.
5. Старые run и schema v1 остаются воспроизводимыми.
6. Candidate selection, exposure и claimed usage различимы в PostgreSQL.
7. Pattern Lab позволяет технически просмотреть, перезапустить и отревьюить
   паттерн на нейтральной fixture.
8. Контрактные, worker и PostgreSQL тесты проходят без полноценной платной
   генерации.

## 13. Следующий этап после первой итерации

Отдельная визуальная работа наполняет категории собственными Kaigo-паттернами и
скриншотами. Когда полный каталог станет слишком большим для экономной передачи
описаний selector, retrieval можно заменить на `pgvector`, не меняя manifest,
stage-aware injection, provenance, Pattern Lab и исторические планы.
