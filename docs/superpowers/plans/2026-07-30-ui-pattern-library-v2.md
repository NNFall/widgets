# Kaigo UI Pattern Library v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить существующий Pattern Registry из набора prompt-only референсов в версионируемую библиотеку безопасных source building blocks, сохранить исторические десять паттернов, выпустить их runtime-safe версии и добавить ещё десять вариантов без нарушения фиксированного iframe/chat runtime Kaigo.

**Architecture:** Исторические v1 assets остаются byte-identical. Первый
безопасный slice добавляет только инертный `runtime_source` registry,
детерминированный compiler и проверяемый showcase; production и planner по
умолчанию продолжают использовать `legacy_reference`. Детерминированный
compiler, а не модель, собирает обязательную анатомию `chat-v1` (root,
launcher, panel, header, messages, suggestions, composer и reserved actions),
вставляет только проверенные декоративные фрагменты и scoped CSS. Подключение
этой сборки как foundation seed является отдельным gated slice: после model
edit система обязана заново детерминированно собрать интерактивную анатомию и
проверить именно trusted production runtime, а не model-owned preview.

**Tech Stack:** Python 3.12, dataclasses, `html.parser`, JSON Schema 2020-12, existing `WidgetArtifact`/trusted iframe runtime, SQLAlchemy 2/PostgreSQL 15 provenance, pytest, existing `BrowserAudit`, Codex in-app Browser.

---

## Почему нужна v2

Текущие десять паттернов загружаются и хешируются, но передаются модели как
`untrusted reference code`. Они пока не являются исходником итогового виджета.
Более того, часть v1 assets заведомо не совместима с уже действующим
`chat-v1`/`validate_artifact`:

- `single-line-pill-v1` и `multiline-soft-v1` содержат запрещённый `<form>`;
- shell и motion v1 используют `<slot>`, которого нет в разрешённом HTML;
- shell/composer v1 создают `CustomEvent`, а launcher v1 — собственные timers;
- CSS v1 начинается с локальных классов, а не с `.kaigo-widget`, поэтому не
  проходит production scoping gate;
- trusted runtime всё равно не исполняет artifact JavaScript: open, close,
  send, retry, transcript и 15-секундное attention принадлежат Kaigo runtime.

Поэтому v1 нельзя «исправить на месте»: manifest и implementation hash уже
являются provenance. Их нужно оставить для исторического resume, а безопасные
реализации выпустить отдельными версиями.

## Целевой каталог

После завершения planner видит по четыре `runtime_source` паттерна в каждом
слоте (двадцать всего):

| Слот | Сохраняемые ID в новой безопасной версии | Новые ID |
|---|---|---|
| launcher | `orb-pulse@2`, `peek-tab@2` | `status-capsule@1`, `avatar-beacon@1` |
| shell | `compact-chat@2`, `floating-card@2` | `docked-rail@1`, `bottom-drawer@1` |
| messages | `paired-bubbles@2`, `advisor-cards@2` | `labelled-strips@1`, `avatar-thread@1` |
| composer | `single-line-pill@2`, `multiline-soft@2` | `inset-textarea@1`, `stacked-compose@1` |
| motion | `spring-reveal@2`, `soft-scale@2` | `side-glide@1`, `fade-settle@1` |

Все десять каталогов `*-v1`, существующие сейчас, остаются без изменений.

## Неподвижные контракты

1. Compiler единолично создаёт `data-region`, `data-action`, `data-suggestion`,
   `data-state` и `data-kaigo-runtime-*` anatomy. Pattern fragments не могут
   объявлять ни один из этих атрибутов.
2. В `runtime_source` directory нет `behavior.js`. Source assets состоят из
   `manifest.json`, `fragment.html` и `styles.css`; interactions принадлежат
   trusted runtime.
3. Fragment не содержит `form`, `slot`, `script`, `style`, `iframe`, URL,
   inline style/event attributes, `id` или пользовательский executable code.
4. Каждый CSS selector начинается с пары
   `.kaigo-widget.<manifest.root_class>`; keyframes имеют уникальный prefix.
5. Motion реагирует только на существующие runtime states:
   `.kaigo-preview-open`, `.kaigo-preview-attention`, `[data-open]`, hover,
   focus-visible, active, pending и error selectors. Никаких собственных
   timers, custom events или state toggles.
6. Default copy compiler-а — короткий русский текст. Технические IDs и ошибки
   source compiler не становятся пользовательским статусом.
7. PostgreSQL продолжает хранить exact pattern version, manifest snapshot и
   implementation hash; дополнительно artifact provenance фиксирует compiled
   source hash и runtime contract.
8. `fragment.html` является только декорацией: запрещены `button`, `input`,
   `textarea`, `select`, `option`, `a`, `details`, `summary`, `label`,
   `contenteditable`, `tabindex` и любые другие focusable/control элементы.
   Интерактивные элементы создаёт только compiler.
9. `root_class`, keyframe names и CSS variable names проходят строгий token
   grammar (`[a-z][a-z0-9-]*` и отдельный allowlisted prefix), а не только
   проверку prefix. Значения CSS bindings остаются типизированными числами.
10. Compiler проверяет exact-one canonical anatomy и запрещает альтернативный
    launcher/composer/runtime region. Наличие root class на произвольном
    элементе само по себе не считается provenance.
11. `runtime_source` artifact всегда имеет `javascript == ""` до и после
    model step. В source-mode `custom_escape` запрещён до появления отдельного
    проверяемого source contract.
12. Compiler revision и полный immutable `base.css` входят в source snapshot и
    hash. Resume использует сохранённый bundle, а не текущие файлы deploy.
13. Operational status/deprecation хранится отдельно от immutable manifest
    snapshot/hash. Deprecated version разрешена для исторического resume, но
    не предлагается planner для нового запуска.

## Обязательный phase gate после независимого review

Tasks 1–3 образуют первый безопасный slice. Они не меняют production mode, не
подключают planner/worker и не разрешают model-owned runtime JavaScript.

Tasks 4–12 нельзя подключать к live generation, пока одновременно не доказаны:

- canonical post-model recomposition с exact-one anatomy;
- durable compiler/base-CSS snapshot для resume;
- передача source contract через `VisualGate` и worker factory;
- `BrowserAudit` на `build_trusted_runtime_document`, то есть на том же
  документе, который публикуется клиенту;
- `KAIGO_PATTERN_LIBRARY_MODE=legacy_reference` как fail-closed default.

## Карта файлов

### Новые файлы

- `builder_lab/css_contract.py` — общий brace-aware CSS parser для artifact и
  source gates без расхождения правил.
- `builder_lab/patterns/source.py` — типизированный compiler безопасного
  `chat-v1` seed и canonical source hash.
- `builder_lab/patterns/quality.py` — category/source validation и проверка
  source anchors в model output.
- `builder_lab/patterns/runtime_source/base.css` — минимальный scoped layout,
  runtime states, accessibility и responsive bounds, общие для всех композиций.
- `builder_lab/patterns/catalog/` — двадцать source directories, явно
  перечисленные в Tasks 6–9, рядом с неизменяемыми десятью legacy directories.
- `builder_lab/patterns/showcase.py` — локальная contact-sheet страница из
  реального compiler output, без отдельной «рисованной» демки.
- `scripts/run_pattern_showcase.py` — localhost server для проверки через
  in-app Browser.
- `tests/builder_lab_cases/test_pattern_source.py` — compiler/hash/parameter tests.
- `tests/builder_lab_cases/test_pattern_quality.py` — static и category gates.
- `tests/builder_lab_cases/test_pattern_catalog_v2.py` — полный каталог и
  combinatorial contracts.
- `tests/builder_lab_cases/test_pattern_showcase.py` — showcase использует
  production compiler.
- `docs/UI_PATTERN_LIBRARY.md` — русская инструкция добавления новой версии.
- `docs/release-evidence/2026-07-30-ui-pattern-library-v2.md` — команды,
  screenshots и честные ограничения выпуска.

### Изменяемые файлы

- `builder_lab/patterns/registry.py` — legacy v1 + strict source manifest v2.
- `builder_lab/patterns/resolver.py` — selectable source-only resolution и
  compiled seed вместо prompt-only asset dump.
- `builder_lab/patterns/planner.py` — planner получает только selectable catalog.
- `builder_lab/patterns/__init__.py` — публичные source/quality contracts.
- `builder_lab/prompts.py` — foundation начинает с compiled source и сохраняет
  anchors/runtime ownership.
- `builder_lab/worker.py` — durable source metadata, seed integration и
  candidate source validation.
- `builder_lab/orchestrator.py` — тот же contract для in-memory/direct path.
- `app/patterns/repository.py` — provenance читается из manifest snapshot и
  source hash попадает в terminal outcome payload.
