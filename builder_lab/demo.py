from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Mapping

from .models import BuilderRequest, TokenUsage, WidgetArtifact
from .validation import validate_artifact


DEMO_SCHEMA_VERSION = 1
MAX_DEMO_BYTES = 1_000_000


class DemoUnavailable(ValueError):
    """The persisted public demo is missing, corrupt, or unsafe."""


@dataclass(frozen=True)
class BuilderDemo:
    generated_at: str
    model: str
    request: BuilderRequest
    usage: TokenUsage
    elapsed_seconds: float
    artifact: WidgetArtifact

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DEMO_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "model": self.model,
            "request": self.request.to_dict(),
            "usage": self.usage.to_dict(),
            "elapsed_seconds": self.elapsed_seconds,
            "artifact": self.artifact.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BuilderDemo":
        try:
            if int(payload.get("schema_version", 0)) != DEMO_SCHEMA_VERSION:
                raise DemoUnavailable("unsupported demo schema")
            generated_at = str(payload["generated_at"])
            datetime.fromisoformat(generated_at)
            model = str(payload["model"]).strip()
            if not model or len(model) > 120:
                raise DemoUnavailable("invalid demo model")
            request = BuilderRequest.from_dict(payload["request"])
            usage = TokenUsage.from_dict(payload.get("usage"))
            elapsed_seconds = float(payload.get("elapsed_seconds", 0))
            if elapsed_seconds < 0 or not math.isfinite(elapsed_seconds):
                raise DemoUnavailable("invalid demo duration")
            artifact = WidgetArtifact.from_dict(payload["artifact"])
        except DemoUnavailable:
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise DemoUnavailable("invalid demo document") from exc
        if validate_artifact(artifact, previous_revision=0):
            raise DemoUnavailable("unsafe demo artifact")
        return cls(
            generated_at=generated_at,
            model=model,
            request=request,
            usage=usage,
            elapsed_seconds=elapsed_seconds,
            artifact=artifact,
        )


def _from_snapshot(payload: Mapping[str, Any], model: str) -> BuilderDemo:
    if payload.get("status") != "completed" or not payload.get("artifact"):
        raise DemoUnavailable("only a completed run can become the public demo")
    document = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "request": payload.get("request"),
        "usage": payload.get("usage"),
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "artifact": payload.get("artifact"),
    }
    return BuilderDemo.from_dict(document)


def save_demo(path: Path, snapshot: Mapping[str, Any], *, model: str) -> BuilderDemo:
    demo = _from_snapshot(snapshot, model)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        demo.to_dict(), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_DEMO_BYTES:
        raise DemoUnavailable("demo document is too large")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return demo


def load_demo(path: Path) -> BuilderDemo:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_DEMO_BYTES:
            raise DemoUnavailable("demo document is too large")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise DemoUnavailable("invalid demo document")
        return BuilderDemo.from_dict(payload)
    except DemoUnavailable:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DemoUnavailable("demo is unavailable") from exc


