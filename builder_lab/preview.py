from __future__ import annotations

from html import escape

from .models import WidgetArtifact


PREVIEW_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "img-src data:; "
    "font-src 'none'; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)


def preview_iframe_attributes() -> dict[str, str]:
    return {
        "sandbox": "allow-scripts",
        "referrerpolicy": "no-referrer",
        "title": "Предпросмотр AI-сотрудника Kaigo",
    }


def _safe_style(css: str) -> str:
    return css.replace("</style", "<\\/style").replace("</STYLE", "<\\/STYLE")


def build_preview_document(artifact: WidgetArtifact) -> str:
    """Combine a validated artifact with the fixed, network-free preview runtime."""

    csp = escape(PREVIEW_CSP, quote=True)
    css = _safe_style(artifact.css)
    revision = int(artifact.revision)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{csp}">
  <title>Kaigo Builder Preview</title>
  <style>
    html, body {{ margin: 0; min-height: 100%; background: transparent; }}
    body {{ min-height: 100vh; overflow: hidden; font-family: Inter, Arial, sans-serif; }}
    {css}
  </style>
</head>
<body>
{artifact.body_html}
<script>
(() => {{
  'use strict';
  const root = document.querySelector('[data-region="root"]');
  const launcher = document.querySelector('[data-region="launcher"]');
  const panel = document.querySelector('[data-region="panel"]');
  const composer = document.querySelector('[data-region="composer"]');
  const input = composer && composer.querySelector('input, textarea');

  const setOpen = (open) => {{
    if (!root) return;
    root.classList.toggle('kaigo-preview-open', Boolean(open));
    root.dataset.state = open ? 'open' : 'closed';
    if (launcher) launcher.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (panel) panel.setAttribute('aria-hidden', open ? 'false' : 'true');
  }};

  if (launcher) launcher.addEventListener('click', () => {{
    setOpen(!root || root.dataset.state !== 'open');
  }});

  document.addEventListener('click', (event) => {{
    const suggestion = event.target.closest('[data-suggestion], [data-region="suggestions"] button');
    if (!suggestion || !input) return;
    input.value = suggestion.dataset.suggestion || suggestion.textContent.trim();
    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
    input.focus();
  }});

  if (composer) composer.addEventListener('click', (event) => {{
    const button = event.target.closest('button');
    if (!button || !input || !input.value.trim()) return;
    input.value = '';
    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
  }});

  window.addEventListener('message', (event) => {{
    if (event.source !== window.parent || !event.data || event.data.source !== 'kaigo-builder-parent' || event.data.version !== 1) return;
    if (event.data.type === 'set-open') setOpen(Boolean(event.data.open));
  }});

  setOpen(true);
  window.parent.postMessage({{
    source: 'kaigo-builder-preview',
    version: 1,
    type: 'rendered',
    revision: {revision}
  }}, '*');
}})();
</script>
</body>
</html>"""