- `tests/builder_lab_cases/test_pattern_registry.py` — dual manifest contract.
- `tests/builder_lab_cases/test_pattern_resolver.py` — legacy reject/new source compile.
- `tests/builder_lab_cases/test_pattern_planner.py` — planner не видит v1.
- `tests/builder_lab_cases/test_worker.py` — foundation seed и repairs.
- `tests/builder_lab_cases/test_orchestrator.py` — non-durable parity.
- `tests/saas_cases/test_pattern_worker_persistence.py` — persisted provenance.
- `docs/product-journal/2026-07.md` — только после подтверждённого результата.

---

### Task 1: Зафиксировать dual-version manifest contract

**Files:**
- Modify: `builder_lab/patterns/registry.py`
- Modify: `tests/builder_lab_cases/test_pattern_registry.py`

- [ ] **Step 1: Добавить RED-тесты legacy/source разделения**

```python
def test_v1_is_loaded_for_history_but_not_selectable():
    registry = load_builtin_registry()
    legacy = registry.resolve("orb-pulse", 1)
    assert legacy.integration_mode is PatternIntegrationMode.LEGACY_REFERENCE
    assert legacy not in registry.selectable_for(PatternCategory.LAUNCHER)

def test_source_manifest_rejects_behavior_javascript(tmp_path):
    write_source_pattern(tmp_path / "status-capsule-v1")
    (tmp_path / "status-capsule-v1" / "behavior.js").write_text(
        "setTimeout(() => {}, 1)", encoding="utf-8"
    )
    with pytest.raises(PatternRegistryError, match="unexpected pattern asset"):
        PatternRegistry.load(tmp_path)

def test_source_manifest_requires_runtime_provenance(tmp_path):
    pattern = write_source_pattern(tmp_path / "status-capsule-v1")
    manifest = json.loads((pattern / "manifest.json").read_text(encoding="utf-8"))
    del manifest["provenance"]
    rewrite_source_manifest(pattern, manifest)
    with pytest.raises(PatternRegistryError, match="manifest fields"):
        PatternRegistry.load(tmp_path)
```

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_registry.py -q`
Expected: FAIL — `PatternIntegrationMode`, source schema и `selectable_for` ещё
не существуют.

- [ ] **Step 3: Ввести explicit source contracts**

```python
class PatternIntegrationMode(str, Enum):
    LEGACY_REFERENCE = "legacy_reference"
    RUNTIME_SOURCE = "runtime_source"

@dataclass(frozen=True, slots=True)
class CssParameterBinding:
    css_variable: str
    unit: str

@dataclass(frozen=True, slots=True)
class PatternSourceContract:
    runtime_contract_id: str
    runtime_contract_version: int
    fragment_role: str
    root_class: str
    parameter_bindings: Mapping[str, CssParameterBinding]

@dataclass(frozen=True, slots=True)
class PatternDefinition:
    # existing fields stay
    integration_mode: PatternIntegrationMode
    source_contract: PatternSourceContract | None
    provenance: Mapping[str, JSONValue]
```

Loader rules:

- manifest `schema_version == 1` принимает только прежние четыре assets и
  автоматически классифицируется как `legacy_reference`;
- manifest `schema_version == 2` принимает только три assets, требует
  `source_contract` и `provenance`, классифицируется как `runtime_source`;
- `source_contract.runtime_contract_id/version` обязаны быть `chat-v1/1`;
- `fragment_role` определяется категорией:
  `launcher-content`, `shell-decoration`, `message-decoration`,
  `composer-decoration`, `motion-none`;
- CSS variable начинается с `--kaigo-pattern-`, unit входит в
  `"" | "px" | "ms"`, а binding key существует в `parameter_schema`;
- source provenance содержит ровно `origin`, `review_state`, `source_revision`,
  `supersedes`; `origin="kaigo-owned"`, `review_state="verified"`, а
  `supersedes` либо `null`, либо `{pattern_id, version}`;
- v1 bytes/hash не изменяются.

- [ ] **Step 4: Сделать planner catalog source-only**

```python
def selectable_for(self, category: PatternCategory) -> tuple[PatternDefinition, ...]:
    return tuple(
        item for item in self.definitions
        if item.category is category
        and item.status is PatternStatus.ACTIVE
        and item.integration_mode is PatternIntegrationMode.RUNTIME_SOURCE
    )

def planner_catalog(self) -> tuple[dict[str, JSONValue], ...]:
    return tuple(
        item.public_dict()
        for category in PatternCategory
        for item in self.selectable_for(category)
    )
```

`public_dict()` source pattern-а включает runtime contract, provenance и hash,
но никогда не включает HTML/CSS. Старый `public_catalog()` временно делегирует
`planner_catalog()`, чтобы не оставить второй небезопасный путь.

Для legacy v1 `public_dict()` обязан вернуть byte-equivalent прежний snapshot
shape — без новых keys `integration_mode`, `source_contract`, `provenance`.
Иначе `PatternRepository.sync_registry()` обнаружит ложный immutable drift у
уже сохранённых production rows. Добавить regression:

```python
def test_legacy_manifest_snapshot_shape_is_unchanged():
    legacy = load_builtin_registry().resolve("orb-pulse", 1).public_dict()
    assert set(legacy) == {
        "pattern_id", "version", "category", "status", "description",
        "parameter_schema", "incompatible_with", "implementation_sha256",
    }
```

- [ ] **Step 5: Запустить GREEN и legacy regression**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_registry.py tests/saas_cases/test_pattern_repository.py -q`
Expected: PASS; старые v1 hashes читаются без drift, но selectable catalog пока
пуст до добавления source assets.

- [ ] **Step 6: Commit**

```bash
git add builder_lab/patterns/registry.py tests/builder_lab_cases/test_pattern_registry.py
git commit -m "refactor: separate legacy and runtime source patterns"
```

### Task 2: Статически запретить небезопасные source assets

**Files:**
- Create: `builder_lab/css_contract.py`
- Create: `builder_lab/patterns/quality.py`
- Create: `tests/builder_lab_cases/test_pattern_quality.py`
- Modify: `builder_lab/validation.py`
- Modify: `builder_lab/patterns/registry.py`
- Modify: `tests/builder_lab_cases/test_validation.py`

- [ ] **Step 1: Написать RED-тесты на известные нарушения v1**

```python
@pytest.mark.parametrize("fragment", [
    "<form></form>",
    "<slot name='messages'></slot>",
    "<button onclick='send()'>Отправить</button>",
    "<div data-region='messages'></div>",
    "<div id='shared-id'></div>",
])
def test_runtime_source_fragment_rejects_owned_or_colliding_markup(fragment):
    with pytest.raises(PatternSourceError):
        validate_source_fragment(definition("launcher"), fragment)

@pytest.mark.parametrize("css", [
    "body { color: red; }",
    ".kaigo-launcher { color: red; }",
    ".kaigo-widget.other-root { color: red; }",
    "@import url(https://example.com/a.css);",
])
def test_runtime_source_css_requires_exact_root_scope(css):
    with pytest.raises(PatternSourceError):
        validate_source_css(definition(root_class="kaigo-pattern-launcher-orb-pulse"), css)

def test_runtime_source_has_no_javascript_channel():
    assert source_expected_files() == {
        "manifest.json", "fragment.html", "styles.css"
    }
```

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_quality.py -q`
Expected: FAIL because `quality.py` is absent.

- [ ] **Step 3: Реализовать source HTML gate**

```python
SOURCE_FORBIDDEN_ELEMENTS = frozenset({
    "form", "slot", "script", "style", "iframe", "object", "embed", "link", "base", "meta"
})
SOURCE_RESERVED_ATTRIBUTES = frozenset({
    "data-region", "data-action", "data-suggestion", "data-state",
    "data-kaigo-runtime-message", "data-kaigo-runtime-label",
    "data-kaigo-runtime-content", "data-kaigo-runtime-status",
    "data-kaigo-runtime-retry",
})

def validate_source_fragment(
    definition: PatternDefinition,
    html: str,
) -> SourceFragmentFacts:
    """Parse bounded HTML, reject reserved anatomy and return deterministic facts."""
```

Gate also rejects duplicate attributes, `id`, URL attributes with non-empty
values, inline `style`, every `on*`, disallowed SVG path commands, unbalanced
markup, category-incompatible tags and more than 40 nodes per fragment.

- [ ] **Step 4: Вынести brace-aware parser в один общий модуль**

Перенести без изменения поведения `_matching_brace`, `_css_rules` и
`_selector_is_scoped` из `validation.py` в `builder_lab/css_contract.py` под
именами `matching_brace`, `parse_css_rules`, `selector_is_scoped`.
`validate_artifact()` продолжает вызывать их через import. До добавления новых
правил запустить:

Run: `python -m pytest tests/builder_lab_cases/test_validation.py -q`
Expected: PASS с тем же числом тестов; это pure extraction.

- [ ] **Step 5: Реализовать exact CSS scope gate**

```python
def validate_source_css(definition: PatternDefinition, css: str) -> None:
    required_prefix = f".kaigo-widget.{definition.source_contract.root_class}"
    selectors, at_rules, malformed = parse_css_rules(css)
    if malformed or any(not selector.startswith(required_prefix) for selector in selectors):
        raise PatternSourceError("runtime source CSS escapes its exact root class")
