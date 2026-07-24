from __future__ import annotations

from html import escape

from .models import EngineName
from .preview import preview_iframe_attributes


DEFAULT_BRIEF = ""
BUILDER_UI_RELEASE = "2026.07.24.2"


def render_builder_page(
    enabled_engines: tuple[EngineName, ...],
    *,
    default_engine: EngineName = EngineName.DIRECT,
    default_temperature: float = 0.9,
    default_max_repairs: int = 3,
) -> str:
    if default_engine not in enabled_engines:
        default_engine = enabled_engines[0]
    options = "".join(
        f'<option value="{escape(engine.value)}"'
        + (" selected" if engine is default_engine else "")
        + ">"
        + ("Gemini staged" if engine is EngineName.DIRECT else "Antigravity agent")
        + "</option>"
        for engine in enabled_engines
    )
    temperature = f"{default_temperature:.2f}".rstrip("0").rstrip(".")
    iframe = preview_iframe_attributes()
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Kaigo Builder Lab</title>
  <style>
    :root {{ color-scheme: dark; --bg:#171714; --surface:#211f1b; --line:#3b3832; --muted:#a49f95; --text:#f4f0e8; --accent:#d88360; --accent-dark:#9c5035; --danger:#d36e67; }}
    * {{ box-sizing:border-box; }}
    html {{ background:var(--bg); }}
    body {{ margin:0; min-width:320px; min-height:100dvh; color:var(--text); background:radial-gradient(circle at 77% 12%,rgba(216,131,96,.08),transparent 34%),var(--bg); font-family:Geist,"Segoe UI",Arial,sans-serif; }}
    button,input,select,textarea {{ font:inherit; }}
    button {{ color:inherit; }}
    .shell {{ width:min(1680px,100%); min-height:100dvh; margin:0 auto; padding:18px; display:grid; grid-template-columns:minmax(330px,.78fr) minmax(520px,1.45fr); gap:18px; }}
    .rail,.stage {{ min-width:0; border:1px solid var(--line); background:rgba(33,31,27,.91); box-shadow:inset 0 1px rgba(255,255,255,.035),0 24px 60px rgba(9,8,6,.22); }}
    .rail {{ border-radius:26px; padding:24px; display:flex; flex-direction:column; gap:18px; overflow:auto; }}
    .stage {{ border-radius:34px; padding:20px; display:grid; grid-template-rows:auto auto minmax(520px,1fr); gap:14px; overflow:hidden; }}
    .eyebrow {{ margin:0 0 10px; color:var(--accent); font:600 11px/1.2 "Cascadia Mono",monospace; letter-spacing:.15em; text-transform:uppercase; }}
    h1 {{ margin:0; max-width:15ch; font-size:clamp(28px,2.7vw,46px); line-height:1; letter-spacing:-.045em; font-weight:650; }}
    .lede {{ margin:15px 0 0; max-width:44ch; color:var(--muted); font-size:14px; line-height:1.55; }}
    .control-grid {{ display:grid; grid-template-columns:1fr 112px; gap:12px; }}
    .field {{ display:grid; gap:8px; }}
    .field-wide {{ grid-column:1/-1; }}
    label {{ color:#d9d4ca; font-size:12px; font-weight:600; }}
    select,input,textarea {{ width:100%; border:1px solid var(--line); border-radius:13px; color:var(--text); background:#191814; outline:none; transition:border-color .22s ease,transform .22s ease; }}
    select,input {{ min-height:42px; padding:0 12px; }}
    textarea {{ min-height:88px; resize:vertical; padding:13px; line-height:1.5; }}
    select:focus,input:focus,textarea:focus {{ border-color:var(--accent); }}
    .helper {{ margin:0; color:#837f77; font-size:11px; line-height:1.45; }}
    .actions {{ display:grid; grid-template-columns:1fr auto auto; gap:9px; }}
    details {{ grid-column:1/-1; border-top:1px solid var(--line); padding-top:10px; }}
    summary {{ color:#9b968d; cursor:pointer; font-size:11px; }}
    .advanced-controls {{ margin-top:12px; display:grid; grid-template-columns:1fr 112px; gap:12px; }}
    .button {{ min-height:43px; border:1px solid var(--line); border-radius:13px; padding:0 15px; background:#292721; cursor:pointer; transition:transform .2s cubic-bezier(.16,1,.3,1),background .2s ease,border-color .2s ease; }}
    .button:hover {{ border-color:#625e55; background:#302e28; }}
    .button:active {{ transform:translateY(1px) scale(.985); }}
    .button-primary {{ color:#211712; border-color:transparent; background:var(--accent); font-weight:700; }}
    .button-primary:hover {{ border-color:transparent; background:#e09472; }}
    .button:disabled {{ opacity:.45; cursor:not-allowed; transform:none; }}
    .error {{ display:none; padding:11px 13px; border-left:2px solid var(--danger); color:#e8b4af; background:rgba(211,110,103,.08); font-size:12px; line-height:1.45; }}
    .error.visible {{ display:block; }}
    .telemetry {{ display:grid; grid-template-columns:repeat(3,1fr); border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
    .metric {{ padding:13px 10px 13px 0; }}
    .metric+.metric {{ padding-left:12px; border-left:1px solid var(--line); }}
    .metric-label {{ display:block; color:#837f77; font:500 9px/1.3 "Cascadia Mono",monospace; letter-spacing:.11em; text-transform:uppercase; }}
    .metric-value {{ display:block; margin-top:5px; font:600 14px/1.2 "Cascadia Mono",monospace; }}
    .timeline-head {{ display:flex; align-items:center; justify-content:space-between; gap:16px; }}
    .timeline-head h2 {{ margin:0; font-size:13px; letter-spacing:-.01em; }}
    .pulse {{ width:7px; height:7px; border-radius:50%; background:#67635b; }}
    .pulse.running {{ background:var(--accent); animation:pulse 1.8s ease-in-out 6; }}
    @keyframes pulse {{ 50% {{ opacity:.35; transform:scale(.82); }} }}
    .timeline {{ min-height:150px; max-height:320px; overflow:auto; display:grid; align-content:start; gap:9px; padding:12px; border:1px solid var(--line); border-radius:14px; background:#191814; }}
    .empty {{ padding:28px 0; color:#77736c; font-size:12px; line-height:1.55; }}
    .event {{ position:relative; width:min(92%,360px); padding:10px 12px; border:1px solid rgba(59,56,50,.9); border-radius:13px 13px 13px 4px; background:#24221e; animation:arrive .36s cubic-bezier(.16,1,.3,1) both; }}
    .event.user {{ justify-self:end; border-color:rgba(216,131,96,.45); border-radius:13px 13px 4px 13px; background:rgba(216,131,96,.13); }}
    .event::before {{ content:""; position:absolute; left:-4px; top:15px; width:6px; height:6px; border:1px solid #77736c; border-radius:50%; background:var(--surface); }}
    .event.user::before {{ display:none; }}
    .event[data-status="completed"]::before {{ border-color:var(--accent); background:var(--accent); }}
    .event[data-status="failed"]::before {{ border-color:var(--danger); background:var(--danger); }}
    .event-meta {{ display:flex; justify-content:space-between; gap:12px; color:#8c877e; font:500 9px/1.3 "Cascadia Mono",monospace; letter-spacing:.06em; text-transform:uppercase; }}
    .event-message {{ margin-top:4px; color:#d6d1c7; font-size:12px; line-height:1.4; }}
    .event-changes {{ margin-top:5px; color:#9c978e; font:500 9px/1.45 "Cascadia Mono",monospace; letter-spacing:.035em; }}
    .refinement-box {{ display:grid; grid-template-columns:1fr auto; gap:8px; margin-top:10px; }}
    .refinement-box textarea {{ min-height:48px; max-height:120px; resize:vertical; }}
    @keyframes arrive {{ from {{ opacity:0; transform:translateY(7px); }} to {{ opacity:1; transform:translateY(0); }} }}
    .stage-head {{ display:grid; grid-template-columns:1fr auto; gap:18px; align-items:center; padding:3px 4px 1px; }}
    .stage-title {{ display:flex; align-items:center; gap:12px; min-width:0; }}
    .stage-title h2 {{ margin:0; font-size:14px; white-space:nowrap; }}
    .status {{ max-width:34ch; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); font:500 10px/1.2 "Cascadia Mono",monospace; }}
    .view-toggle {{ display:flex; padding:3px; border:1px solid var(--line); border-radius:12px; background:#191814; }}
    .view-toggle button {{ min-height:32px; border:0; border-radius:8px; padding:0 12px; color:#8d887f; background:transparent; cursor:pointer; font-size:11px; }}
    .view-toggle button.active {{ color:var(--text); background:#302e28; }}
    .artifact-meta {{ min-width:0; display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:18px; padding:11px 13px; border:1px solid var(--line); border-radius:14px; background:#191814; }}
    .artifact-label {{ display:block; color:#77736c; font:600 9px/1.2 "Cascadia Mono",monospace; letter-spacing:.11em; text-transform:uppercase; }}
    .art-direction {{ margin:4px 0 0; overflow:hidden; color:#cfc9bf; font-size:11px; line-height:1.35; text-overflow:ellipsis; white-space:nowrap; }}
    .validation-badge {{ padding:7px 9px; border:1px solid #514d45; border-radius:9px; color:#8f8a81; font:600 9px/1 "Cascadia Mono",monospace; letter-spacing:.07em; text-transform:uppercase; }}
    .validation-badge.valid {{ border-color:rgba(216,131,96,.65); color:#edb199; background:rgba(216,131,96,.08); }}
    .validation-badge.invalid {{ border-color:rgba(211,110,103,.65); color:#e5a39e; background:rgba(211,110,103,.08); }}
    .canvas {{ position:relative; min-height:0; overflow:hidden; border:1px solid #34312c; border-radius:24px; background:linear-gradient(135deg,#ede8de,#d7d0c4); display:grid; place-items:center; }}
    .canvas::before {{ content:""; position:absolute; inset:0; pointer-events:none; opacity:.28; background-image:linear-gradient(rgba(33,31,27,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(33,31,27,.055) 1px,transparent 1px); background-size:32px 32px; }}
    .viewport {{ position:relative; width:100%; height:100%; min-height:520px; transition:width .45s cubic-bezier(.16,1,.3,1),border-radius .45s ease; }}
    .viewport.mobile {{ width:min(390px,calc(100% - 32px)); height:calc(100% - 32px); min-height:600px; border:9px solid #282622; border-radius:34px; overflow:hidden; box-shadow:0 20px 50px rgba(27,23,18,.2); }}
    iframe {{ display:block; width:100%; height:100%; min-height:inherit; border:0; background:transparent; }}
    .preview-empty {{ position:absolute; z-index:1; left:50%; top:50%; width:min(380px,calc(100% - 48px)); padding:26px 0; border-top:1px solid rgba(33,31,27,.25); border-bottom:1px solid rgba(33,31,27,.25); color:#5d574f; transform:translate(-50%,-50%); }}
    .preview-empty strong {{ display:block; color:#2e2a25; font-size:18px; letter-spacing:-.025em; }}
    .preview-empty span {{ display:block; margin-top:8px; font-size:13px; line-height:1.55; }}
    .preview-empty.hidden {{ display:none; }}
    .draft-flag {{ position:absolute; z-index:2; top:14px; right:14px; padding:7px 9px; border:1px solid rgba(33,31,27,.22); border-radius:9px; color:#5a5148; background:rgba(242,237,228,.82); backdrop-filter:blur(10px); font:600 9px/1 "Cascadia Mono",monospace; letter-spacing:.08em; text-transform:uppercase; }}
    @media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ animation:none!important; transition:none!important; }} }}
    @media (max-width:900px) {{ .shell {{ grid-template-columns:1fr; padding:10px; }} .rail,.stage {{ border-radius:20px; }} .stage {{ min-height:760px; }} }}
    @media (max-width:560px) {{ .rail {{ padding:18px; }} .stage {{ padding:10px; grid-template-rows:auto auto minmax(620px,1fr); }} .control-grid {{ grid-template-columns:1fr; }} .field-wide {{ grid-column:auto; }} .actions {{ grid-template-columns:1fr 1fr; }} .button-primary {{ grid-column:1/-1; }} .stage-head {{ grid-template-columns:1fr; }} .view-toggle {{ width:max-content; }} .artifact-meta {{ grid-template-columns:1fr; gap:9px; }} .validation-badge {{ width:max-content; }} .telemetry {{ grid-template-columns:1fr; }} .metric+.metric {{ padding-left:0; border-left:0; border-top:1px solid var(--line); }} }}
  </style>
</head>
<body data-builder-release="{escape(BUILDER_UI_RELEASE)}">
  <main class="shell">
    <section class="rail" aria-label="Настройки генерации">
      <header>
        <p class="eyebrow">Kaigo / функциональный прототип / build {escape(BUILDER_UI_RELEASE)}</p>
        <h1>Создайте AI-сотрудника для сайта.</h1>
        <p class="lede">Вставьте ссылку. Kaigo снимет страницу, соберёт визуальный бриф, создаст виджет и автоматически проверит результат.</p>
      </header>
      <div class="control-grid">
        <div class="field field-wide">
          <label for="source-url">Ссылка на сайт</label>
          <input id="source-url" type="url" inputmode="url" autocomplete="url" placeholder="https://example.com" required>
          <p class="helper">Пока анализируется одна публичная HTTPS-страница: desktop и mobile, верх, середина и низ.</p>
        </div>
        <div class="field field-wide">
          <label for="brief">Пожелание к AI-сотруднику <span class="helper">· необязательно</span></label>
          <textarea id="brief" maxlength="12000" spellcheck="true" placeholder="Например: спокойный консультант, который помогает выбрать услугу">{escape(DEFAULT_BRIEF)}</textarea>
        </div>
        <details>
          <summary>Дополнительные параметры прототипа</summary>
          <div class="advanced-controls">
            <div class="field">
              <label for="engine">Движок</label>
              <select id="engine">{options}</select>
            </div>
            <div class="field" id="creativity-field">
              <label for="creativity">Творчество</label>
              <input id="creativity" type="number" min="0" max="2" step="0.05" value="{temperature}">
            </div>
          </div>
        </details>
      </div>
      <div class="actions">
        <button class="button button-primary" id="generate" type="button">Создать виджет</button>
        <button class="button" id="cancel" type="button" disabled>Отмена</button>
        <button class="button" id="retry" type="button" disabled>Повторить</button>
      </div>
      <div class="error" id="error" role="alert"></div>
      <section>
        <div class="timeline-head"><h2>Диалог с генератором</h2><span class="pulse" id="pulse"></span></div>
        <div id="builder-messages">
          <div class="timeline" id="timeline"><div class="empty">После запуска здесь появятся реальные этапы анализа, генерации и проверки.</div></div>
        </div>
        <div class="refinement-box">
          <textarea id="refinement" maxlength="2000" placeholder="Что изменить в готовом виджете?" disabled></textarea>
          <button class="button" id="refine" type="button" disabled>Изменить</button>
        </div>
      </section>
      <div class="telemetry" aria-label="Метрики запуска">
        <div class="metric"><span class="metric-label">Ревизия</span><span class="metric-value" id="revision">—</span></div>
        <div class="metric"><span class="metric-label">Токены</span><span class="metric-value" id="tokens">—</span></div>
        <div class="metric"><span class="metric-label">Время</span><span class="metric-value" id="elapsed">—</span></div>
      </div>
    </section>

    <section class="stage" aria-label="Предпросмотр виджета">
      <header class="stage-head">
        <div class="stage-title"><h2>Live preview</h2><span class="status" id="status">Ожидает запуска</span></div>
        <div class="view-toggle" aria-label="Размер предпросмотра">
          <button type="button" class="active" data-view="desktop">Desktop</button>
          <button type="button" data-view="mobile">Mobile</button>
        </div>
      </header>
      <div class="artifact-meta" aria-live="polite">
        <div><span class="artifact-label">Art direction</span><p class="art-direction" id="art-direction">Появится после первой валидной ревизии</p></div>
        <span class="validation-badge" id="validation-badge">Не проверено</span>
      </div>
      <div class="canvas">
        <div class="draft-flag">Экспериментальный черновик</div>
        <div class="viewport" id="viewport">
          <div class="preview-empty" id="preview-empty"><strong>Пока здесь чистый лист.</strong><span>После первой проверенной стадии появится рабочий виджет. Следующие части будут меняться без перезагрузки страницы.</span></div>
          <iframe id="preview" sandbox="{escape(iframe['sandbox'])}" referrerpolicy="{escape(iframe['referrerpolicy'])}" title="{escape(iframe['title'])}"></iframe>
        </div>
      </div>
    </section>
  </main>
  <script>
  (() => {{
    'use strict';
    const elements = Object.fromEntries(['source-url','engine','creativity','creativity-field','brief','generate','cancel','retry','error','revision','tokens','elapsed','builder-messages','timeline','pulse','status','preview','preview-empty','viewport','art-direction','validation-badge','refinement','refine'].map(id => [id, document.getElementById(id)]));
    const defaultMaxRepairs = {int(default_max_repairs)};
    let currentRun = null;
    let stream = null;
    let terminal = false;
    let snapshotTimer = null;
    let previewChannel = null;
    let previewRevision = null;
    let previewRun = null;
    const previewRequests = new Set();

    const labUrl = path => new URL(path, document.baseURI).toString();
    const showError = (message) => {{ elements.error.textContent = message || ''; elements.error.classList.toggle('visible', Boolean(message)); }};
    const setRunning = (running) => {{ elements.generate.disabled = running; elements.cancel.disabled = !running; elements.refinement.disabled = running || !terminal; elements.refine.disabled = running || !terminal; elements.pulse.classList.toggle('running', running); }};
    const formatNumber = value => new Intl.NumberFormat('ru-RU').format(value || 0);
    const requestPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{{7,95}}$/;

    function createChannel() {{
      return Array.from(crypto.getRandomValues(new Uint8Array(18)), value => value.toString(16).padStart(2, '0')).join('');
    }}

    function loadPreview(revision) {{
      previewRevision = Number(revision);
      previewRun = currentRun;
      previewChannel = createChannel();
      previewRequests.clear();
      elements.preview.src = labUrl(`api/runs/${{currentRun}}/preview?revision=${{previewRevision}}&channel=${{encodeURIComponent(previewChannel)}}`);
      elements['preview-empty'].classList.add('hidden');
    }}

    function sendToPreview(payload) {{
      elements.preview.contentWindow?.postMessage({{
        source:'kaigo-builder-parent',
        version:2,
        channel_id:previewChannel,
        revision:previewRevision,
        ...payload
      }}, '*');
    }}

    async function bridgeChatRequest(data) {{
      if (previewRequests.has(data.request_id)) return;
      previewRequests.add(data.request_id);
      try {{
        const response = await fetch(labUrl(`api/runs/${{currentRun}}/chat`), {{
          method:'POST',
          credentials:'same-origin',
          headers:{{'Content-Type':'application/json','X-Kaigo-Chat':'v2'}},
          body:JSON.stringify({{request_id:data.request_id,message:data.text,revision:previewRevision}})
        }});
        const payload = await response.json().catch(() => ({{}}));
        if (!response.ok) throw Object.assign(new Error(payload.error?.message || `HTTP ${{response.status}}`), {{ payload }});
        if (payload.request_id !== data.request_id || typeof payload.reply !== 'string' || payload.reply.length > 4000) throw new Error('Некорректный ответ chat bridge');
        sendToPreview({{type:'chat.response',request_id:data.request_id,text:payload.reply}});
      }} catch (error) {{
        const payload = error.payload || {{}};
        sendToPreview({{
          type:'chat.error',
          request_id:data.request_id,
          code:payload.error?.code || 'chat_network_error',
          message:payload.error?.message || 'Связь прервалась. Текст сохранён.',
          retryable:payload.error?.retryable !== false
        }});
      }} finally {{
        previewRequests.delete(data.request_id);
      }}
    }}

    async function requestJSON(url, options = {{}}) {{
      const response = await fetch(url, {{ ...options, headers: {{ 'Content-Type':'application/json', ...(options.headers || {{}}) }} }});
      const payload = await response.json().catch(() => ({{}}));
      if (!response.ok) throw new Error(payload.error?.message || `HTTP ${{response.status}}`);
      return payload;
    }}

    function resetTimeline() {{
      elements.timeline.replaceChildren();
      const skeleton = document.createElement('div');
      skeleton.className = 'empty';
      skeleton.textContent = 'Kaigo готовит анализ сайта…';
      elements.timeline.append(skeleton);
      elements['art-direction'].textContent = 'Появится после первой валидной ревизии';
      elements['validation-badge'].textContent = 'Не проверено';
      elements['validation-badge'].className = 'validation-badge';
    }}

    function addBuilderMessage(role, text) {{
      if (elements.timeline.firstElementChild?.classList.contains('empty')) elements.timeline.replaceChildren();
      const row = document.createElement('article');
      row.className = `event ${{role === 'user' ? 'user' : 'assistant'}}`;
      const meta = document.createElement('div');
      meta.className = 'event-meta';
      meta.textContent = role === 'user' ? 'Вы' : 'Kaigo';
      const message = document.createElement('div');
      message.className = 'event-message';
      message.textContent = text;
      row.append(meta, message);
      elements.timeline.append(row);
      elements.timeline.scrollTop = elements.timeline.scrollHeight;
    }}

    function appendEvent(event) {{
      if (elements.timeline.firstElementChild?.classList.contains('empty')) elements.timeline.replaceChildren();
      const row = document.createElement('article');
      row.className = 'event assistant';
      row.dataset.status = event.status || '';
      const meta = document.createElement('div');
      meta.className = 'event-meta';
      const type = document.createElement('span');
      type.textContent = event.type || 'event';
      const stage = document.createElement('span');
      stage.textContent = event.stage || `#${{event.sequence}}`;
      meta.append(type, stage);
      const message = document.createElement('div');
      message.className = 'event-message';
      message.textContent = event.message || '';
      row.append(meta, message);
      if (Array.isArray(event.changes) && event.changes.length) {{
        const labels = {{
          art_direction:'арт-направление',
          body_html:'HTML',
          css:'CSS',
          javascript:'JavaScript',
          layout_contract:'геометрия',
          theme_tokens:'палитра'
        }};
        const changes = document.createElement('div');
        changes.className = 'event-changes';
        changes.textContent = `Изменено: ${{event.changes.map(item => labels[item] || item).join(' · ')}}`;
        row.append(changes);
      }}
      elements.timeline.append(row);
      elements.timeline.scrollTop = elements.timeline.scrollHeight;
      elements.status.textContent = `${{event.stage || 'run'}} · ${{event.status || ''}}`;
      if ((event.type === 'artifact.committed' || event.type === 'artifact.seeded') && event.revision) {{
        loadPreview(event.revision);
      }}
      if (event.type === 'artifact.validated') {{
        const valid = event.status === 'completed';
        elements['validation-badge'].textContent = valid ? 'Проверено' : 'Требует repair';
        elements['validation-badge'].className = `validation-badge ${{valid ? 'valid' : 'invalid'}}`;
      }}
      if (event.type === 'run.failed' || event.type === 'run.cancelled') showError(event.message);
      scheduleSnapshot();
    }}

    function scheduleSnapshot() {{
      clearTimeout(snapshotTimer);
      snapshotTimer = setTimeout(refreshSnapshot, 80);
    }}

    async function refreshSnapshot() {{
      if (!currentRun) return;
      try {{
        const snapshot = await requestJSON(labUrl(`api/runs/${{currentRun}}`));
        const usage = snapshot.usage || {{}};
        elements.revision.textContent = snapshot.artifact?.revision ?? '—';
        elements['art-direction'].textContent = snapshot.artifact?.art_direction || 'Пока нет валидной арт-дирекции';
        elements.tokens.textContent = formatNumber(usage.total_tokens);
        elements.elapsed.textContent = snapshot.elapsed_seconds ? `${{snapshot.elapsed_seconds.toFixed(1)}} с` : 'в процессе';
        terminal = ['completed','failed','cancelled'].includes(snapshot.status);
        if (snapshot.artifact?.revision && (previewRun !== currentRun || previewRevision !== snapshot.artifact.revision)) loadPreview(snapshot.artifact.revision);
        if (terminal) {{
          setRunning(false);
          elements.retry.disabled = snapshot.status === 'completed';
          const refinable = snapshot.status === 'completed' && snapshot.request?.engine === 'direct';
          elements.refinement.disabled = !refinable;
          elements.refine.disabled = !refinable;
          elements.status.textContent = snapshot.status === 'completed' ? 'Готово · артефакт проверен' : `Остановлено · ${{snapshot.error_code || snapshot.status}}`;
        }}
      }} catch (error) {{ showError(error.message); }}
    }}

    function connectEvents(runId) {{
      if (stream) stream.close();
      stream = new EventSource(labUrl(`api/runs/${{runId}}/events`));
      stream.onmessage = message => {{
        try {{ appendEvent(JSON.parse(message.data)); }} catch (_) {{ showError('Получено повреждённое событие'); }}
      }};
      stream.onerror = () => {{ if (terminal && stream) stream.close(); }};
    }}

    async function generate() {{
      showError('');
      const sourceUrl = elements['source-url'].value.trim();
      if (!sourceUrl) {{ showError('Сначала вставьте HTTPS-ссылку на сайт.'); elements['source-url'].focus(); return; }}
      let parsedUrl;
      try {{ parsedUrl = new URL(sourceUrl); }} catch (_) {{ showError('Ссылка на сайт некорректна.'); return; }}
      if (parsedUrl.protocol !== 'https:' || parsedUrl.username || parsedUrl.password || parsedUrl.port || parsedUrl.search || parsedUrl.hash) {{ showError('Нужна публичная HTTPS-ссылка без параметров и авторизации.'); return; }}
      const brief = elements.brief.value.trim() || 'Создай компактного AI-сотрудника, который консультирует посетителей по подтверждённым данным этого сайта.';
      setRunning(true);
      elements.retry.disabled = true;
      elements.status.textContent = 'Создаём запуск';
      resetTimeline();
      addBuilderMessage('user', elements.brief.value.trim() ? `${{sourceUrl}}\n${{elements.brief.value.trim()}}` : sourceUrl);
      try {{
        const run = await requestJSON(labUrl('api/runs'), {{ method:'POST', body:JSON.stringify({{ engine:elements.engine.value, brief, source_url:elements['source-url'].value.trim(), creativity:Number(elements.creativity.value), max_repairs:defaultMaxRepairs, locale:'ru' }}) }});
        currentRun = run.run_id;
        terminal = false;
        connectEvents(currentRun);
        await refreshSnapshot();
      }} catch (error) {{ setRunning(false); showError(error.message); }}
    }}

    async function refineWidget() {{
      showError('');
      const message = elements.refinement.value.trim();
      if (!currentRun || !terminal) {{ showError('Сначала дождитесь проверенной версии.'); return; }}
      if (!message) {{ showError('Напишите, что изменить в виджете.'); elements.refinement.focus(); return; }}
      addBuilderMessage('user', message);
      elements.refinement.value = '';
      setRunning(true);
      elements.status.textContent = 'Создаём проверяемую доработку';
      try {{
        const run = await requestJSON(labUrl(`api/runs/${{currentRun}}/refine`), {{ method:'POST', body:JSON.stringify({{message}}) }});
        currentRun = run.run_id;
        terminal = false;
        if (run.artifact?.revision) loadPreview(run.artifact.revision);
        connectEvents(currentRun);
        await refreshSnapshot();
      }} catch (error) {{ terminal = true; setRunning(false); showError(error.message); }}
    }}

    elements.generate.addEventListener('click', generate);
    elements.refine.addEventListener('click', refineWidget);
    elements.refinement.addEventListener('keydown', event => {{ if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) refineWidget(); }});
    elements.cancel.addEventListener('click', async () => {{ if (!currentRun) return; try {{ await requestJSON(labUrl(`api/runs/${{currentRun}}/cancel`), {{ method:'POST', body:'{{}}' }}); }} catch (error) {{ showError(error.message); }} }});
    elements.retry.addEventListener('click', async () => {{ if (!currentRun) return; showError(''); setRunning(true); resetTimeline(); try {{ const run = await requestJSON(labUrl(`api/runs/${{currentRun}}/retry`), {{ method:'POST', body:'{{}}' }}); currentRun=run.run_id; terminal=false; connectEvents(currentRun); }} catch (error) {{ setRunning(false); showError(error.message); }} }});
    elements.engine.addEventListener('change', () => {{ elements['creativity-field'].style.opacity = elements.engine.value === 'direct' ? '1' : '.42'; elements.creativity.disabled = elements.engine.value !== 'direct'; }});
    document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {{ document.querySelectorAll('[data-view]').forEach(item => item.classList.toggle('active', item === button)); elements.viewport.classList.toggle('mobile', button.dataset.view === 'mobile'); }}));
    window.addEventListener('message', event => {{
      const data = event.data;
      if (event.source!==elements.preview.contentWindow || !data || data.source!=='kaigo-builder-preview' || data.version!==2 || data.channel_id!==previewChannel || data.revision!==previewRevision) return;
      if (data.type === 'rendered') {{ elements.status.textContent = `Ревизия ${{data.revision}} отрисована`; return; }}
      if (data.type !== 'chat.request' || !requestPattern.test(data.request_id) || typeof data.text !== 'string' || data.text.length < 1 || data.text.length > 1000) return;
      bridgeChatRequest(data);
    }});
  }})();
  </script>
</body>
</html>"""
