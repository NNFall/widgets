# Visual Critic Committee Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Разделить browser/AI бюджеты и проверять каждый готовый виджет тремя независимыми визуальными ролями без ложного `visual_quality_failed` при ошибке ответа Gemini.

**Architecture:** `GeminiVisualCritic` получает неизменяемую роль и самостоятельно делает один корректирующий повтор контракта. Новый `VisualCriticCommittee` параллельно запускает три роли, формирует кворум и объединяет только доказанные замечания. `VisualRepairGate` отдельно считает browser capture attempts, AI review cycles и ремонты кандидата.

**Tech Stack:** Python 3.12, asyncio, Google GenAI SDK, Playwright browser audit, unittest/pytest, aiohttp, Docker Compose.

---

### Task 1: Надёжный контракт отдельного Gemini-критика

**Files:**
- Modify: `builder_lab/visual_critic.py`
- Modify: `builder_lab/visual_models.py`
- Test: `tests/builder_lab_cases/test_visual_critic.py`

- [ ] **Step 1: Write the failing tests**

Добавить тесты, которые передают два последовательных ответа fake client:
первый с недопустимым `finding_id` или неконкретным observation, второй
валидный. Проверить, что `critique()` возвращает второй результат, суммирует
usage и делает ровно два provider-вызова. Отдельный тест проверяет, что роль
попадает в system instruction.

```python
critic = GeminiVisualCritic(
    client=FakeSequenceClient([invalid_payload, valid_payload]),
    role=VisualCriticRole.CONVERSATION_UX,
)
result = await critic.critique(audit=report(), brief="Brief", art_direction="Direction")
assert result.critique.verdict is VisualVerdict.PASS
assert len(critic._client.calls) == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_visual_critic.py -k "contract_retry or critic_role" -q
```

Expected: FAIL because `VisualCriticRole`, `role` and correction retry do not
exist.

- [ ] **Step 3: Implement the minimal role and retry**

Добавить enum:

```python
class VisualCriticRole(str, Enum):
    CONVERSATION_UX = "conversation_ux"
    BRAND_MOTION = "brand_motion"
    ADVERSARIAL_CUSTOMER = "adversarial_customer"
```

`GeminiVisualCritic.critique()` делает максимум два `_critique_once()` вызова.
Первый `invalid_visual_critique` или `visual_evidence_unproven` превращается в
короткую недоверенную correction-инструкцию для второго вызова. Usage обоих
ответов суммируется. В system instruction добавляется только заранее
определённый текст выбранной роли.

Перед `VisualCritique.from_dict()` заменить каждый model-generated
`finding_id` на детерминированный безопасный ID:

```python
item["finding_id"] = f"{self.role.value}-{index + 1}"
```

- [ ] **Step 4: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_critic.py -k "contract_retry or critic_role or finding_id" -q
```

Expected: PASS.

### Task 2: Независимый комитет и кворум

**Files:**
- Create: `builder_lab/visual_committee.py`
- Create: `tests/builder_lab_cases/test_visual_committee.py`
- Modify: `builder_lab/visual_models.py`

- [ ] **Step 1: Write committee tests**

Покрыть четыре решения:

```python
assert (await committee(two_passes_one_failure)).critique.requires_repair is False
assert (await committee(two_matching_majors)).critique.requires_repair is True
assert (await committee(one_evidenced_blocker)).critique.requires_repair is True
with pytest.raises(VisualCommitteeError, match="visual_review_inconclusive"):
    await committee(one_pass_two_failures)
```

Также проверить независимые instances, параллельный запуск, суммарный usage,
уникальные IDs и закрытие всех трёх критиков.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_committee.py -q
```

Expected: collection FAIL because `builder_lab.visual_committee` does not
exist.

- [ ] **Step 3: Implement committee**

Создать:

```python
class VisualCriticCommittee:
    def __init__(self, factories: Mapping[VisualCriticRole, Callable[[], VisualCritic]]): ...
    async def critique(self, *, audit, brief: str, art_direction: str) -> VisualCriticResult: ...
    async def aclose(self) -> None: ...
```

Три `critique()` запускаются через `asyncio.gather(..., return_exceptions=True)`.
Два валидных ответа образуют кворум. `blocker` проходит от одной роли;
`major` группируется по screenshot/category/semantic region/artifact fields и
проходит при двух разных ролях. В результат попадает максимум три blocking
группы, а usage суммируется.

