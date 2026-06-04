from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class WidgetPreset:
    key: str
    template_key: str
    name: str
    slug: str
    industry: str
    badge: str
    headline: str
    lead: str
    greeting: str
    accent: str
    accent_dark: str
    accent_soft: str
    metrics: tuple[tuple[str, str], ...]
    services: tuple[tuple[str, str], ...]
    quick_replies: tuple[str, ...]
    prompt: str
    temperature: float = 0.35
    max_tokens: int = 1800


def build_widget_html(preset: WidgetPreset) -> str:
    metrics_html = "\n".join(
        f"""
        <div class="kw-metric">
          <strong>{escape(value)}</strong>
          <span>{escape(label)}</span>
        </div>"""
        for label, value in preset.metrics
    )
    services_html = "\n".join(
        f"""
        <article class="kw-service">
          <h3>{escape(title)}</h3>
          <p>{escape(text)}</p>
        </article>"""
        for title, text in preset.services
    )
    quick_html = "\n".join(
        f'<button type="button" class="kw-chip" data-question="{escape(question)}">{escape(question)}</button>'
        for question in preset.quick_replies
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(preset.name)}</title>
  <style>
    :root {{
      --kw-bg: #f7f9fc;
      --kw-ink: #111827;
      --kw-muted: #64748b;
      --kw-line: #d9e2ef;
      --kw-surface: #ffffff;
      --kw-accent: {preset.accent};
      --kw-accent-dark: {preset.accent_dark};
      --kw-accent-soft: {preset.accent_soft};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--kw-bg);
      color: var(--kw-ink);
      font-family: Inter, "Segoe UI", system-ui, -apple-system, sans-serif;
    }}
    a {{ color: inherit; }}
    .kw-page {{
      width: min(1160px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 96px;
    }}
    .kw-topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 20px;
      margin-bottom: 42px;
    }}
    .kw-brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 800;
    }}
    .kw-mark {{
      width: 38px;
      height: 38px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      color: #ffffff;
      background: var(--kw-accent);
      font-weight: 900;
    }}
    .kw-nav {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .kw-nav a,
    .kw-button {{
      min-height: 40px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 10px 14px;
      border: 1px solid var(--kw-line);
      border-radius: 8px;
      background: var(--kw-surface);
      text-decoration: none;
      font-weight: 800;
    }}
    .kw-button-primary {{
      border-color: var(--kw-accent);
      background: var(--kw-accent);
      color: #ffffff;
    }}
    .kw-hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(300px, 0.8fr);
      gap: 32px;
      align-items: center;
      margin-bottom: 28px;
    }}
    .kw-eyebrow {{
      margin: 0 0 10px;
      color: var(--kw-accent-dark);
      font-size: 13px;
      font-weight: 900;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 0;
      max-width: 760px;
      font-size: 52px;
      line-height: 1;
      letter-spacing: 0;
    }}
    .kw-lead {{
      max-width: 680px;
      margin: 18px 0 0;
      color: var(--kw-muted);
      font-size: 18px;
      line-height: 1.62;
    }}
    .kw-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 26px;
    }}
    .kw-panel {{
      border: 1px solid var(--kw-line);
      border-radius: 8px;
      background: var(--kw-surface);
      box-shadow: 0 16px 34px rgba(15, 23, 42, 0.08);
      padding: 22px;
    }}
    .kw-panel h2 {{
      margin: 0 0 14px;
      font-size: 20px;
    }}
    .kw-metrics {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .kw-metric {{
      min-height: 88px;
      border: 1px solid var(--kw-line);
      border-radius: 8px;
      background: var(--kw-accent-soft);
      padding: 14px;
    }}
    .kw-metric strong {{
      display: block;
      margin-bottom: 6px;
      font-size: 20px;
    }}
    .kw-metric span {{
      color: var(--kw-muted);
      font-size: 13px;
      line-height: 1.35;
    }}
    .kw-services {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .kw-service {{
      min-height: 162px;
      border: 1px solid var(--kw-line);
      border-radius: 8px;
      background: var(--kw-surface);
      padding: 18px;
    }}
    .kw-service h3 {{
      margin: 0 0 8px;
      font-size: 18px;
    }}
    .kw-service p {{
      margin: 0;
      color: var(--kw-muted);
      line-height: 1.55;
    }}
    .kw-chat {{
      position: fixed;
      right: 24px;
      bottom: 24px;
      z-index: 2147483647;
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 12px;
    }}
    .kw-chat-panel {{
      width: min(380px, calc(100vw - 32px));
      height: min(560px, calc(100vh - 118px));
      display: none;
      overflow: hidden;
      border: 1px solid var(--kw-line);
      border-radius: 8px;
      background: var(--kw-surface);
      box-shadow: 0 24px 62px rgba(15, 23, 42, 0.24);
    }}
    .kw-chat.is-open .kw-chat-panel {{ display: flex; flex-direction: column; }}
    .kw-chat-head {{
      padding: 16px;
      background: var(--kw-accent);
      color: #ffffff;
      display: flex;
      justify-content: space-between;
      gap: 12px;
    }}
    .kw-chat-title {{
      margin: 0 0 4px;
      font-weight: 900;
    }}
    .kw-chat-subtitle {{
      font-size: 13px;
      opacity: 0.84;
    }}
    .kw-icon-button {{
      width: 36px;
      height: 36px;
      border: 0;
      border-radius: 8px;
      display: inline-grid;
      place-items: center;
      background: rgba(255, 255, 255, 0.18);
      color: #ffffff;
      cursor: pointer;
      font-size: 22px;
    }}
    .kw-log {{
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 10px;
      overflow-y: auto;
      padding: 16px;
      background: #f8fafc;
    }}
    .kw-msg {{
      max-width: 88%;
      border-radius: 8px;
      padding: 10px 12px;
      line-height: 1.48;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 14px;
    }}
    .kw-msg.assistant {{
      align-self: flex-start;
      background: #ffffff;
      border: 1px solid var(--kw-line);
    }}
    .kw-msg.user {{
      align-self: flex-end;
      background: var(--kw-accent);
      color: #ffffff;
    }}
    .kw-quick {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      padding: 0 16px 12px;
      background: #f8fafc;
    }}
    .kw-chip {{
      border: 1px solid var(--kw-line);
      border-radius: 8px;
      background: #ffffff;
      padding: 8px 10px;
      color: var(--kw-ink);
      cursor: pointer;
      font-weight: 700;
      font-size: 13px;
    }}
    .kw-form {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      padding: 14px;
      border-top: 1px solid var(--kw-line);
      background: #ffffff;
    }}
    .kw-input {{
      min-width: 0;
      min-height: 42px;
      border: 1px solid var(--kw-line);
      border-radius: 8px;
      padding: 0 12px;
      font: inherit;
    }}
    .kw-send,
    .kw-toggle {{
      border: 0;
      border-radius: 8px;
      background: var(--kw-accent);
      color: #ffffff;
      cursor: pointer;
      font-weight: 900;
    }}
    .kw-send {{
      min-width: 92px;
      padding: 0 14px;
    }}
    .kw-toggle {{
      min-height: 56px;
      padding: 0 18px;
      box-shadow: 0 18px 34px rgba(15, 23, 42, 0.22);
    }}
    .kw-status {{
      padding: 0 16px 10px;
      min-height: 24px;
      color: var(--kw-muted);
      background: #ffffff;
      font-size: 12px;
    }}
    @media (max-width: 860px) {{
      .kw-page {{ padding-top: 18px; }}
      .kw-topbar,
      .kw-hero {{ display: block; }}
      .kw-nav {{ justify-content: flex-start; margin-top: 14px; }}
      h1 {{ font-size: 36px; }}
      .kw-lead {{ font-size: 16px; }}
      .kw-panel {{ margin-top: 24px; }}
      .kw-services,
      .kw-metrics {{ grid-template-columns: 1fr; }}
      .kw-chat {{ right: 16px; bottom: 16px; left: 16px; }}
      .kw-chat-panel {{ width: 100%; }}
      .kw-toggle {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <main class="kw-page">
    <header class="kw-topbar">
      <div class="kw-brand">
        <div class="kw-mark">AI</div>
        <div>
          <div>{escape(preset.badge)}</div>
          <small>{escape(preset.industry)}</small>
        </div>
      </div>
      <nav class="kw-nav" aria-label="Навигация">
        <a href="/">Kaigo Widgets</a>
        <a href="/client/login">Кабинет</a>
        <a href="/w/{escape(preset.slug)}" class="kw-button kw-button-primary">Открыть чат</a>
      </nav>
    </header>

    <section class="kw-hero">
      <div>
        <p class="kw-eyebrow">{escape(preset.badge)}</p>
        <h1>{escape(preset.headline)}</h1>
        <p class="kw-lead">{escape(preset.lead)}</p>
        <div class="kw-actions">
          <button type="button" class="kw-button kw-button-primary" data-open-chat>Задать вопрос</button>
          <a class="kw-button" href="/api/health/ai">Проверить AI</a>
        </div>
      </div>
      <aside class="kw-panel" aria-label="Показатели демо">
        <h2>Что демонстрирует шаблон</h2>
        <div class="kw-metrics">{metrics_html}
        </div>
      </aside>
    </section>

    <section class="kw-services" aria-label="Сценарии">{services_html}
    </section>
  </main>

  <section class="kw-chat" data-chat>
    <div class="kw-chat-panel" role="dialog" aria-label="AI-консультант">
      <div class="kw-chat-head">
        <div>
          <p class="kw-chat-title">{escape(preset.name)}</p>
          <div class="kw-chat-subtitle">Gemini 3 Flash, ответы по сценарию</div>
        </div>
        <button class="kw-icon-button" type="button" data-close-chat aria-label="Закрыть">×</button>
      </div>
      <div class="kw-log" data-log>
        <div class="kw-msg assistant">{escape(preset.greeting)}</div>
      </div>
      <div class="kw-quick">{quick_html}
      </div>
      <form class="kw-form" data-chat-form>
        <input class="kw-input" name="message" autocomplete="off" placeholder="Напишите вопрос" required>
        <button class="kw-send" type="submit">Отправить</button>
      </form>
      <div class="kw-status" data-status></div>
    </div>
    <button class="kw-toggle" type="button" data-open-chat>AI-консультант</button>
  </section>

  <script>
    (function () {{
      var root = document.querySelector('[data-chat]');
      var log = document.querySelector('[data-log]');
      var form = document.querySelector('[data-chat-form]');
      var input = form ? form.querySelector('input[name="message"]') : null;
      var status = document.querySelector('[data-status]');
      var busy = false;

      function setOpen(open) {{
        if (!root) {{ return; }}
        root.classList.toggle('is-open', open);
        if (open && input) {{ setTimeout(function () {{ input.focus(); }}, 80); }}
      }}

      function setStatus(text) {{
        if (status) {{ status.textContent = text || ''; }}
      }}

      function addMessage(role, text) {{
        if (!log) {{ return; }}
        var bubble = document.createElement('div');
        bubble.className = 'kw-msg ' + role;
        bubble.textContent = text;
        log.appendChild(bubble);
        log.scrollTop = log.scrollHeight;
      }}

      async function sendMessage(text) {{
        var message = (text || '').trim();
        if (!message || busy) {{ return; }}
        busy = true;
        setOpen(true);
        addMessage('user', message);
        setStatus('AI готовит ответ...');
        try {{
          var response = await fetch('/api/send', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ message: message }})
          }});
          var data = await response.json().catch(function () {{ return {{}}; }});
          if (!response.ok || data.type === 'error') {{
            addMessage('assistant', data.content || 'Не удалось получить ответ. Попробуйте еще раз.');
          }} else {{
            addMessage('assistant', data.content || data.ai_response || 'Готово, чем еще помочь?');
          }}
        }} catch (error) {{
          addMessage('assistant', 'Не получилось связаться с сервером. Проверьте соединение и повторите запрос.');
        }} finally {{
          busy = false;
          setStatus('');
        }}
      }}

      Array.prototype.slice.call(document.querySelectorAll('[data-open-chat]')).forEach(function (button) {{
        button.addEventListener('click', function () {{ setOpen(true); }});
      }});
      var close = document.querySelector('[data-close-chat]');
      if (close) {{ close.addEventListener('click', function () {{ setOpen(false); }}); }}
      Array.prototype.slice.call(document.querySelectorAll('[data-question]')).forEach(function (button) {{
        button.addEventListener('click', function () {{ sendMessage(button.getAttribute('data-question')); }});
      }});
      if (form && input) {{
        form.addEventListener('submit', function (event) {{
          event.preventDefault();
          var value = input.value;
          input.value = '';
          sendMessage(value);
        }});
      }}
    }}());
  </script>