def render_demo_page(demo: BuilderDemo) -> str:
    prompt = escape(demo.request.brief)
    model = escape(demo.model)
    stage = escape(demo.artifact.stage.value)
    art_direction = escape(demo.artifact.art_direction)
    generated_at = escape(demo.generated_at)
    tokens = f"{demo.usage.total_tokens:,}".replace(",", " ")
    elapsed = f"{demo.elapsed_seconds:.1f} с"
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Kaigo — живой пример AI-виджета</title>
  <style>
    :root {{ color-scheme:dark; --bg:#131310; --surface:#211f1a; --line:#3c3931; --text:#f4efe5; --muted:#a49d91; --accent:#dd8b67; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-width:320px; min-height:100dvh; color:var(--text); background:radial-gradient(circle at 78% 8%,rgba(221,139,103,.11),transparent 34%),var(--bg); font-family:Inter,"Segoe UI",Arial,sans-serif; }}
    main {{ width:min(1500px,100%); min-height:100dvh; margin:auto; padding:18px; display:grid; grid-template-columns:minmax(300px,.62fr) minmax(520px,1.38fr); gap:18px; }}
    aside,.stage {{ border:1px solid var(--line); background:rgba(33,31,26,.94); box-shadow:0 24px 70px rgba(0,0,0,.22); }}
    aside {{ padding:27px; border-radius:25px; overflow:auto; }}
    .stage {{ min-height:760px; padding:14px; border-radius:31px; display:grid; grid-template-rows:auto 1fr; gap:12px; }}
    .eyebrow {{ margin:0 0 11px; color:var(--accent); font:700 10px/1.2 ui-monospace,monospace; letter-spacing:.14em; text-transform:uppercase; }}
    h1 {{ margin:0; max-width:11ch; font-size:clamp(32px,4vw,58px); line-height:.96; letter-spacing:-.052em; }}
    .lead {{ margin:16px 0 25px; color:var(--muted); line-height:1.55; }}
    dl {{ display:grid; grid-template-columns:1fr 1fr; gap:1px; margin:0 0 24px; background:var(--line); border:1px solid var(--line); }}
    dl div {{ min-width:0; padding:12px; background:#191814; }}
    dt {{ color:#777168; font:700 9px/1.2 ui-monospace,monospace; letter-spacing:.1em; text-transform:uppercase; }}
    dd {{ margin:5px 0 0; overflow:hidden; text-overflow:ellipsis; font-size:12px; white-space:nowrap; }}
    h2 {{ margin:24px 0 9px; font-size:13px; }}
    pre,p.direction {{ margin:0; color:#cec7bb; white-space:pre-wrap; font:400 12px/1.55 ui-monospace,monospace; }}
    .stage-head {{ display:flex; align-items:center; justify-content:space-between; gap:16px; padding:4px 6px; }}
    .stage-head strong {{ font-size:13px; }}
    .toggle {{ display:flex; gap:3px; padding:3px; border:1px solid var(--line); border-radius:11px; background:#191814; }}
    button {{ min-height:31px; border:0; border-radius:7px; padding:0 11px; color:#918a80; background:transparent; cursor:pointer; }}
    button.active {{ color:var(--text); background:#312e27; }}
    .canvas {{ min-height:680px; display:flex; align-items:stretch; justify-content:center; overflow:hidden; border-radius:22px; background:linear-gradient(135deg,#ede7dc,#d6cdc0); }}
    .viewport {{ width:100%; min-height:680px; align-self:stretch; display:flex; transition:width .35s ease,border-radius .35s ease; }}
    .viewport.mobile {{ flex:0 1 min(390px,calc(100% - 28px)); width:min(390px,calc(100% - 28px)); min-height:620px; margin:14px 0; overflow:hidden; border:8px solid #28251f; border-radius:32px; }}
    iframe {{ display:block; flex:1 1 auto; width:100%; min-height:0; border:0; }}
    @media (max-width:900px) {{ main {{ grid-template-columns:1fr; padding:10px; }} .stage {{ min-height:720px; }} }}
    @media (max-width:520px) {{ aside {{ padding:20px; }} dl {{ grid-template-columns:1fr; }} .stage {{ padding:9px; border-radius:22px; }} .stage-head {{ align-items:flex-start; flex-direction:column; }} }}
    @media (prefers-reduced-motion:reduce) {{ * {{ transition:none!important; }} }}
  </style>
</head>
<body>
  <main>
    <aside>
      <p class="eyebrow">Kaigo / реальная Gemini-генерация</p>
      <h1>Готовый AI-виджет.</h1>
      <p class="lead">Это не макет и не заранее нарисованная анимация. Справа — финальный артефакт, созданный моделью по этапам и прошедший проверку Kaigo.</p>
      <dl>
        <div><dt>Модель</dt><dd>{model}</dd></div>
        <div><dt>Движок</dt><dd>{escape(demo.request.engine.value)}</dd></div>
        <div><dt>Финальная стадия</dt><dd>{stage}</dd></div>
        <div><dt>Ревизия</dt><dd>{demo.artifact.revision}</dd></div>
        <div><dt>Токены</dt><dd>{tokens}</dd></div>
        <div><dt>Время</dt><dd>{elapsed}</dd></div>
      </dl>
      <h2>Исходный запрос</h2>
      <pre>{prompt}</pre>
      <h2>Арт-дирекция модели</h2>
      <p class="direction">{art_direction}</p>
      <h2>Сохранено</h2>
      <p class="direction">{generated_at}</p>
    </aside>
    <section class="stage" aria-label="Интерактивный виджет">
      <header class="stage-head">
        <strong>Интерактивный результат</strong>
        <div class="toggle" aria-label="Размер предпросмотра">
          <button type="button" class="active" data-view="desktop">Компьютер</button>
          <button type="button" data-view="mobile">Телефон</button>
        </div>
      </header>
      <div class="canvas"><div class="viewport" id="viewport"><iframe src="preview" sandbox="allow-scripts" referrerpolicy="no-referrer" title="Сгенерированный AI-виджет Kaigo"></iframe></div></div>
    </section>
  </main>
  <script>
    const viewport=document.getElementById('viewport');
    document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{{
      document.querySelectorAll('[data-view]').forEach(item=>item.classList.toggle('active',item===button));
      viewport.classList.toggle('mobile',button.dataset.view==='mobile');
    }}));
  </script>
</body>
</html>"""


def render_demo_unavailable() -> str:
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kaigo — демо готовится</title><style>body{min-height:100vh;margin:0;display:grid;place-items:center;color:#f4efe5;background:#151512;font:16px/1.5 system-ui}main{max-width:560px;padding:36px;border:1px solid #3c3931;border-radius:24px;background:#211f1a}h1{margin:0 0 12px}p{margin:0;color:#aaa298}</style></head><body><main><h1>Демонстрация пока готовится</h1><p>Kaigo сохраняет проверенный результат реальной Gemini-генерации. Обновите страницу немного позже.</p></main></body></html>"""