```

Использовать общий brace-aware CSS parser, а не второй regex-парсер.
Дополнительные правила:

- разрешены только `@media`, `@supports`, `@container`, `@keyframes`;
- keyframe name начинается с вычисленного prefix
  `kaigo-pattern-{definition.pattern_id}-`;
- запрещены `url()`, `@import`, `expression`, `javascript:`, `behavior:`;
- attention selector использует только `.kaigo-preview-attention`;
- открытие использует только `.kaigo-preview-open` или `[data-open]`;
- если есть keyframes/transition, должен быть
  `@media (prefers-reduced-motion: reduce)` override;
- root class из manifest реально встречается в каждом selector branch.

- [ ] **Step 6: Подключить gate в `_load_definition` только для schema v2**

После чтения и до hash comparison выполнить `validate_source_fragment` и
`validate_source_css`. Legacy v1 всё ещё проверяется старым bounded/forbidden
capability gate, но не проходит через новый source contract.

- [ ] **Step 7: Запустить GREEN**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_quality.py tests/builder_lab_cases/test_pattern_registry.py tests/builder_lab_cases/test_validation.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add builder_lab/css_contract.py builder_lab/validation.py builder_lab/patterns/quality.py builder_lab/patterns/registry.py tests/builder_lab_cases/test_validation.py tests/builder_lab_cases/test_pattern_quality.py tests/builder_lab_cases/test_pattern_registry.py
git commit -m "feat: validate safe runtime pattern sources"
```

### Task 3: Собрать реальный `chat-v1` source seed

**Files:**
- Create: `builder_lab/patterns/source.py`
- Create: `builder_lab/patterns/runtime_source/base.css`
- Create: `tests/builder_lab_cases/test_pattern_source.py`
- Modify: `builder_lab/patterns/__init__.py`

- [ ] **Step 1: Написать RED-тест детерминированной сборки**

```python
def test_compiler_owns_runtime_anatomy(source_registry, source_plan):
    source = compile_runtime_source(source_plan, source_registry)
    assert source.body_html.count('data-region="root"') == 1
    for region in ("launcher", "panel", "header", "messages", "suggestions", "composer"):
        assert source.body_html.count(f'data-region="{region}"') == 1
    assert '<form' not in source.body_html
    assert '<slot' not in source.body_html
    assert source.javascript == ""
    assert validate_artifact(
        source.as_seed(revision=1, art_direction="Тестовое направление")
    ) == ()

def test_compiler_hash_is_canonical(source_registry, source_plan):
    first = compile_runtime_source(source_plan, source_registry)
    second = compile_runtime_source(source_plan, source_registry)
    assert first.source_sha256 == second.source_sha256
    assert first.body_html == second.body_html
    assert first.css == second.css

def test_css_parameter_binding_cannot_escape_declaration(source_registry, source_plan):
    selected = replace_selection(source_plan, "launcher", {"size_px": "1px;}body{"})
    with pytest.raises((ValueError, PatternResolutionError)):
        compile_runtime_source(selected, source_registry)
```

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_source.py -q`
Expected: FAIL because compiler and source assets do not exist.

- [ ] **Step 3: Добавить immutable compiler result**

```python
@dataclass(frozen=True, slots=True)
class PatternSourceProvenance:
    runtime_contract_id: str
    runtime_contract_version: int
    compiler_bundle_id: str
    compiler_revision: int
    base_css_sha256: str
    pattern_versions: tuple[str, ...]
    implementation_hashes: tuple[str, ...]
    source_sha256: str

@dataclass(frozen=True, slots=True)
class CompiledPatternSource:
    body_html: str
    css: str
    javascript: str
    root_classes: tuple[str, ...]
    compiler_bundle_snapshot: str
    provenance: PatternSourceProvenance

    @property
    def source_sha256(self) -> str:
        return self.provenance.source_sha256

    def as_seed(self, *, revision: int, art_direction: str) -> WidgetArtifact:
        return WidgetArtifact(
            schema_version="1.0",
            revision=revision,
            stage=Stage.ART_DIRECTION,
            art_direction=art_direction,
            body_html=self.body_html,
            css=self.css,
            javascript="",
            change_summary="Kaigo собрал безопасную основу AI-консультанта.",
            layout_contract={"runtime_contract": "chat-v1@1"},
        )
```

- [ ] **Step 4: Compiler должен владеть всей интерактивной анатомией**

`compile_runtime_source()` создаёт один root с `.kaigo-widget`, пять pattern
root classes и следующую структуру:

```html
<section class="kaigo-widget …" data-region="root">
  <button type="button" data-region="launcher" data-action="open"
          aria-label="Открыть AI-консультанта">…launcher fragment…</button>
  <section data-region="panel" role="dialog" aria-label="AI-консультант">
    …shell decoration…
    <header data-region="header">
      <div><strong>AI-консультант</strong><span>На связи</span></div>
      <button type="button" data-action="close" aria-label="Закрыть">×</button>
    </header>
    <main data-region="messages" role="log" aria-live="polite">
      <article class="kaigo-widget__message kaigo-widget__message--assistant">
        …message decoration…
        <span class="kaigo-widget__message-label">AI-консультант</span>
        <p>Здравствуйте! Я изучил ваш сайт. Чем помочь?</p>
      </article>
    </main>
    <div data-region="suggestions" aria-label="Быстрые вопросы">
      <button type="button" data-suggestion="Подобрать вариант">Подобрать вариант</button>
      <button type="button" data-suggestion="Уточнить условия">Уточнить условия</button>
    </div>
    <div data-region="composer" aria-label="Сообщение AI-консультанту">
      …composer decoration…
      <label class="kaigo-widget__sr-only" for="kaigo-message">Сообщение</label>
      <textarea id="kaigo-message" rows="1" placeholder="Напишите вопрос…"></textarea>
      <button type="button" data-action="send" aria-label="Отправить сообщение">↑</button>
    </div>
  </section>
