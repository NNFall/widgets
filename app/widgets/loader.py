from __future__ import annotations

import base64
import hashlib
import json
from html import escape

from app.publication.service import PublishedRelease
from builder_lab.models import WidgetArtifact
from builder_lab.preview import build_trusted_runtime_document


def render_loader() -> str:
    return r"""(() => {
  'use strict';
  const script = document.currentScript;
  if (!script || !script.src) return;
  const source = new URL(script.src, document.baseURI);
  const match = source.pathname.match(/\/embed\/([^/]+)\.js$/);
  if (!match) return;
  const key = match[1];
  const registry = window.__kaigoWidgetRegistryV1 || (window.__kaigoWidgetRegistryV1 = new Set());
  if (registry.has(key)) return;
  registry.add(key);
  const iframe = document.createElement('iframe');
  iframe.setAttribute('data-kaigo-widget-key', key);
  iframe.setAttribute('sandbox', 'allow-scripts');
  iframe.setAttribute('title', 'Консультант Kaigo');
  iframe.referrerPolicy = 'strict-origin';
  iframe.src = new URL('/runtime/' + encodeURIComponent(key), source.origin).href;
  iframe.style.cssText = 'position:fixed;right:16px;bottom:16px;width:420px;height:640px;max-width:calc(100vw - 32px);max-height:calc(100vh - 32px);border:0;z-index:2147483000;background:transparent;pointer-events:none;overflow:hidden;';
  const geometry = (event) => {
    if (event.source !== iframe.contentWindow || !event.data || typeof event.data !== 'object') return;
    if (event.data.type !== 'kaigo:geometry' || event.data.key !== key) return;
    const requestedWidth = Number(event.data.width);
    const requestedHeight = Number(event.data.height);
    if (!Number.isFinite(requestedWidth) || !Number.isFinite(requestedHeight)) return;
    const maxWidth = Math.max(44, window.innerWidth - 32);
    const maxHeight = Math.max(44, window.innerHeight - 32);
    iframe.style.width = Math.max(44, Math.min(420, maxWidth, Math.ceil(requestedWidth))) + 'px';
    iframe.style.height = Math.max(44, Math.min(640, maxHeight, Math.ceil(requestedHeight))) + 'px';
    iframe.style.pointerEvents = 'auto';
  };
  window.addEventListener('message', geometry);
  const mount = () => {
    if (!iframe.isConnected) (document.body || document.documentElement).appendChild(iframe);
  };
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount, { once: true });
})();
"""


def _channel_id(stable_key: str) -> str:
    digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:40]
    return f"publication-{digest}"


def _inner_document(release: PublishedRelease) -> tuple[str, str]:
    artifact = WidgetArtifact.from_dict(release.manifest["artifact"])
    persona_payload = release.manifest.get("assistant_persona")
    assistant_label = (
        persona_payload.get("display_name")
        if isinstance(persona_payload, dict)
        and isinstance(persona_payload.get("display_name"), str)
        else None
    )
    channel = _channel_id(release.stable_key)
    document = build_trusted_runtime_document(
        artifact,
        channel_id=channel,
        assistant_label=assistant_label,
    )
    geometry_bridge = r"""
<script data-kaigo-public-geometry>
(() => {
  'use strict';
  const channelId = __CHANNEL__;
  const revision = __REVISION__;
  const root = document.querySelector('[data-region="root"]');
  const launcher = document.querySelector('[data-region="launcher"]');
  const panel = document.querySelector('[data-region="panel"]');
  let animationFrame = 0;
  function report() {
    const state = root && root.dataset.state === 'open' ? 'open' : 'closed';
    const target = state === 'open' ? panel : launcher;
    if (!target) return;
    const rect = target.getBoundingClientRect();
    const width = Math.max(rect.width, target.scrollWidth || 0);
    const height = Math.max(rect.height, target.scrollHeight || 0);
    if (!(width > 0) || !(height > 0)) return;
    window.parent.postMessage({
      source: 'kaigo-builder-preview',
      version: 2,
      channel_id: channelId,
      type: 'kaigo:inner-geometry',
      revision,
      state,
      width: Math.ceil(width),
      height: Math.ceil(height)
    }, '*');
  }
  function followMotion() {
    cancelAnimationFrame(animationFrame);
    let remaining = 45;
    const tick = () => {
      report();
      remaining -= 1;
      if (remaining > 0) animationFrame = requestAnimationFrame(tick);
    };
    animationFrame = requestAnimationFrame(tick);
  }
  if (root) new MutationObserver(followMotion).observe(root, {
    attributes: true,
    attributeFilter: ['class', 'data-state']
  });
  if (typeof ResizeObserver === 'function') {
    const observer = new ResizeObserver(report);
    if (launcher) observer.observe(launcher);
    if (panel) observer.observe(panel);
  }
  addEventListener('message', event => {
    const data = event.data;
    if (event.source !== window.parent || !data || data.type !== 'kaigo:measure' || data.channel_id !== channelId) return;
    followMotion();
  });
  addEventListener('load', followMotion);
  followMotion();
})();
</script>
""".replace("__CHANNEL__", json.dumps(channel)).replace(
        "__REVISION__", str(artifact.revision)
    )
    return document.replace("</body>", f"{geometry_bridge}</body>"), channel


