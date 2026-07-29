"""Build the public, redacted Agent Kernel model comparison package."""

from __future__ import annotations

import json
import html
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODELS = ("glm-5.2", "gpt-5.5")
SCREENSHOTS = (
    "desktop-after_turn_2.jpg",
    "desktop-closed.jpg",
    "desktop-open_initial.jpg",
    "mobile-after_turn_2.jpg",
    "mobile-closed.jpg",
    "mobile-open_initial.jpg",
)


@dataclass(frozen=True)
class ComparisonInputs:
    report: Path
    private_root: Path


def _load_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("benchmark report schema is invalid")
    runs = report.get("runs")
    if not isinstance(runs, list):
        raise ValueError("benchmark report runs are invalid")
    models = tuple(run.get("model") for run in runs if isinstance(run, dict))
    if models != MODELS:
        raise ValueError("benchmark must contain exactly glm-5.2 and gpt-5.5")
    return report


def _copy_public_files(private_root: Path, output: Path) -> None:
    for model in MODELS:
        source_root = private_root / model
        preview = source_root / "preview.html"
        if not preview.is_file() or preview.is_symlink():
            raise ValueError(f"missing safe preview for {model}")
        preview_target = output / model / "index.html"
        preview_target.parent.mkdir(parents=True)
        shutil.copyfile(preview, preview_target)

        screenshot_target = output / "assets" / model
        screenshot_target.mkdir(parents=True)
        for name in SCREENSHOTS:
            screenshot = source_root / "browser-screenshots" / name
            if not screenshot.is_file() or screenshot.is_symlink():
                raise ValueError(f"missing safe screenshot for {model}: {name}")
            shutil.copyfile(screenshot, screenshot_target / name)


