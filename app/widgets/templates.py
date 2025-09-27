from __future__ import annotations

from typing import Dict

from wrappers.web_app_aiohttp import HTML_PAGE

HTML_RED = (
    HTML_PAGE
    .replace('#2563eb', '#dc2626')
    .replace('#1d4ed8', '#b91c1c')
    .replace('#4f46e5', '#ef4444')
    .replace('#2563eb;', '#dc2626;')
    .replace('#2563eb,', '#dc2626,')
)

DEFAULT_TEMPLATE_KEY = 'blue'

TEMPLATES: Dict[str, str] = {
    'blue': HTML_PAGE,
    'red': HTML_RED,
}


def get_template_html(template_key: str) -> str:
    return TEMPLATES.get(template_key, HTML_PAGE)
