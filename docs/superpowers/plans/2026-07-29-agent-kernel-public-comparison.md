# Agent Kernel Public Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a safe interactive GLM-5.2 versus GPT-5.5 Agent Kernel comparison under the existing Kaigo comparison routes.

**Architecture:** A tested Python packager reads the redacted frozen benchmark report and an explicit allowlist of two previews plus twelve screenshots. It generates one self-contained comparison hub and two frozen preview routes in an ignored output directory, which is copied atomically to the existing production static comparison root without restarting the application.

**Tech Stack:** Python 3.11+, pytest, static HTML/CSS/JavaScript, nginx static files, in-app Codex Browser.

---

## File map

- `scripts/build_agent_kernel_public_comparison.py` — validates inputs, copies the explicit public allowlist and renders the hub.
- `tests/model_cases/test_build_agent_kernel_public_comparison.py` — proves allowlist, redaction, metrics and sandbox contracts.
- `output/comparison/agent-kernel-v1/**` — ignored generated package used for local and production validation.
- `docs/model-benchmarks/2026-07-29-agent-kernel-frozen-v1.json` — existing redacted metrics source.
- `data/benchmarks/private/agent-kernel-frozen-v1-final/**` — existing private source; only explicit previews/screenshots may be copied.

### Task 1: Public-package contract

**Files:**
- Create: `tests/model_cases/test_build_agent_kernel_public_comparison.py`
- Create: `scripts/build_agent_kernel_public_comparison.py`

- [ ] **Step 1: Write the failing allowlist and redaction tests**

```python
def test_build_copies_only_public_allowlist_and_omits_request_ids(tmp_path):
    output = build_public_comparison(fixture_inputs(tmp_path), tmp_path / "out")
    files = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert files == expected_public_files()
    assert "request_ids" not in (output / "index.html").read_text(encoding="utf-8")


def test_build_rejects_unexpected_models(tmp_path):
    inputs = fixture_inputs(tmp_path, models=("glm-5.2", "other"))
    with pytest.raises(ValueError, match="exactly glm-5.2 and gpt-5.5"):
        build_public_comparison(inputs, tmp_path / "out")
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python -m pytest tests/model_cases/test_build_agent_kernel_public_comparison.py -q`  
Expected: collection fails because the builder module does not exist.

- [ ] **Step 3: Implement the minimal validated builder API**

```python
@dataclass(frozen=True)
class ComparisonInputs:
    report: Path
    private_root: Path


def build_public_comparison(inputs: ComparisonInputs, output: Path) -> Path:
    report = _load_report(inputs.report)
    _validate_models(report)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        _copy_allowlisted_files(inputs.private_root, temporary)
        (temporary / "index.html").write_text(_render_page(report), encoding="utf-8")
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output
```

- [ ] **Step 4: Run tests and confirm GREEN**

Run: `python -m pytest tests/model_cases/test_build_agent_kernel_public_comparison.py -q`  
Expected: all tests pass.

- [ ] **Step 5: Commit the builder contract**

```bash
git add scripts/build_agent_kernel_public_comparison.py tests/model_cases/test_build_agent_kernel_public_comparison.py
git commit -m "feat: build public agent kernel comparison"
```

### Task 2: Comparison experience

**Files:**
- Modify: `scripts/build_agent_kernel_public_comparison.py`
- Modify: `tests/model_cases/test_build_agent_kernel_public_comparison.py`

- [ ] **Step 1: Write failing page-contract tests**

```python
def test_page_contains_live_frames_metrics_and_honest_verdict(tmp_path):
    page = build_fixture(tmp_path).joinpath("index.html").read_text(encoding="utf-8")
    assert 'sandbox="allow-scripts"' in page
    assert "allow-same-origin" not in page
    assert "162,38 ₽" in page and "101,10 ₽" in page
    assert "Оба результата не прошли browser gate" in page
    assert "horizontal overflow" in page


def test_page_has_mobile_and_evidence_controls(tmp_path):
    page = build_fixture(tmp_path).joinpath("index.html").read_text(encoding="utf-8")
    assert 'data-viewport="mobile"' in page
    assert 'data-state="after_turn_2"' in page
    assert 'aria-pressed="false"' in page
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python -m pytest tests/model_cases/test_build_agent_kernel_public_comparison.py -q`  
Expected: new assertions fail because the full hub is not rendered yet.

- [ ] **Step 3: Render the complete responsive hub**

Implement a Kaigo-styled static page with live/evidence/metrics/process sections, two sandboxed preview frames, desktop/mobile buttons, screenshot-state buttons, Russian copy, exact frozen metrics and a small script that only toggles local classes, iframe sizes and image sources.

- [ ] **Step 4: Run focused and existing comparison tests**

Run: `python -m pytest tests/model_cases/test_build_agent_kernel_public_comparison.py tests/builder_lab_cases/test_comparison.py -q`  
Expected: all tests pass.

- [ ] **Step 5: Commit the experience**

```bash
git add scripts/build_agent_kernel_public_comparison.py tests/model_cases/test_build_agent_kernel_public_comparison.py
git commit -m "feat: render interactive model comparison"
```

### Task 3: Build and local Browser acceptance

**Files:**
- Generate: `output/comparison/agent-kernel-v1/**`

- [ ] **Step 1: Build from the real frozen artifacts**

Run:

```powershell
python scripts/build_agent_kernel_public_comparison.py `
  --report docs/model-benchmarks/2026-07-29-agent-kernel-frozen-v1.json `
  --private-root data/benchmarks/private/agent-kernel-frozen-v1-final `
  --output output/comparison/agent-kernel-v1
```

Expected: a compact manifest reports 15 public files and no private-path/request-ID leakage.

- [ ] **Step 2: Run package verification**

Run: `python scripts/build_agent_kernel_public_comparison.py --verify output/comparison/agent-kernel-v1`  
Expected: `verified: true`.

- [ ] **Step 3: Serve locally and inspect with the in-app Browser**

Open the hub in the in-app Browser, click both live widgets, switch desktop/mobile and evidence states, and assert no horizontal overflow at 1440x900 and 390x844.

### Task 4: GitHub and atomic production publication

**Files:**
- No source changes; this task publishes the already verified package.

- [ ] **Step 1: Run fresh verification before push**

Run:

```powershell
python -m pytest tests/model_cases/test_build_agent_kernel_public_comparison.py tests/builder_lab_cases/test_comparison.py -q
python -m ruff check scripts/build_agent_kernel_public_comparison.py tests/model_cases/test_build_agent_kernel_public_comparison.py
git diff --check
```

Expected: zero failures and zero formatting errors.

- [ ] **Step 2: Push the exact feature branch**

Run: `git push origin codex/saas-foundation`  
Expected: remote branch points at the local reviewed SHA and no Telegram files are staged.

- [ ] **Step 3: Publish the generated package atomically**

Copy the verified local package to a new server-side temporary directory under
`/var/www/kaigo-builder-comparison`, rename it to
`agent-kernel-v1`, preserve any previous version as a timestamped rollback
directory, run `nginx -t`, and do not restart the Kaigo application.

- [ ] **Step 4: Final public Browser acceptance**

Verify:

- `https://kaigo.space/builder-comparison/agent-kernel-v1/`
- `https://kaigo.space/builder-comparison/agent-kernel-v1/glm-5.2/`
- `https://kaigo.space/builder-comparison/agent-kernel-v1/gpt-5.5/`
- both preview interactions;
- desktop/mobile and evidence controls;
- no horizontal overflow on desktop and mobile;
- existing `/builder-comparison/index.html` remains available.