def _fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _fmt_decimal(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _render_metric_cards(report: dict[str, Any]) -> str:
    cards: list[str] = []
    for run in report["runs"]:
        usage = run["usage"]
        total_tokens = int(usage["input_tokens"]) + int(usage["output_tokens"])
        model = html.escape(str(run["model"]))
        cards.append(
            f"""
            <article class="metric-card metric-card--{model.replace('.', '-')}">
              <div class="metric-card__heading">
                <div><span class="eyebrow">Модель</span><h3>{model}</h3></div>
                <span class="reject-pill">Не опубликован</span>
              </div>
              <div class="metric-grid">
                <div><span>Время</span><strong>{_fmt_decimal(float(run['elapsed_seconds']), 1)} с</strong></div>
                <div><span>Стоимость</span><strong>{_fmt_decimal(float(run['cost_rub']))} ₽</strong></div>
                <div><span>Все токены</span><strong>{_fmt_int(total_tokens)}</strong></div>
                <div><span>Visual score</span><strong>{_fmt_decimal(float(run['visual_score']), 3)}</strong></div>
                <div><span>Retry</span><strong>{int(run['retry_count'])}</strong></div>
                <div><span>Repair</span><strong>{int(run['repair_count'])} + {int(run['browser_repair_count'])} browser</strong></div>
              </div>
              <dl class="token-breakdown">
                <div><dt>Вход</dt><dd>{_fmt_int(int(usage['input_tokens']))}</dd></div>
                <div><dt>Выход</dt><dd>{_fmt_int(int(usage['output_tokens']))}</dd></div>
                <div><dt>Thinking</dt><dd>{_fmt_int(int(usage['thinking_tokens']))}</dd></div>
              </dl>
              <div class="gate-row">
                <span class="pass">Validator: пройден</span>
                <span class="fail">Browser gate: не пройден</span>
              </div>
            </article>"""
        )
    return "".join(cards)


def _evidence_map() -> dict[str, dict[str, dict[str, str]]]:
    result: dict[str, dict[str, dict[str, str]]] = {}
    for model in MODELS:
        result[model] = {}
        for viewport in ("desktop", "mobile"):
            result[model][viewport] = {
                state: f"assets/{model}/{viewport}-{state}.jpg"
                for state in ("closed", "open_initial", "after_turn_2")
            }
    return result


PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>GLM-5.2 против GPT-5.5 — Kaigo Agent Kernel</title>
  <style>
    :root{--ink:#071f33;--muted:#64727d;--paper:#fffaf6;--surface:#fff;--line:#e8ded7;--coral:#ff6438;--mint:#68b29b;--soft-coral:#fff0e9;--soft-mint:#eaf5f1;--shadow:0 24px 70px rgba(23,42,56,.1)}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at 8% 8%,#eff8f4 0,transparent 24%),radial-gradient(circle at 92% 12%,#fff0e7 0,transparent 28%),var(--paper);color:var(--ink);font:16px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
    button,a{font:inherit}a{color:inherit}.wrap{width:min(1440px,calc(100% - 40px));margin:auto}.topbar{display:flex;align-items:center;justify-content:space-between;padding:24px 0}.brand{display:flex;gap:11px;align-items:center;text-decoration:none;font-size:21px;font-weight:760}.brand-mark{display:grid;place-items:center;width:35px;height:35px;border-radius:11px;background:linear-gradient(135deg,var(--mint),#3b9d8a);color:#fff;font-weight:850}.topbar-link{text-decoration:none;color:var(--muted);font-size:14px}.hero{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(320px,.9fr);gap:42px;align-items:end;padding:58px 0 72px}.eyebrow{display:block;margin-bottom:10px;color:#4c8d7b;font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.hero h1{max-width:820px;margin:0;font-size:clamp(42px,6.3vw,92px);line-height:.94;letter-spacing:-.065em}.hero-copy{max-width:540px;margin:24px 0 0;color:var(--muted);font-size:clamp(17px,1.6vw,22px)}.verdict{border:1px solid #f2c4b5;border-radius:28px;background:rgba(255,255,255,.72);box-shadow:var(--shadow);padding:28px;backdrop-filter:blur(18px)}.verdict strong{display:block;font-size:24px;line-height:1.15}.verdict p{margin:12px 0 0;color:var(--muted)}.verdict-line{display:flex;gap:10px;align-items:center;margin-bottom:18px;color:#b73f1e;font-size:12px;font-weight:780;text-transform:uppercase;letter-spacing:.08em}.verdict-dot{width:9px;height:9px;border-radius:50%;background:var(--coral);box-shadow:0 0 0 7px var(--soft-coral)}
    .section{padding:74px 0;border-top:1px solid var(--line)}.section-heading{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:28px}.section-heading h2{margin:0;font-size:clamp(30px,4vw,54px);line-height:1;letter-spacing:-.045em}.section-heading p{max-width:570px;margin:0;color:var(--muted)}.toolbar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:22px}.toggle{border:1px solid var(--line);border-radius:999px;background:#fff;padding:10px 16px;color:var(--muted);cursor:pointer;transition:.2s ease}.toggle:hover{transform:translateY(-1px);border-color:#c7b8ae}.toggle[aria-pressed="true"]{border-color:var(--ink);background:var(--ink);color:#fff}.live-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.live-card{min-width:0;border:1px solid var(--line);border-radius:28px;background:rgba(255,255,255,.76);padding:18px;box-shadow:var(--shadow)}.live-card__top{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:14px}.live-card h3{margin:0;font-size:21px}.model-note{color:var(--muted);font-size:13px}.open-link{color:#d84c26;font-size:13px;font-weight:700;text-decoration:none}.frame-shell{width:100%;height:680px;margin:auto;overflow:hidden;border:1px solid #d8dfe1;border-radius:20px;background:#f6f7f7;transition:width .35s ease,height .35s ease}.frame-shell.is-mobile{width:min(390px,100%);height:720px}.frame-shell iframe{display:block;width:100%;height:100%;border:0;background:#fff}
    .evidence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.evidence-card{overflow:hidden;border:1px solid var(--line);border-radius:28px;background:#fff;box-shadow:var(--shadow)}.evidence-card__head{display:flex;justify-content:space-between;align-items:center;padding:18px 20px}.evidence-card h3{margin:0}.evidence-card span{color:var(--muted);font-size:13px}.evidence-canvas{display:grid;place-items:center;min-height:460px;padding:20px;background:linear-gradient(145deg,#f2eee9,#f7faf8)}.evidence-canvas img{display:block;max-width:100%;max-height:680px;border-radius:16px;box-shadow:0 18px 55px rgba(14,35,48,.18)}
    .metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.metric-card{border:1px solid var(--line);border-radius:28px;background:#fff;padding:26px;box-shadow:var(--shadow)}.metric-card__heading{display:flex;justify-content:space-between;gap:12px;align-items:start}.metric-card h3{margin:0;font-size:30px}.reject-pill{border-radius:999px;background:var(--soft-coral);padding:7px 10px;color:#ac3e20;font-size:11px;font-weight:800;text-transform:uppercase}.metric-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;margin:25px 0;background:var(--line);border:1px solid var(--line);border-radius:18px;overflow:hidden}.metric-grid div{background:#fff;padding:16px}.metric-grid span,.token-breakdown dt{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}.metric-grid strong{display:block;margin-top:4px;font-size:19px}.token-breakdown{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:0}.token-breakdown div{border-radius:15px;background:#f6f5f3;padding:13px}.token-breakdown dd{margin:3px 0 0;font-weight:740}.gate-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:20px}.gate-row span{border-radius:999px;padding:8px 11px;font-size:12px;font-weight:700}.pass{background:var(--soft-mint);color:#2d7562}.fail{background:var(--soft-coral);color:#a23a1e}
    .process{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;counter-reset:step}.step{position:relative;min-height:210px;border:1px solid var(--line);border-radius:24px;background:#fff;padding:22px;counter-increment:step}.step:before{content:"0" counter(step);display:grid;place-items:center;width:36px;height:36px;border-radius:12px;background:var(--soft-mint);color:#367d6a;font-weight:800}.step h3{margin:34px 0 8px;font-size:19px}.step p{margin:0;color:var(--muted);font-size:14px}.conclusion{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px}.conclusion article{border-radius:24px;padding:24px}.conclusion article:first-child{background:var(--soft-mint)}.conclusion article:last-child{background:var(--soft-coral)}.conclusion h3{margin:0 0 8px}.conclusion p{margin:0;color:#46545d}.footer{display:flex;justify-content:space-between;gap:20px;padding:28px 0 46px;color:var(--muted);font-size:13px}
    @media(max-width:980px){.hero{grid-template-columns:1fr}.live-grid,.evidence-grid,.metrics{grid-template-columns:1fr}.process{grid-template-columns:1fr 1fr}.frame-shell{height:720px}.section-heading{align-items:start;flex-direction:column}}
    @media(max-width:620px){.wrap{width:min(100% - 24px,1440px)}.topbar{padding:16px 0}.topbar-link{display:none}.hero{padding:36px 0 52px}.hero h1{font-size:48px}.verdict{padding:21px}.section{padding:54px 0}.section-heading h2{font-size:36px}.live-card{padding:10px;border-radius:20px}.live-card__top{padding:5px 5px 0}.frame-shell,.frame-shell.is-mobile{width:100%;height:690px}.evidence-grid{gap:14px}.evidence-canvas{min-height:360px;padding:10px}.metrics{gap:14px}.metric-card{padding:18px}.metric-grid{grid-template-columns:1fr 1fr}.token-breakdown{grid-template-columns:1fr}.process{grid-template-columns:1fr}.step{min-height:180px}.conclusion{grid-template-columns:1fr}.footer{flex-direction:column}.toggle{padding:9px 13px}}
    @media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.toggle,.frame-shell{transition:none}}
  </style>
</head>
<body>
  <header class="wrap topbar"><a class="brand" href="/"><span class="brand-mark">K</span>Kaigo</a><a class="topbar-link" href="#live">Живые виджеты ↓</a></header>
  <main>
    <section class="wrap hero">
      <div><span class="eyebrow">Замороженный benchmark · один вход</span><h1>GLM‑5.2<br>против GPT‑5.5</h1><p class="hero-copy">Один и тот же сайт, один Composition Plan и одинаковые правила Kaigo Agent Kernel. Здесь можно открыть оба результата, сравнить кадры и увидеть реальную стоимость.</p></div>
      <aside class="verdict"><div class="verdict-line"><span class="verdict-dot"></span>Честный итог</div><strong>Дешевле и визуально сильнее — GPT‑5.5. Быстрее — GLM‑5.2.</strong><p>Оба результата не прошли browser gate: остался horizontal overflow, поэтому ни один пока нельзя публиковать клиенту.</p></aside>
    </section>

    <section class="section" id="live"><div class="wrap">
      <div class="section-heading"><div><span class="eyebrow">01 · Потрогать</span><h2>Два живых результата</h2></div><p>Виджеты изолированы от страницы. Можно открыть launcher, прокрутить чат и попробовать элементы интерфейса. Сетевой ответ консультанта в этом frozen preview не подключён.</p></div>
      <div class="toolbar" aria-label="Размер живого превью"><button class="toggle" type="button" data-viewport="desktop" aria-pressed="true" aria-controls="live-comparison">Desktop</button><button class="toggle" type="button" data-viewport="mobile" aria-pressed="false" aria-controls="live-comparison">Mobile</button></div>
      <div class="live-grid" id="live-comparison">
        <article class="live-card"><div class="live-card__top"><div><h3>GLM‑5.2</h3><span class="model-note">быстрее · больше токенов</span></div><a class="open-link" href="glm-5.2/" target="_blank" rel="noopener">Открыть отдельно ↗</a></div><div class="frame-shell"><iframe src="glm-5.2/" title="GLM-5.2 frozen widget" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe></div></article>
        <article class="live-card"><div class="live-card__top"><div><h3>GPT‑5.5</h3><span class="model-note">дешевле · выше visual score</span></div><a class="open-link" href="gpt-5.5/" target="_blank" rel="noopener">Открыть отдельно ↗</a></div><div class="frame-shell"><iframe src="gpt-5.5/" title="GPT-5.5 frozen widget" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe></div></article>
      </div>
    </div></section>

    <section class="section"><div class="wrap">
      <div class="section-heading"><div><span class="eyebrow">02 · Увидеть</span><h2>Снимки проверки</h2></div><p>Одинаковые состояния desktop и mobile: закрытый launcher, первое открытие и чат после двух пользовательских действий.</p></div>
      <div class="toolbar" aria-label="Размер снимка"><button class="toggle evidence-viewport" type="button" data-viewport="desktop" aria-pressed="true" aria-controls="evidence-comparison">Desktop</button><button class="toggle evidence-viewport" type="button" data-viewport="mobile" aria-pressed="false" aria-controls="evidence-comparison">Mobile</button></div>
      <div class="toolbar" aria-label="Состояние виджета"><button class="toggle evidence-state" type="button" data-state="closed" aria-pressed="false" aria-controls="evidence-comparison">Закрыт</button><button class="toggle evidence-state" type="button" data-state="open_initial" aria-pressed="true" aria-controls="evidence-comparison">Первое открытие</button><button class="toggle evidence-state" type="button" data-state="after_turn_2" aria-pressed="false" aria-controls="evidence-comparison">После двух действий</button></div>
      <div class="evidence-grid" id="evidence-comparison"><article class="evidence-card"><div class="evidence-card__head"><h3>GLM‑5.2</h3><span data-evidence-label>Desktop · первое открытие</span></div><div class="evidence-canvas"><img data-evidence-model="glm-5.2" src="assets/glm-5.2/desktop-open_initial.jpg" alt="GLM-5.2: desktop, первое открытие"></div></article><article class="evidence-card"><div class="evidence-card__head"><h3>GPT‑5.5</h3><span data-evidence-label>Desktop · первое открытие</span></div><div class="evidence-canvas"><img data-evidence-model="gpt-5.5" src="assets/gpt-5.5/desktop-open_initial.jpg" alt="GPT-5.5: desktop, первое открытие"></div></article></div>
    </div></section>

    <section class="section"><div class="wrap"><div class="section-heading"><div><span class="eyebrow">03 · Посчитать</span><h2>Цена результата</h2></div><p>Курс для сравнения — 100 ₽ за доллар. Thinking уже входит в output usage и второй раз не прибавляется.</p></div><div class="metrics">__METRIC_CARDS__</div></div></section>

    <section class="section"><div class="wrap"><div class="section-heading"><div><span class="eyebrow">04 · Понять</span><h2>Что делал Agent Kernel</h2></div><p>Это не два одиночных запроса. Обе модели прошли одинаковый управляемый цикл, а исходный контекст и Composition Plan были заморожены.</p></div><div class="process"><article class="step"><h3>Frozen input</h3><p>Один evidence bundle, один сайт и одна задача без подмены условий между моделями.</p></article><article class="step"><h3>Composition Plan</h3><p>Kernel заранее выбрал структуру launcher, chat shell, messages, composer и motion из Pattern Registry.</p></article><article class="step"><h3>Repair loop</h3><p>Детерминированный validator и браузерная проверка возвращали конкретные проблемы на исправление.</p></article><article class="step"><h3>Publication gate</h3><p>Обе версии сохранились для исследования, но не получили право на публикацию из-за horizontal overflow.</p></article></div><div class="conclusion"><article><h3>Что показал GLM‑5.2</h3><p>Завершил прогон примерно на 11,6 минуты быстрее, но потребовал почти вдвое больше токенов и четыре retry.</p></article><article><h3>Что показал GPT‑5.5</h3><p>Стоил на 61,28 ₽ дешевле и получил visual score 0,772, но работал дольше и тоже не закрыл browser gate.</p></article></div></div></section>
  </main>
  <footer class="wrap footer"><span>Kaigo Agent Kernel · frozen comparison v1</span><span>Сгенерировано 29 июля 2026 · оба результата отклонены</span></footer>
  <script>
    const evidence=__EVIDENCE_MAP__;
    let evidenceViewport='desktop';let evidenceState='open_initial';
    const labels={closed:'закрыт',open_initial:'первое открытие',after_turn_2:'после двух действий'};
    function press(group,active){document.querySelectorAll(group).forEach(button=>button.setAttribute('aria-pressed',String(button===active)))}
    document.querySelectorAll('#live [data-viewport]').forEach(button=>button.addEventListener('click',()=>{press('#live [data-viewport]',button);document.querySelectorAll('.frame-shell').forEach(shell=>shell.classList.toggle('is-mobile',button.dataset.viewport==='mobile'))}));
    function renderEvidence(){document.querySelectorAll('[data-evidence-model]').forEach(image=>{const model=image.dataset.evidenceModel;image.src=evidence[model][evidenceViewport][evidenceState];image.alt=`${model}: ${evidenceViewport}, ${labels[evidenceState]}`});document.querySelectorAll('[data-evidence-label]').forEach(label=>label.textContent=`${evidenceViewport==='desktop'?'Desktop':'Mobile'} · ${labels[evidenceState]}`)}
    document.querySelectorAll('.evidence-viewport').forEach(button=>button.addEventListener('click',()=>{evidenceViewport=button.dataset.viewport;press('.evidence-viewport',button);renderEvidence()}));
    document.querySelectorAll('.evidence-state').forEach(button=>button.addEventListener('click',()=>{evidenceState=button.dataset.state;press('.evidence-state',button);renderEvidence()}));
  </script>
</body>
</html>
"""


def _render_page(report: dict[str, Any]) -> str:
    return (
        PAGE_TEMPLATE.replace("__METRIC_CARDS__", _render_metric_cards(report))
        .replace(
            "__EVIDENCE_MAP__",
            json.dumps(_evidence_map(), ensure_ascii=False, separators=(",", ":")),
        )
    )


def build_public_comparison(inputs: ComparisonInputs, output: Path) -> Path:
    report = _load_report(inputs.report)
    private_root = inputs.private_root.resolve()
    destination = output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to replace existing package: {destination}")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        _copy_public_files(private_root, temporary)
        (temporary / "index.html").write_text(
            _render_page(report), encoding="utf-8", newline="\n"
        )
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


__all__ = ["ComparisonInputs", "build_public_comparison"]