</body>
</html>"""


PRESETS: tuple[WidgetPreset, ...] = (
    WidgetPreset(
        key="demo",
        template_key="consult_demo",
        name="Kaigo Demo Consultant",
        slug="demka",
        industry="универсальный AI-консультант",
        badge="Kaigo Widgets",
        headline="AI-консультант для сайта, который собирает заявки и отвечает по делу",
        lead="Демо показывает, как виджет встречает посетителя, уточняет задачу, помогает выбрать услугу и мягко переводит диалог к заявке.",
        greeting="Здравствуйте! Я демо-консультант Kaigo. Могу показать сценарии для сайта, помочь выбрать шаблон и объяснить, как виджет собирает заявки.",
        accent="#2563eb",
        accent_dark="#1d4ed8",
        accent_soft="#eff6ff",
        metrics=(("Сценарии", "5"), ("Интеграция", "1 день"), ("Канал", "web-chat")),
        services=(
            ("Консультации", "Отвечает на частые вопросы и помогает посетителю быстрее сформулировать запрос."),
            ("Квалификация", "Уточняет нишу, задачу, сроки и контактные данные без длинных форм."),
            ("Передача заявки", "Финализирует диалог в понятную заявку для менеджера или владельца бизнеса."),
        ),
        quick_replies=(
            "Подбери шаблон для стоматологии",
            "Как виджет собирает заявки?",
            "Что можно подключить к сайту?",
        ),
        prompt=(
            "Ты AI-консультант платформы Kaigo Widgets. Отвечай по-русски, кратко и полезно. "
            "Помогай владельцу бизнеса выбрать сценарий виджета, объясняй пользу, задавай 1-2 уточняющих вопроса "
            "и предлагай передать заявку менеджеру, если пользователь готов обсудить внедрение."
        ),
        temperature=0.35,
        max_tokens=1600,
    ),
    WidgetPreset(
        key="dental",
        template_key="consult_dental",
        name="Стоматология: консультант записи",
        slug="dental-consultant",
        industry="стоматология",
        badge="Dental Care AI",
        headline="Консультант для стоматологии: запись, услуги и первичная маршрутизация",
        lead="Шаблон помогает пациенту выбрать услугу, понять порядок записи и оставить контакт без ожидания ответа администратора.",
        greeting="Здравствуйте! Я консультант клиники. Подскажу по услугам, подготовке к визиту и помогу сориентироваться по записи.",
        accent="#0f766e",
        accent_dark="#115e59",
        accent_soft="#ecfdf5",
        metrics=(("Услуги", "8+"), ("Тон", "бережный"), ("Цель", "запись")),
        services=(
            ("Первичная консультация", "Помогает понять, к какому врачу записаться: терапевт, хирург, ортодонт или ортопед."),
            ("Планирование визита", "Собирает удобное время, имя и контакт для связи администратора."),
            ("Безопасные ответы", "Не ставит диагнозы и направляет к врачу при боли, отеке или срочных симптомах."),
        ),
        quick_replies=(
            "Болит зуб, к кому записаться?",
            "Сколько длится консультация?",
            "Помоги выбрать время визита",
        ),
        prompt=(
            "Ты AI-консультант стоматологической клиники. Не ставь диагнозы, не назначай препараты и не обещай лечение без осмотра. "
            "Объясняй услуги простым языком, уточняй симптом или цель визита, имя, телефон и удобное время. "
            "Если пользователь пишет о сильной боли, отеке, температуре, травме или кровотечении, мягко рекомендуй срочно связаться с клиникой или врачом. "
            "Цель диалога - помочь выбрать направление и подготовить заявку для администратора."
        ),
        temperature=0.25,
        max_tokens=1700,
    ),
    WidgetPreset(
        key="realty",
        template_key="consult_realty",
        name="Недвижимость: подбор объекта",
        slug="realty-consultant",
        industry="недвижимость",
        badge="Real Estate AI",
        headline="AI-консультант для агентства недвижимости и подбора объектов",
        lead="Виджет уточняет район, бюджет, сроки, формат сделки и помогает сформировать понятную заявку риелтору.",
        greeting="Здравствуйте! Помогу подобрать объект: уточню бюджет, район, цель покупки или аренды и передам заявку специалисту.",
        accent="#7c3aed",
        accent_dark="#6d28d9",
        accent_soft="#f5f3ff",
        metrics=(("Заявка", "за 2 минуты"), ("Поля", "бюджет/район"), ("Роль", "подбор")),
        services=(
            ("Покупка", "Собирает критерии объекта и помогает не забыть важные параметры."),
            ("Аренда", "Уточняет срок, район, бюджет, состав жильцов и желаемую дату просмотра."),
            ("Инвестиции", "Фиксирует цель, горизонт, ликвидность и ожидаемый бюджет для консультации."),
        ),
        quick_replies=(
            "Подбери квартиру до 12 млн",
            "Хочу сдать объект",
            "Какие данные нужны для подбора?",
        ),
        prompt=(
            "Ты AI-консультант агентства недвижимости. Помогай пользователю сформулировать заявку на покупку, продажу или аренду. "
            "Уточняй город/район, бюджет, срок, тип объекта, количество комнат, цель сделки и контакт. "
            "Не давай юридических гарантий и не обещай конкретную цену без проверки рынка. "
            "В конце кратко резюмируй заявку и предложи передать ее специалисту."
        ),
        temperature=0.4,
        max_tokens=1800,
    ),
    WidgetPreset(
        key="auto",
        template_key="consult_auto",
        name="Автосервис: запись и диагностика",
        slug="auto-service-consultant",
        industry="автосервис",
        badge="Auto Service AI",
        headline="Консультант для автосервиса: диагностика, запись и подбор услуги",
        lead="Шаблон помогает водителю описать проблему, собрать данные по авто и записаться на диагностику или обслуживание.",
        greeting="Здравствуйте! Опишите марку, модель, год и что происходит с автомобилем. Помогу понять, с какой услуги начать.",
        accent="#ca8a04",
        accent_dark="#a16207",
        accent_soft="#fffbeb",
        metrics=(("Данные", "марка/год"), ("Фокус", "запись"), ("Режим", "быстро")),
        services=(
            ("Диагностика", "Уточняет симптомы, индикаторы на панели, пробег и условия появления проблемы."),
            ("ТО", "Помогает подобрать регламентное обслуживание по пробегу и сроку."),
            ("Запись", "Собирает контакт, удобное время и краткое описание работ для мастера."),
        ),
        quick_replies=(
            "Горит Check Engine",
            "Нужно ТО, что указать?",
            "Хочу записаться на диагностику",
        ),
        prompt=(
            "Ты AI-консультант автосервиса. Не выдавай точный диагноз без осмотра, а помогай собрать симптомы и данные: марка, модель, год, пробег, "
            "когда появилась проблема, есть ли индикаторы на панели. Объясняй вероятные направления проверки осторожно. "
            "Цель - подготовить заявку на диагностику, ТО или ремонт и предложить запись к мастеру."
        ),
        temperature=0.3,
        max_tokens=1700,
    ),
    WidgetPreset(
        key="beauty",
        template_key="consult_beauty",
        name="Салон красоты: запись на услугу",
        slug="beauty-consultant",
        industry="beauty & wellness",
        badge="Beauty Studio AI",
        headline="Консультант для салона: выбор услуги, мастер и запись",
        lead="Виджет помогает выбрать процедуру, уточняет пожелания и переводит посетителя к записи без перегруза формами.",
        greeting="Здравствуйте! Помогу выбрать услугу, мастера и удобное время. Расскажите, что хотите сделать и когда удобно прийти.",
        accent="#db2777",
        accent_dark="#be185d",
        accent_soft="#fdf2f8",
        metrics=(("Услуги", "каталог"), ("Тон", "теплый"), ("Цель", "бронь")),
        services=(
            ("Подбор услуги", "Уточняет желаемый результат, длину волос, тип процедуры или повод."),
            ("Запись", "Собирает дату, время, имя и телефон для подтверждения администратором."),
            ("Допродажа", "Мягко предлагает сопутствующие услуги, если они релевантны запросу."),
        ),
        quick_replies=(
            "Помоги выбрать процедуру",
            "Хочу записаться на выходные",
            "Какие данные нужны для записи?",
        ),
        prompt=(
            "Ты AI-консультант салона красоты. Общайся тепло, но без навязчивости. Помогай выбрать услугу, мастера, дату и время. "
            "Уточняй желаемый результат, ограничения, длину/тип волос или особенности процедуры, если это важно. "
            "Не обещай медицинский эффект и не давай противопоказаний как врач. Цель - подготовить заявку на запись."
        ),
        temperature=0.45,
        max_tokens=1700,
    ),
)


PRESET_BY_TEMPLATE = {preset.template_key: preset for preset in PRESETS}
PRESET_BY_SLUG = {preset.slug: preset for preset in PRESETS}
