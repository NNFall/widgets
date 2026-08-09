"""Shared, deterministic HTML/CSS renderers for schema-v3 visual patterns."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Callable


@dataclass(frozen=True, slots=True)
class PatternVisualSpec:
    pattern_id: str
    version: int
    category: str
    variant: str
    title: str
    summary: str
    concept: str
    adaptation_policy: str
    accent: str
    accent_alt: str
    ink: str


@dataclass(frozen=True, slots=True)
class RenderedAssets:
    html: str
    css: str


def _base_css(spec: PatternVisualSpec) -> str:
    return f""":root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; --accent: {spec.accent}; --accent-soft: {spec.accent_alt}; --ink: {spec.ink}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-width: 280px; min-height: 100vh; overflow: hidden; background: #ece8e2; color: var(--ink); }}
button, input, textarea {{ font: inherit; }}
.pattern-stage {{ min-height: 100vh; display: grid; place-items: center; padding: 24px; background: linear-gradient(145deg, #f9f7f3, #e9e5de); }}
.showcase {{ width: min(460px, 94vw); position: relative; }}
.preview-label {{ margin: 0 0 12px; color: #756f69; font-size: 11px; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }}
.preview-note {{ margin: 13px 0 0; color: #706b65; font-size: 12px; line-height: 1.45; }}
"""


def _command(selector: str, animation: str, duration: str = "1.35s") -> str:
    return f"""html[data-pattern-command='run'] {selector},
html[data-pattern-command='replay'] {selector} {{ animation: {animation} {duration} cubic-bezier(.22,.8,.2,1) both; }}
"""


def _reduced_motion() -> str:
    return """@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
}
"""


def _wrap(spec: PatternVisualSpec, content: str) -> str:
    return (
        f'<main class="pattern-stage" data-pattern-preview="{spec.pattern_id}">\n'
        f'  <section class="showcase {spec.category} {spec.variant}">\n'
        f'    <p class="preview-label">{escape(spec.title)}</p>\n'
        f"{content}\n"
        f'    <p class="preview-note">{escape(spec.summary)}</p>\n'
        "  </section>\n"
        "</main>\n"
    )


def _render_launcher_shape(spec: PatternVisualSpec) -> RenderedAssets:
    symbols = {
        "faceted_pebble": "✦",
        "orbit_notch": "↗",
        "portrait_lozenge": "А",
        "split_medallion": "···",
        "origami_kite": "◆",
    }
    geometry = {
        "faceted_pebble": "width:84px;height:76px;border-radius:34% 58% 42% 64%/56% 38% 62% 44%;transform:rotate(-4deg);",
        "orbit_notch": "width:82px;height:82px;border-radius:50%;box-shadow:inset -10px 0 0 rgba(255,255,255,.2),0 18px 36px rgba(25,55,74,.2);",
        "portrait_lozenge": "width:70px;height:94px;border-radius:38px 38px 30px 30px;box-shadow:inset 0 -18px 28px rgba(255,255,255,.16),0 18px 36px rgba(25,55,74,.2);",
        "split_medallion": "width:88px;height:78px;border-radius:48% 52% 45% 55%;background:linear-gradient(115deg,var(--accent) 0 53%,var(--accent-soft) 53%);color:var(--ink);",
        "origami_kite": "width:96px;height:78px;border-radius:18px 40px 20px 42px;clip-path:polygon(8% 48%,38% 8%,94% 24%,72% 88%,32% 96%);",
    }[spec.variant]
    detail = {
        "faceted_pebble": ".launcher::after{content:'';position:absolute;inset:9px 12px 40px 18px;border-radius:50%;background:rgba(255,255,255,.28);transform:rotate(-9deg)}",
        "orbit_notch": ".launcher::after{content:'';position:absolute;right:-7px;top:27px;width:20px;height:28px;border-radius:50%;background:#f6f3ee;box-shadow:-7px 0 0 rgba(255,255,255,.25)}.launcher-detail{position:absolute;inset:-8px;border:1px solid var(--accent-soft);border-radius:50%;clip-path:inset(0 0 49% 0)}",
        "portrait_lozenge": ".launcher-symbol{width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.9);color:var(--accent);display:grid;place-items:center}.launcher-detail{position:absolute;right:8px;bottom:9px;width:12px;height:12px;border:3px solid var(--accent);border-radius:50%;background:#7bd38c}",
        "split_medallion": ".launcher::after{content:'';position:absolute;inset:11px 43px 11px 11px;border-radius:40px;background:rgba(255,255,255,.24)}.launcher-symbol{font-size:22px;letter-spacing:2px}",
        "origami_kite": ".launcher::before{content:'';position:absolute;inset:8px 12px 16px 28px;background:rgba(255,255,255,.32);clip-path:polygon(0 50%,56% 0,100% 35%,68% 100%)}.launcher::after{content:'';position:absolute;inset:17px 38px 10px 18px;border:2px solid rgba(255,255,255,.58);clip-path:polygon(0 50%,62% 0,100% 42%,66% 100%)}.launcher-symbol{position:relative;z-index:2;font-size:22px}.launcher-detail{position:absolute;right:12px;bottom:10px;width:10px;height:10px;border:2px solid var(--accent);border-radius:3px;background:#fff}",
    }[spec.variant]
    html = _wrap(
        spec,
        f"""    <div class="shape-board">
      <button class="launcher" type="button" aria-label="Открыть диалог">
        <span class="launcher-detail" aria-hidden="true"></span>
        <span class="launcher-symbol">{symbols[spec.variant]}</span>
      </button>
      <div class="shape-copy"><strong>AI-консьерж</strong><span>форма видна даже без подписи</span></div>
    </div>""",
    )
    accessibility = ".launcher{min-width:44px;min-height:44px}" if spec.variant == "origami_kite" else ""
    reduced_accessibility = (
        "@media (prefers-reduced-motion: reduce){.launcher{animation:none!important;transform:none!important}}"
        if spec.variant == "origami_kite"
        else ""
    )
    css = _base_css(spec) + f""".shape-board {{ min-height:260px;display:flex;align-items:center;justify-content:center;gap:24px;padding:30px;border:1px solid rgba(28,35,51,.08);border-radius:30px;background:rgba(255,255,255,.86);box-shadow:0 26px 60px rgba(36,42,55,.12); }}
.launcher {{ {geometry} position:relative;display:grid;place-items:center;border:0;background:var(--accent);color:white;box-shadow:0 18px 36px rgba(25,55,74,.2);cursor:pointer; }}
.launcher-symbol {{ position:relative;z-index:2;font-size:25px;font-weight:800; }}
{detail}{accessibility}
.shape-copy {{ display:grid;gap:5px;max-width:145px; }}
.shape-copy strong {{ font-size:15px; }} .shape-copy span {{ color:#756f69;font-size:12px;line-height:1.4; }}
.launcher {{ animation:{spec.variant}-shape 5s ease-in-out infinite; }}
@keyframes {spec.variant}-shape {{ 0%,72%,100%{{transform:translateY(0) rotate(0)}} 80%{{transform:translateY(-5px) rotate(-2deg)}} 88%{{transform:translateY(0) rotate(1deg)}} }}
""" + _command(".launcher", f"{spec.variant}-shape") + """@media (max-width:380px){.shape-board{gap:16px;padding:22px}.shape-copy{max-width:118px}}
""" + _reduced_motion() + reduced_accessibility
    return RenderedAssets(html, css)


def _render_launcher_idle(spec: PatternVisualSpec) -> RenderedAssets:
    symbol = {
        "tidal_breathe": "●",
        "compass_drift": "⌁",
        "satin_shimmer": "✦",
        "status_orbit": "◎",
    }[spec.variant]
    variant_css = {
        "tidal_breathe": """.idle-glyph span{position:absolute;inset:16px;border:1px solid rgba(255,255,255,.7);border-radius:50%;animation:tidal_breathe-idle 4.8s ease-out infinite}.idle-glyph span+span{animation-delay:1.1s}@keyframes tidal_breathe-idle{0%,18%{opacity:0;transform:scale(.55)}38%{opacity:.8}64%,100%{opacity:0;transform:scale(1.45)}}""",
        "compass_drift": """.idle-glyph::after{content:'↟';display:block;font-size:29px;transform-origin:50% 66%;animation:compass_drift-idle 4.8s ease-in-out infinite}@keyframes compass_drift-idle{0%,20%,100%{transform:rotate(-12deg)}48%{transform:rotate(18deg)}72%{transform:rotate(-4deg)}}""",
        "satin_shimmer": """.launcher{overflow:hidden}.idle-glyph{font-size:24px}.launcher::after{content:'';position:absolute;inset:-18px auto -18px -28px;width:18px;background:linear-gradient(90deg,transparent,rgba(255,255,255,.55),transparent);transform:skewX(-18deg);animation:satin_shimmer-idle 5.2s ease-in-out infinite}@keyframes satin_shimmer-idle{0%,48%{left:-28px;opacity:0}58%{opacity:.8}78%{left:96px;opacity:0}100%{left:96px;opacity:0}}""",
        "status_orbit": """.idle-glyph{position:absolute;inset:8px;border:1px solid rgba(255,255,255,.38);border-radius:50%;animation:status_orbit-idle 5s linear infinite}.idle-glyph::after{content:'';position:absolute;left:4px;top:4px;width:10px;height:10px;border:3px solid var(--accent);border-radius:50%;background:#80dd91;box-shadow:0 0 0 2px white}@keyframes status_orbit-idle{to{transform:rotate(360deg)}}""",
    }[spec.variant]
    target = {
        "tidal_breathe": ".idle-glyph span",
        "compass_drift": ".idle-glyph::after",
        "satin_shimmer": ".launcher::after",
        "status_orbit": ".idle-glyph",
    }[spec.variant]
    html = _wrap(
        spec,
        f"""    <div class="idle-board">
      <button class="launcher" type="button" aria-label="Открыть диалог"><span class="base-symbol">{symbol}</span><span class="idle-glyph"><span></span><span></span></span></button>
      <div class="idle-copy"><strong>Мария · онлайн</strong><span>Спокойное присутствие между обращениями</span></div>
    </div>""",
    )
    css = _base_css(spec) + f""".idle-board{{min-height:250px;display:grid;place-items:center;align-content:center;gap:18px;padding:28px;border-radius:30px;background:#fff;box-shadow:0 24px 58px rgba(30,38,52,.12)}}
.launcher{{position:relative;width:82px;height:82px;display:grid;place-items:center;border:0;border-radius:28px;background:linear-gradient(145deg,var(--accent),color-mix(in srgb,var(--accent),#111 12%));color:white;box-shadow:0 17px 34px color-mix(in srgb,var(--accent),transparent 72%)}}
.base-symbol{{position:relative;z-index:2;font-size:22px}}.idle-glyph{{position:absolute;inset:0}}.idle-copy{{display:grid;text-align:center;gap:4px}}.idle-copy strong{{font-size:14px}}.idle-copy span{{color:#77716c;font-size:12px}}
{variant_css}
""" + _command(target, f"{spec.variant}-idle", "1.8s") + _reduced_motion()
    return RenderedAssets(html, css)


def _render_launcher_attention(spec: PatternVisualSpec) -> RenderedAssets:
    note = {
        "inbox_nudge": "1 новый совет",
        "ribbon_peek": "Помочь с выбором?",
        "signal_petal": "✦",
        "greeting_arc": "Я рядом",
    }[spec.variant]
    variant_css = {
        "inbox_nudge": ".attention-note{right:-18px;top:-12px;border-radius:16px;background:var(--accent);color:white}.attention-cluster{animation:inbox_nudge-attention 5.2s ease-in-out infinite}@keyframes inbox_nudge-attention{0%,58%,100%{transform:none}68%{transform:translateY(-9px)}76%{transform:translateY(0)}82%{transform:translateY(-3px)}}",
        "ribbon_peek": ".attention-note{right:54px;top:18px;width:132px;border-radius:16px 5px 5px 16px;background:var(--accent-soft);color:var(--ink);transform-origin:right center;animation:ribbon_peek-attention 5.4s ease-in-out infinite}@keyframes ribbon_peek-attention{0%,18%,82%,100%{opacity:0;transform:translateX(38px) scaleX(.6)}34%,68%{opacity:1;transform:none}}",
        "signal_petal": ".attention-note{right:-19px;top:5px;width:42px;height:54px;display:grid;place-items:center;border-radius:70% 30% 62% 38%;background:var(--accent-soft);color:var(--accent);transform-origin:left bottom;animation:signal_petal-attention 5s ease-in-out infinite}@keyframes signal_petal-attention{0%,24%,82%,100%{opacity:0;transform:rotate(-30deg) scale(.55)}40%,68%{opacity:1;transform:rotate(5deg) scale(1)}}",
        "greeting_arc": ".attention-note{right:17px;bottom:70px;border-radius:15px;background:#fff;color:var(--ink);box-shadow:0 12px 30px rgba(31,39,50,.13);animation:greeting_arc-attention 5.4s ease-in-out infinite}.attention-cluster::before{content:'';position:absolute;right:35px;bottom:55px;width:72px;height:48px;border-top:2px solid var(--accent);border-radius:50%;animation:greeting_arc-attention 5.4s ease-in-out infinite}@keyframes greeting_arc-attention{0%,20%,82%,100%{opacity:0;transform:translateY(8px) scale(.86)}38%,68%{opacity:1;transform:none}}",
    }[spec.variant]
    target = ".attention-cluster" if spec.variant == "inbox_nudge" else ".attention-note"
    html = _wrap(
        spec,
        f"""    <div class="attention-board"><div class="attention-cluster">
      <span class="attention-note">{note}</span>
      <button class="launcher" type="button" aria-label="Открыть диалог"><b>К</b><i></i></button>
    </div><p>Один читаемый сигнал, затем полный покой</p></div>""",
    )
    css = _base_css(spec) + f""".attention-board{{min-height:255px;display:grid;place-items:center;align-content:center;gap:18px;padding:28px;border:1px solid rgba(29,36,49,.08);border-radius:30px;background:rgba(255,255,255,.9);box-shadow:0 24px 58px rgba(31,38,50,.11)}}
.attention-cluster{{position:relative;width:190px;height:112px;display:flex;align-items:flex-end;justify-content:flex-end}}.launcher{{position:relative;width:72px;height:72px;border:0;border-radius:25px;background:var(--accent);color:white;box-shadow:0 16px 30px color-mix(in srgb,var(--accent),transparent 72%)}}.launcher b{{font-size:22px}}.launcher i{{position:absolute;right:8px;bottom:8px;width:9px;height:9px;border:2px solid white;border-radius:50%;background:#78d98e}}.attention-note{{position:absolute;z-index:2;padding:8px 11px;font-size:11px;font-weight:750;white-space:nowrap}}.attention-board p{{margin:0;color:#77716b;font-size:12px}}
{variant_css}
""" + _command(target, f"{spec.variant}-attention", "1.7s") + _reduced_motion()
    return RenderedAssets(html, css)


def _shell_content(spec: PatternVisualSpec) -> str:
    if spec.variant == "context_ribbon":
        return """    <div class="chat-shell context-ribbon-shell">
      <div class="shell-ribbon" data-shell-ribbon="context"><b>Контекст запроса</b><span>Тариф Команда · 3 шага</span><i>обновлено</i></div>
      <section class="shell-main" data-shell-main="dialogue"><header><span class="avatar">К</span><div><strong>Каира</strong><small>AI-консьерж</small></div><button type="button" aria-label="Закрыть">×</button></header>
        <div class="thread"><p class="assistant">Подберу подходящий вариант и объясню различия.</p><p class="user">Нужен формат для небольшой команды.</p></div>
        <footer><span>Задайте уточняющий вопрос</span><button type="button" aria-label="Отправить">↑</button></footer>
      </section>
    </div>"""
    side = {
        "dialogue_gallery": "<aside class=\"shell-side\"><b>01</b><span>02</span><span>03</span></aside>",
        "concierge_column": "<aside class=\"shell-side\"><b>МК</b><small>Консьерж<br>онлайн</small></aside>",
        "split_context": "<aside class=\"shell-side\"><small>Вы выбрали</small><b>Тариф Команда</b><span>от 12 000 ₽</span></aside>",
        "pocket_agenda": "<aside class=\"shell-side\"><b>1 Запрос</b><span>2 Подбор</span><span>3 Решение</span></aside>",
    }[spec.variant]
    return f"""    <div class="chat-shell">
      {side}
      <section class="shell-main"><header><span class="avatar">К</span><div><strong>Каира</strong><small>AI-консьерж</small></div><button type="button" aria-label="Закрыть">×</button></header>
        <div class="thread"><p class="assistant">Подберу подходящий вариант и объясню различия.</p><p class="user">Нужен формат для небольшой команды.</p></div>
        <footer><span>Задайте уточняющий вопрос</span><button type="button" aria-label="Отправить">↑</button></footer>
      </section>
    </div>"""


def _render_shell_layout(spec: PatternVisualSpec) -> RenderedAssets:
    layouts = {
        "dialogue_gallery": "grid-template-columns:58px 1fr;.shell-side{align-items:center}.shell-side span,.shell-side b{width:30px;height:30px;display:grid;place-items:center;border-radius:11px;background:#fff}.shell-side b{background:var(--accent);color:#fff}",
        "concierge_column": "grid-template-columns:104px 1fr;.shell-side{align-content:start;text-align:center}.shell-side b{width:48px;height:48px;display:grid;place-items:center;margin:auto;border-radius:18px;background:var(--accent);color:#fff}.shell-side small{line-height:1.45}",
        "split_context": "grid-template-columns:150px 1fr;.shell-side{align-content:center}.shell-side b{font-size:14px}.shell-side span{color:var(--accent);font-weight:750}",
        "pocket_agenda": "grid-template-columns:112px 1fr;.shell-side{align-content:center}.shell-side>*{padding:8px;border-left:2px solid #dcd8d2}.shell-side b{border-color:var(--accent);color:var(--accent)}",
        "context_ribbon": "grid-template:56px 1fr / 1fr;grid-template-columns:1fr;.shell-ribbon{grid-row:1}.shell-main{grid-row:2}",
    }[spec.variant]
    variant_extra = """
.context-ribbon-shell{min-width:0;overflow:hidden}.context-ribbon-shell .shell-ribbon{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:8px;min-width:0;padding:12px 16px;background:color-mix(in srgb,var(--accent-soft),white 54%);border-bottom:1px solid #e4e0da;font-size:10px}.shell-ribbon b{font-size:11px}.shell-ribbon span{color:#756f69;white-space:nowrap}.shell-ribbon i{padding:4px 6px;border-radius:8px;background:rgba(255,255,255,.72);color:var(--accent);font-size:9px;font-style:normal}.context-ribbon-shell .shell-main{min-width:0;min-height:0;overflow:hidden}
@media(max-width:430px){.context-ribbon-shell{grid-template:52px 1fr / 1fr;height:350px}.context-ribbon-shell .shell-ribbon{grid-template-columns:1fr auto;padding-inline:12px}.shell-ribbon i{display:none}.shell-ribbon span{overflow:hidden;text-overflow:ellipsis}}
""" if spec.variant == "context_ribbon" else ""
    css = _base_css(spec) + f""".chat-shell{{height:330px;display:grid;{layouts};overflow:hidden;border:1px solid rgba(28,35,48,.08);border-radius:28px;background:#fff;box-shadow:0 26px 62px rgba(30,38,51,.13);animation:{spec.variant}-shell 6s ease-in-out infinite}}.shell-side{{display:grid;gap:9px;padding:18px 14px;background:color-mix(in srgb,var(--accent-soft),white 65%);font-size:11px}}.shell-main{{min-width:0;display:grid;grid-template:auto 1fr auto/1fr}}header{{display:flex;align-items:center;gap:9px;padding:15px 17px;border-bottom:1px solid #ece9e4}}header .avatar{{width:34px;height:34px;display:grid;place-items:center;border-radius:12px;background:var(--accent);color:white;font-weight:800}}header div{{display:grid;gap:1px}}header strong{{font-size:13px}}header small{{color:#7c7771;font-size:10px}}header button{{margin-left:auto;border:0;background:transparent;color:#777;font-size:20px}}.thread{{display:flex;flex-direction:column;justify-content:center;gap:10px;padding:18px;background:#fbfaf8}}.thread p{{max-width:78%;margin:0;padding:10px 12px;border-radius:15px;font-size:11px;line-height:1.45}}.assistant{{background:#fff;box-shadow:0 7px 18px rgba(35,42,50,.08)}}.user{{align-self:flex-end;background:var(--accent);color:white}}footer{{display:flex;align-items:center;gap:8px;margin:10px;padding:8px 8px 8px 12px;border:1px solid #e2ded8;border-radius:16px;color:#827b75;font-size:10px}}footer button{{margin-left:auto;width:32px;height:32px;border:0;border-radius:11px;background:var(--accent);color:#fff}}@keyframes {spec.variant}-shell{{0%,74%,100%{{box-shadow:0 26px 62px rgba(30,38,51,.13)}}84%{{box-shadow:0 30px 72px color-mix(in srgb,var(--accent),transparent 76%)}}}}{variant_extra}""" + _command(".chat-shell", f"{spec.variant}-shell", "1.5s") + """@media(max-width:430px){.chat-shell{grid-template-columns:1fr;height:350px}.shell-side{display:flex;align-items:center;padding:10px 14px;overflow:hidden}.shell-side>*{white-space:nowrap}.shell-side span:last-child{display:none}}
    """ + _reduced_motion()
    if not variant_extra:
        css = css.replace("}}html[data-pattern-command", "}}\nhtml[data-pattern-command", 1)
    css = css.replace(
        "\n    @media (prefers-reduced-motion: reduce)",
        "\n@media (prefers-reduced-motion: reduce)",
        1,
    )
    return RenderedAssets(_wrap(spec, _shell_content(spec)), css)


def _widget_panel_content(action: str, *, variant: str | None = None) -> str:
    if variant in {"portal_draw", "curtain_rise", "page_turn"}:
        panel_body = (
            '<header><b>\u041a\u0430\u0439\u0440\u0430</b><span>AI-\u043a\u043e\u043d\u0441\u044c\u0435\u0440\u0436</span></header>'
            '<div class="panel-thread"><p>\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435! \u041f\u043e\u043c\u043e\u0433\u0443 \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u043f\u043e\u0434\u0445\u043e\u0434\u044f\u0449\u0438\u0439 \u0444\u043e\u0440\u043c\u0430\u0442.</p><i></i><i></i></div>'
            '<footer>\u0412\u0430\u0448 \u0432\u043e\u043f\u0440\u043e\u0441 <b>\u2191</b></footer>'
        )
        if variant == "portal_draw":
            return f"""    <div class="widget-scene portal-scene"><button class="origin-launcher" type="button" aria-label="{action}">K</button>
      <section class="widget-panel portal-panel" data-motion-family="portal-draw">
        <span class="portal-contour" data-portal-part="contour" aria-hidden="true"></span>
        <span class="portal-surface" data-portal-part="surface" aria-hidden="true"></span>
        <div class="portal-content" data-portal-part="content">{panel_body}</div>
      </section>
    </div>"""
        if variant == "curtain_rise":
            return f"""    <div class="widget-scene curtain-scene"><button class="origin-launcher" type="button" aria-label="{action}">K</button>
      <section class="widget-panel curtain-panel" data-motion-family="curtain-rise">
        <span class="curtain-layer curtain-layer-back" data-curtain-layer="back" aria-hidden="true"></span>
        <span class="curtain-layer curtain-layer-front" data-curtain-layer="front" aria-hidden="true"></span>
        <div class="curtain-content" data-curtain-part="content">{panel_body}</div>
      </section>
    </div>"""
        return f"""    <div class="widget-scene page-turn-scene"><button class="origin-launcher" type="button" aria-label="{action}">K</button>
      <section class="widget-panel page-turn-panel" data-motion-family="page-turn">
        <div class="page-turn-sheet" data-page-turn-part="sheet">
          <div class="page-turn-face" data-page-turn-part="face">{panel_body}</div>
          <span class="page-turn-shadow" data-page-turn-part="shadow" aria-hidden="true"></span>
        </div>
      </section>
    </div>"""
    return f"""    <div class="widget-scene"><button class="origin-launcher" type="button" aria-label="{action}">К</button>
      <section class="widget-panel"><header><b>Каира</b><span>AI-консьерж</span></header><div class="panel-thread"><p>Здравствуйте! Помогу выбрать подходящий формат.</p><i></i><i></i></div><footer>Ваш вопрос <b>↑</b></footer></section>
    </div>"""


def _signature_widget_chrome(panel_class: str) -> str:
    return """.widget-scene{position:relative;height:360px;padding:5px 8px}.origin-launcher{position:absolute;right:0;bottom:0;width:58px;height:58px;min-width:44px;min-height:44px;border:0;border-radius:21px;background:var(--accent);color:white;font-weight:850;box-shadow:0 15px 30px color-mix(in srgb,var(--accent),transparent 70%);z-index:8}.widget-panel.__PANEL__{position:absolute;right:34px;bottom:34px;width:310px;height:300px}.widget-panel header{display:flex;align-items:baseline;gap:8px;padding:17px 19px;background:color-mix(in srgb,var(--accent-soft),white 70%)}.widget-panel header b{font-size:15px}.widget-panel header span{color:#756f69;font-size:10px}.panel-thread{height:190px;padding:26px 18px;background:#faf9f7}.panel-thread p{max-width:78%;margin:0;padding:12px 14px;border-radius:17px;background:white;box-shadow:0 8px 20px rgba(33,39,49,.08);font-size:11px;line-height:1.45}.panel-thread i{display:block;width:54%;height:8px;margin:13px 0 0;border-radius:8px;background:#e7e3dd}.panel-thread i+i{width:38%;margin-top:7px}.widget-panel footer{margin:10px;padding:10px 10px 10px 13px;border:1px solid #e1ddd7;border-radius:15px;color:#817a73;font-size:10px}.widget-panel footer b{float:right;color:var(--accent)}@media(max-width:410px){.widget-panel.__PANEL__{right:12px;width:calc(100% - 24px)}.origin-launcher{right:4px}}""".replace("__PANEL__", panel_class)


def _render_portal_open(spec: PatternVisualSpec) -> RenderedAssets:
    html = _wrap(spec, _widget_panel_content("Открыть диалог", variant="portal_draw"))
    css = _base_css(spec) + _signature_widget_chrome("portal-panel") + """
.portal-panel{overflow:visible;border:0;border-radius:28px;background:transparent;box-shadow:none;isolation:isolate}
.portal-contour,.portal-surface{position:absolute;inset:0;border-radius:28px;pointer-events:none}
.portal-contour{z-index:0;border:2px solid var(--accent);opacity:0;transform-origin:100% 100%;clip-path:polygon(90% 100%,100% 100%,100% 90%,90% 90%);animation:portal_draw-contour 6.4s cubic-bezier(.22,.8,.2,1) both}
.portal-surface{z-index:1;border:1px solid rgba(30,36,48,.09);background:#fff;box-shadow:0 25px 60px rgba(31,38,50,.16);opacity:0;clip-path:polygon(88% 100%,100% 100%,100% 88%,88% 88%);animation:portal_draw-surface 6.4s .35s cubic-bezier(.22,.8,.2,1) both}
.portal-content{position:relative;z-index:2;display:grid;grid-template:auto 1fr auto/1fr;height:100%;overflow:hidden;border-radius:28px;opacity:0;transform:translateY(20px);animation:portal_draw-content 6.4s .8s cubic-bezier(.22,.8,.2,1) both}
@keyframes portal_draw-contour{0%,12%{opacity:0;clip-path:polygon(90% 100%,100% 100%,100% 90%,90% 90%);transform:scale(.94)}32%{opacity:1;clip-path:polygon(76% 100%,100% 100%,100% 0,76% 0);transform:scale(1)}52%,100%{opacity:1;clip-path:inset(0);transform:none}}
@keyframes portal_draw-surface{0%,18%{opacity:0;clip-path:polygon(88% 100%,100% 100%,100% 88%,88% 88%)}44%{opacity:1;clip-path:polygon(20% 100%,100% 100%,100% 0,20% 0)}64%,100%{opacity:1;clip-path:inset(0)}}
@keyframes portal_draw-content{0%,24%{opacity:0;transform:translateY(20px)}56%{opacity:.35;transform:translateY(8px)}76%,100%{opacity:1;transform:none}}
html[data-pattern-command='run'] .portal-contour,html[data-pattern-command='replay'] .portal-contour{animation:portal_draw-contour 1.8s cubic-bezier(.22,.8,.2,1) both}
html[data-pattern-command='run'] .portal-surface,html[data-pattern-command='replay'] .portal-surface{animation:portal_draw-surface 1.8s .32s cubic-bezier(.22,.8,.2,1) both}
html[data-pattern-command='run'] .portal-content,html[data-pattern-command='replay'] .portal-content{animation:portal_draw-content 1.8s .7s cubic-bezier(.22,.8,.2,1) both}
""" + _reduced_motion() + """@media (prefers-reduced-motion: reduce){.portal-contour,.portal-surface{opacity:1!important;clip-path:inset(0)!important;transform:none!important}.portal-content{opacity:1!important;transform:none!important}}"""
    return RenderedAssets(html, css)


def _render_curtain_open(spec: PatternVisualSpec) -> RenderedAssets:
    html = _wrap(spec, _widget_panel_content("Открыть диалог", variant="curtain_rise"))
    css = _base_css(spec) + _signature_widget_chrome("curtain-panel") + """
.curtain-panel{overflow:visible;border:0;border-radius:28px;background:transparent;box-shadow:none;isolation:isolate}
.curtain-layer{position:absolute;inset:0;border-radius:28px;pointer-events:none;transform-origin:50% 100%}
.curtain-layer-back{z-index:0;background:linear-gradient(160deg,color-mix(in srgb,var(--accent-soft),white 18%),color-mix(in srgb,var(--accent),white 70%));opacity:0;clip-path:inset(100% 0 0 0 round 28px);transform:translateY(28px) scaleY(.76);animation:curtain_rise-back 6.4s cubic-bezier(.22,.8,.2,1) both}
.curtain-layer-front{z-index:1;border:1px solid rgba(30,36,48,.09);background:#fff;box-shadow:0 25px 60px rgba(31,38,50,.16);opacity:0;clip-path:inset(100% 0 0 0 round 28px);transform:translateY(38px) scaleY(.68);animation:curtain_rise-front 6.4s .26s cubic-bezier(.22,.8,.2,1) both}
.curtain-content{position:relative;z-index:2;display:grid;grid-template:auto 1fr auto/1fr;height:100%;overflow:hidden;border-radius:28px;opacity:0;transform:translateY(26px);animation:curtain_rise-content 6.4s .72s cubic-bezier(.22,.8,.2,1) both}
@keyframes curtain_rise-back{0%,12%{opacity:0;clip-path:inset(100% 0 0 0 round 28px);transform:translateY(28px) scaleY(.76)}42%{opacity:.72;clip-path:inset(38% 0 0 0 round 28px);transform:translateY(6px) scaleY(.96)}68%,100%{opacity:1;clip-path:inset(0 round 28px);transform:none}}
@keyframes curtain_rise-front{0%,18%{opacity:0;clip-path:inset(100% 0 0 0 round 28px);transform:translateY(38px) scaleY(.68)}48%{opacity:.92;clip-path:inset(48% 0 0 0 round 28px);transform:translateY(8px) scaleY(.95)}74%,100%{opacity:1;clip-path:inset(0 round 28px);transform:none}}
@keyframes curtain_rise-content{0%,26%{opacity:0;transform:translateY(26px)}58%{opacity:.2;transform:translateY(12px)}82%,100%{opacity:1;transform:none}}
html[data-pattern-command='run'] .curtain-layer-back,html[data-pattern-command='replay'] .curtain-layer-back{animation:curtain_rise-back 1.8s cubic-bezier(.22,.8,.2,1) both}
html[data-pattern-command='run'] .curtain-layer-front,html[data-pattern-command='replay'] .curtain-layer-front{animation:curtain_rise-front 1.8s .22s cubic-bezier(.22,.8,.2,1) both}
html[data-pattern-command='run'] .curtain-content,html[data-pattern-command='replay'] .curtain-content{animation:curtain_rise-content 1.8s .56s cubic-bezier(.22,.8,.2,1) both}
""" + _reduced_motion() + """@media (prefers-reduced-motion: reduce){.curtain-layer{opacity:1!important;clip-path:inset(0 round 28px)!important;transform:none!important}.curtain-content{opacity:1!important;transform:none!important}}"""
    return RenderedAssets(html, css)


def _render_page_turn_close(spec: PatternVisualSpec) -> RenderedAssets:
    html = _wrap(spec, _widget_panel_content("Закрыть диалог", variant="page_turn"))
    css = _base_css(spec) + _signature_widget_chrome("page-turn-panel") + """
.page-turn-scene{perspective:1100px}
.page-turn-panel{overflow:visible;border:0;border-radius:28px;background:transparent;box-shadow:none;perspective:1100px;transform-style:preserve-3d;isolation:isolate}
.page-turn-sheet{position:relative;width:100%;height:100%;transform-origin:100% 50%;transform-style:preserve-3d;backface-visibility:hidden;animation:page_turn-close 6.4s cubic-bezier(.22,.8,.2,1) both}
.page-turn-face{position:absolute;inset:0;z-index:1;display:grid;grid-template:auto 1fr auto/1fr;overflow:hidden;border:1px solid rgba(30,36,48,.09);border-radius:28px;background:#fff;box-shadow:0 25px 60px rgba(31,38,50,.16);backface-visibility:hidden}
.page-turn-shadow{position:absolute;inset:0;z-index:2;border-radius:28px;pointer-events:none;transform-origin:100% 50%;background:linear-gradient(105deg,transparent 42%,color-mix(in srgb,var(--accent),transparent 72%) 78%,transparent 100%);opacity:0;animation:page_turn-shadow 6.4s cubic-bezier(.22,.8,.2,1) both}
.page-turn-panel header{position:relative}.page-turn-panel .panel-thread{position:relative}.page-turn-panel footer{position:relative}
@keyframes page_turn-close{0%,34%{opacity:1;transform:rotateY(0deg) translate3d(0,0,0) scale(1)}48%{opacity:1;transform:rotateY(-18deg) translate3d(1px,0,0) scale(.995)}68%{opacity:.76;transform:rotateY(-72deg) translate3d(11px,2px,0) scale(.96)}86%,100%{opacity:.06;transform:rotateY(-96deg) translate3d(28px,5px,0) scale(.9)}}
@keyframes page_turn-shadow{0%,34%{opacity:0;transform:rotateY(0deg)}54%{opacity:.16;transform:rotateY(-34deg)}72%{opacity:.28;transform:rotateY(-70deg)}86%,100%{opacity:0;transform:rotateY(-96deg)}}
@keyframes page_turn-launcher{0%,45%{opacity:.42;transform:scale(.84)}72%,100%{opacity:1;transform:none}}
html[data-pattern-command='run'] .page-turn-sheet,html[data-pattern-command='replay'] .page-turn-sheet{animation:page_turn-close 1.8s cubic-bezier(.22,.8,.2,1) both}
html[data-pattern-command='run'] .page-turn-shadow,html[data-pattern-command='replay'] .page-turn-shadow{animation:page_turn-shadow 1.8s cubic-bezier(.22,.8,.2,1) both}
html[data-pattern-command='run'] .origin-launcher,html[data-pattern-command='replay'] .origin-launcher{animation:page_turn-launcher 1.8s cubic-bezier(.22,.8,.2,1) both}
""" + _reduced_motion() + """@media (prefers-reduced-motion: reduce){.page-turn-sheet{animation:none!important;opacity:.06!important;transform:rotateY(-96deg) translate3d(28px,5px,0) scale(.9)!important}.page-turn-shadow{animation:none!important;opacity:0!important}.page-turn-scene .origin-launcher{animation:none!important;opacity:1!important;transform:none!important}}"""
    return RenderedAssets(html, css)


def _render_widget_open(spec: PatternVisualSpec) -> RenderedAssets:
    if spec.variant == "portal_draw":
        return _render_portal_open(spec)
    if spec.variant == "curtain_rise":
        return _render_curtain_open(spec)
    transform = {
        "curtain_rise": "transform-origin:bottom right;clip-path:inset(100% 0 0 0);opacity:.65",
        "focus_bloom": "transform-origin:bottom right;transform:scale(.62);filter:blur(8px);opacity:0",
        "card_deal": "transform-origin:bottom right;transform:translate(42px,35px) rotate(4deg) scale(.82);opacity:0",
        "portal_draw": "transform-origin:bottom right;clip-path:polygon(96% 100%,100% 100%,100% 96%,96% 96%);opacity:.5",
    }[spec.variant]
    middle = {
        "curtain_rise": "clip-path:inset(28% 0 0 0);opacity:1",
        "focus_bloom": "transform:scale(.9);filter:blur(2px);opacity:1",
        "card_deal": "transform:translate(10px,8px) rotate(1deg) scale(.96);opacity:1",
        "portal_draw": "clip-path:polygon(12% 100%,100% 100%,100% 0,82% 0);opacity:1",
    }[spec.variant]
    css = _base_css(spec) + f""".widget-scene{{position:relative;height:360px;padding:5px 8px}}.origin-launcher{{position:absolute;right:0;bottom:0;width:58px;height:58px;border:0;border-radius:21px;background:var(--accent);color:white;font-weight:850;box-shadow:0 15px 30px color-mix(in srgb,var(--accent),transparent 70%)}}.widget-panel{{position:absolute;right:34px;bottom:34px;width:310px;height:300px;overflow:hidden;border:1px solid rgba(30,36,48,.09);border-radius:28px;background:#fff;box-shadow:0 25px 60px rgba(31,38,50,.16);animation:{spec.variant}-open 5.8s cubic-bezier(.22,.8,.2,1) infinite}}.widget-panel header{{display:flex;align-items:baseline;gap:8px;padding:17px 19px;background:color-mix(in srgb,var(--accent-soft),white 70%)}}.widget-panel header b{{font-size:15px}}.widget-panel header span{{color:#756f69;font-size:10px}}.panel-thread{{height:190px;padding:26px 18px;background:#faf9f7}}.panel-thread p{{max-width:78%;margin:0;padding:12px 14px;border-radius:17px;background:white;box-shadow:0 8px 20px rgba(33,39,49,.08);font-size:11px;line-height:1.45}}.panel-thread i{{display:block;width:54%;height:8px;margin:13px 0 0;border-radius:8px;background:#e7e3dd}}.panel-thread i+i{{width:38%;margin-top:7px}}.widget-panel footer{{margin:10px;padding:10px 10px 10px 13px;border:1px solid #e1ddd7;border-radius:15px;color:#817a73;font-size:10px}}.widget-panel footer b{{float:right;color:var(--accent)}}@keyframes {spec.variant}-open{{0%,10%{{{transform}}}30%{{{middle}}}46%,82%{{transform:none;clip-path:inset(0);filter:none;opacity:1}}94%,100%{{opacity:0;transform:scale(.96)}}}}
""" + _command(".widget-panel", f"{spec.variant}-open", "1.45s") + """@media(max-width:410px){.widget-panel{right:12px;width:calc(100% - 24px)}.origin-launcher{right:4px}}
""" + _reduced_motion()
    return RenderedAssets(_wrap(spec, _widget_panel_content("Открыть диалог")), css)


def _render_widget_close(spec: PatternVisualSpec) -> RenderedAssets:
    if spec.variant == "page_turn":
        return _render_page_turn_close(spec)
    end = {
        "satin_collapse": "clip-path:inset(0 0 92% 82% round 22px);transform:translate(18px,28px);opacity:.2",
        "dock_return": "transform:translate(34px,44px) scale(.18);border-radius:50%;opacity:.25",
        "page_turn": "transform:translate(24px,22px) perspective(520px) rotateY(-78deg) scale(.55);transform-origin:right center;opacity:.18",
        "orbit_recede": "transform:translate(48px,36px) rotate(12deg) scale(.16);filter:blur(2px);opacity:.2",
    }[spec.variant]
    css = _base_css(spec) + f""".widget-scene{{position:relative;height:360px;padding:5px 8px}}.origin-launcher{{position:absolute;right:0;bottom:0;width:58px;height:58px;border:0;border-radius:21px;background:var(--accent);color:white;font-weight:850;box-shadow:0 15px 30px color-mix(in srgb,var(--accent),transparent 70%);animation:{spec.variant}-launcher 5.8s ease-in-out infinite}}.widget-panel{{position:absolute;right:34px;bottom:34px;width:310px;height:300px;overflow:hidden;border:1px solid rgba(30,36,48,.09);border-radius:28px;background:#fff;box-shadow:0 25px 60px rgba(31,38,50,.16);animation:{spec.variant}-close 5.8s cubic-bezier(.22,.8,.2,1) infinite}}.widget-panel header{{display:flex;align-items:baseline;gap:8px;padding:17px 19px;background:color-mix(in srgb,var(--accent-soft),white 70%)}}.widget-panel header b{{font-size:15px}}.widget-panel header span{{color:#756f69;font-size:10px}}.panel-thread{{height:190px;padding:26px 18px;background:#faf9f7}}.panel-thread p{{max-width:78%;margin:0;padding:12px 14px;border-radius:17px;background:white;box-shadow:0 8px 20px rgba(33,39,49,.08);font-size:11px;line-height:1.45}}.panel-thread i{{display:block;width:54%;height:8px;margin:13px 0 0;border-radius:8px;background:#e7e3dd}}.panel-thread i+i{{width:38%;margin-top:7px}}.widget-panel footer{{margin:10px;padding:10px 10px 10px 13px;border:1px solid #e1ddd7;border-radius:15px;color:#817a73;font-size:10px}}.widget-panel footer b{{float:right;color:var(--accent)}}@keyframes {spec.variant}-close{{0%,42%{{transform:none;clip-path:inset(0);filter:none;opacity:1}}72%,100%{{{end}}}}}@keyframes {spec.variant}-launcher{{0%,48%{{transform:scale(.82);opacity:.45}}76%,100%{{transform:none;opacity:1}}}}
""" + _command(".widget-panel", f"{spec.variant}-close", "1.55s") + _command(".origin-launcher", f"{spec.variant}-launcher", "1.55s") + """@media(max-width:410px){.widget-panel{right:12px;width:calc(100% - 24px)}.origin-launcher{right:4px}}
""" + _reduced_motion()
    return RenderedAssets(_wrap(spec, _widget_panel_content("Закрыть диалог")), css)


def _render_background_effect(spec: PatternVisualSpec) -> RenderedAssets:
    ambient_markup = {
        "chat_constellation": "<span></span><span></span><span></span><span></span><span></span>",
        "message_topography": "<span></span><span></span><span></span>",
        "thread_spotlight": "<span></span>",
        "conversation_ribbons": "<span></span><span></span>",
        "history_lantern": "<span class=\"lantern-glow\" data-lantern-layer=\"glow\"></span><span class=\"lantern-path\" data-lantern-layer=\"path\"></span><span class=\"lantern-dot\" data-lantern-layer=\"dot\"></span>",
    }[spec.variant]
    effect_css = {
        "chat_constellation": ".ambient-effect span{position:absolute;width:5px;height:5px;border-radius:50%;background:var(--accent)}.ambient-effect span:nth-child(1){left:14%;top:22%}.ambient-effect span:nth-child(2){left:39%;top:13%}.ambient-effect span:nth-child(3){left:70%;top:31%}.ambient-effect span:nth-child(4){left:28%;top:70%}.ambient-effect span:nth-child(5){left:81%;top:76%}.ambient-effect::after{content:'';position:absolute;inset:18% 15%;border:1px solid color-mix(in srgb,var(--accent),transparent 70%);transform:skewY(-12deg)}",
        "message_topography": ".ambient-effect span{position:absolute;left:5%;top:9%;width:92%;height:58%;border:1px solid color-mix(in srgb,var(--accent),transparent 66%);border-radius:45% 55% 48% 52%}.ambient-effect span:nth-child(2){left:16%;top:25%;width:72%;height:48%}.ambient-effect span:nth-child(3){left:28%;top:42%;width:50%;height:32%}",
        "thread_spotlight": ".ambient-effect{background:radial-gradient(circle at 32% 35%,color-mix(in srgb,var(--accent-soft),transparent 25%),transparent 38%),radial-gradient(circle at 75% 72%,color-mix(in srgb,var(--accent),transparent 82%),transparent 32%)}.ambient-effect span{position:absolute;inset:0;background:linear-gradient(115deg,transparent 25%,rgba(255,255,255,.38) 50%,transparent 75%)}",
        "conversation_ribbons": ".ambient-effect span{position:absolute;left:-18%;width:138%;height:72px;border:18px solid color-mix(in srgb,var(--accent),transparent 84%);border-left-color:transparent;border-right-color:transparent;border-radius:50%}.ambient-effect span:first-child{top:12%;transform:rotate(-8deg)}.ambient-effect span:last-child{top:58%;transform:rotate(9deg);border-color:color-mix(in srgb,var(--accent-soft),transparent 62%);border-left-color:transparent;border-right-color:transparent}",
        "history_lantern": ".ambient-effect{background:radial-gradient(circle at 18% 22%,color-mix(in srgb,var(--accent-soft),transparent 24%),transparent 28%),radial-gradient(circle at 82% 76%,color-mix(in srgb,var(--accent),transparent 82%),transparent 34%)}.lantern-glow{position:absolute;left:12%;top:16%;width:72%;height:64%;border:1px solid color-mix(in srgb,var(--accent),transparent 66%);border-radius:48% 52% 44% 56%;transform:rotate(-12deg)}.lantern-path{position:absolute;left:16%;top:25%;width:66%;height:44%;border-top:2px solid color-mix(in srgb,var(--accent-soft),transparent 30%);border-radius:50%;transform:rotate(8deg)}.lantern-dot{position:absolute;right:18%;top:28%;width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 5px color-mix(in srgb,var(--accent-soft),transparent 58%)}",
    }[spec.variant]
    html = _wrap(
        spec,
        f"""    <section class="chat-surface"><header><b>Каира</b><span>AI-консьерж · онлайн</span></header>
      <div class="chat-history" data-effect-scope="chat-history"><div class="ambient-effect" aria-hidden="true">{ambient_markup}</div>
        <p class="assistant">Расскажу о различиях и помогу выбрать.</p><p class="user">Мне важны сроки и поддержка.</p><p class="assistant short">Сравню оба варианта.</p>
      </div><footer>Продолжить разговор <b>↑</b></footer>
    </section>""",
    )
    css = _base_css(spec) + f""".chat-surface{{height:340px;display:grid;grid-template:auto 1fr auto/1fr;overflow:hidden;border:1px solid rgba(28,35,48,.08);border-radius:28px;background:#fff;box-shadow:0 25px 60px rgba(31,38,50,.13)}}.chat-surface header{{display:grid;padding:16px 18px;border-bottom:1px solid #ece9e4}}.chat-surface header b{{font-size:14px}}.chat-surface header span{{color:#78726c;font-size:10px}}.chat-history{{position:relative;isolation:isolate;display:flex;flex-direction:column;justify-content:center;gap:10px;overflow:hidden;padding:20px;background:#faf9f7}}.chat-history .ambient-effect{{position:absolute;z-index:-1;inset:0;overflow:hidden;opacity:.78;animation:{spec.variant}-ambient 8s ease-in-out infinite}}{effect_css}.chat-history p{{max-width:73%;margin:0;padding:10px 12px;border-radius:15px;background:rgba(255,255,255,.9);box-shadow:0 7px 18px rgba(33,39,49,.07);font-size:11px;line-height:1.4}}.chat-history .user{{align-self:flex-end;background:var(--accent);color:white}}.chat-history .short{{max-width:52%}}.chat-surface footer{{margin:10px;padding:10px 12px;border:1px solid #e2ded8;border-radius:15px;color:#817b74;font-size:10px}}.chat-surface footer b{{float:right;color:var(--accent)}}@keyframes {spec.variant}-ambient{{0%,100%{{transform:translate3d(-2%,-1%,0) scale(1)}}50%{{transform:translate3d(3%,2%,0) scale(1.06)}}}}
""" + _command(".chat-history .ambient-effect", f"{spec.variant}-ambient", "2.2s") + _reduced_motion()
    return RenderedAssets(html, css)


def _render_assistant_message(spec: PatternVisualSpec) -> RenderedAssets:
    extra = {
        "ink_reveal": ".assistant-bubble{clip-path:inset(0 100% 0 0);animation:ink_reveal-message 5.2s ease-in-out infinite}@keyframes ink_reveal-message{0%,14%{clip-path:inset(0 100% 0 0);opacity:.5}38%,82%{clip-path:inset(0);opacity:1}96%,100%{opacity:0}}",
        "card_bloom": ".assistant-bubble{transform-origin:top left;animation:card_bloom-message 5.2s cubic-bezier(.22,.8,.2,1) infinite}.assistant-bubble::after{content:'подбор готов';display:block;width:max-content;margin-top:9px;padding:4px 7px;border-radius:8px;background:var(--accent-soft);color:var(--ink);font-size:9px}@keyframes card_bloom-message{0%,14%{opacity:0;transform:scale(.68)}38%,82%{opacity:1;transform:none}96%,100%{opacity:0}}",
        "line_compose": ".assistant-bubble span{display:block;opacity:0;animation:line_compose-line 5.2s ease-in-out infinite}.assistant-bubble span:nth-child(2){animation-delay:.13s}.assistant-bubble span:nth-child(3){animation-delay:.26s}@keyframes line_compose-line{0%,16%{opacity:0;transform:translateX(-14px)}36%,82%{opacity:1;transform:none}96%,100%{opacity:0}}@keyframes line_compose-message{0%,100%{transform:none}}",
        "stamp_settle": ".assistant-bubble{animation:stamp_settle-message 5.2s cubic-bezier(.2,.9,.24,1) infinite}.assistant-bubble::after{content:'K';position:absolute;right:10px;bottom:-12px;width:25px;height:25px;display:grid;place-items:center;border:2px solid var(--accent);border-radius:50%;background:white;color:var(--accent);font-size:10px;font-weight:850}@keyframes stamp_settle-message{0%,14%{opacity:0;transform:translateY(-18px) scale(.94)}38%,82%{opacity:1;transform:none}96%,100%{opacity:0}}",
    }[spec.variant]
    inner = (
        "<span>Сравнила два варианта.</span><span>Для вашей команды лучше второй:</span><span>он быстрее запускается.</span>"
        if spec.variant == "line_compose"
        else "Сравнила два варианта. Для вашей команды лучше второй: он быстрее запускается."
    )
    html = _wrap(
        spec,
        f"""    <div class="message-board"><div class="previous-message">Нужен вариант для команды из шести человек.</div><div class="assistant-row"><span class="avatar">К</span><div class="assistant-bubble">{inner}</div></div></div>""",
    )
    css = _base_css(spec) + f""".message-board{{min-height:270px;display:flex;flex-direction:column;justify-content:center;gap:18px;padding:28px;border-radius:28px;background:#f8f7f4;box-shadow:0 24px 58px rgba(31,38,50,.12)}}.previous-message{{align-self:flex-end;max-width:70%;padding:10px 13px;border-radius:16px 16px 5px;background:var(--accent);color:white;font-size:11px}}.assistant-row{{display:flex;align-items:flex-start;gap:9px}}.avatar{{flex:0 0 auto;width:31px;height:31px;display:grid;place-items:center;border-radius:11px;background:var(--accent-soft);color:var(--ink);font-weight:850;font-size:11px}}.assistant-bubble{{position:relative;max-width:76%;padding:12px 14px;border:1px solid rgba(29,35,47,.07);border-radius:6px 17px 17px;background:#fff;box-shadow:0 8px 20px rgba(32,39,49,.08);font-size:11px;line-height:1.55}}{extra}
""" + _command(".assistant-bubble", f"{spec.variant}-message", "1.5s") + _reduced_motion()
    return RenderedAssets(html, css)


def _render_user_message(spec: PatternVisualSpec) -> RenderedAssets:
    extra = {
        "arc_arrival": ".user-bubble{animation:arc_arrival-user 5.2s cubic-bezier(.2,.9,.24,1) infinite}@keyframes arc_arrival-user{0%,14%{opacity:0;transform:translate(62px,54px) rotate(5deg) scale(.78)}40%,82%{opacity:1;transform:none}96%,100%{opacity:0}}",
        "satin_drop": ".user-bubble{animation:satin_drop-user 5.2s cubic-bezier(.2,.9,.24,1) infinite}@keyframes satin_drop-user{0%,14%{opacity:0;transform:translateY(-28px);box-shadow:0 30px 45px rgba(29,35,47,.25)}42%,82%{opacity:1;transform:none;box-shadow:0 8px 20px rgba(29,35,47,.11)}96%,100%{opacity:0}}",
        "snap_stack": ".user-stack::after{content:'';position:absolute;right:0;top:0;bottom:0;border-right:1px dashed var(--accent);opacity:.35}.user-bubble{animation:snap_stack-user 5.2s cubic-bezier(.2,.9,.24,1) infinite}@keyframes snap_stack-user{0%,14%{opacity:0;transform:translateX(58px)}32%{opacity:1;transform:translateX(-7px)}44%,82%{opacity:1;transform:none}96%,100%{opacity:0}}",
        "receipt_lock": ".receipt{display:block;opacity:0;animation:receipt_lock-receipt 5.2s ease-in-out infinite}.user-bubble{animation:receipt_lock-user 5.2s ease-in-out infinite}@keyframes receipt_lock-user{0%,14%{opacity:0;transform:translateX(38px)}36%,82%{opacity:1;transform:none}96%,100%{opacity:0}}@keyframes receipt_lock-receipt{0%,34%{opacity:0;transform:translateY(-4px)}48%,82%{opacity:1;transform:none}96%,100%{opacity:0}}",
    }[spec.variant]
    html = _wrap(
        spec,
        """    <div class="message-board"><div class="assistant-ghost">Расскажите, что для вас важнее всего.</div><div class="user-stack"><div class="user-bubble">Быстрый запуск и понятная поддержка.</div><small class="receipt">✓ принято в диалог</small></div><div class="composer-ghost">Сообщение принято</div></div>""",
    )
    css = _base_css(spec) + f""".message-board{{min-height:280px;display:flex;flex-direction:column;justify-content:center;gap:16px;padding:28px;border-radius:28px;background:#f8f7f4;box-shadow:0 24px 58px rgba(31,38,50,.12)}}.assistant-ghost{{max-width:65%;padding:10px 13px;border-radius:6px 16px 16px;background:white;box-shadow:0 7px 18px rgba(31,38,50,.08);font-size:11px}}.user-stack{{position:relative;align-self:flex-end;display:grid;justify-items:end;gap:5px;padding-right:9px}}.user-bubble{{max-width:250px;padding:12px 14px;border-radius:17px 17px 5px;background:var(--accent);color:white;box-shadow:0 8px 20px rgba(29,35,47,.11);font-size:11px;line-height:1.45}}.receipt{{display:{'block' if spec.variant == 'receipt_lock' else 'none'};color:#77716b;font-size:9px}}.composer-ghost{{margin-top:14px;padding:10px 13px;border:1px solid #e3dfd8;border-radius:15px;background:white;color:#8a837c;font-size:10px}}{extra}
""" + _command(".user-bubble", f"{spec.variant}-user", "1.45s") + _reduced_motion()
    return RenderedAssets(html, css)


def _render_typing_indicator(spec: PatternVisualSpec) -> RenderedAssets:
    markup = {
        "waveform_whisper": "<span></span><span></span><span></span><span></span><span></span>",
        "quill_trace": "<b class=\"quill\">✦</b><i></i>",
        "word_build": "<span>смотрю</span><span>сверяю</span><span>отвечаю</span>",
        "orbit_dots": "<span></span><span></span><span></span>",
    }[spec.variant]
    variant_css = {
        "waveform_whisper": ".typing-mark{height:28px;display:flex;align-items:center;gap:4px}.typing-mark span{width:3px;height:8px;border-radius:3px;background:var(--accent);animation:waveform_whisper-typing 1.4s ease-in-out infinite}.typing-mark span:nth-child(2),.typing-mark span:nth-child(4){animation-delay:.14s}.typing-mark span:nth-child(3){animation-delay:.28s}@keyframes waveform_whisper-typing{0%,100%{height:7px;opacity:.45}50%{height:22px;opacity:1}}",
        "quill_trace": ".typing-mark{position:relative;width:120px;height:30px}.typing-mark i{position:absolute;left:7px;right:7px;bottom:7px;height:2px;border-radius:2px;background:linear-gradient(90deg,var(--accent),transparent);transform-origin:left}.quill{position:absolute;z-index:2;left:5px;top:0;color:var(--accent);animation:quill_trace-typing 2s ease-in-out infinite}.typing-mark i{animation:quill_trace-line 2s ease-in-out infinite}@keyframes quill_trace-typing{0%,100%{transform:translateX(0) rotate(-12deg)}65%{transform:translateX(96px) rotate(8deg)}}@keyframes quill_trace-line{0%,15%{transform:scaleX(.05);opacity:0}65%{transform:scaleX(1);opacity:1}100%{opacity:0}}",
        "word_build": ".typing-mark{display:flex;gap:5px}.typing-mark span{padding:5px 7px;border-radius:8px;background:#efede9;color:#8a837c;font-size:9px;animation:word_build-typing 2.4s ease-in-out infinite}.typing-mark span:nth-child(2){animation-delay:.35s}.typing-mark span:nth-child(3){animation-delay:.7s}@keyframes word_build-typing{0%,100%{background:#efede9;color:#8a837c;transform:none}24%,45%{background:var(--accent);color:white;transform:translateY(-2px)}}",
        "orbit_dots": ".typing-mark{position:relative;width:72px;height:34px}.typing-mark span{position:absolute;left:31px;top:13px;width:8px;height:8px;border-radius:50%;background:var(--accent);animation:orbit_dots-typing 1.9s linear infinite}.typing-mark span:nth-child(2){animation-delay:-.63s}.typing-mark span:nth-child(3){animation-delay:-1.26s}@keyframes orbit_dots-typing{from{transform:rotate(0) translateX(23px) rotate(0);opacity:.45}50%{opacity:1}to{transform:rotate(360deg) translateX(23px) rotate(-360deg);opacity:.45}}",
    }[spec.variant]
    command_target = {
        "waveform_whisper": ".typing-mark span",
        "quill_trace": ".typing-mark .quill",
        "word_build": ".typing-mark span",
        "orbit_dots": ".typing-mark span",
    }[spec.variant]
    html = _wrap(
        spec,
        f"""    <div class="typing-board"><div class="agent"><span>К</span><div><b>Каира</b><small>готовит ответ</small></div></div><div class="typing-bubble"><div class="typing-mark" data-typing-kind="{spec.variant}">{markup}</div></div><p>Ожидание объяснено, высота истории стабильна</p></div>""",
    )
    css = _base_css(spec) + f""".typing-board{{min-height:270px;display:grid;align-content:center;gap:14px;padding:28px;border-radius:28px;background:#f8f7f4;box-shadow:0 24px 58px rgba(31,38,50,.12)}}.agent{{display:flex;align-items:center;gap:9px}}.agent>span{{width:32px;height:32px;display:grid;place-items:center;border-radius:11px;background:var(--accent);color:white;font-weight:850;font-size:11px}}.agent div{{display:grid}}.agent b{{font-size:12px}}.agent small{{color:#79736d;font-size:9px}}.typing-bubble{{width:max-content;min-width:104px;padding:10px 14px;border-radius:5px 17px 17px;background:white;box-shadow:0 8px 20px rgba(31,38,50,.08)}}.typing-board>p{{margin:5px 0 0;color:#7c756f;font-size:10px}}{variant_css}
""" + _command(command_target, f"{spec.variant}-typing", "1.8s") + _reduced_motion()
    return RenderedAssets(html, css)


def _render_message_send(spec: PatternVisualSpec) -> RenderedAssets:
    icon = {
        "seal_release": "✓",
        "route_confirm": "→",
        "press_lift": "↑",
        "luminous_delivery": "✦",
    }[spec.variant]
    variant_css = {
        "seal_release": ".delivery-state{border-radius:50%;border:2px solid var(--accent);color:var(--accent)}.message-flight{animation:seal_release-send 6s cubic-bezier(.22,.8,.2,1) infinite}@keyframes seal_release-send{0%,10%{opacity:0;transform:translate(40px,88px) scale(.72)}24%{opacity:.45;transform:translate(26px,62px) scale(.82)}48%,80%{opacity:1;transform:none}94%,100%{opacity:0}}",
        "route_confirm": ".delivery-route{display:block;position:absolute;right:54px;bottom:61px;width:180px;height:92px;border-top:2px solid var(--accent);border-radius:50%;transform:rotate(-18deg);opacity:.35}.delivery-state{border-radius:10px;background:var(--accent-soft);color:var(--ink)}.message-flight{animation:route_confirm-send 6s cubic-bezier(.22,.8,.2,1) infinite}@keyframes route_confirm-send{0%,10%{opacity:0;transform:translate(92px,94px) scale(.7)}30%{opacity:.65;transform:translate(42px,48px) scale(.86)}52%,80%{opacity:1;transform:none}94%,100%{opacity:0}}",
        "press_lift": ".delivery-state{border-radius:9px;background:rgba(255,255,255,.22);color:white}.message-flight{animation:press_lift-send 6s cubic-bezier(.18,.84,.2,1) infinite}@keyframes press_lift-send{0%,10%{opacity:0;transform:translateY(104px) scaleX(.84)}25%{opacity:.55;transform:translateY(78px) scale(.9)}50%,80%{opacity:1;transform:none}94%,100%{opacity:0}}",
        "luminous_delivery": ".delivery-route{display:block;position:absolute;right:64px;bottom:68px;width:12px;height:12px;border-radius:50%;background:var(--accent-soft);box-shadow:0 0 24px 8px var(--accent-soft);animation:luminous_delivery-light 6s ease-in-out infinite}.delivery-state{color:var(--accent);font-size:13px}.message-flight{animation:luminous_delivery-send 6s cubic-bezier(.22,.8,.2,1) infinite}@keyframes luminous_delivery-send{0%,10%{opacity:0;transform:translate(54px,88px) scale(.78);box-shadow:0 0 0 transparent}46%,80%{opacity:1;transform:none;box-shadow:0 0 26px color-mix(in srgb,var(--accent),transparent 72%)}94%,100%{opacity:0}}@keyframes luminous_delivery-light{0%,12%{transform:translate(0,0);opacity:0}26%{opacity:1}52%{transform:translate(-174px,-82px);opacity:.8}68%,100%{opacity:0}}",
    }[spec.variant]
    html = _wrap(
        spec,
        f"""    <div class="send-demo"><span class="delivery-route" aria-hidden="true"></span><div class="message-flight">Хочу сравнить два варианта.<span class="delivery-state" data-delivery-feedback>{icon}</span></div><div class="send-context"><small>Каира увидит ваш вопрос здесь</small></div><footer><span>Хочу сравнить два варианта.</span><button type="button" aria-label="Отправить">↑</button></footer></div>""",
    )
    css = _base_css(spec) + f""".send-demo{{--send-duration: 1.8s;position:relative;height:310px;display:flex;flex-direction:column;justify-content:flex-end;gap:11px;padding:22px;overflow:hidden;border:1px solid rgba(28,35,48,.08);border-radius:29px;background:#fff;box-shadow:0 26px 62px rgba(31,38,50,.13)}}.message-flight{{position:absolute;right:22px;top:64px;max-width:72%;display:flex;align-items:center;gap:9px;padding:12px 14px;border-radius:18px 18px 5px;background:var(--accent);color:white;box-shadow:0 12px 28px color-mix(in srgb,var(--accent),transparent 72%);font-size:11px;line-height:1.4}}.delivery-state{{min-width:24px;height:24px;display:grid;place-items:center;font-weight:850;font-size:10px}}.send-context{{min-height:130px;padding:8px;color:#817b74;font-size:10px}}.send-demo footer{{display:flex;align-items:center;gap:8px;padding:8px 8px 8px 13px;border:1px solid #dfdbd5;border-radius:17px;background:#faf9f7;color:#69645f;font-size:10px;box-shadow:inset 0 1px 0 white}}.send-demo footer button{{margin-left:auto;width:38px;height:38px;border:0;border-radius:13px;background:var(--accent);color:white;font-size:16px;box-shadow:0 8px 18px color-mix(in srgb,var(--accent),transparent 72%)}}.delivery-route{{display:none}}{variant_css}
html[data-pattern-command='run'] .message-flight,
html[data-pattern-command='replay'] .message-flight {{ animation: {spec.variant}-send var(--send-duration) cubic-bezier(.22,.8,.2,1) both; }}
""" + _reduced_motion()
    return RenderedAssets(html, css)


def _composer_markup(spec: PatternVisualSpec) -> str:
    fields = {
        "floating_label": """<div class="composer" data-composer-field="floating-label"><label for="floating-message">Ваш вопрос</label><textarea id="floating-message" rows="2">Подскажите подходящий тариф</textarea><button type="button" aria-label="Отправить">↑</button></div>""",
        "contextual_rail": """<div class="composer" data-composer-field="contextual-rail"><span class="context-rail"><b>Тема</b>Подбор тарифа</span><textarea aria-label="Сообщение" rows="2">Что входит в поддержку?</textarea><button type="button" aria-label="Отправить">↑</button></div>""",
        "soft_inset": """<div class="composer" data-composer-field="soft-inset"><div class="inset-field"><textarea aria-label="Сообщение" rows="2">Нужна помощь с запуском</textarea><small>Можно написать своими словами</small></div><button type="button" aria-label="Отправить">↑</button></div>""",
        "command_glow": """<div class="composer" data-composer-field="command-glow"><textarea aria-label="Сообщение" rows="2">Сравните варианты для команды</textarea><kbd>⌘ Enter</kbd><button type="button" aria-label="Отправить">↑</button></div>""",
    }
    return fields[spec.variant]


def _render_composer_focus(spec: PatternVisualSpec) -> RenderedAssets:
    extra = {
        "floating_label": ".composer{padding:17px 54px 8px 13px;border:1px solid #d9d5cf}.composer label{position:absolute;left:13px;top:19px;padding:0 5px;background:white;color:#817a74;font-size:11px;animation:floating_label-focus-label 5s ease-in-out infinite}.composer textarea{padding-top:6px}.composer button{right:8px;bottom:8px}@keyframes floating_label-focus{0%,18%{border-color:#d9d5cf;box-shadow:none}42%,82%{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent),transparent 84%)}100%{border-color:#d9d5cf}}@keyframes floating_label-focus-label{0%,18%{transform:none;color:#817a74}42%,82%{transform:translateY(-17px) scale(.9);color:var(--accent)}100%{transform:none}}",
        "contextual_rail": ".composer{grid-template-columns:88px 1fr 42px;padding:7px;border:1px solid #dedad4}.context-rail{display:grid;align-content:center;padding:6px 9px;border-radius:12px;background:#efede9;color:#6f6963;font-size:9px}.context-rail b{color:var(--accent);font-size:8px;text-transform:uppercase}.composer button{position:static}.composer::after{content:'';position:absolute;left:8px;bottom:5px;width:0;height:2px;background:var(--accent);animation:contextual_rail-focus-rail 5s ease-in-out infinite}@keyframes contextual_rail-focus{0%,18%{box-shadow:none}42%,82%{box-shadow:0 10px 28px color-mix(in srgb,var(--accent),transparent 82%)}100%{box-shadow:none}}@keyframes contextual_rail-focus-rail{0%,18%{width:0}48%,82%{width:calc(100% - 16px)}100%{width:0}}",
        "soft_inset": ".composer{grid-template-columns:1fr 44px;padding:8px;border:0;background:#ebe8e3;box-shadow:inset 0 2px 8px rgba(39,42,48,.12)}.inset-field{display:grid;padding:7px 10px;border-radius:13px;background:#f7f5f2;box-shadow:inset 0 1px 4px rgba(39,42,48,.08)}.inset-field small{color:#918a83;font-size:8px}.composer button{position:static}@keyframes soft_inset-focus{0%,18%{box-shadow:inset 0 2px 8px rgba(39,42,48,.12)}42%,82%{box-shadow:inset 0 2px 10px color-mix(in srgb,var(--accent),transparent 75%),0 10px 26px rgba(39,42,48,.09)}100%{box-shadow:inset 0 2px 8px rgba(39,42,48,.12)}}",
        "command_glow": ".composer{grid-template-columns:1fr auto 42px;padding:8px;border:1px solid #dcd8d2;overflow:hidden}.composer::after{content:'';position:absolute;left:-30%;bottom:0;width:34%;height:2px;background:linear-gradient(90deg,transparent,var(--accent),var(--accent-soft));animation:command_glow-focus-glow 5s ease-in-out infinite}.composer kbd{align-self:center;padding:5px 7px;border:1px solid #ddd8d1;border-radius:8px;background:#f4f2ee;color:#77716b;font:9px ui-monospace,monospace}.composer button{position:static}@keyframes command_glow-focus{0%,18%{border-color:#dcd8d2}42%,82%{border-color:var(--accent);box-shadow:0 0 22px color-mix(in srgb,var(--accent),transparent 80%)}100%{border-color:#dcd8d2}}@keyframes command_glow-focus-glow{0%,18%{left:-30%;opacity:0}44%{opacity:1}74%,82%{left:96%;opacity:0}100%{opacity:0}}",
    }[spec.variant]
    html = _wrap(
        spec,
        f"""    <div class="composer-board"><div class="thread-hint"><span>Каира</span><p>Задайте вопрос — я сохраню контекст выбора.</p></div>{_composer_markup(spec)}</div>""",
    )
    css = _base_css(spec) + f""".composer-board{{min-height:280px;display:flex;flex-direction:column;justify-content:flex-end;gap:48px;padding:25px;border-radius:29px;background:#fff;box-shadow:0 25px 60px rgba(31,38,50,.13)}}.thread-hint{{display:flex;align-items:flex-start;gap:9px}}.thread-hint span{{width:32px;height:32px;display:grid;place-items:center;border-radius:11px;background:var(--accent-soft);color:var(--ink);font-size:10px;font-weight:800}}.thread-hint p{{max-width:220px;margin:0;padding:10px 12px;border-radius:5px 15px 15px;background:#f4f2ee;font-size:10px;line-height:1.45}}.composer{{position:relative;min-height:68px;display:grid;align-items:center;gap:7px;border-radius:18px;background:white;animation:{spec.variant}-focus 5s ease-in-out infinite}}.composer textarea{{min-width:0;width:100%;resize:none;border:0;outline:0;background:transparent;color:var(--ink);font-size:11px;line-height:1.4}}.composer button{{position:absolute;width:40px;height:40px;border:0;border-radius:13px;background:var(--accent);color:white;box-shadow:0 8px 18px color-mix(in srgb,var(--accent),transparent 72%)}}{extra}
""" + _command(".composer", f"{spec.variant}-focus", "1.6s") + """@media(max-width:380px){.composer-board{padding:18px}.context-rail,.composer kbd{display:none}.contextual_rail .composer,.command_glow .composer{grid-template-columns:1fr 42px}}
""" + _reduced_motion()
    return RenderedAssets(html, css)


def _render_control_hover(spec: PatternVisualSpec) -> RenderedAssets:
    extra = {
        "caption_reveal": ".focus-control{width:44px;overflow:hidden;justify-content:flex-start;padding-left:13px;animation:caption_reveal-hover 4.8s ease-in-out infinite}.focus-control span{margin-left:10px;white-space:nowrap;font-size:10px}@keyframes caption_reveal-hover{0%,18%,100%{width:44px}42%,76%{width:108px;background:var(--accent);color:white}}",
        "ring_track": ".focus-control::after{content:'';position:absolute;inset:-5px;border:2px solid transparent;border-top-color:var(--accent);border-right-color:var(--accent);border-radius:50%;animation:ring_track-hover 4.8s ease-in-out infinite}@keyframes ring_track-hover{0%,18%{transform:rotate(-90deg);opacity:0}48%,76%{transform:rotate(270deg);opacity:1}100%{opacity:0}}",
        "soft_tilt": ".focus-control{animation:soft_tilt-hover 4.8s ease-in-out infinite;transform-style:preserve-3d}@keyframes soft_tilt-hover{0%,18%,100%{transform:none;box-shadow:0 7px 18px rgba(31,38,50,.1)}48%,76%{transform:perspective(180px) rotateX(8deg) rotateY(-10deg) translateY(-3px);box-shadow:9px 13px 24px color-mix(in srgb,var(--accent),transparent 78%)}}",
        "ink_fill": ".focus-control{overflow:hidden}.focus-control::before{content:'';position:absolute;z-index:0;left:0;right:0;bottom:0;height:0;background:var(--accent);animation:ink_fill-hover 4.8s ease-in-out infinite}.focus-control b{position:relative;z-index:1;animation:ink_fill-icon 4.8s ease-in-out infinite}@keyframes ink_fill-hover{0%,18%,100%{height:0}48%,76%{height:100%}}@keyframes ink_fill-icon{0%,28%,100%{color:var(--ink)}48%,76%{color:white}}",
    }[spec.variant]
    label = "<span>Свернуть</span>" if spec.variant == "caption_reveal" else ""
    html = _wrap(
        spec,
        f"""    <div class="control-board"><header><div><strong>Каира</strong><small>AI-консьерж</small></div><nav><button type="button" aria-label="Звук">⌁</button><button class="focus-control" type="button" aria-label="Свернуть"><b>−</b>{label}</button><button type="button" aria-label="Закрыть">×</button></nav></header><div class="control-copy">Наведённое действие остаётся устойчивым и читаемым.</div></div>""",
    )
    css = _base_css(spec) + f""".control-board{{min-height:250px;padding:23px;border-radius:28px;background:#fff;box-shadow:0 25px 60px rgba(31,38,50,.13)}}.control-board header{{display:flex;align-items:center;padding-bottom:18px;border-bottom:1px solid #ece8e2}}.control-board header>div{{display:grid}}.control-board strong{{font-size:14px}}.control-board small{{color:#7c756f;font-size:9px}}nav{{display:flex;gap:8px;margin-left:auto}}nav button{{position:relative;width:44px;height:44px;display:flex;align-items:center;justify-content:center;border:1px solid #e1ddd7;border-radius:14px;background:#f8f6f2;color:var(--ink)}}nav button b{{font-size:17px}}.control-copy{{margin-top:32px;padding:18px;border-radius:17px;background:color-mix(in srgb,var(--accent-soft),white 73%);font-size:11px;line-height:1.45}}{extra}
""" + _command(".focus-control", f"{spec.variant}-hover", "1.55s") + _reduced_motion()
    return RenderedAssets(html, css)


def _responsive_panel(kind: str) -> str:
    attribute = "data-desktop-panel" if kind == "desktop" else "data-mobile-sheet"
    return f"""<section class="device {kind}" {attribute}><header><b>К</b><span>Каира</span></header><div class="mini-thread"><i></i><i></i><i></i></div><footer>Сообщение <b>↑</b></footer></section>"""


def _render_responsive_transition(spec: PatternVisualSpec) -> RenderedAssets:
    extra = {
        "device_morph": ".transition-arrow::before{content:'↝';font-size:30px;color:var(--accent)}.desktop{animation:device_morph-desktop 5.4s ease-in-out infinite}.mobile{animation:device_morph-mobile 5.4s ease-in-out infinite}@keyframes device_morph-desktop{0%,38%,100%{opacity:1;transform:none}64%,82%{opacity:.38;transform:scale(.92)}}@keyframes device_morph-mobile{0%,38%,100%{opacity:.38;transform:scale(.92)}64%,82%{opacity:1;transform:none}}@keyframes device_morph-transition{0%,100%{transform:translateX(-4px)}50%{transform:translateX(4px)}}",
        "panel_to_sheet": ".transition-arrow::before{content:'width · radius · dock';display:block;color:var(--accent);font-size:9px;font-weight:800}.responsive-board{animation:panel_to_sheet-transition 5.4s ease-in-out infinite}@keyframes panel_to_sheet-transition{0%,100%{column-gap:18px}50%{column-gap:28px}}",
        "width_choreography": ".responsive-board::after{content:'tablet';position:absolute;left:50%;top:47%;padding:5px 8px;border-radius:8px;background:var(--accent-soft);color:var(--ink);font-size:8px;transform:translate(-50%,-50%)}.transition-arrow::before{content:'1  ·  2  ·  3';color:var(--accent);font-weight:850}@keyframes width_choreography-transition{0%,100%{opacity:.55}50%{opacity:1}}",
        "layout_relay": ".transition-arrow::before{content:'●  ●  ●';display:block;color:var(--accent);letter-spacing:3px}.transition-arrow::after{content:'';display:block;width:58px;border-top:1px dashed var(--accent);animation:layout_relay-transition 2.4s ease-in-out infinite}@keyframes layout_relay-transition{0%,100%{transform:scaleX(.45);opacity:.4}50%{transform:scaleX(1);opacity:1}}",
    }[spec.variant]
    html = _wrap(
        spec,
        f"""    <div class="responsive-board"><div class="device-label">DESKTOP</div><div class="device-label mobile-label">MOBILE</div>{_responsive_panel('desktop')}<div class="transition-arrow"><strong>desktop → mobile</strong></div>{_responsive_panel('mobile')}</div>""",
    )
    css = _base_css(spec) + f""".responsive-board{{position:relative;min-height:320px;display:grid;grid-template-columns:minmax(150px,1fr) 74px minmax(118px,.72fr);align-items:end;gap:14px;padding:38px 18px 18px;border-radius:29px;background:#ddd9d3;box-shadow:0 25px 60px rgba(31,38,50,.13)}}.device-label{{position:absolute;left:26px;top:17px;color:#6f6963;font-size:9px;font-weight:800;letter-spacing:.12em}}.mobile-label{{left:auto;right:37px}}.device{{display:grid;grid-template:auto 1fr auto/1fr;overflow:hidden;background:#fff;box-shadow:0 14px 34px rgba(31,38,50,.15)}}.device header{{display:flex;align-items:center;gap:6px;padding:10px;background:color-mix(in srgb,var(--accent-soft),white 66%);font-size:9px}}.device header b{{width:22px;height:22px;display:grid;place-items:center;border-radius:8px;background:var(--accent);color:white}}.mini-thread{{display:grid;align-content:center;gap:8px;padding:12px;background:#faf9f7}}.mini-thread i{{display:block;width:72%;height:17px;border-radius:9px;background:#e9e5df}}.mini-thread i:nth-child(2){{justify-self:end;width:64%;background:var(--accent)}}.mini-thread i:nth-child(3){{width:48%}}.device footer{{margin:8px;padding:7px;border:1px solid #e1ddd7;border-radius:10px;color:#827b75;font-size:8px}}.device footer b{{float:right;color:var(--accent)}}.desktop{{height:250px;border-radius:22px}}.mobile{{height:268px;border-radius:26px 26px 9px 9px}}.transition-arrow{{align-self:center;display:grid;place-items:center;gap:8px;text-align:center;animation:{spec.variant}-transition 3s ease-in-out infinite}}.transition-arrow strong{{font-size:9px;line-height:1.4}}{extra}
""" + _command(".transition-arrow", f"{spec.variant}-transition", "1.8s") + """@media(max-width:420px){.responsive-board{grid-template-columns:1fr 46px .8fr;padding-inline:10px}.device-label{left:16px}.mobile-label{left:auto;right:22px}.desktop{height:222px}.mobile{height:244px}.transition-arrow strong{font-size:8px}}
""" + _reduced_motion()
    return RenderedAssets(html, css)


Renderer = Callable[[PatternVisualSpec], RenderedAssets]
_RENDERERS: dict[str, Renderer] = {
    "launcher_shape": _render_launcher_shape,
    "launcher_idle": _render_launcher_idle,
    "launcher_attention": _render_launcher_attention,
    "shell_layout": _render_shell_layout,
    "widget_open": _render_widget_open,
    "widget_close": _render_widget_close,
    "background_effect": _render_background_effect,
    "assistant_message_enter": _render_assistant_message,
    "user_message_enter": _render_user_message,
    "typing_indicator": _render_typing_indicator,
    "message_send": _render_message_send,
    "composer_focus": _render_composer_focus,
    "control_hover": _render_control_hover,
    "responsive_transition": _render_responsive_transition,
}


def render_pattern(spec: PatternVisualSpec) -> RenderedAssets:
    """Render one category-specific preview from a validated declarative spec."""

    return _RENDERERS[spec.category](spec)
