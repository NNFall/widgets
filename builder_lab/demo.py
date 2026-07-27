from __future__ import annotations

import json
import hashlib
import ipaddress
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .models import BuilderRequest, TokenUsage, WidgetArtifact
from .validation import validate_artifact


DEMO_SCHEMA_VERSION = 2
MAX_DEMO_BYTES = 1_000_000
_ARTIFACT_IDENTITY = re.compile(r"^[0-9a-f]{64}$")


class DemoUnavailable(ValueError):
    """The persisted public demo is missing, corrupt, or unsafe."""


@dataclass(frozen=True)
class BuilderDemo:
    schema_version: int
    generated_at: str
    model: str
    request: BuilderRequest
    usage: TokenUsage
    elapsed_seconds: float
    artifact: WidgetArtifact
    artifact_identity: str
    source_url: str | None = None
    chat_system_prompt: str | None = None

    @property
    def chat_enabled(self) -> bool:
        return (
            self.schema_version >= 2
            and self.source_url is not None
            and self.chat_system_prompt is not None
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "model": self.model,
            "request": self.request.to_dict(),
            "usage": self.usage.to_dict(),
            "elapsed_seconds": self.elapsed_seconds,
            "artifact": self.artifact.to_dict(),
        }
        if self.schema_version >= 2:
            payload.update(
                {
                    "artifact_identity": self.artifact_identity,
                    "source_url": self.source_url,
                    "chat_system_prompt": self.chat_system_prompt,
                }
            )
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BuilderDemo":
        try:
            schema_version = int(payload.get("schema_version", 0))
            if schema_version not in {1, DEMO_SCHEMA_VERSION}:
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
            computed_identity = _artifact_identity(artifact)
            if schema_version == 1:
                artifact_identity = computed_identity
                source_url = None
                chat_system_prompt = None
            else:
                artifact_identity = str(payload.get("artifact_identity", ""))
                if (
                    not _ARTIFACT_IDENTITY.fullmatch(artifact_identity)
                    or artifact_identity != computed_identity
                ):
                    raise DemoUnavailable("invalid demo artifact identity")
                raw_source = payload.get("source_url")
                raw_prompt = payload.get("chat_system_prompt")
                if raw_source is None and raw_prompt is None:
                    source_url = None
                    chat_system_prompt = None
                elif raw_source is None or raw_prompt is None:
                    raise DemoUnavailable("incomplete demo chat grounding")
                else:
                    source_url = _verified_source_url(str(raw_source))
                    chat_system_prompt = _chat_system_prompt(str(raw_prompt))
        except DemoUnavailable:
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise DemoUnavailable("invalid demo document") from exc
        if validate_artifact(artifact, previous_revision=0):
            raise DemoUnavailable("unsafe demo artifact")
        return cls(
            schema_version=schema_version,
            generated_at=generated_at,
            model=model,
            request=request,
            usage=usage,
            elapsed_seconds=elapsed_seconds,
            artifact=artifact,
            artifact_identity=artifact_identity,
            source_url=source_url,
            chat_system_prompt=chat_system_prompt,
        )


def _artifact_identity(artifact: WidgetArtifact) -> str:
    encoded = json.dumps(
        artifact.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verified_source_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise DemoUnavailable("invalid demo source URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
    ):
        raise DemoUnavailable("invalid demo source URL")
    lowered = host.rstrip(".").lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise DemoUnavailable("invalid demo source URL")
    try:
        address = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise DemoUnavailable("invalid demo source URL")
    if address is None:
        try:
            ascii_host = lowered.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise DemoUnavailable("invalid demo source URL") from exc
        labels = ascii_host.split(".")
        if (
            len(ascii_host) > 253
            or len(labels) < 2
            or any(
                len(label) > 63
                or not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label
                )
                for label in labels
            )
            or labels[-1].isdigit()
        ):
            raise DemoUnavailable("invalid demo source URL")
        lowered = ascii_host
    netloc = lowered if port is None else f"{lowered}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))


