# Transparent Visual Review and Resumable Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить независимого AI-судью и code-verifier, разделить бюджеты исправлений, сохранять непубликованный draft и восстанавливать Builder Lab после reload.

**Architecture:** Три существующих критика остаются независимыми и параллельными. Новый judge семантически объединяет только замечания минимум двух ролей, executor исправляет единый список, verifier проверяет diff, а browser audit повторно проверяет реальный результат. Store разделяет verified artifact и deterministically valid draft; UI сохраняет run ID и восстанавливает историю через snapshot и SSE.

**Tech Stack:** Python 3.12, asyncio, Google GenAI SDK structured outputs, aiohttp, Playwright, unittest/pytest, vanilla JavaScript, Docker Compose.

---

### Task 1: Контракты AI-судьи и verifier

**Files:**
- Create: `builder_lab/visual_review.py`
- Create: `tests/builder_lab_cases/test_visual_review.py`
- Modify: `builder_lab/visual_committee.py`
- Modify: `tests/builder_lab_cases/test_visual_committee.py`

- [ ] **Step 1: Write failing tests**

Добавить fake judge и provider-client тесты, доказывающие:

```python
result = await judge.judge(role_results=three_differently_worded_findings)
assert result.critique.requires_repair
assert result.supporting_roles["judge-1"] == (
    "conversation_ux",
    "adversarial_customer",
)
```

Проверить отказ от выдуманного finding ID, одной supporting role и неизвестного
artifact field. Для verifier проверить полный набор исходных finding ID и
`fixed/unresolved`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_visual_review.py tests/builder_lab_cases/test_visual_committee.py -q
```

Expected: FAIL, потому что `visual_review` и judge/verifier contracts отсутствуют.

- [ ] **Step 3: Implement minimal structured clients**

Создать `GeminiVisualJudge`, `GeminiRepairVerifier`, их строгие JSON schemas,
результаты и публичные helpers. `VisualCriticCommittee` вызывает judge после
кворума и суммирует usage четырёх запросов.

- [ ] **Step 4: Verify GREEN**

Run тот же pytest. Expected: PASS.

### Task 2: Публичные замечания и понятный отчёт исполнителя

**Files:**
- Modify: `builder_lab/visual_gate.py`
- Modify: `builder_lab/prompts.py`
- Modify: `builder_lab/models.py`
- Test: `tests/builder_lab_cases/test_visual_repair_gate.py`
- Test: `tests/builder_lab_cases/test_prompts.py`
- Test: `tests/builder_lab_cases/test_models.py`

- [ ] **Step 1: Write failing tests**

Проверить, что `visual_critic.completed`, `visual_judge.completed`,
`visual_audit.blocked` и `visual_verifier.completed` содержат публичные `issues`,
а repair event использует `change_summary`.

Проверить prompt:

```python
assert "без CSS-селекторов" in prompt
assert '"change_summary"' in allowed_fields
```

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_prompts.py -q
```

Expected: FAIL на отсутствующих событиях, issues и новом контракте отчёта.

- [ ] **Step 3: Implement events and report contract**

Преобразовать visual findings в безопасные `ValidationIssue`, публиковать raw-role
и judge summaries, подключить verifier после repair. Разрешить менять только
`change_summary` как metadata плюс явно разрешённые judge поля.

- [ ] **Step 4: Verify GREEN**

Run тот же pytest. Expected: PASS.

### Task 3: Раздельные бюджеты и детерминированная нормализация

**Files:**
- Modify: `builder_lab/visual_gate.py`
- Modify: `builder_lab/validation.py`
- Modify: `builder_lab/models.py`
- Test: `tests/builder_lab_cases/test_visual_repair_gate.py`
- Test: `tests/builder_lab_cases/test_validation.py`
- Test: `tests/builder_lab_cases/test_models.py`

- [ ] **Step 1: Write failing regressions**

Сценарий должен потратить browser repair и deterministic cleanup, затем всё ещё
иметь полный visual budget:

```python
assert counters.browser_repairs == 1
assert counters.validation_repairs == 1
assert counters.visual_repairs == request.visual_repair_limit
```

