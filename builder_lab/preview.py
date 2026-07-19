from __future__ import annotations

import re
import json
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
_CHANNEL_ID = re.compile(r"^[A-Za-z0-9_-]{22,96}$")
DEFAULT_VISUAL_CHANNEL = "visual-only-preview-channel"


def preview_iframe_attributes() -> dict[str, str]:
    return {
        "sandbox": "allow-scripts",
        "referrerpolicy": "no-referrer",
        "title": "Предпросмотр AI-сотрудника Kaigo",
    }


def _safe_style(css: str) -> str:
    return re.sub(r"</\s*style", "<\\\\/style", css, flags=re.IGNORECASE)


def build_preview_document(
    artifact: WidgetArtifact, *, channel_id: str = DEFAULT_VISUAL_CHANNEL
) -> str:
    """Combine a validated artifact with the fixed, network-free preview runtime."""

    if not _CHANNEL_ID.fullmatch(channel_id):
        raise ValueError("preview channel_id is invalid")
    csp = escape(PREVIEW_CSP, quote=True)
    css = _safe_style(artifact.css)
    revision = int(artifact.revision)
    encoded_channel = json.dumps(channel_id)
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
    [data-region="root"] [data-kaigo-runtime-input] {{ resize: none; scrollbar-width: none; }}
    [data-region="root"] [data-kaigo-runtime-input]::-webkit-scrollbar,
    [data-region="root"] [data-region="messages"]::-webkit-scrollbar {{ display: none; width: 0; height: 0; }}
    [data-region="root"] [data-region="messages"] {{ scrollbar-width: none; }}
  </style>