def render_runtime(
    release: PublishedRelease,
    *,
    host_origin: str | None = None,
    chat_capability: str | None = None,
) -> str:
    inner, channel = _inner_document(release)
    persona_payload = release.manifest.get("assistant_persona")
    assistant_label = (
        persona_payload.get("display_name")
        if isinstance(persona_payload, dict)
        and isinstance(persona_payload.get("display_name"), str)
        else "Консультант Kaigo"
    )
    encoded_assistant_title = escape(assistant_label, quote=True)
    encoded_inner = base64.b64encode(inner.encode("utf-8")).decode("ascii")
    encoded_host_origin = json.dumps(host_origin)
    encoded_chat_capability = json.dumps(chat_capability)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{{margin:0;width:100%;height:100%;background:transparent;overflow:hidden}}iframe{{display:block;width:100%;height:100%;border:0;background:transparent}}</style></head>
<body data-kaigo-runtime="kaigo-widget"><iframe id="kaigo-generated-widget" title="{encoded_assistant_title}" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
<script>(()=>{{
  const key={release.stable_key!r};
  const channelId={channel!r};
  const revision={release.revision};
  const hostOrigin={encoded_host_origin};
  const chatCapability={encoded_chat_capability};
  const chatEndpoint=new URL('/runtime/'+encodeURIComponent(key)+'/chat',location.href).href;
  const sessionId='public-'+Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
  const frame=document.getElementById('kaigo-generated-widget');
  const bytes=Uint8Array.from(atob({encoded_inner!r}),c=>c.charCodeAt(0));
  frame.srcdoc=new TextDecoder().decode(bytes);
  let state='loading';
  addEventListener('message',event=>{{
    const data=event.data;
    if(event.source!==frame.contentWindow||!data||typeof data!=='object'||data.source!=='kaigo-builder-preview'||data.version!==2||data.channel_id!==channelId||data.revision!==revision)return;
    if(data.type==='chat.request'){{
      if(!hostOrigin||!chatCapability){{
        frame.contentWindow.postMessage({{source:'kaigo-builder-parent',version:2,channel_id:channelId,type:'chat.error',request_id:data.request_id,revision,message:'Чат недоступен на этом домене',retryable:false}},'*');
        return;
      }}
      fetch(chatEndpoint,{{
        method:'POST',
        mode:'cors',
        headers:{{'Content-Type':'text/plain;charset=UTF-8'}},
        body:JSON.stringify({{request_id:data.request_id,message:data.text,revision,session_id:sessionId,host_origin:hostOrigin,capability:chatCapability}})
      }}).then(async response=>{{
        let payload={{}};
        try{{payload=await response.json();}}catch(_error){{}}
        if(!response.ok){{
          const failure=payload&&payload.error||{{}};
          throw Object.assign(new Error(failure.message||'Связь прервалась'),{{retryable:failure.retryable!==false}});
        }}
        frame.contentWindow.postMessage({{source:'kaigo-builder-parent',version:2,channel_id:channelId,type:'chat.response',request_id:data.request_id,revision,text:payload.reply}},'*');
      }}).catch(error=>{{
        frame.contentWindow.postMessage({{source:'kaigo-builder-parent',version:2,channel_id:channelId,type:'chat.error',request_id:data.request_id,revision,message:String(error.message||'Связь прервалась').slice(0,320),retryable:error.retryable!==false}},'*');
      }});
      return;
    }}
    if(data.type!=='kaigo:inner-geometry')return;
    const width=Number(data.width);
    const height=Number(data.height);
    if(!Number.isFinite(width)||!Number.isFinite(height))return;
    const nextState=data.state==='open'?'open':'closed';
    if(nextState==='open'&&state!=='open'){{
      parent.postMessage({{type:'kaigo:geometry',key,state:'open',width:420,height:640}},'*');
      requestAnimationFrame(()=>frame.contentWindow.postMessage({{type:'kaigo:measure',channel_id:channelId}},'*'));
    }}
    state=nextState;
    parent.postMessage({{
      type:'kaigo:geometry',key,state,
      width:Math.max(1,Math.min(420,Math.ceil(width))),
      height:Math.max(1,Math.min(640,Math.ceil(height)))
    }},'*');
  }});
}})();</script>
<span hidden data-kaigo-release="{release.stable_key}"
 data-kaigo-release-id="{release.release_id}"
 data-kaigo-project-version-id="{release.project_version_id or ''}"></span></body></html>"""


__all__ = ["render_loader", "render_runtime"]
