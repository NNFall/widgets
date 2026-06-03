import asyncio
import json
import logging
import uuid

from aiohttp import web

from core import ai_service, api as core_api, database as db

logger = logging.getLogger(__name__)

COOKIE_NAME = "uid"
COOKIE_MAX_AGE = 24 * 60 * 60  # 24 hours
PROVIDER_ERROR_CODES = {
    "missing_api_key",
    "invalid_api_key",
    "account_inactive",
    "provider_timeout",
    "provider_connection_error",
    "provider_api_error",
    "provider_error",
    "empty_provider_response",
    "model_not_found",
    "rate_limited",
    "location_unsupported",
}


def _status_for_result(result: dict | None, default_error_status: int = 502) -> int:
    if not result:
        return default_error_status
    if result.get("type") != "error":
        return 200
    if result.get("code") in PROVIDER_ERROR_CODES:
        return int(result.get("status_code") or default_error_status)
    return 500


async def _get_or_set_uid(request) -> tuple[str, bool]:
    """Возвращает текущий UID пользователя и признак того, что он был создан заново."""
    uid = request.cookies.get(COOKIE_NAME)
    is_new = False
    if not uid:
        uid = uuid.uuid4().hex
        is_new = True
    await db.add_or_get_user(user_id=_uid_to_user_id(uid), username=f"web-{uid[:8]}")
    return uid, is_new


def _uid_to_user_id(uid: str) -> int:
    return abs(hash(uid)) % (2**31)


HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI Widget</title>
  <style>
    :root {
      --chat-primary: #2563eb;
      --chat-primary-hover: #1d4ed8;
      --chat-bg: #ffffff;
      --chat-surface: #f8fafc;
      --chat-accent: #10b981;
      --chat-warning: #f59e0b;
      --chat-shadow: 0 24px 60px rgba(15, 23, 42, 0.22);
      --chat-radius: 18px;
      --chat-gray-100: #f9fafb;
      --chat-gray-200: #e5e7eb;
      --chat-gray-300: #d1d5db;
      --chat-gray-400: #9ca3af;
      --chat-gray-500: #6b7280;
      --chat-gray-900: #111827;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: transparent;
      font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
      color: var(--chat-gray-900);
    }

    .chat-widget {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 2147483647;
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 12px;
      pointer-events: none;
    }

    .chat-widget * {
      pointer-events: auto;
    }

    .chat-toggle {
      width: 64px;
      height: 64px;
      border-radius: 50%;
      border: none;
      background: linear-gradient(135deg, var(--chat-primary), #14b8a6);
      color: #ffffff;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: var(--chat-shadow);
      cursor: pointer;
      transition: transform 0.3s ease, box-shadow 0.3s ease, filter 0.2s ease;
      position: relative;
      outline: none;
    }

    .chat-toggle:focus-visible {
      outline: 3px solid rgba(37, 99, 235, 0.35);
      outline-offset: 3px;
    }

    .chat-toggle:hover {
      filter: saturate(1.08);
      transform: translateY(-2px);
    }

    .chat-toggle__icon::before {
      content: "💬";
      font-size: 30px;
      line-height: 1;
      display: block;
      transition: transform 0.3s ease, opacity 0.3s ease;
    }

    .chat-widget--open .chat-toggle__icon::before {
      content: "×";
      font-size: 36px;
      transform: rotate(90deg);
    }

    .chat-toggle__hint {
      position: absolute;
      right: 72px;
      top: 50%;
      transform: translateY(-50%) translateX(10px);
      background: #ffffff;
      color: var(--chat-gray-900);
      padding: 8px 14px;
      border-radius: 999px;
      box-shadow: 0 12px 24px rgba(17, 24, 39, 0.18);
      opacity: 0;
      pointer-events: none;
      white-space: nowrap;
      font-size: 14px;
      font-weight: 500;
      transition: opacity 0.3s ease, transform 0.3s ease;
    }

    .chat-toggle__hint::after {
      content: '';
      position: absolute;
      right: -6px;
      top: 50%;
      transform: translateY(-50%);
      border-width: 6px 0 6px 6px;
      border-style: solid;
      border-color: transparent transparent transparent #ffffff;
    }

    .chat-widget--hint .chat-toggle__hint {
      opacity: 1;
      transform: translateY(-50%) translateX(0);
    }

    .chat-widget--hint .chat-toggle {
      box-shadow: 0 12px 32px rgba(37, 99, 235, 0.45);
    }

    .chat-panel {
      width: min(360px, calc(100vw - 32px));
      height: min(520px, calc(100vh - 120px));
      background: var(--chat-bg);
      border-radius: var(--chat-radius);
      border: 1px solid rgba(148, 163, 184, 0.28);
      box-shadow: var(--chat-shadow);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      transform: translateY(24px) scale(0.95);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.3s ease, transform 0.3s ease;
    }

    .chat-widget--open .chat-panel {
      opacity: 1;
      transform: translateY(0) scale(1);
      pointer-events: auto;
    }

    .chat-panel__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 20px;
      background: linear-gradient(135deg, #1f2937, var(--chat-primary));
      color: #ffffff;
    }

    .chat-panel__title {
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 4px;
    }

    .chat-panel__subtitle {
      font-size: 13px;
      opacity: 0.8;
    }

    .chat-panel__close {
      border: none;
      background: rgba(255, 255, 255, 0.15);
      color: #ffffff;
      width: 32px;
      height: 32px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: background 0.2s ease;
      font-size: 20px;
    }

    .chat-panel__close:hover {
      background: rgba(255, 255, 255, 0.3);
    }

    .chat-panel__log {
      flex: 1;
      background: var(--chat-surface);
      padding: 20px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .chat-message {
      display: flex;
    }

    .chat-message__bubble {
      max-width: 85%;
      padding: 12px 16px;
      border-radius: 16px;
      background: #ffffff;
      border: 1px solid rgba(229, 231, 235, 0.9);
      box-shadow: 0 10px 22px rgba(15, 23, 42, 0.07);
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .chat-message__label {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--chat-gray-500);
    }

    .chat-message__text {
      font-size: 14px;
      line-height: 1.5;
      color: var(--chat-gray-900);
      word-break: break-word;
    }

    .chat-message--user {
      justify-content: flex-end;
    }

    .chat-message--user .chat-message__bubble {
      background: var(--chat-primary);
      color: #ffffff;
      box-shadow: 0 8px 16px rgba(37, 99, 235, 0.25);
    }

    .chat-message--user .chat-message__label {
      color: rgba(255, 255, 255, 0.75);
    }

    .chat-message--user .chat-message__text {
      color: #ffffff;
    }

    .chat-message--system .chat-message__bubble {
      background: #fff7ed;
      border-color: #fed7aa;
    }

    .chat-message--system .chat-message__label,
    .chat-message--system .chat-message__text {
      color: #9a3412;
    }

    .chat-message--typing .chat-message__bubble {
      background: #ecfdf5;
      border-color: #bbf7d0;
    }

    .chat-message--typing .chat-message__text::after {
      content: '';
      display: inline-block;
      width: 6px;
      height: 6px;
      margin-left: 6px;
      border-radius: 50%;
      background: var(--chat-accent);
      animation: chatPulse 1s ease-in-out infinite;
      vertical-align: middle;
    }

    @keyframes chatPulse {
      0%, 100% { opacity: 0.25; transform: translateY(0); }
      50% { opacity: 1; transform: translateY(-2px); }
    }

    .chat-panel__controls {
      padding: 18px 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      background: #ffffff;
    }

    .chat-panel__input-row {
      display: flex;
      gap: 8px;
    }

    .chat-panel__input-row input {
      flex: 1;
      padding: 10px 14px;
      border: 1px solid var(--chat-gray-200);
      border-radius: 10px;
      font-size: 14px;
      transition: border 0.2s ease, box-shadow 0.2s ease;
    }

    .chat-panel__input-row input:focus {
      border-color: var(--chat-primary);
      box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.15);
      outline: none;
    }

    .chat-panel__send {
      padding: 0 18px;
      border: none;
      border-radius: 10px;
      background: var(--chat-primary);
      color: #ffffff;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s ease;
      min-width: 100px;
    }

    .chat-panel__send:hover,
    .chat-panel__voice:hover {
      background: var(--chat-primary-hover);
    }

    .chat-panel__send:disabled {
      background: var(--chat-gray-300);
      color: var(--chat-gray-500);
      cursor: not-allowed;
    }

    .chat-panel__voice {
      border: none;
      border-radius: 10px;
      padding: 12px 16px;
      font-size: 14px;
      font-weight: 600;
      background: var(--chat-primary);
      color: #ffffff;
      cursor: pointer;
      transition: background 0.2s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }

    .chat-panel__status {
      font-size: 13px;
      color: var(--chat-gray-500);
      min-height: 18px;
    }

    @media (max-width: 600px) {
      .chat-widget {
        right: 16px;
        bottom: 16px;
      }

      .chat-panel {
        width: calc(100vw - 24px);
        height: calc(100vh - 96px);
        border-radius: 16px;
      }

      .chat-toggle {
        width: 58px;
        height: 58px;
      }
    }
  </style>
</head>
<body>
  <div id="chat-widget" class="chat-widget">
    <button id="chat-toggle" class="chat-toggle" aria-expanded="false" aria-controls="chat-panel" aria-label="Открыть чат">
      <span class="chat-toggle__icon" aria-hidden="true"></span>
      <span class="chat-toggle__hint">Напишите мне</span>
    </button>
    <div id="chat-panel" class="chat-panel" aria-hidden="true">
      <div class="chat-panel__header">
        <div>
          <div class="chat-panel__title">AI ассистент</div>
          <div class="chat-panel__subtitle">Всегда готов помочь</div>
        </div>
        <button id="chat-close" class="chat-panel__close" aria-label="Закрыть чат">×</button>
      </div>
      <div id="log" class="chat-panel__log"></div>
      <div class="chat-panel__controls">
        <div class="chat-panel__input-row">
          <input id="msg" type="text" placeholder="Введите сообщение" autocomplete="off" />
          <button id="send" class="chat-panel__send">Отправить</button>
        </div>
        <button id="rec" class="chat-panel__voice">🎙 Записать голос</button>
        <div id="recStatus" class="chat-panel__status"></div>
      </div>
    </div>
  </div>
  <script>
    const chatWidget = document.getElementById('chat-widget');
    const chatPanel = document.getElementById('chat-panel');
    const chatToggle = document.getElementById('chat-toggle');
    const chatClose = document.getElementById('chat-close');
    const chatToggleHint = chatToggle.querySelector('.chat-toggle__hint');
    const logEl = document.getElementById('log');
    const msgEl = document.getElementById('msg');
    const sendBtn = document.getElementById('send');
    const recBtn = document.getElementById('rec');
    const recStatusEl = document.getElementById('recStatus');

    const roleLabels = { user: 'Вы', assistant: 'ИИ', system: 'Система', typing: 'ИИ' };

    const params = new URLSearchParams(window.location.search);
    const autoOpenDelay = Number(params.get('autoOpenDelay') || 0);
    const autoOpenSelector = params.get('autoOpenSelector');
    const autoOpenMode = (params.get('autoOpenMode') || 'hint').toLowerCase();
    const autoOpenOnce = params.get('autoOpenOnce') !== 'false';
    const hintText = params.get('hintText') || 'Напишите мне';

    chatToggleHint.textContent = hintText;

    let isOpen = false;
    let historyLoaded = false;
    let hintTimeoutId = null;
    let autoTriggered = false;

    function setOpenState(nextState, { focus = true } = {}) {
      if (nextState === isOpen) {
        return;
      }

      isOpen = nextState;
      chatWidget.classList.toggle('chat-widget--open', isOpen);
      chatPanel.setAttribute('aria-hidden', String(!isOpen));
      chatToggle.setAttribute('aria-expanded', String(isOpen));

      if (isOpen) {
        chatWidget.classList.remove('chat-widget--hint');
        ensureHistoryLoaded();
        if (focus) {
          setTimeout(() => msgEl.focus({ preventScroll: true }), 150);
        }
      } else {
        msgEl.blur();
      }
    }

    async function ensureHistoryLoaded() {
      if (historyLoaded) {
        return;
      }
      await loadHistory();
      historyLoaded = true;
    }

    function showHint() {
      if (isOpen) {
        return;
      }
      chatWidget.classList.add('chat-widget--hint');
      clearTimeout(hintTimeoutId);
      hintTimeoutId = window.setTimeout(() => {
        chatWidget.classList.remove('chat-widget--hint');
      }, 4000);
    }

    function runAutoAction() {
      if (autoOpenOnce && autoTriggered) {
        return;
      }
      autoTriggered = true;
      if (autoOpenMode === 'open') {
        setOpenState(true, { focus: false });
      } else {
        showHint();
      }
    }

    function appendMessage(role, text) {
      const row = document.createElement('div');
      row.className = `chat-message chat-message--${role}`;

      const bubble = document.createElement('div');
      bubble.className = 'chat-message__bubble';

      const label = document.createElement('span');
      label.className = 'chat-message__label';
      label.textContent = roleLabels[role] || role;

      const content = document.createElement('div');
      content.className = 'chat-message__text';
      content.textContent = text;

      bubble.appendChild(label);
      bubble.appendChild(content);
      row.appendChild(bubble);
      logEl.appendChild(row);
      logEl.scrollTop = logEl.scrollHeight;
      return row;
    }

    function removeMessage(row) {
      if (row && row.parentNode) {
        row.parentNode.removeChild(row);
      }
    }

    function providerErrorText(data) {
      if (!data || !data.code) {
        return (data && data.content) || 'Не удалось получить ответ.';
      }
      if (data.code === 'invalid_api_key' || data.code === 'missing_api_key') {
        return 'Gemini сейчас не настроен: добавьте ключ Google AI Studio.';
      }
      if (data.code === 'model_not_found') {
        return 'Gemini-модель недоступна. Проверьте модель в настройках виджета.';
      }
      if (data.code === 'rate_limited') {
        return 'Квота Google AI Studio временно ограничила запросы. Попробуйте позже.';
      }
      if (data.code === 'location_unsupported') {
        return 'Gemini API недоступен из региона этого сервера. Нужен поддерживаемый регион или Vertex AI.';
      }
      if (data.code === 'provider_timeout') {
        return 'Gemini не успел ответить. Попробуйте отправить сообщение еще раз.';
      }
      return data.content || 'Gemini временно недоступен.';
    }

    function setSending(isSending) {
      sendBtn.disabled = isSending;
      sendBtn.textContent = isSending ? 'Жду ответ' : 'Отправить';
      recBtn.disabled = isSending;
    }

    async function loadHistory() {
      try {
        const response = await fetch('/api/history');
        if (!response.ok) {
          return;
        }
        const data = await response.json();
        if (!data || !Array.isArray(data.messages)) {
          return;
        }
        data.messages.forEach(({ role, content }) => {
          appendMessage(role || 'system', content || '');
        });
      } catch (error) {
        console.error('Не удалось загрузить историю', error);
      }
    }

    async function sendText() {
      const message = msgEl.value.trim();
      if (!message) {
        return;
      }
      appendMessage('user', message);
      msgEl.value = '';
      setSending(true);
      recStatusEl.textContent = 'AI думает...';
      const typingRow = appendMessage('typing', 'Печатаю ответ');
      try {
        const response = await fetch('/api/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message })
        });
        const data = await response.json();
        removeMessage(typingRow);
        if (response.ok && data.type !== 'error') {
          if (data.type === 'tool_calls') {
            appendMessage('system', JSON.stringify(data.content));
          } else {
            appendMessage('assistant', data.content || '[пустой ответ]');
          }
        } else {
          appendMessage('system', providerErrorText(data));
        }
      } catch (error) {
        removeMessage(typingRow);
        appendMessage('system', String(error));
      } finally {
        setSending(false);
        recStatusEl.textContent = '';
        msgEl.focus({ preventScroll: true });
      }
    }

    async function startRecording() {
      if (window.mediaRecorder) {
        window.mediaRecorder.stop();
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const chunks = [];
        window.mediaRecorder = new MediaRecorder(stream);
        window.mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            chunks.push(event.data);
          }
        };
        window.mediaRecorder.onstop = async () => {
          stream.getTracks().forEach(track => track.stop());
          recStatusEl.textContent = '';
          recBtn.textContent = '🎙 Записать голос';
          const blob = new Blob(chunks, { type: 'audio/ogg' });
          if (!blob.size) {
            appendMessage('system', 'Запись не содержит данных.');
            window.mediaRecorder = null;
            return;
          }
          appendMessage('user', '[Голосовое сообщение]');
          recStatusEl.textContent = 'Обрабатываю голос...';
          try {
            const formData = new FormData();
            formData.append('audio', blob, 'recording.ogg');
            const response = await fetch('/api/audio', { method: 'POST', body: formData });
            const data = await response.json();
            if (response.ok && data.type !== 'error') {
              appendMessage('user', '[Транскрипция] ' + (data.transcription || '(пусто)'));
              appendMessage('assistant', data.ai_response || '[пустой ответ]');
            } else {
              appendMessage('system', providerErrorText(data));
            }
          } catch (error) {
            appendMessage('system', String(error));
          } finally {
            window.mediaRecorder = null;
            recStatusEl.textContent = '';
          }
        };
        window.mediaRecorder.start();
        recStatusEl.textContent = 'Запись...';
        recBtn.textContent = '■ Остановить запись';
      } catch (error) {
        appendMessage('system', 'Не удалось получить доступ к микрофону: ' + error);
        window.mediaRecorder = null;
      }
    }

    chatToggle.addEventListener('click', () => {
      setOpenState(!isOpen);
    });

    chatClose.addEventListener('click', () => setOpenState(false));

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && isOpen) {
        setOpenState(false, { focus: false });
        chatToggle.focus();
      }
    });

    sendBtn.addEventListener('click', sendText);

    msgEl.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendText();
      }
    });

    recBtn.addEventListener('click', startRecording);

    if (autoOpenDelay > 0) {
      setTimeout(runAutoAction, autoOpenDelay);
    }

    if (autoOpenSelector) {
      try {
        const selector = decodeURIComponent(autoOpenSelector);
        const target = document.querySelector(selector);
        if (target) {
          const observer = new IntersectionObserver((entries, obs) => {
            entries.forEach((entry) => {
              if (entry.isIntersecting) {
                runAutoAction();
                if (autoOpenOnce) {
                  obs.disconnect();
                }
              }
            });
          }, { threshold: 0.4 });
          observer.observe(target);
        }
      } catch (error) {
        console.warn('Не удалось настроить автооткрытие по селектору', error);
      }
    }

    window.addEventListener('message', (event) => {
      const data = event.data;
      if (!data || typeof data !== 'object') {
        return;
      }
      switch (data.widgetAction) {
        case 'open':
          setOpenState(true);
          break;
        case 'close':
          setOpenState(false);
          break;
        case 'hint':
          showHint();
          break;
        default:
          break;
      }
    });
  </script>