</head>
<body>
{artifact.body_html}
<script>
(() => {{
  'use strict';
  const channelId = {encoded_channel};
  const revision = {revision};
  const root = document.querySelector('[data-region="root"]');
  const launcher = document.querySelector('[data-region="launcher"]');
  const panel = document.querySelector('[data-region="panel"]');
  const composer = document.querySelector('[data-region="composer"]');
  const messages = document.querySelector('[data-region="messages"]');
  let input = composer && composer.querySelector('input, textarea');
  const toggle = root && root.querySelector('.kaigo-toggle, [data-action="toggle"], input[type="checkbox"]');
  const requestPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{{7,95}}$/;
  let pending = null;

  if (input && input.tagName !== 'TEXTAREA') {{
    const replacement = document.createElement('textarea');
    replacement.className = input.className;
    replacement.placeholder = input.placeholder || '';
    replacement.setAttribute('aria-label', input.getAttribute('aria-label') || 'Введите сообщение');
    replacement.autocomplete = 'off';
    input.replaceWith(replacement);
    input = replacement;
  }}
  if (input) {{
    input.rows = 1;
    input.setAttribute('data-kaigo-runtime-input', 'true');
  }}
  if (messages) {{
    messages.setAttribute('role', 'log');
    messages.setAttribute('aria-live', 'polite');
    messages.setAttribute('aria-relevant', 'additions');
  }}

  const setOpen = (open, returnFocus = false) => {{
    if (!root) return;
    root.classList.toggle('kaigo-preview-open', Boolean(open));
    root.dataset.state = open ? 'open' : 'closed';
    if (toggle) toggle.checked = Boolean(open);
    if (launcher) launcher.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (panel) panel.setAttribute('aria-hidden', open ? 'false' : 'true');
    if (open && input && matchMedia('(hover: hover) and (pointer: fine)').matches) {{
      setTimeout(() => input.focus(), 0);
    }}
    if (!open && returnFocus && launcher && document.activeElement !== launcher) launcher.focus();
  }};

  if (launcher) launcher.addEventListener('click', (event) => {{
    if (toggle && event.target === toggle) return;
    event.preventDefault();
    const opening = !root || root.dataset.state !== 'open';
    setOpen(opening, !opening);
  }});

  if (toggle) toggle.addEventListener('change', () => setOpen(toggle.checked));

  const closeButton = document.querySelector('[data-action="close"]');
  if (closeButton) closeButton.addEventListener('click', (event) => {{
    event.preventDefault();
    setOpen(false, true);
  }});

  function appendMessage(role, text) {{
    if (!messages) return;
    const message = document.createElement('article');
    message.setAttribute('data-kaigo-runtime-message', role);
    const label = document.createElement('span');
    label.setAttribute('data-kaigo-runtime-label', role);
    label.textContent = role === 'user' ? 'ВЫ' : 'RAW AI';
    const content = document.createElement('p');
    content.setAttribute('data-kaigo-runtime-content', role);
    content.textContent = text;
    message.append(label, content);
    messages.append(message);
    messages.scrollTop = messages.scrollHeight;
  }}

  function clearStatus() {{
    const status = messages && messages.querySelector('[data-kaigo-runtime-status]');
    if (status) status.remove();
  }}

  function showStatus(kind, text, retryable = false) {{
    if (!messages) return;
    clearStatus();
    const status = document.createElement('div');
    status.setAttribute('data-kaigo-runtime-status', kind);
    status.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    const copy = document.createElement('span');
    copy.textContent = text;
    status.append(copy);
    if (retryable) {{
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.setAttribute('data-kaigo-runtime-retry', 'true');
      retry.textContent = 'ПОВТОРИТЬ';
      retry.addEventListener('click', () => postPending());
      status.append(retry);
    }}
    messages.append(status);
    messages.scrollTop = messages.scrollHeight;
  }}

  function setBusy(value) {{
    if (!composer) return;
    composer.setAttribute('aria-busy', value ? 'true' : 'false');
    const send = composer.querySelector('[data-action="send"], button');
    if (send) send.disabled = Boolean(value);
  }}

  function requestId() {{
    if (crypto.randomUUID) return crypto.randomUUID();
    return 'request-' + Array.from(crypto.getRandomValues(new Uint8Array(12)), value => value.toString(16).padStart(2, '0')).join('');
  }}

  function postPending() {{
    if (!pending) return;
    clearStatus();
    setBusy(true);
    showStatus('pending', 'Gemini готовит ответ');
    window.parent.postMessage({{
      source: 'kaigo-builder-preview',
      version: 2,
      channel_id: channelId,
      type: 'chat.request',
      request_id: pending.requestId,
      revision,
      text: pending.text
    }}, '*');
  }}

  function sendText(text) {{
    const normalized = String(text || '').trim();
    if (!normalized || normalized.length > 1000 || pending) return;
    pending = {{ requestId: requestId(), text: normalized }};
    appendMessage('user', normalized);
    postPending();
  }}

  const suggestions = Array.from(document.querySelectorAll('[data-suggestion], [data-region="suggestions"] button'));
  suggestions.slice(2).forEach(item => {{ item.hidden = true; item.disabled = true; }});
  suggestions.slice(0, 2).forEach(suggestion => suggestion.addEventListener('click', event => {{
    event.preventDefault();
    const text = suggestion.dataset.suggestion || suggestion.textContent.trim();
    sendText(text);
  }}));

  if (composer) composer.addEventListener('click', event => {{
    const button = event.target.closest('[data-action="send"], button');
    if (!button || button.hasAttribute('data-kaigo-runtime-retry') || !input) return;
    event.preventDefault();
    sendText(input.value);
  }});

  function autosize() {{
    if (!input) return;
    input.style.height = 'auto';
    const lineHeight = parseFloat(getComputedStyle(input).lineHeight) || 20;
    const maxHeight = lineHeight * 4 + 8;
    input.style.height = `${{Math.min(input.scrollHeight, maxHeight)}}px`;
    input.style.overflowY = input.scrollHeight > maxHeight ? 'auto' : 'hidden';
  }}
  if (input) {{
    input.addEventListener('input', autosize);
    input.addEventListener('keydown', event => {{
      if (event.isComposing) return;
      if (event.key === 'Enter' && !event.shiftKey) {{
        event.preventDefault();
        sendText(input.value);
      }}
    }});
    autosize();
  }}

  document.addEventListener('keydown', event => {{
    if (event.key === 'Escape' && root && root.dataset.state === 'open') {{
      event.preventDefault();
      setOpen(false, true);
      if (launcher) launcher.focus();
    }}
  }});

  window.addEventListener('message', (event) => {{
    const data = event.data;
    if (event.source !== window.parent || !data || data.source !== 'kaigo-builder-parent' || data.version !== 2 || data.channel_id !== channelId || data.revision !== revision) return;
    if (data.type === 'set-open') {{ setOpen(Boolean(data.open)); return; }}
    if (!pending || !requestPattern.test(data.request_id) || data.request_id !== pending.requestId) return;
    if (data.type === 'chat.response') {{
      if (typeof data.text !== 'string' || !data.text.trim() || data.text.length > 4000) return;
      clearStatus();
      appendMessage('assistant', data.text.trim());
      if (input.value === pending.text) input.value = '';
      pending = null;
      setBusy(false);
      autosize();
      return;
    }}
    if (data.type === 'chat.error') {{
      clearStatus();
      setBusy(false);
      showStatus('error', typeof data.message === 'string' ? data.message.slice(0, 320) : 'Связь прервалась. Текст сохранён.', data.retryable !== false);
    }}
  }});

  setOpen(false);
  window.parent.postMessage({{
    source: 'kaigo-builder-preview',
    version: 2,
    channel_id: channelId,
    type: 'rendered',
    revision
  }}, '*');
}})();
</script>
</body>
</html>"""