- [ ] **Step 4: Run committee tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_committee.py -q
```

Expected: PASS.

### Task 3: Раздельные бюджеты VisualRepairGate

**Files:**
- Modify: `builder_lab/visual_gate.py`
- Test: `tests/builder_lab_cases/test_visual_repair_gate.py`

- [ ] **Step 1: Write failing regression tests**

Добавить сценарий последнего production-сбоя: четыре browser-attempts,
затем два технических сбоя AI и успешный комитет. Проверить, что:

```python
assert len(auditor.calls) == 4
assert len(committee.calls) == 3
assert len(engine.calls) == 0
assert result == candidate
```

Отдельно проверить, что исчерпание AI contract/quorum возвращает
`visual_review_inconclusive`, а доказанный repair exhaustion —
`visual_quality_failed`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_repair_gate.py -k "independent or inconclusive" -q
```

Expected: FAIL because текущий общий `MAX_VISUAL_AUDITS` исчерпывается.

- [ ] **Step 3: Split counters**

Заменить общий цикл тремя счётчиками:

```python
MAX_BROWSER_CAPTURE_ATTEMPTS = 6
MAX_AI_REVIEW_ATTEMPTS = 3
MAX_VISUAL_REPAIRS = 5
```

Browser retries происходят внутри capture-фазы и не уменьшают
`MAX_AI_REVIEW_ATTEMPTS`. После реального ремонта начинается новый capture.
Технические ошибки комитета повторяют AI review на тех же доказательствах и
после исчерпания поднимают `BuilderEngineError("visual_review_inconclusive")`.

- [ ] **Step 4: Run gate tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_repair_gate.py -q
```

Expected: PASS.

### Task 4: Runtime wiring и события

**Files:**
- Modify: `scripts/run_builder_lab.py`
- Modify: `builder_lab/orchestrator.py`
- Modify: `builder_lab/config.py`
- Modify: `.env.example`
- Modify: `tests/builder_lab_cases/test_config.py`
- Modify: `tests/builder_lab_cases/test_packaging.py`
- Modify: `tests/builder_lab_cases/test_orchestrator.py`

- [ ] **Step 1: Write failing wiring tests**

Проверить, что production factory создаёт ровно роли
`conversation_ux`, `brand_motion`, `adversarial_customer`, все используют
настроенные model/thinking/timeout, а публичная ошибка
`visual_review_inconclusive` не заменяется на `visual_quality_failed`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_packaging.py tests/builder_lab_cases/test_orchestrator.py -q
```

Expected: FAIL на новом committee wiring.

- [ ] **Step 3: Wire committee**

В `build_app()` заменить одиночный factory на `VisualCriticCommittee` с тремя
отдельными `GeminiVisualCritic`. Сохранить текущие
`GEMINI_VISUAL_CRITIC_MODEL=gemini-3.6-flash`,
`GEMINI_VISUAL_CRITIC_THINKING_LEVEL=high` и timeout. Добавить события с
названием роли и итогом кворума без записи приватных model diagnostics в
публичное сообщение.

- [ ] **Step 4: Run wiring tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_packaging.py tests/builder_lab_cases/test_orchestrator.py -q
```

Expected: PASS.

### Task 5: Полная проверка, GitHub и сервер

**Files:**
- Modify: `docs/KAIGO_BUILDER_LAB_OPERATIONS.md`

- [ ] **Step 1: Document operator semantics**

Описать три роли, раздельные бюджеты и отличие
`visual_review_inconclusive` от `visual_quality_failed`.

- [ ] **Step 2: Run verification**

```powershell
python -m pytest tests/builder_lab_cases -q
python -m compileall builder_lab scripts
git diff --check
```

Expected: all tests PASS, compileall exit 0, no diff errors.

- [ ] **Step 3: Commit and push**

```powershell
git add builder_lab scripts tests .env.example docs
git commit -m "Add independent visual critic committee"
git push origin codex/gemini-technical-foundation
```

- [ ] **Step 4: Deploy exact commit**

На `/root/ai_project` получить ветку, пересобрать
`ai_project_builder_lab`, проверить container health и HTTP:

```bash
docker compose up -d --build builder-lab
curl -fsS http://127.0.0.1:8091/ >/dev/null
docker inspect ai_project_builder_lab --format '{{.State.Status}}'
```

Expected: HTTP 200 and `running`.

- [ ] **Step 5: Verify in in-app Browser**

Открыть `http://127.0.0.1:18091/`, проверить загрузку формы и отсутствие
console/page errors. Не запускать генерацию вместо пользователя.