</body>
</html>"""


async def index(request):
    """Возвращает главную страницу виджета."""
    uid, is_new = await _get_or_set_uid(request)
    response = web.Response(text=HTML_PAGE, content_type="text/html")
    if is_new:
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response



async def api_send(request):
    """Обработка текстовых сообщений"""
    try:
        data = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("api_send: некорректный JSON: %s", exc)
        return web.json_response({"type": "error", "content": "Некорректный JSON."}, status=400)

    text_message = (data.get("message", "") or "").strip()
    if not text_message:
        return web.json_response({"type": "error", "content": "Пустое сообщение."}, status=400)

    uid, is_new = await _get_or_set_uid(request)
    user_id = _uid_to_user_id(uid)
    logger.info("api_send: uid=%s user_id=%s length=%s", uid[:8], user_id, len(text_message))

    try:
        result = await core_api.process_message(user_id=user_id, text=text_message)
    except Exception as exc:  # noqa: BLE001
        logger.exception("api_send: ошибка обработки сообщения")
        payload = {"type": "error", "content": "Ошибка обработки сообщения."}
        status_code = 500
    else:
        payload = result or {"type": "error", "content": "Нет ответа."}
        status_code = _status_for_result(result)

    response = web.json_response(payload, status=status_code)
    if is_new:
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response



async def api_history(request):
    """Возвращает историю сообщений пользователя."""
    try:
        uid, is_new = await _get_or_set_uid(request)
        user_id = _uid_to_user_id(uid)
        history = await db.get_history(user_id=user_id, limit=50)
    except Exception as exc:  # noqa: BLE001
        logger.exception("api_history: ошибка получения истории")
        return web.json_response({"type": "error", "content": "Не удалось получить историю."}, status=500)

    logger.info("api_history: uid=%s user_id=%s messages=%s", uid[:8], user_id, len(history))
    response = web.json_response({"type": "history", "messages": history})
    if is_new:
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response



async def api_audio(request):
    """Обработка голосовых сообщений."""
    reader = await request.multipart()
    audio_data = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "audio":
            audio_data = await part.read()
            break

    if not audio_data:
        return web.json_response({"type": "error", "content": "Аудио не найдено."}, status=400)

    uid, is_new = await _get_or_set_uid(request)
    user_id = _uid_to_user_id(uid)
    logger.info("api_audio: uid=%s user_id=%s bytes=%s", uid[:8], user_id, len(audio_data))

    try:
        transcription = await ai_service.transcribe_audio(audio_data, mime_type="audio/ogg")
    except ai_service.AIServiceError as exc:
        logger.error("api_audio: ошибка Gemini STT [%s]: %s", exc.code, exc.detail)
        response = web.json_response(
            {"type": "error", "code": exc.code, "content": exc.public_message, "status_code": exc.status_code},
            status=_status_for_result({"type": "error", "code": exc.code, "status_code": exc.status_code}),
        )
        if is_new:
            response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
        return response
    except Exception as exc:  # noqa: BLE001
        logger.exception("api_audio: ошибка распознавания аудио")
        response = web.json_response({"type": "error", "content": "Не удалось распознать аудио."}, status=502)
        if is_new:
            response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
        return response

    if not transcription:
        response = web.json_response(
            {"type": "error", "content": "Не удалось распознать речь."},
            status=502,
        )
        if is_new:
            response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
        return response

    try:
        ai_result = await core_api.process_message(user_id=user_id, text=transcription)
    except Exception as exc:  # noqa: BLE001
        logger.exception("api_audio: ошибка обработки транскрипции")
        payload = {"type": "error", "content": "Не удалось обработать транскрипцию."}
        status_code = 500
    else:
        if not ai_result or ai_result.get("type") == "error":
            content = (ai_result or {}).get("content", "Не удалось получить ответ от AI.")
            status_code = _status_for_result(ai_result)
            payload = {
                "type": "error",
                "code": (ai_result or {}).get("code"),
                "content": content,
                "status_code": (ai_result or {}).get("status_code"),
            }
        else:
            if ai_result.get("type") == "tool_calls":
                ai_text = json.dumps(ai_result.get("content", []), ensure_ascii=False)
            else:
                ai_text = ai_result.get("content", "")
            payload = {
                "type": "audio_response",
                "transcription": transcription,
                "ai_response": ai_text or "",
            }
            status_code = 200

    response = web.json_response(payload, status=status_code)
    if is_new:
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response

@web.middleware
async def cors_middleware(request, handler):
    """Простое CORS middleware."""
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def create_app():
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get('/', index)
    app.router.add_post('/api/send', api_send)
    app.router.add_get('/api/history', api_history)
    app.router.add_post('/api/audio', api_audio)
    return app


async def start():
    """Запускает aiohttp-сервер."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8002)
    await site.start()
    print("aiohttp сервер запущен на http://0.0.0.0:8002")

    try:
        await asyncio.Future()
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()