Проверить default `visual_repair_limit == 8`, максимум 10 и удаление трёх
зарезервированных runtime attributes без model call.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_validation.py tests/builder_lab_cases/test_models.py -q
```

Expected: FAIL из-за общего repair_count и текущего диапазона 0..5.

- [ ] **Step 3: Implement separated counters**

Добавить отдельные browser/validation/visual counters, использовать request limit
только для visual repairs и отдельный semantic-stagnation counter.

- [ ] **Step 4: Verify GREEN**

Run тот же pytest. Expected: PASS.

### Task 4: Проверяемый draft при failed visual gate

**Files:**
- Modify: `builder_lab/models.py`
- Modify: `builder_lab/store.py`
- Modify: `builder_lab/orchestrator.py`
- Modify: `builder_lab/web.py`
- Test: `tests/builder_lab_cases/test_store.py`
- Test: `tests/builder_lab_cases/test_orchestrator.py`
- Test: `tests/builder_lab_cases/test_web.py`

- [ ] **Step 1: Write failing tests**

Проверить, что failed run возвращает:

```python
assert snapshot.artifact.revision == 4
assert snapshot.draft_artifact.revision == 5
assert snapshot.quality_status == "needs_repair"
```

Preview revision 5 должен вернуть 200, а chat revision 5 — 409.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_store.py tests/builder_lab_cases/test_orchestrator.py tests/builder_lab_cases/test_web.py -q
```

Expected: FAIL, потому что snapshot и preview не знают о draft.

- [ ] **Step 3: Implement draft storage and preview**

Хранить только последний deterministically valid draft. Разрешить его sandboxed
preview, но не добавлять в verified artifact registry и не разрешать chat/refine.

- [ ] **Step 4: Verify GREEN**

Run тот же pytest. Expected: PASS.

### Task 5: Reload recovery и полный UI-текст

**Files:**
- Modify: `builder_lab/ui.py`
- Modify: `tests/builder_lab_cases/test_web.py`
- Modify: `tests/builder_lab_cases/test_demo_browser.py`

- [ ] **Step 1: Write failing browser and HTML tests**

Искусственно создать running run и события без Gemini. Открыть Builder, записать
его ID через штатный UI-flow, reload и проверить:

```python
await page.reload()
await expect(page.locator("#status")).to_contain_text("running")
assert await page.locator(".event-message").count() >= 2
```

Отдельно проверить failed run с draft preview и отсутствие CSS ellipsis.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_web.py tests/builder_lab_cases/test_demo_browser.py -q
```

Expected: FAIL, потому что local run ID не сохраняется и текст обрезается.

- [ ] **Step 3: Implement recovery and wrapping**

Добавить namespaced localStorage key, `resumeStoredRun()`, восстановление формы,
snapshot, preview и SSE. Рендерить event issues списком и переносить status/art
direction без ellipsis.

- [ ] **Step 4: Verify GREEN**

Run тот же pytest. Expected: PASS.

### Task 6: Production wiring, verification and deploy

**Files:**
- Modify: `builder_lab/config.py`
- Modify: `scripts/run_builder_lab.py`
- Modify: `.env.example`
- Modify: `docs/KAIGO_BUILDER_LAB_OPERATIONS.md`
- Test: `tests/builder_lab_cases/test_config.py`
- Test: `tests/builder_lab_cases/test_run_builder_lab.py`

- [ ] **Step 1: Write failing wiring tests**

Проверить production factory: три critics, отдельный judge и отдельный verifier
используют настроенную модель/thinking/timeout.

- [ ] **Step 2: Verify RED and implement wiring**

```powershell
python -m pytest tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_run_builder_lab.py -q
```

Expected initial FAIL, final PASS.

- [ ] **Step 3: Full local verification**

```powershell
python -m pytest tests/builder_lab_cases -q
python -m compileall builder_lab scripts
git diff --check
```

Expected: all tests PASS, compileall exit 0, no whitespace errors.

- [ ] **Step 4: Commit, push and deploy exact commit**

```powershell
git add builder_lab scripts tests .env.example docs
git commit -m "Make visual review transparent and resumable"
git push origin codex/gemini-technical-foundation
```

На сервере пересобрать только `builder-lab` из exact commit.

- [ ] **Step 5: In-app Browser verification without Gemini generation**

Через встроенный браузер проверить:

- deployed release marker;
- восстановление искусственного run после reload;
- полную историю и перенос текста;
- failed draft preview;
- отсутствие page/console errors.

Полноценную генерацию виджета не запускать.
