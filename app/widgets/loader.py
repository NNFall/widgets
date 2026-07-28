from __future__ import annotations

import base64
import html

from app.publication.service import PublishedRelease


def render_loader() -> str:
    return r"""(() => {
  'use strict';
  const script = document.currentScript;
  if (!script || !script.src) return;
  const source = new URL(script.src, document.baseURI);
  const match = source.pathname.match(/\/embed\/([^/]+)\.js$/);
  if (!match) return;
  const key = match[1];
  const duplicate = Array.from(document.querySelectorAll('[data-kaigo-widget-key]'))
    .some((node) => node.getAttribute('data-kaigo-widget-key') === key);
  if (duplicate) return;
  const iframe = document.createElement('iframe');
  iframe.setAttribute('data-kaigo-widget-key', key);
  iframe.setAttribute('sandbox', 'allow-scripts');
  iframe.setAttribute('title', 'Kaigo AI-консультант');
  iframe.referrerPolicy = 'strict-origin';
  iframe.src = new URL('/runtime/' + encodeURIComponent(key), source.origin).href;
  iframe.style.cssText = 'position:fixed;right:16px;bottom:16px;width:min(420px,calc(100vw - 32px));height:640px;max-height:calc(100vh - 32px);border:0;z-index:2147483000;background:transparent;';
  const resize = (event) => {
    if (event.source !== iframe.contentWindow || !event.data || typeof event.data !== 'object') return;
    if (event.data.type !== 'kaigo:resize' || event.data.key !== key) return;
    const requested = Number(event.data.height);
    if (!Number.isFinite(requested)) return;
    iframe.style.height = Math.max(240, Math.min(1200, Math.round(requested))) + 'px';
  };
  window.addEventListener('message', resize);
  const mount = () => {
    if (!iframe.isConnected) (document.body || document.documentElement).appendChild(iframe);
  };
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount, { once: true });
})();
"""


def render_runtime(release: PublishedRelease) -> str:
    artifact = release.manifest["artifact"]
    css_data = base64.b64encode(artifact["css"].encode("utf-8")).decode("ascii")
    javascript = artifact.get("javascript", "")
    script_element = ""
    if javascript:
        js_data = base64.b64encode(javascript.encode("utf-8")).decode("ascii")
        script_element = f'<script src="data:text/javascript;base64,{js_data}"></script>'
    key = html.escape(release.stable_key, quote=True)
    inner = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' data:; script-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; connect-src 'none'; media-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; navigate-to 'none'">
<link rel="stylesheet" href="data:text/css;base64,{css_data}">
<style>html,body{{margin:0;background:transparent;overflow:hidden}}</style></head>
<body>{artifact['body_html']}{script_element}
<script>(()=>{{const key={release.stable_key!r};const send=()=>parent.postMessage({{type:'kaigo:inner-resize',key,height:Math.ceil(document.documentElement.getBoundingClientRect().height)}},'*');new ResizeObserver(send).observe(document.documentElement);addEventListener('load',send);send();}})();</script>
<noscript>Для работы AI-виджета требуется JavaScript.</noscript></body></html>"""
    encoded_inner = base64.b64encode(inner.encode("utf-8")).decode("ascii")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{{margin:0;background:transparent;overflow:hidden}}iframe{{display:block;width:100%;height:640px;border:0;background:transparent}}</style></head>
<body data-kaigo-runtime="kaigo-widget"><iframe id="kaigo-generated-widget" title="Kaigo AI-консультант" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
<script>(()=>{{const key={release.stable_key!r};const frame=document.getElementById('kaigo-generated-widget');const bytes=Uint8Array.from(atob({encoded_inner!r}),c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([bytes],{{type:'text/html;charset=utf-8'}}));frame.src=url;addEventListener('message',(event)=>{{if(event.source!==frame.contentWindow||!event.data||typeof event.data!=='object'||event.data.type!=='kaigo:inner-resize'||event.data.key!==key)return;const requested=Number(event.data.height);if(!Number.isFinite(requested))return;const height=Math.max(240,Math.min(1200,Math.round(requested)));frame.style.height=height+'px';parent.postMessage({{type:'kaigo:resize',key,height}},'*');}});addEventListener('pagehide',()=>URL.revokeObjectURL(url),{{once:true}});}})();</script>
<span hidden data-kaigo-release="{key}"></span></body></html>"""


__all__ = ["render_loader", "render_runtime"]
