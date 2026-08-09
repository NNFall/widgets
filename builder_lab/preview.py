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
TOP_LEVEL_VISUAL_ONLY_MESSAGE = (
    "\u042d\u0442\u043e \u0432\u0438\u0437\u0443\u0430\u043b\u044c\u043d\u044b\u0439 \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440. "
    "\u0414\u0438\u0430\u043b\u043e\u0433 \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u0432 \u0441\u0442\u0443\u0434\u0438\u0438."
)


def preview_iframe_attributes() -> dict[str, str]:
    return {
        "sandbox": "allow-scripts",
        "referrerpolicy": "no-referrer",
        "title": "Предпросмотр консультанта Kaigo",
    }


def _safe_style(css: str) -> str:
    return re.sub(r"</\s*style", "<\\\\/style", css, flags=re.IGNORECASE)


def _safe_script(javascript: str) -> str:
    return re.sub(
        r"</\s*script",
        lambda _match: "<\\/script",
        javascript,
        flags=re.IGNORECASE,
    )


def _build_runtime_document(
    artifact: WidgetArtifact,
    *,
    channel_id: str,
    include_generated_javascript: bool,
    assistant_label: str | None = None,
) -> str:
    if not _CHANNEL_ID.fullmatch(channel_id):
        raise ValueError("preview channel_id is invalid")
    csp = escape(PREVIEW_CSP, quote=True)
    css = _safe_style(artifact.css)
    generated_javascript = _safe_script(artifact.javascript)
    generated_script = ""
    if include_generated_javascript and generated_javascript.strip():
        generated_script = f"""
<script data-kaigo-generated>
try {{
{generated_javascript}
}} catch (error) {{
  console.error('kaigo-generated-javascript', error);
}}
</script>"""
    revision = int(artifact.revision)
    encoded_channel = json.dumps(channel_id)
    if assistant_label is not None:
        if not isinstance(assistant_label, str):
            raise TypeError("assistant_label must be a string or null")
        assistant_label = assistant_label.strip()
        if not assistant_label or len(assistant_label) > 80 or "\x00" in assistant_label:
            raise ValueError("assistant_label is invalid")
    encoded_assistant_label = _safe_script(
        json.dumps(assistant_label, ensure_ascii=False)
    )
    encoded_visual_only_message = json.dumps(TOP_LEVEL_VISUAL_ONLY_MESSAGE)
    trusted_runtime = "true" if not include_generated_javascript else "false"
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
    [data-region="root"] [hidden] {{ display: none !important; }}
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
  const trustedRuntime = {trusted_runtime};
  const topLevelVisualOnlyMessage = {encoded_visual_only_message};
  const root = document.querySelector('[data-region="root"]');
  const trustedAssistantLabel = {encoded_assistant_label};
  const generatedAssistantLabel = root
    ? String(root.dataset.assistantLabel || root.getAttribute('aria-label') || '').trim()
    : '';
  const genericAiLabel = /(?:^|[^A-Za-zА-Яа-яЁё])(?:AI|ИИ)(?:$|[^A-Za-zА-Яа-яЁё])/iu;
  const assistantLabel = String(
    trustedAssistantLabel
    || (genericAiLabel.test(generatedAssistantLabel) ? '' : generatedAssistantLabel)
    || 'КОНСУЛЬТАНТ'
  ).trim().slice(0, 80) || 'КОНСУЛЬТАНТ';
  if (root) {{
    root.dataset.assistantLabel = assistantLabel;
    root.setAttribute('aria-label', assistantLabel);
    const assistantLabelNodes = root.querySelectorAll(
      '[data-assistant-name], [data-role="assistant-name"], [data-assistant-label]:not([data-region="root"]), [data-kaigo-assistant-label], [data-region="header"] h1, [data-region="header"] h2, [data-region="header"] h3, [data-region="header"] strong, header h1, header h2, header h3, header strong, .kaigo-widget__message--assistant .kaigo-widget__message-label, [data-message-role="assistant"] [data-message-label], [data-role="assistant-message"] [data-role="message-label"]'
    );
    assistantLabelNodes.forEach((node) => {{ node.textContent = assistantLabel; }});
  }}
  const launcher = document.querySelector('[data-region="launcher"]');
  const panel = document.querySelector('[data-region="panel"]');
  const composer = document.querySelector('[data-region="composer"]');
  const messages = document.querySelector('[data-region="messages"]');
  const suggestionsRegion = document.querySelector('[data-region="suggestions"]');
  if (panel) panel.setAttribute('aria-label', `Диалог с ${{assistantLabel}}`);
  if (launcher) launcher.setAttribute('aria-label', `Открыть диалог с ${{assistantLabel}}`);
  if (launcher && genericAiLabel.test(String(launcher.textContent || '').trim())) {{
    launcher.textContent = Array.from(assistantLabel)[0] || 'К';
  }}
  if (root) {{
    root.querySelectorAll('[data-action="close"], [aria-label^="Закрыть"]').forEach((node) => {{
      node.setAttribute('aria-label', `Закрыть диалог с ${{assistantLabel}}`);
    }});
    root.querySelectorAll('[aria-label]').forEach((node) => {{
      const value = String(node.getAttribute('aria-label') || '');
      if (genericAiLabel.test(value)) {{
        node.setAttribute(
          'aria-label',
          value.replace(
            /(?:^|[^A-Za-zА-Яа-яЁё])(?:AI|ИИ)(?=$|[^A-Za-zА-Яа-яЁё])/giu,
            (match) => `${{match.slice(0, Math.max(0, match.length - 2))}}${{assistantLabel}}`
          )
        );
      }}
    }});
  }}
  const suggestions = suggestionsRegion
    ? Array.from(suggestionsRegion.querySelectorAll('[data-suggestion], button'))
    : [];
  let input = composer && composer.querySelector('input, textarea');
  const toggle = root && root.querySelector('.kaigo-toggle, [data-action="toggle"], input[type="checkbox"]');
  const requestPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{{7,95}}$/;
  const ATTENTION_DELAY_MS = 15000;
  const reducedMotionQuery = matchMedia('(prefers-reduced-motion: reduce)');
  let pendingRequest = null;
  let failedRequest = null;
  let attentionTimer = null;
  let attentionCancelled = false;

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
  if (suggestionsRegion) {{
    suggestionsRegion.hidden = suggestions.length === 0;
    suggestionsRegion.setAttribute('aria-hidden', suggestions.length === 0 ? 'true' : 'false');
  }}

  function cancelAttention() {{
    if (attentionCancelled) return;
    attentionCancelled = true;
    if (attentionTimer !== null) clearTimeout(attentionTimer);
    attentionTimer = null;
    if (root) root.classList.remove('kaigo-preview-attention');
  }}

  function scheduleAttention() {{
    if (
      !root
      || attentionCancelled
      || attentionTimer !== null
      || document.visibilityState !== 'visible'
      || root.dataset.state !== 'closed'
      || root.dataset.chatStarted === 'true'
      || reducedMotionQuery.matches
    ) return;
    attentionTimer = setTimeout(() => {{
      attentionTimer = null;
      if (
        attentionCancelled
        || document.visibilityState !== 'visible'
        || root.dataset.state !== 'closed'
        || root.dataset.chatStarted === 'true'
        || reducedMotionQuery.matches
      ) return;
      root.classList.add('kaigo-preview-attention');
    }}, ATTENTION_DELAY_MS);
  }}

  const setOpen = (open, returnFocus = false) => {{
    if (!root) return;
    if (open) cancelAttention();
    root.classList.toggle('kaigo-preview-open', Boolean(open));
    root.dataset.state = open ? 'open' : 'closed';
    if (panel) panel.toggleAttribute('data-open', Boolean(open));
    if (toggle) toggle.checked = Boolean(open);
    if (launcher) {{
      launcher.hidden = Boolean(open);
      launcher.setAttribute('aria-hidden', open ? 'true' : 'false');
      launcher.setAttribute('aria-expanded', open ? 'true' : 'false');
    }}
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
    message.className = `kaigo-widget__message kaigo-widget__message--${{role}}`;
    message.setAttribute('data-kaigo-runtime-message', role);
    const label = document.createElement('span');
    label.className = 'kaigo-widget__message-label';
    label.setAttribute('data-kaigo-runtime-label', role);
    label.textContent = role === 'user' ? 'ВЫ' : assistantLabel;
    const content = document.createElement('p');
    content.className = 'kaigo-widget__message-content';
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
    status.className = `kaigo-widget__message-status kaigo-widget__message-status--${{kind}}`;
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
      retry.addEventListener('click', retryFailedRequest);
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

  function clearComposerIfMatching(text) {{
    if (!input || input.value !== text) return;
    input.value = '';
    autosize();
  }}

  function restoreComposerIfEmpty(text) {{
    if (!input || input.value !== '') return;
    input.value = text;
    autosize();
  }}

  function postPendingRequest() {{
    if (!pendingRequest) return;
    if (trustedRuntime && window.parent === window) {{
      const failedComposerValue = pendingRequest.composerValue;
      pendingRequest = null;
      failedRequest = null;
      clearStatus();
      setBusy(false);
      restoreComposerIfEmpty(failedComposerValue);
      showStatus('error', topLevelVisualOnlyMessage, false);
      return;
    }}
    clearStatus();
    setBusy(true);
    showStatus('pending', `${{assistantLabel}} готовит ответ`);
    window.parent.postMessage({{
      source: 'kaigo-builder-preview',
      version: 2,
      channel_id: channelId,
      type: 'chat.request',
      request_id: pendingRequest.requestId,
      revision,
      text: pendingRequest.text
    }}, '*');
  }}

  function retryFailedRequest() {{
    if (!failedRequest || pendingRequest) return;
    pendingRequest = failedRequest;
    failedRequest = null;
    clearComposerIfMatching(pendingRequest.composerValue);
    postPendingRequest();
  }}

  function sendText(text) {{
    const composerValue = String(text || '');
    const normalized = composerValue.trim();
    if (!normalized || normalized.length > 1000 || pendingRequest) return;
    cancelAttention();
    failedRequest = null;
    clearStatus();
    if (root) root.dataset.chatStarted = 'true';
    if (suggestionsRegion) {{
      suggestionsRegion.hidden = true;
      suggestionsRegion.setAttribute('aria-hidden', 'true');
    }}
    suggestions.forEach(item => {{ item.disabled = true; }});
    pendingRequest = {{ requestId: requestId(), text: normalized, composerValue }};
    appendMessage('user', normalized);
    clearComposerIfMatching(pendingRequest.composerValue);
    postPendingRequest();
  }}

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
    const minHeight = Math.max(44, lineHeight + 8);
    const maxHeight = lineHeight * 4 + 8;
    input.style.height = `${{Math.max(minHeight, Math.min(input.scrollHeight, maxHeight))}}px`;
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
    if (!pendingRequest || !requestPattern.test(data.request_id) || data.request_id !== pendingRequest.requestId) return;
    if (data.type === 'chat.response') {{
      if (typeof data.text !== 'string' || !data.text.trim() || data.text.length > 4000) return;
      clearStatus();
      appendMessage('assistant', data.text.trim());
      pendingRequest = null;
      setBusy(false);
      autosize();
      return;
    }}
    if (data.type === 'chat.error') {{
      failedRequest = pendingRequest;
      pendingRequest = null;
      restoreComposerIfEmpty(failedRequest.composerValue);
      clearStatus();
      setBusy(false);
      showStatus('error', typeof data.message === 'string' ? data.message.slice(0, 320) : 'Связь прервалась. Текст сохранён.', data.retryable !== false);
    }}
  }});

  document.addEventListener('pointerdown', cancelAttention, {{capture: true, once: true}});
  document.addEventListener('keydown', cancelAttention, {{capture: true, once: true}});
  document.addEventListener('visibilitychange', cancelAttention, {{once: true}});
  reducedMotionQuery.addEventListener('change', event => {{
    if (event.matches) cancelAttention();
  }});
  setOpen(false);
  scheduleAttention();
  window.parent.postMessage({{
    source: 'kaigo-builder-preview',
    version: 2,
    channel_id: channelId,
    type: 'rendered',
    revision
  }}, '*');
}})();
</script>
{generated_script}
</body>
</html>"""


def build_preview_document(
    artifact: WidgetArtifact, *, channel_id: str = DEFAULT_VISUAL_CHANNEL
) -> str:
    """Build the legacy Builder Lab preview, including generated JavaScript."""

    return _build_runtime_document(
        artifact,
        channel_id=channel_id,
        include_generated_javascript=True,
    )


def build_trusted_runtime_document(
    artifact: WidgetArtifact,
    *,
    channel_id: str = DEFAULT_VISUAL_CHANNEL,
    assistant_label: str | None = None,
) -> str:
    """Build a trusted runtime that never embeds or executes artifact JavaScript."""

    return _build_runtime_document(
        artifact,
        channel_id=channel_id,
        include_generated_javascript=False,
        assistant_label=assistant_label,
    )
