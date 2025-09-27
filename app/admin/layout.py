from __future__ import annotations

from html import escape

from aiohttp import web


def render_layout(title: str, content: str, *, nav_extra: str | None = None) -> web.Response:
    extra_html = f" {nav_extra}" if nav_extra else ""
    html = f"""
    <html><head><title>{escape(title)}</title>
    <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; background: #f9fafb; color: #111827; }}
    header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: 28px; }}
    nav a {{ margin-right: 12px; color: #2563eb; text-decoration: none; font-weight: 600; padding: 6px 10px; border-radius: 6px; }}
    nav a.button {{ display: inline-block; background: #2563eb; color: #fff; }}
    nav a.current {{ background: #1d4ed8; color: #fff; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; background: #fff; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 10px; text-align: left; }}
    th {{ background: #f3f4f6; text-transform: uppercase; font-size: 12px; letter-spacing: .04em; }}
    form.inline {{ display: inline; }}
    label {{ display: block; margin-bottom: 12px; font-weight: 600; }}
    input[type=text], input[type=email], input[type=password], textarea {{ width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 8px; box-sizing: border-box; background: #fff; }}
    textarea {{ min-height: 140px; font-family: monospace; }}
    button {{ padding: 8px 16px; border: none; border-radius: 8px; background: #2563eb; color: #fff; cursor: pointer; }}
    section.card {{ background: #fff; padding: 24px; border-radius: 16px; box-shadow: 0 8px 30px rgba(15, 23, 42, 0.08); margin-bottom: 24px; }}
    .muted {{ color: #6b7280; font-size: 14px; }}
    .actions {{ margin-top: 16px; display: flex; gap: 12px; flex-wrap: wrap; }}
    .actions a, .actions button {{ margin-right: 0; }}
    .notice {{ background: #eff6ff; border-left: 4px solid #2563eb; }}
    .preview-frame {{ width: 100%; height: 520px; border: 1px solid #d1d5db; border-radius: 12px; margin-top: 16px; }}
    .stack {{ display: flex; flex-direction: row; gap: 24px; align-items: flex-start; flex-wrap: wrap; }}
    .stack .column {{ flex: 1; min-width: 220px; }}
    .chip {{ display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; background: #f3f4f6; font-size: 13px; color: #374151; margin-right: 8px; margin-bottom: 4px; }}
    </style>
    </head><body>
    <header>
      <h1>{escape(title)}</h1>
      <nav><a href="/admin" class="button">Панель</a><a href="/admin/tenants">Заказчики</a>{extra_html}<a href="/admin/logout">Выход</a></nav>
    </header>
    {content}
    </body></html>
    """
    return web.Response(text=html, content_type='text/html')