</section>
```

`id="kaigo-message"` принадлежит compiler, а не fragment, поэтому остаётся
единственным контролируемым ID. Runtime source gate по-прежнему запрещает ID в
каталожных фрагментах.

Compiler после сборки считает canonical anatomy fingerprint: ровно один root,
launcher, panel, header, messages, suggestions и composer; ровно один
compiler-owned textarea, send и close control; ни одного дополнительного
focusable элемента из fragments. Этот fingerprint сохраняется рядом с
immutable compiler bundle snapshot и повторно проверяется после model step.

- [ ] **Step 5: Применить только типизированные CSS bindings**

```python
def _render_parameter_css(definition, parameters) -> str:
    declarations = []
    for name, value in sorted(parameters.items()):
        binding = definition.source_contract.parameter_bindings[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PatternSourceError(f"parameter {name} is not numeric")
        declarations.append(f"{binding.css_variable}:{value:g}{binding.unit}")
    if not declarations:
        return ""
    return (
        f".kaigo-widget.{definition.source_contract.root_class}"
        + "{" + ";".join(declarations) + ";}"
    )
```

Никаких arbitrary selectors, attributes, HTML или copy bindings в первой v2.
Тексты дальше адаптирует генератор в рамках обычного artifact contract.

- [ ] **Step 6: Базовый CSS должен проходить production validator**

`base.css` задаёт только `.kaigo-widget …` selectors, closed/open runtime state,
panel viewport bounds, peer regions, 44×44 controls, hidden scrollbar styling,
assistant/user runtime classes, pending/error/retry и reduced-motion. Он не
задаёт брендовые цвета/точные радиусы и не конкурирует с pattern CSS.

`chat-v1@1` фиксирует byte-identical compiler revision и `base.css`. Любое
изменение compiler/base CSS создаёт новый bundle id; запрещено переписывать
bundle, который уже встречается в durable run context.

- [ ] **Step 7: Запустить GREEN**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_source.py tests/builder_lab_cases/test_validation.py -q`
Expected: PASS; compiled source с synthetic source fixtures проходит текущий
`validate_artifact` без исключений.

- [ ] **Step 8: Commit**

```bash
git add builder_lab/patterns/source.py builder_lab/patterns/runtime_source/base.css builder_lab/patterns/__init__.py tests/builder_lab_cases/test_pattern_source.py
git commit -m "feat: compile pattern compositions into chat runtime source"
```

### Task 4: Резолвить новые планы в source, а legacy — только для resume

Эта и следующие задачи остаются за phase gate. Для новых source-plan
`custom_escape` отклоняется. Deprecated source definition скрывается от нового
planner, но исторический resume разрешает exact saved version и использует
сохранённый immutable compiler bundle.

**Files:**
- Modify: `builder_lab/patterns/resolver.py`
- Modify: `builder_lab/patterns/planner.py`
- Modify: `builder_lab/prompts.py`
- Modify: `tests/builder_lab_cases/test_pattern_resolver.py`
- Modify: `tests/builder_lab_cases/test_pattern_planner.py`

- [ ] **Step 1: Перевести test fixtures на safe versions и добавить RED для legacy**

```python
SAFE_PATTERNS = {
    PatternCategory.LAUNCHER: ("orb-pulse", 2),
    PatternCategory.SHELL: ("compact-chat", 2),
    PatternCategory.MESSAGES: ("paired-bubbles", 2),
    PatternCategory.COMPOSER: ("single-line-pill", 2),
    PatternCategory.MOTION: ("spring-reveal", 2),
}

def test_new_resolution_rejects_prompt_only_v1():
    with pytest.raises(PatternResolutionError, match="legacy_reference"):
        resolve_composition(legacy_complete_plan(), load_builtin_registry())

def test_explicit_legacy_resume_never_compiles_javascript():
    result = resolve_composition(
        legacy_complete_plan(),
        load_builtin_registry(),
        allow_legacy_reference=True,
    )
    assert result.source is None
    assert "untrusted reference code" in result.prompt_text

def test_source_resolution_returns_compiled_seed():
    result = resolve_composition(source_complete_plan(), load_builtin_registry())
    assert result.source is not None
    assert result.source.javascript == ""
    assert result.source.source_sha256 in result.prompt_text
```

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_resolver.py tests/builder_lab_cases/test_pattern_planner.py -q`
Expected: FAIL — result пока содержит только prompt bundle, planner видит v1.

- [ ] **Step 3: Расширить `ResolvedComposition` без второго source of truth**

```python
@dataclass(frozen=True, slots=True)
class ResolvedComposition:
    plan: CompositionPlan
    pattern_ids: tuple[str, ...]
    implementation_hashes: tuple[str, ...]
    prompt_text: str
    source: CompiledPatternSource | None
    integration_mode: PatternIntegrationMode
```

`resolve_composition(..., allow_legacy_reference=False)` выполняет правила:

- смешивать legacy/source версии в одной композиции запрещено;
- source composition компилируется и получает короткий prompt с IDs, params,
  runtime contract, source hash и обязательными root classes; полный CSS/HTML
  уже приходит как `previous_artifact`, поэтому повторно сжигать токены не надо;
- legacy composition допустима только с explicit flag, сохраняет прежний
  bounded prompt и никогда не исполняет `behavior.js`;
- new planner path никогда не передаёт flag.

- [ ] **Step 4: Planner должен получать только `registry.planner_catalog()`**

```python
result = await engine.plan_composition(
    request=request,
    selected_direction=selected_direction,
    public_catalog=registry.planner_catalog(),
    correction=diagnostic or None,
)
```

Prompt дополнить явной фразой: «Каталог уже отфильтрован сервером; выбирай
только `runtime_source`, не угадывай другие версии». `reason` и `summary`
остаются на русском и не содержат имён CSS/HTML.

- [ ] **Step 5: Проверить catalog cardinality**

Пока safe assets ещё не добавлены, RED test должен ожидать пустой catalog.
В Task 6 он меняется на exact 20/4-per-category. Это единственный намеренный
временный checkpoint; не разворачивать промежуточную версию.

- [ ] **Step 6: Запустить GREEN для resolver contracts**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_resolver.py tests/builder_lab_cases/test_pattern_planner.py -q`
Expected: PASS с synthetic schema-v2 fixtures.

- [ ] **Step 7: Commit**

```bash
git add builder_lab/patterns/resolver.py builder_lab/patterns/planner.py builder_lab/prompts.py tests/builder_lab_cases/test_pattern_resolver.py tests/builder_lab_cases/test_pattern_planner.py
git commit -m "feat: resolve selectable patterns into runtime source"
```

### Task 5: Встроить source seed в durable и in-memory generation

**Files:**
- Modify: `builder_lab/patterns/resolver.py`
- Modify: `builder_lab/worker.py`
- Modify: `builder_lab/orchestrator.py`
- Modify: `builder_lab/prompts.py`
- Modify: `app/patterns/repository.py`
- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `tests/builder_lab_cases/test_orchestrator.py`
- Modify: `tests/saas_cases/test_pattern_worker_persistence.py`

- [ ] **Step 1: Написать RED-тесты foundation seed и source anchors**

```python
@pytest.mark.asyncio
async def test_foundation_receives_compiled_source_as_previous_artifact(worker_fixture):
    await worker_fixture.run_composition(source_plan())
    await worker_fixture.run_foundation()
    prompt = worker_fixture.engine.stage_prompts[-1]
    assert 'data-region="launcher"' in prompt
    assert 'data-region="composer"' in prompt
    assert "kaigo-pattern-launcher-orb-pulse" in prompt

@pytest.mark.asyncio
async def test_stage_repair_rejects_removed_source_anchor(worker_fixture):
    worker_fixture.engine.next_artifact = artifact(
        body_html=valid_html().replace("kaigo-pattern-launcher-orb-pulse", "")
    )
    result = await worker_fixture.run_foundation()
    assert "pattern_source_anchor_missing" in worker_fixture.repair_issue_codes

@pytest.mark.asyncio
async def test_persisted_artifact_records_source_provenance(database_fixture):
    artifact = await complete_source_run(database_fixture)
    assert artifact.provenance["runtime_contract"] == "chat-v1@1"
    assert len(artifact.provenance["composition_source_sha256"]) == 64
    assert artifact.provenance["pattern_versions"] == [
        "orb-pulse@2", "compact-chat@2", "paired-bubbles@2",
        "single-line-pill@2", "spring-reveal@2",
    ]
```

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_orchestrator.py tests/saas_cases/test_pattern_worker_persistence.py -q`
Expected: FAIL — foundation всё ещё получает art-direction artifact, provenance
содержит только `output_refs`.

- [ ] **Step 3: Сохранить bounded source metadata в stage context**

На `Stage.COMPOSITION` записать:

```python
next_context.update({
    "composition_plan": planned.plan.to_dict(),
    "composition_source_version": 2,
    "composition_source_sha256": planned.resolved.source.source_sha256,
    "composition_runtime_contract": "chat-v1@1",
})
```

Это не дублирует assets и остаётся значительно ниже `StageResult.context`
limit. При resume:

- `composition_source_version == 2` требует source resolution и exact hash;
- отсутствующее поле означает исторический v1 run и включает только
  `allow_legacy_reference=True`;
- несовпадение hash после deploy — fail closed с diagnostic, а не тихая сборка
  из другого source.

Вынести это в один provider-neutral helper, используемый worker и
in-memory orchestrator:

```python
def resolve_composition_context(
    context: Mapping[str, object],
    registry: PatternRegistry,
    *,
    configured_mode: PatternIntegrationMode,
) -> ResolvedComposition:
    """Persisted mode/hash win over current config; any drift fails closed."""
```

- [ ] **Step 4: Foundation использует seed с прежней revision**

```python
effective_previous = previous
if (
    stage is Stage.FOUNDATION
    and composition is not None
    and composition.source is not None
):
    effective_previous = composition.source.as_seed(
        revision=previous.revision if previous else 1,
        art_direction=selected_direction.art_direction,
    )
```

Вызов модели получает `effective_previous`; monotonic candidate revision всё
ещё сравнивается с реально сохранённой предыдущей revision. Seed не создаёт
лишнюю DB revision и не меняет stage sequence.

- [ ] **Step 5: Добавить composition-aware validation после каждой модели**

```python
def validate_pattern_source_usage(
    artifact: WidgetArtifact,
    source: CompiledPatternSource,
) -> tuple[ValidationIssue, ...]:
    root_classes = parse_root_classes(artifact.body_html)
    return tuple(
        ValidationIssue(
            code="pattern_source_anchor_missing",
            field="body_html",
            message=f"Required pattern source anchor is missing: {class_name}",
        )
        for class_name in source.root_classes
        if class_name not in root_classes
    )
```

Склеивать эти issues с `validate_artifact` до fingerprint/repair loop. Prompt
repair объясняет модели: восстановить root class/фиксированную анатомию, не
добавлять pattern JavaScript и не менять runtime-owned actions. Проверка
выполняется одинаково в `worker.py` и `orchestrator.py` через общий helper.

- [ ] **Step 6: Убрать противоречие про unrestricted interaction JavaScript**

В `build_stage_prompt` для source composition явно написать:

- fixed runtime единолично владеет open/close/send/suggestion/retry/attention;
- artifact JavaScript не является способом реализовать выбранные паттерны;
- animations делаются CSS по runtime states;
- модель сохраняет compiler anatomy/root classes;
- русский business copy можно редактировать, но UX остаётся двухсторонним чатом.

Legacy/Antigravity wording не менять шире необходимого в этой задаче.

- [ ] **Step 7: Persist provenance и outcome payload**

`GenerationArtifact.provenance` получает source hash, runtime contract,
pattern versions и hashes из result context. Terminal
`PatternOutcomeMetrics.payload` получает `composition_source_sha256` и
`runtime_contract`; raw source, prompts и user copy туда не попадают.

- [ ] **Step 8: Запустить GREEN и boundary regression**

Run: `python -m pytest tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_orchestrator.py tests/saas_cases/test_pattern_worker_persistence.py tests/saas_cases/test_pattern_outcome_publication.py -q`
Expected: PASS; legacy resume test тоже проходит.

- [ ] **Step 9: Commit**

```bash
git add builder_lab/patterns/resolver.py builder_lab/worker.py builder_lab/orchestrator.py builder_lab/prompts.py app/patterns/repository.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_orchestrator.py tests/saas_cases/test_pattern_worker_persistence.py
git commit -m "feat: seed generation from verified pattern sources"
```

### Task 6: Перенести текущие десять IDs в runtime-safe версии

**Files:**
- Create: `builder_lab/patterns/catalog/orb-pulse-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/peek-tab-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/compact-chat-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/floating-card-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/paired-bubbles-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/advisor-cards-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/single-line-pill-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/multiline-soft-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/spring-reveal-v2/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/soft-scale-v2/{manifest.json,fragment.html,styles.css}`
- Create: `scripts/hash_pattern_manifest.py`
- Create: `tests/builder_lab_cases/test_pattern_catalog_v2.py`
- Create: `tests/scripts_cases/test_hash_pattern_manifest.py`

- [ ] **Step 1: Написать RED catalog contract**

```python
EXPECTED_PORTS = {
    "orb-pulse": 2, "peek-tab": 2,
    "compact-chat": 2, "floating-card": 2,
    "paired-bubbles": 2, "advisor-cards": 2,
    "single-line-pill": 2, "multiline-soft": 2,
    "spring-reveal": 2, "soft-scale": 2,
}

def test_existing_ids_have_runtime_source_versions():
    registry = load_builtin_registry()
    for pattern_id, version in EXPECTED_PORTS.items():
        item = registry.resolve(pattern_id, version)
        assert item.integration_mode is PatternIntegrationMode.RUNTIME_SOURCE
        assert item.provenance["supersedes"] == {
            "pattern_id": pattern_id, "version": 1,
        }

def test_legacy_v1_hashes_did_not_change():
    registry = load_builtin_registry()
    assert registry.resolve("orb-pulse", 1).implementation_sha256 == (
        "d3873d7f63c8f54be631d4c84f19b16acc88c5d20b3d9b1648b754c2f20559dc"
    )
```

Закрепить все десять существующих v1 hashes, а не только пример.

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py -q`
Expected: FAIL — v2 directories отсутствуют.

- [ ] **Step 3: Добавить launcher и shell ports**

`orb-pulse@2` и `peek-tab@2` содержат только внутренний visual content; button,
ARIA и click принадлежат compiler. Attention CSS слушает
`.kaigo-preview-attention`, а не `[data-attention=true]`.

`compact-chat@2` и `floating-card@2` содержат только decorative shell layer;
никаких `<slot>`, close button или custom event. CSS стилизует compiler-owned
peer regions через exact source root class.

- [ ] **Step 4: Добавить messages и composer ports**

`paired-bubbles@2`/`advisor-cards@2` не дублируют transcript. Их CSS одновременно
стилизует initial `.kaigo-widget__message--assistant` и injected
`[data-kaigo-runtime-message="assistant|user"]`.

`single-line-pill@2`/`multiline-soft@2` не содержат `<form>` и не отправляют
events. Compiler-owned textarea/send button остаются 44×44 и доступны по
клавиатуре через fixed runtime.

- [ ] **Step 5: Добавить motion ports**

`spring-reveal@2`/`soft-scale@2` имеют пустой или purely decorative fragment и
CSS только для `.kaigo-preview-open [data-region="panel"]`, closing state,
hover/focus и reduced motion. Никакого `requestAnimationFrame`.

- [ ] **Step 6: Добавить один canonical hash writer и пересчитать hashes**

Экспортировать `compute_implementation_hash()` из `registry.py` и использовать
его в loader, tests и `scripts/hash_pattern_manifest.py`. Скрипт принимает
catalog root, обрабатывает только manifest schema v2, переписывает только
`implementation_sha256` и завершает работу ненулевым кодом при invalid source.
Отдельный алгоритм в скрипте запрещён.

RED test:

```python
def test_hash_writer_uses_registry_canonical_digest(tmp_path):
    pattern = write_source_pattern(tmp_path / "status-capsule-v1", digest="0" * 64)
    assert hash_main(["--catalog", str(tmp_path), "--write"]) == 0
    manifest = json.loads((pattern / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["implementation_sha256"] == compute_implementation_hash(pattern)
```

Run: `python -m pytest tests/scripts_cases/test_hash_pattern_manifest.py -q`
Expected: PASS after implementation.

Hash all new source directories:

Run: `python scripts/hash_pattern_manifest.py --catalog builder_lab/patterns/catalog --write --schema-version 2`
Expected: `updated 10 runtime_source manifests; legacy manifests unchanged`.

- [ ] **Step 7: Запустить GREEN и exact cardinality**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_registry.py tests/builder_lab_cases/test_pattern_source.py -q`
Expected: PASS; planner catalog содержит 10 source patterns (2 per category),
registry definitions — 20 total с legacy.

- [ ] **Step 8: Commit**

```bash
git add builder_lab/patterns/catalog builder_lab/patterns/registry.py scripts/hash_pattern_manifest.py tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_registry.py tests/scripts_cases/test_hash_pattern_manifest.py
git commit -m "feat: port core widget patterns to runtime source"
```

### Task 7: Добавить новые launcher и shell patterns

**Files:**
- Create: `builder_lab/patterns/catalog/status-capsule-v1/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/avatar-beacon-v1/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/docked-rail-v1/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/bottom-drawer-v1/{manifest.json,fragment.html,styles.css}`
- Modify: `tests/builder_lab_cases/test_pattern_catalog_v2.py`

- [ ] **Step 1: Написать RED-тесты category/quality metadata**

```python
@pytest.mark.parametrize(("pattern_id", "category"), [
    ("status-capsule", PatternCategory.LAUNCHER),
    ("avatar-beacon", PatternCategory.LAUNCHER),
    ("docked-rail", PatternCategory.SHELL),
    ("bottom-drawer", PatternCategory.SHELL),
])
def test_new_launcher_shell_pattern_is_selectable(pattern_id, category):
    definition = load_builtin_registry().resolve(pattern_id, 1)
    assert definition.category is category
    assert definition.integration_mode is PatternIntegrationMode.RUNTIME_SOURCE
    assert definition in load_builtin_registry().selectable_for(category)
```

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py -q`
Expected: FAIL — четыре IDs отсутствуют.

- [ ] **Step 3: Реализовать два launcher языка**

- `status-capsule`: горизонтальная компактная капсула «AI на связи» с
  status-dot, минимум 44px, внимание через один мягкий runtime pulse;
- `avatar-beacon`: круглый/скошенный avatar mark с отдельным badge, но без
  remote image, aggressive infinite attention или текста меньше читаемого.

Оба используют русский `description`, не прописывают click logic и сохраняют
полную видимость на 390px viewport.

- [ ] **Step 4: Реализовать два shell языка**

- `docked-rail`: компактная панель, визуально пристыкованная к краю, но её
  bounding box всегда внутри viewport и launcher остаётся доступен;
- `bottom-drawer`: панель поднимается снизу, но на mobile занимает не больше
  78dvh и оставляет минимум 12px внешнего контекста; это не fullscreen sheet.

CSS не меняет DOM hierarchy и стилизует все peer regions из compiler.

- [ ] **Step 5: Записать canonical hashes новых четырёх manifests**

Run: `python scripts/hash_pattern_manifest.py --catalog builder_lab/patterns/catalog --write --schema-version 2`
Expected: `updated 4 runtime_source manifests; 10 already current; legacy manifests unchanged`.

- [ ] **Step 6: Запустить GREEN**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_source.py -q`
Expected: PASS; launcher/shell selectable count стал 4/4.

- [ ] **Step 7: Commit**

```bash
git add builder_lab/patterns/catalog/status-capsule-v1 builder_lab/patterns/catalog/avatar-beacon-v1 builder_lab/patterns/catalog/docked-rail-v1 builder_lab/patterns/catalog/bottom-drawer-v1 tests/builder_lab_cases/test_pattern_catalog_v2.py
git commit -m "feat: add launcher and shell pattern sources"
```

### Task 8: Добавить новые messages и composer patterns

**Files:**
- Create: `builder_lab/patterns/catalog/labelled-strips-v1/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/avatar-thread-v1/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/inset-textarea-v1/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/stacked-compose-v1/{manifest.json,fragment.html,styles.css}`
- Modify: `tests/builder_lab_cases/test_pattern_catalog_v2.py`
- Modify: `tests/builder_lab_cases/test_pattern_quality.py`

- [ ] **Step 1: Написать RED chat/composer quality tests**

```python
@pytest.mark.parametrize("pattern_id", [
    "paired-bubbles", "advisor-cards", "labelled-strips", "avatar-thread",
])
def test_message_pattern_styles_initial_and_runtime_turns(pattern_id):
    definition = current_source(pattern_id)
    assert '.kaigo-widget__message--assistant' in definition.css
    assert '[data-kaigo-runtime-message="assistant"]' in definition.css
    assert '[data-kaigo-runtime-message="user"]' in definition.css
    assert '[data-kaigo-runtime-label]' in definition.css

@pytest.mark.parametrize("pattern_id", [
    "single-line-pill", "multiline-soft", "inset-textarea", "stacked-compose",
])
def test_composer_pattern_never_owns_submission(pattern_id):
    definition = current_source(pattern_id)
    assert "<form" not in definition.html
    assert "data-action" not in definition.html
    assert definition.javascript == ""
```

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_quality.py -q`
Expected: FAIL — новые IDs отсутствуют и runtime selector quality ещё не
проверяется для каждой messages версии.

- [ ] **Step 3: Реализовать два dialog языка**

- `labelled-strips`: короткая подпись автора и strip-like surface; AI слева,
  пользователь справа, widths ограничены, никакого третьего «центрального»
  типа сообщения;
- `avatar-thread`: avatar/decorative rail связывает AI turns, но label остаётся
  текстом, user turns не маскируются под AI, runtime error остаётся в stream.

Оба стилизуют статическое приветствие и реальные runtime messages одной
визуальной системой.

- [ ] **Step 4: Реализовать два composer языка**

- `inset-textarea`: textarea визуально утоплена в surface, send выделен и имеет
  44×44, native focus ring не уничтожен;
- `stacked-compose`: поле над full-width send row, но first-open transcript всё
  ещё помещается; mobile клавиатура не переводит widget в fullscreen.

Fragments — decoration only. Input, label, send и interaction остаются в
compiler/runtime.

- [ ] **Step 5: Усилить category quality gate**

`validate_source_css` для messages требует selectors для assistant, user,
runtime label/content и max-width обеих сторон. Для composer требует selectors
input/textarea, send, `:focus-visible`/`:focus-within`, disabled/pending и
actual min-size 44px в compiled browser measurement (не только текст в CSS).

- [ ] **Step 6: Записать canonical hashes новых четырёх manifests**

Run: `python scripts/hash_pattern_manifest.py --catalog builder_lab/patterns/catalog --write --schema-version 2`
Expected: `updated 4 runtime_source manifests; 14 already current; legacy manifests unchanged`.

- [ ] **Step 7: Запустить GREEN**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_quality.py tests/builder_lab_cases/test_pattern_source.py -q`
Expected: PASS; messages/composer selectable count стал 4/4.

- [ ] **Step 8: Commit**

```bash
git add builder_lab/patterns/catalog/labelled-strips-v1 builder_lab/patterns/catalog/avatar-thread-v1 builder_lab/patterns/catalog/inset-textarea-v1 builder_lab/patterns/catalog/stacked-compose-v1 tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_quality.py
git commit -m "feat: add conversation and composer pattern sources"
```

### Task 9: Добавить новые motion patterns без JavaScript

**Files:**
- Create: `builder_lab/patterns/catalog/side-glide-v1/{manifest.json,fragment.html,styles.css}`
- Create: `builder_lab/patterns/catalog/fade-settle-v1/{manifest.json,fragment.html,styles.css}`
- Modify: `tests/builder_lab_cases/test_pattern_catalog_v2.py`
- Modify: `tests/builder_lab_cases/test_pattern_quality.py`

- [ ] **Step 1: Написать RED motion contract**

```python
@pytest.mark.parametrize("pattern_id", [
    "spring-reveal", "soft-scale", "side-glide", "fade-settle",
])
def test_motion_pattern_uses_runtime_states_and_reduced_motion(pattern_id):
    definition = current_source(pattern_id)
    css = definition.css
    assert (
        ".kaigo-preview-open" in css or '[data-open]' in css
    )
    assert "prefers-reduced-motion: reduce" in css
    assert "setTimeout" not in definition.html
    assert definition.javascript == ""

def test_motion_source_rejects_private_state_selector(tmp_path):
    definition = source_definition(category="motion")
    with pytest.raises(PatternSourceError, match="runtime state"):
        validate_source_css(definition, exact_scope("[data-opening='true']{opacity:1}"))
```

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_quality.py -q`
Expected: FAIL — новые motion IDs отсутствуют.

- [ ] **Step 3: Реализовать `side-glide`**

Panel входит с коротким lateral travel, launcher получает только immediate
hover/active response. Никакого auto-open, собственного attention loop или
постоянного движения transcript.

- [ ] **Step 4: Реализовать `fade-settle`**

Panel проявляется через opacity + небольшое settle displacement без blur,
который ухудшает читаемость. Closing state короче opening, reduced motion
сводит transition к немедленной смене состояния.

- [ ] **Step 5: Записать canonical hashes двух новых manifests**

Run: `python scripts/hash_pattern_manifest.py --catalog builder_lab/patterns/catalog --write --schema-version 2`
Expected: `updated 2 runtime_source manifests; 18 already current; legacy manifests unchanged`.

- [ ] **Step 6: Запустить GREEN и exact 20 catalog check**

```python
def test_planner_catalog_has_exactly_four_sources_per_category():
    registry = load_builtin_registry()
    assert len(registry.planner_catalog()) == 20
    assert all(len(registry.selectable_for(category)) == 4 for category in PatternCategory)
```

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_quality.py -q`
Expected: PASS; registry definitions = 30 (10 legacy + 20 source), planner
catalog = 20.

- [ ] **Step 7: Commit**

```bash
git add builder_lab/patterns/catalog/side-glide-v1 builder_lab/patterns/catalog/fade-settle-v1 tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_quality.py
git commit -m "feat: add runtime owned motion pattern sources"
```

### Task 10: Проверить весь каталог, а не только happy-path композицию

**Files:**
- Modify: `tests/builder_lab_cases/test_pattern_catalog_v2.py`
- Modify: `tests/builder_lab_cases/test_pattern_source.py`
- Modify: `builder_lab/browser_audit.py`
- Modify: `tests/builder_lab_cases/test_browser_audit.py`

- [ ] **Step 1: Добавить exhaustive deterministic matrix**

```python
def test_every_selectable_cross_slot_composition_compiles():
    registry = load_builtin_registry()
    groups = [registry.selectable_for(category) for category in PatternCategory]
    accepted = 0
    rejected = 0
    for definitions in itertools.product(*groups):
        plan = plan_for(definitions)
        if explicitly_incompatible(definitions):
            with pytest.raises(PatternResolutionError, match="incompatible"):
                resolve_composition(plan, registry)
            rejected += 1
            continue
        source = resolve_composition(plan, registry).source
        assert source is not None
        assert validate_artifact(source.as_seed(revision=1, art_direction="Тест")) == ()
        accepted += 1
    assert accepted + rejected == 4 ** 5
    assert rejected == len(EXPLICIT_INCOMPATIBLE_COMPOSITIONS)
```

Не «исправлять» тест снижением порога: все 4⁵ комбинаций обязаны завершиться
ровно одним из двух проверяемых исходов — compiled или exact explicit reject.

- [ ] **Step 2: Запустить RED/поймать реальные conflicts**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_catalog_v2.py -q`
Expected: либо PASS, либо конкретная несовместимость manifest/source. Любую
несовместимость сначала зафиксировать двумя симметричными
`incompatible_with`, затем повторить тест; не добавлять broad exclusions.

- [ ] **Step 3: Добавить browser-аудит source provenance**

Расширить существующий `BrowserAudit` обязательным source contract, который
передаётся через `VisualGate`, worker factory и resume context. Source-mode
аудит строит документ только через `build_trusted_runtime_document`; model
preview document не считается production evidence. Аудит фиксирует в каждом
viewport/state:

- все source root classes присутствуют;
- launcher и panel целиком внутри viewport;
- closed panel не перехватывает launcher;
- close/send/suggestions/retry имеют фактические 44×44;
- open panel не fullscreen и сохраняет minimum 12px outer context;
- first-open messages не скроллятся;
- AI/runtime assistant слева, user справа, обе стороны имеют labels;
- composer видим, input доступен, send реально отправляет;
- attention прекращается после первого interaction;
- reduced motion не скрывает controls и не оставляет intermediate opacity.

- [ ] **Step 4: Написать RED browser-gate tests на регрессии**

```python
@pytest.mark.asyncio
async def test_browser_gate_rejects_missing_pattern_anchor(source_artifact):
    broken = replace(source_artifact, body_html=source_artifact.body_html.replace(
        "kaigo-pattern-motion-side-glide", ""
    ))
    with pytest.raises(BrowserAuditError, match="pattern source anchor"):
        await BrowserAudit(expected_pattern_root_classes=(
            "kaigo-pattern-motion-side-glide",
        )).audit(broken)

@pytest.mark.asyncio
async def test_bottom_drawer_is_not_mobile_fullscreen(bottom_drawer_artifact):
    report = await BrowserAudit(
        source_contract=bottom_drawer_source_contract,
    ).audit_trusted_runtime(bottom_drawer_artifact)
    mobile = layout(report, "mobile", "open")
    assert mobile.panel_box.height <= mobile.viewport.height * 0.78
    assert mobile.panel_box.top >= 12
```

- [ ] **Step 5: Запустить browser suite**

Run: `python -m pytest tests/builder_lab_cases/test_browser_audit.py -q`
Expected: PASS. Это automated regression, но не заменяет in-app visual review
следующей задачи.

- [ ] **Step 6: Commit**

```bash
git add builder_lab/browser_audit.py tests/builder_lab_cases/test_browser_audit.py tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_source.py
git commit -m "test: gate pattern sources across runtime states"
```

### Task 11: Сделать визуальный showcase из production compiler и проверить in-app Browser

**Files:**
- Create: `builder_lab/patterns/showcase.py`
- Create: `scripts/run_pattern_showcase.py`
- Create: `tests/builder_lab_cases/test_pattern_showcase.py`
- Create: `docs/evidence/2026-07-30-ui-pattern-library-v2/README.md`
- Create: `docs/evidence/2026-07-30-ui-pattern-library-v2/*.png`

- [ ] **Step 1: Написать RED-тест, запрещающий отдельную mock-разметку**

```python
def test_showcase_embeds_only_compiler_documents():
    matrix = showcase_matrix(load_builtin_registry())
    page = render_showcase(matrix)
    assert len(matrix) >= 16
    for entry in matrix:
        compiled = compile_runtime_source(entry.plan, load_builtin_registry())
        assert compiled.source_sha256 in page
        assert escape(build_trusted_runtime_document(compiled.as_seed(
            revision=1, art_direction="Showcase"
        ))) in page
```

Тест может проверять generated `srcdoc`/JSON bootstrap вместо literal escaped
document, но source обязан происходить из `compile_runtime_source()` и
`build_trusted_runtime_document()`. Запрещён отдельный showcase-only widget.

- [ ] **Step 2: Запустить RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_showcase.py -q`
Expected: FAIL — showcase отсутствует.

- [ ] **Step 3: Создать проверяемую pairwise matrix минимум из 16 композиций**

Каждый из 20 source patterns встречается минимум два раза, а matrix содержит:

- спокойный baseline;
- максимально выразительную связку;
- compact/docked/bottom drawer варианты;
- оба новых message языка;
- все четыре composer варианта;
- все четыре motion варианта;
- mobile-sensitive и long Russian copy варианты.

Тест автоматически вычисляет pairwise coverage для всех пар slot factors и
требует каждое из 16 сочетаний значений для каждой пары. Ручное число строк без
coverage check не является доказательством.

Страница показывает pattern IDs/versions, source hash, Desktop/Mobile toggle и
кнопки `Открыть`, `Отправить`, `Ошибка`, `Повторить`, но эти кнопки общаются с
тем же fixed preview runtime, а не подменяют его.

- [ ] **Step 4: Реализовать localhost runner**

`scripts/run_pattern_showcase.py` слушает только `127.0.0.1`, по умолчанию порт
`18092`, не читает секреты и отдаёт `/` + health endpoint. Он не добавляется в
production compose/nginx.

- [ ] **Step 5: Запустить GREEN**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_showcase.py -q`
Expected: PASS.

- [ ] **Step 6: Провести визуальную проверку именно через in-app Browser skill**

1. Прочитать актуальный `browser:control-in-app-browser` SKILL.md.
2. Запустить `python scripts/run_pattern_showcase.py --port 18092`.
3. Открыть `http://127.0.0.1:18092/` во встроенном браузере Codex.
4. Проверить 1440×900, 601×700 и 390×844; для каждого matrix case открыть,
   ввести русский вопрос, отправить, дождаться AI fixture response, закрыть и
   открыть снова.
5. Отдельно проверить error/retry и `prefers-reduced-motion`.
6. Сделать screenshots не только панели крупно, но и полного viewport, чтобы
   видеть подчинение host page.

Terminal Playwright screenshots не считаются визуальной приёмкой этой задачи;
они остаются только внутри automated BrowserAudit. Если in-app Browser
временно не отвечает, перезапустить его по инструкции skill, а в evidence
честно записать blocker вместо замены внешним Chrome.

- [ ] **Step 7: Зафиксировать визуальные дефекты до commit**

В `docs/evidence/2026-07-30-ui-pattern-library-v2/README.md` для каждой matrix
строки записать viewport/state, pass/fail и screenshot path. Любой дефект
overlap, clipped copy, неясных сторон чата, target <44px, fullscreen mobile,
невидимого focus или бесконечного aggressive motion блокирует выпуск паттерна:
исправить asset/hash, повторить unit + browser check.

- [ ] **Step 8: Commit**

```bash
git add builder_lab/patterns/showcase.py scripts/run_pattern_showcase.py tests/builder_lab_cases/test_pattern_showcase.py docs/evidence/2026-07-30-ui-pattern-library-v2
git commit -m "test: add visual pattern source showcase"
```

### Task 12: Документация, review, rollout и финальная проверка

**Files:**
- Create: `docs/UI_PATTERN_LIBRARY.md`
- Create: `docs/release-evidence/2026-07-30-ui-pattern-library-v2.md`
- Modify: `docs/product-journal/2026-07.md`
- Modify: `.env.example`
- Modify: `builder_lab/config.py`
- Modify: `builder_lab/patterns/registry.py`
- Modify: `builder_lab/patterns/planner.py`
- Modify: `builder_lab/worker.py`
- Modify: `builder_lab/orchestrator.py`
- Modify: `scripts/run_builder_worker.py`
- Modify: `tests/builder_lab_cases/test_config.py`
- Modify: `tests/builder_lab_cases/test_pattern_planner.py`
- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `tests/builder_lab_cases/test_orchestrator.py`
- Modify: `tests/builder_lab_cases/test_worker_service_heartbeat.py`

- [ ] **Step 1: Добавить проверяемый rollback switch**

Не вводить второй почти одинаковый enum: config использует уже созданный
`PatternIntegrationMode`. `KAIGO_PATTERN_LIBRARY_MODE` по умолчанию и в
production остаётся `legacy_reference`; `runtime_source` включается только
явным canary opt-in после phase gate.

```python
@dataclass(frozen=True, slots=True)
class BuilderConfig:
    # existing fields
    pattern_library_mode: PatternIntegrationMode = (
        PatternIntegrationMode.LEGACY_REFERENCE
    )

def catalog_for_mode(
    self,
    mode: PatternIntegrationMode,
) -> tuple[dict[str, JSONValue], ...]:
    return tuple(
        item.public_dict()
        for item in self.definitions
        if item.status is PatternStatus.ACTIVE
        and item.integration_mode is mode
    )
```

`plan_composition(..., integration_mode=...)` получает соответствующий catalog
и включает legacy resolver flag только для `LEGACY_REFERENCE`. Worker фиксирует
`composition_integration_mode` и source version/hash в stage context, поэтому
env change влияет только на новые runs и не меняет уже выбранную композицию.
Не делать `auto` mode, который может переключиться посередине запуска.

RED tests:

```python
def test_pattern_library_mode_defaults_fail_closed_to_legacy(monkeypatch):
    monkeypatch.delenv("KAIGO_PATTERN_LIBRARY_MODE", raising=False)
    assert load_config().pattern_library_mode is PatternIntegrationMode.LEGACY_REFERENCE

def test_existing_run_uses_persisted_mode_after_config_change():
    context = {
        "composition_plan": source_complete_plan().to_dict(),
        "composition_source_version": 2,
        "composition_integration_mode": "runtime_source",
    }
    resolved = resolve_composition_context(
        context,
        load_builtin_registry(),
        configured_mode=PatternIntegrationMode.LEGACY_REFERENCE,
    )
    assert resolved.integration_mode is PatternIntegrationMode.RUNTIME_SOURCE
```

Run: `python -m pytest tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_pattern_planner.py tests/builder_lab_cases/test_worker_service_heartbeat.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_orchestrator.py -q`
Expected: PASS after minimal config wiring.

- [ ] **Step 2: Написать русскую runbook-документацию**

`docs/UI_PATTERN_LIBRARY.md` обязана объяснять:

- разницу legacy reference и runtime source;
- почему v1 нельзя редактировать;
- структуру manifest v2 и allowed assets;
- как выбрать новый version number и `supersedes`;
- как пересчитать canonical hash;
- какие unit/browser commands выполнить;
- category-specific quality checklist;
- что interactions принадлежат runtime, а не каталогу;
- как deprecate версию без удаления исторических данных.

Все user-facing examples и statuses — на русском; technical identifiers можно
оставить английскими только там, где это имена API/файлов.

- [ ] **Step 3: Запустить узкие тесты всего pattern slice**

Run:

```powershell
python -m pytest `
  tests/builder_lab_cases/test_pattern_models.py `
  tests/builder_lab_cases/test_pattern_registry.py `
  tests/builder_lab_cases/test_pattern_resolver.py `
  tests/builder_lab_cases/test_pattern_planner.py `
  tests/builder_lab_cases/test_pattern_source.py `
  tests/builder_lab_cases/test_pattern_quality.py `
  tests/builder_lab_cases/test_pattern_catalog_v2.py `
  tests/builder_lab_cases/test_pattern_showcase.py -q
```

Expected: PASS, 20 selectable sources, 10 immutable legacy definitions, zero
source JavaScript assets.

- [ ] **Step 4: Запустить integration/runtime regression**

Run:

```powershell
python -m pytest `
  tests/builder_lab_cases/test_validation.py `
  tests/builder_lab_cases/test_browser_audit.py `
  tests/builder_lab_cases/test_worker.py `
  tests/builder_lab_cases/test_orchestrator.py `
  tests/saas_cases/test_pattern_repository.py `
  tests/saas_cases/test_pattern_worker_persistence.py `
  tests/saas_cases/test_pattern_outcome_publication.py -q
```

Expected: PASS. Если suite длиннее лимита CI, разделить на две команды, но не
заменять её только unit-тестами.

- [ ] **Step 5: Проверить PostgreSQL 15**

Новая миграция не нужна: manifest v2/provenance уже помещаются в существующий
`manifest_snapshot`, а exact version/hash — в `widget_pattern_versions`.
На disposable PostgreSQL выполнить текущие pattern repository/migration tests и
проверить, что sync добавляет 20 новых строк, не изменяет 10 v1 rows и повторный
sync idempotent.

Run: `$env:KAIGO_TEST_POSTGRES_URL='<disposable-url>'; python -m pytest tests/saas_cases/test_pattern_registry_migration.py tests/saas_cases/test_pattern_repository.py tests/saas_cases/test_pattern_worker_persistence.py -m postgres -q`
Expected: PASS. URL не сохранять в history/evidence.

- [ ] **Step 6: Провести двухэтапный code review**

Использовать `requesting-code-review`: сначала spec-compliance reviewer, затем
code-quality reviewer. Блокирующие темы:

- v1 asset/hash mutation;
- обход fixed runtime через JS/events/timers;
- selector scope escape;
- возможность planner выбрать legacy в runtime-source mode;
- resume/hash drift;
- missing Russian copy/accessibility;
- source/compiler/showcase divergence.

Исправить Important/Critical findings и повторить затронутые команды.

- [ ] **Step 7: Обновить evidence и product journal только фактами**

Release evidence содержит commit, точные команды/результаты, число patterns,
source hashes, browser screenshots, PostgreSQL result, review findings и
непроверенные external ограничения. В `docs/product-journal/2026-07.md`
добавить короткую запись простым русским языком после всех PASS; Telegram не
публиковать без отдельного разрешения пользователя.

- [ ] **Step 8: Canary rollout без дорогой полной генерации первым действием**

1. Deploy code/catalog с `KAIGO_PATTERN_LIBRARY_MODE=legacy_reference`.
2. Проверить health, registry load и что v1 rows не drifted.
3. Переключить один worker/canary на `runtime_source` только при отсутствии
   активного composition stage на этом worker.
4. Выполнить один controlled generation с сохранёнными provider/cost limits.
5. Проверить `composition_source_sha256`, pattern versions, stage events,
   browser audit и финальный preview.
6. Только после canary PASS включить `runtime_source` всем новым runs.

Если canary не проходит, вернуть env в `legacy_reference`; исторические source
runs продолжаются по persisted mode, exact pattern versions и сохранённому
compiler bundle snapshot, а не перекомпилируются текущими файлами.

- [ ] **Step 9: Финальный scoped commit**

```bash
git add .env.example builder_lab/config.py builder_lab/patterns/registry.py builder_lab/patterns/planner.py builder_lab/worker.py builder_lab/orchestrator.py scripts/run_builder_worker.py tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_pattern_planner.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_orchestrator.py tests/builder_lab_cases/test_worker_service_heartbeat.py docs/UI_PATTERN_LIBRARY.md docs/release-evidence/2026-07-30-ui-pattern-library-v2.md docs/product-journal/2026-07.md
git commit -m "docs: document verified pattern source rollout"
```

---

## Category-specific Definition of Done

### Launcher

- closed state сразу читается как AI action, а не декоративная картинка;
- реальный target минимум 44×44, полностью внутри desktop/mobile viewport;
- attention запускается только `.kaigo-preview-attention`, мягкий и конечный;
- после первого interaction cue больше не возвращается;
- hover/focus/active различимы, reduced motion не скрывает feedback.

### Shell / открытый чат

- panel подчинён host page, не становится fullscreen;
- desktop ориентир 320–440 CSS px; mobile 64–78dvh и внешние поля ≥12px;
- header, messages, suggestions, composer — peer regions внутри panel;
- закрытая panel не блокирует launcher/host page;
- close всегда видим и минимум 44×44.

### Messages

- AI слева, пользователь справа, разные surfaces и видимые author labels;
- initial assistant и runtime-injected turns используют один visual language;
- максимум два коротких quick reply до первого вопроса;
- pending/error/retry остаются частью transcript, не третьей колонкой;
- первый экран чата не превращается в каталог/лендинг и не скроллится.

### Composer

- никакого `<form>` и собственной submit/event логики;
- label, input/textarea, send доступны с клавиатуры и screen reader;
- send минимум 44×44, focus не обрезан, pending/disabled очевидны;
- системный scrollbar не становится частью дизайна;
- русский placeholder короткий и понятный.

### Motion

- open/close/attention реагируют только на реальные runtime states;
- нет JS, timers, custom events, auto-open и частого бесконечного attention;
- движения не меняют hit target и не оставляют invisible overlay;
- reduced motion переводит transition в мгновенное доступное состояние;
- motion поддерживает выбранную геометрию, а не маскирует layout defects.

## Главные риски и stop rules

1. **Исторический drift.** Нельзя менять ни байт в текущих `*-v1` directories.
   Любая правка — новая version и новый hash.
2. **Двойной runtime.** Если source asset требует JS, timer, custom event или
   собственный send/open handler, паттерн отклоняется, а не получает исключение.
3. **Prompt-only откат.** Source считается внедрённым только если compiled seed
   реально поступает в foundation и source anchors проверяются после каждой
   model revision.
4. **Скрытая CSS-утечка.** Один unscoped selector блокирует весь pattern version.
5. **Комбинаторные конфликты.** Нельзя доказывать качество только одной красивой
   связкой; exhaustive compile + 12-case browser matrix обязательны.
6. **Фальшивая визуальная приёмка.** Showcase обязан использовать production
   compiler/trusted runtime и проверяться через Codex in-app Browser.
7. **Плохой чат при красивом shell.** Любой результат без лево/право dialog,
   runtime turns, понятного composer или first-open fit не проходит gate.
8. **Модель удаляет source.** Missing root class/anchor — deterministic repair
   issue; после исчерпания repair лимита run завершается последней доступной
   версией только по общей fallback policy, но не получает статус verified.