def _chat_system_prompt(value: str) -> str:
    prompt = value.strip()
    if not prompt or len(prompt) > 16_000 or "\x00" in prompt:
        raise DemoUnavailable("invalid demo chat system prompt")
    return prompt


def _from_snapshot(
    payload: Mapping[str, Any],
    model: str,
    *,
    source_url: str | None = None,
    chat_system_prompt: str | None = None,
) -> BuilderDemo:
    if payload.get("status") != "completed" or not payload.get("artifact"):
        raise DemoUnavailable("only a completed run can become the public demo")
    if (source_url is None) != (chat_system_prompt is None):
        raise DemoUnavailable("source URL and chat prompt must be supplied together")
    artifact = WidgetArtifact.from_dict(payload["artifact"])
    document = {
        "schema_version": DEMO_SCHEMA_VERSION if source_url is not None else 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "request": payload.get("request"),
        "usage": payload.get("usage"),
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "artifact": payload.get("artifact"),
        "artifact_identity": _artifact_identity(artifact),
        "source_url": source_url,
        "chat_system_prompt": chat_system_prompt,
    }
    return BuilderDemo.from_dict(document)


def save_demo(
    path: Path,
    snapshot: Mapping[str, Any],
    *,
    model: str,
    source_url: str | None = None,
    chat_system_prompt: str | None = None,
) -> BuilderDemo:
    demo = _from_snapshot(
        snapshot,
        model,
        source_url=source_url,
        chat_system_prompt=chat_system_prompt,
    )
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
    chat_status = (
        "Реальный Gemini-чат включён"
        if demo.chat_enabled
        else "Только визуальный preview — перегенерируйте demo для реального чата"
    )
    chat_enabled = "true" if demo.chat_enabled else "false"
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Kaigo — AI-виджет в работе</title>
  <style>
    :root {{ color-scheme:dark; --bg:#131310; --surface:#211f1a; --line:#3c3931; --text:#f4efe5; --muted:#a49d91; --accent:#dd8b67; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-width:320px; min-height:100dvh; color:var(--text); background:radial-gradient(circle at 78% 8%,rgba(221,139,103,.11),transparent 34%),var(--bg); font-family:Inter,"Segoe UI",Arial,sans-serif; }}
    main {{ width:min(1480px,100%); min-height:100dvh; margin:auto; padding:clamp(10px,2vw,24px); display:grid; grid-template-rows:auto minmax(0,1fr); gap:14px; }}
    .intro,.stage {{ border:1px solid var(--line); background:rgba(33,31,26,.94); }}
    .intro {{ padding:clamp(18px,2.4vw,30px); display:grid; grid-template-columns:minmax(0,1fr) minmax(260px,.55fr); gap:24px; align-items:end; }}
    .stage {{ min-height:clamp(540px,76dvh,900px); padding:12px; display:grid; grid-template-rows:auto minmax(0,1fr); gap:10px; }}
    .eyebrow {{ margin:0 0 11px; color:var(--accent); font:700 10px/1.2 ui-monospace,monospace; letter-spacing:.14em; text-transform:uppercase; }}
    h1 {{ margin:0; max-width:15ch; font-size:clamp(30px,4vw,60px); line-height:.96; letter-spacing:-.052em; }}
    .lead {{ margin:14px 0 0; max-width:56ch; color:var(--muted); line-height:1.55; }}
    details {{ border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
    summary {{ min-height:44px; display:flex; align-items:center; cursor:pointer; color:#d8d1c6; font-size:12px; }}
    .evidence {{ padding:4px 0 14px; }}
    dl {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1px; margin:0; background:var(--line); border:1px solid var(--line); }}
    dl div {{ min-width:0; padding:10px; background:#191814; }}
    dt {{ color:#777168; font:700 9px/1.2 ui-monospace,monospace; letter-spacing:.1em; text-transform:uppercase; }}
    dd {{ margin:5px 0 0; overflow:hidden; text-overflow:ellipsis; font-size:12px; white-space:nowrap; }}
    pre,p.direction {{ margin:10px 0 0; color:#aaa399; white-space:pre-wrap; font:400 11px/1.5 ui-monospace,monospace; }}
    .stage-head {{ display:flex; align-items:center; justify-content:space-between; gap:16px; padding:4px 6px; }}
    .stage-head strong {{ font-size:13px; }}
    .status {{ color:var(--muted); font:600 10px/1.25 ui-monospace,monospace; letter-spacing:.06em; text-transform:uppercase; }}
    .toggle {{ display:flex; gap:3px; padding:3px; border:1px solid var(--line); border-radius:11px; background:#191814; }}
    button {{ min-height:44px; border:0; border-radius:7px; padding:0 13px; color:#918a80; background:transparent; cursor:pointer; }}
    button.active {{ color:var(--text); background:#312e27; }}
    .canvas {{ min-height:0; display:flex; align-items:stretch; justify-content:center; overflow:hidden; background:linear-gradient(135deg,#ede7dc,#d6cdc0); }}
    .viewport {{ width:100%; min-height:0; align-self:stretch; display:flex; transition:width .25s ease,border-radius .25s ease; }}
    .viewport.mobile {{ flex:0 1 min(390px,calc(100% - 24px)); width:min(390px,calc(100% - 24px)); margin:12px 0; overflow:hidden; border:6px solid #28251f; border-radius:26px; }}
    iframe {{ display:block; flex:1 1 auto; width:100%; min-height:0; border:0; }}
    @media (max-width:760px) {{ .intro {{ grid-template-columns:1fr; }} .stage {{ min-height:clamp(520px,78dvh,820px); }} .stage-head {{ align-items:flex-start; flex-direction:column; }} dl {{ grid-template-columns:1fr 1fr; }} }}
    @media (max-width:460px) {{ dl {{ grid-template-columns:1fr; }} .toggle {{ width:100%; }} .toggle button {{ flex:1; }} }}
    @media (prefers-reduced-motion:reduce) {{ * {{ transition:none!important; }} }}
  </style>
</head>
<body>
  <main>
    <header class="intro">
      <div><p class="eyebrow">Kaigo / проверенный результат</p><h1>AI на сайте — не макет.</h1><p class="lead">Основной интерактивный объект ниже — сам сгенерированный виджет. {escape(chat_status)}</p></div>
      <details><summary>Доказательства генерации</summary><div class="evidence"><dl>
        <div><dt>Модель</dt><dd>{model}</dd></div><div><dt>Движок</dt><dd>{escape(demo.request.engine.value)}</dd></div><div><dt>Стадия</dt><dd>{stage}</dd></div>
        <div><dt>Ревизия</dt><dd>{demo.artifact.revision}</dd></div><div><dt>Токены</dt><dd>{tokens}</dd></div><div><dt>Время</dt><dd>{elapsed}</dd></div>
      </dl><pre>{prompt}</pre><p class="direction">{art_direction}</p><p class="direction">{generated_at}</p></div></details>
    </header>
    <section class="stage" aria-label="Основной интерактивный объект">
      <header class="stage-head">
        <div><strong>Виджет на странице</strong><div class="status" id="chat-status">{escape(chat_status)}</div></div>
        <div class="toggle" aria-label="Размер предпросмотра">
          <button type="button" class="active" data-view="desktop">Компьютер</button>
          <button type="button" data-view="mobile">Телефон</button>
        </div>
      </header>
      <div class="canvas"><div class="viewport" id="viewport"><iframe id="demo-preview" sandbox="allow-scripts" referrerpolicy="no-referrer" title="Сгенерированный AI-виджет Kaigo" data-revision="{demo.artifact.revision}"></iframe></div></div>
    </section>
  </main>
  <script>
  (()=>{{
    'use strict';
    const frame=document.getElementById('demo-preview');
    const viewport=document.getElementById('viewport');
    const status=document.getElementById('chat-status');
    const revision=Number(frame.dataset.revision);
    const chatEnabled={chat_enabled};
    const demoBase=new URL(document.baseURI.endsWith('/')?document.baseURI:document.baseURI+'/');
    const channel=Array.from(crypto.getRandomValues(new Uint8Array(18)),value=>value.toString(16).padStart(2,'0')).join('');
    const requestPattern=/^[A-Za-z0-9][A-Za-z0-9._-]{{7,95}}$/;
    const pending=new Set();
    frame.src=new URL(`preview?channel=${{encodeURIComponent(channel)}}`,demoBase).toString();

    const sendToFrame=payload=>frame.contentWindow?.postMessage({{source:'kaigo-builder-parent',version:2,channel_id:channel,revision,...payload}},'*');
    async function bridgeChat(data){{
      if(!chatEnabled){{ sendToFrame({{type:'chat.error',request_id:data.request_id,code:'chat_not_ready',message:'Это старый визуальный preview. Перегенерируйте demo для реального чата.',retryable:false}}); return; }}
      if(pending.has(data.request_id)) return;
      pending.add(data.request_id);
      try{{
        const response=await fetch(new URL('chat',demoBase),{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json','X-Kaigo-Chat':'v2'}},body:JSON.stringify({{request_id:data.request_id,message:data.text,revision}})}});
        const payload=await response.json().catch(()=>({{}}));
        if(!response.ok) throw Object.assign(new Error(payload.error?.message||`HTTP ${{response.status}}`),{{payload}});
        if(payload.request_id!==data.request_id||typeof payload.reply!=='string'||payload.reply.length>4000) throw new Error('Некорректный ответ chat bridge');
        sendToFrame({{type:'chat.response',request_id:data.request_id,text:payload.reply}});
        status.textContent='Gemini ответил · запрос '+data.request_id.slice(0,8);
      }}catch(error){{
        const payload=error.payload||{{}};
        sendToFrame({{type:'chat.error',request_id:data.request_id,code:payload.error?.code||'chat_network_error',message:payload.error?.message||'Связь прервалась. Текст сохранён.',retryable:payload.error?.retryable!==false}});
      }}finally{{ pending.delete(data.request_id); }}
    }}
    window.addEventListener('message',event=>{{
      const data=event.data;
      if(event.source!==frame.contentWindow||!data||data.source!=='kaigo-builder-preview'||data.version!==2||data.channel_id!==channel||data.revision!==revision) return;
      if(data.type==='rendered'){{ status.textContent=chatEnabled?'Реальный Gemini-чат готов':'Только визуальный preview'; return; }}
      if(data.type!=='chat.request'||!requestPattern.test(data.request_id)||typeof data.text!=='string'||data.text.length<1||data.text.length>1000) return;
      bridgeChat(data);
    }});
    document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{{document.querySelectorAll('[data-view]').forEach(item=>item.classList.toggle('active',item===button));viewport.classList.toggle('mobile',button.dataset.view==='mobile');}}));
  }})();
  </script>
</body>
</html>"""


def render_demo_unavailable() -> str:
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kaigo — демо готовится</title><style>body{min-height:100vh;margin:0;display:grid;place-items:center;color:#f4efe5;background:#151512;font:16px/1.5 system-ui}main{max-width:560px;padding:36px;border:1px solid #3c3931;border-radius:24px;background:#211f1a}h1{margin:0 0 12px}p{margin:0;color:#aaa298}</style></head><body><main><h1>Демонстрация пока готовится</h1><p>Kaigo сохраняет проверенный результат реальной Gemini-генерации. Обновите страницу немного позже.</p></main></body></html>"""
