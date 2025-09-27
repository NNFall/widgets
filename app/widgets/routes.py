from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiohttp import web

from app.db import models
from app.db.repositories import WidgetAssetRepository, WidgetRepository
from app.db.session import session_scope
from app.widgets.templates import get_template_html
from core import api as core_api, database as db
from wrappers.web_app_aiohttp import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    _get_or_set_uid,
    _transcribe_audio_bytes,
    _uid_to_user_id,
    HTML_PAGE,
)

logger = logging.getLogger(__name__)

DEFAULT_CSS = """.widget-container { font-family: Arial, sans-serif; border: 1px solid #ddd; padding: 16px; border-radius: 12px; box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08);}"""
DEFAULT_JS = """console.log('widget loaded');"""


async def _get_widget(request: web.Request, slug: str) -> models.Widget:
    async with session_scope(request.app) as db_session:
        widget_repo = WidgetRepository(db_session)
        widget = await widget_repo.get_by_slug(slug)
        if widget is None:
            raise web.HTTPNotFound()
        return widget


async def _get_widget_and_asset(
    slug: str,
    version: Optional[int],
    request: web.Request,
) -> tuple[models.Widget, Optional[models.WidgetAsset]]:
    async with session_scope(request.app) as db_session:
        widget_repo = WidgetRepository(db_session)
        asset_repo = WidgetAssetRepository(db_session)
        widget = await widget_repo.get_by_slug(slug)
        if widget is None:
            raise web.HTTPNotFound()
        if version is not None:
            asset = await asset_repo.get_version(widget.id, version)
        else:
            asset = await asset_repo.get_latest(widget.id)
        return widget, asset


def _assemble_document(title: str, html_segment: str, css: str | None, js: str | None) -> str:
    css_block = f"<style>{css}</style>" if css else ""
    js_block = f"<script>{js}</script>" if js else ""
    return f"""<!DOCTYPE html>
<html lang='ru'>
<head>
  <meta charset='UTF-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>{title}</title>
  {css_block}
</head>
<body>{html_segment}{js_block}</body>
</html>"""


def _adjust_widget_urls(html: str, slug: str) -> str:
    return (
        html
        .replace('/api/send', f'/w/{slug}/api/send')
        .replace('/api/history', f'/w/{slug}/api/history')
        .replace('/api/audio', f'/w/{slug}/api/audio')
    )


async def render_widget(request: web.Request) -> web.Response:
    slug = request.match_info.get('slug')
    version_param = request.query.get('version')
    version = int(version_param) if version_param and version_param.isdigit() else None

    widget, asset = await _get_widget_and_asset(slug, version, request)

    if asset and asset.html:
        html_template = asset.html
    else:
        html_template = get_template_html(widget.template)

    html_template = _adjust_widget_urls(html_template, widget.slug)

    if '<html' in html_template.lower():
        return web.Response(text=html_template, content_type='text/html')

    css = asset.css if asset and asset.css else DEFAULT_CSS
    js = asset.js if asset and asset.js else DEFAULT_JS
    document = _assemble_document(widget.name, html_template, css, js)
    return web.Response(text=document, content_type='text/html')


async def widget_api_send(request: web.Request) -> web.Response:
    slug = request.match_info['slug']
    widget = await _get_widget(request, slug)

    try:
        data = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning('widget_api_send: некорректный JSON (%s)', exc)
        return web.json_response({"type": "error", "content": "Некорректный JSON."}, status=400)

    text_message = (data.get('message', '') or '').strip()
    if not text_message:
        return web.json_response({"type": "error", "content": "Пустое сообщение."}, status=400)

    uid, is_new = await _get_or_set_uid(request)
    user_id = _uid_to_user_id(uid)
    logger.info('widget_api_send: slug=%s uid=%s user_id=%s', slug, uid[:8], user_id)

    result = await core_api.process_message(
        user_id=user_id,
        text=text_message,
        model=widget.ai_model,
        prompt_source=widget.prompt_source,
        temperature=widget.temperature,
        max_tokens=widget.max_tokens,
        widget_id=widget.id,
        widget_slug=widget.slug,
    )

    if not result:
        payload = {"type": "error", "content": "Нет ответа."}
        status_code = 502
    else:
        payload = result
        status_code = 200 if result.get("type") != "error" else 500

    response = web.json_response(payload, status=status_code)
    if is_new:
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response


async def widget_api_history(request: web.Request) -> web.Response:
    slug = request.match_info['slug']
    await _get_widget(request, slug)

    try:
        uid, is_new = await _get_or_set_uid(request)
        user_id = _uid_to_user_id(uid)
        history = await db.get_history(user_id=user_id, limit=50)
    except Exception as exc:  # noqa: BLE001
        logger.exception('widget_api_history: ошибка получения истории')
        return web.json_response({"type": "error", "content": "Не удалось получить историю."}, status=500)

    logger.info('widget_api_history: slug=%s uid=%s messages=%s', slug, uid[:8], len(history))
    response = web.json_response({"type": "history", "messages": history})
    if is_new:
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response


async def widget_api_audio(request: web.Request) -> web.Response:
    slug = request.match_info['slug']
    widget = await _get_widget(request, slug)

    reader = await request.multipart()
    audio_data = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == 'audio':
            audio_data = await part.read()
            break

    if not audio_data:
        return web.json_response({"type": "error", "content": "Аудио не найдено."}, status=400)

    uid, is_new = await _get_or_set_uid(request)
    user_id = _uid_to_user_id(uid)
    logger.info('widget_api_audio: slug=%s uid=%s bytes=%s', slug, uid[:8], len(audio_data))

    try:
        transcription = await asyncio.to_thread(_transcribe_audio_bytes, audio_data)
    except Exception as exc:  # noqa: BLE001
        logger.exception('widget_api_audio: ошибка распознавания')
        response = web.json_response(
            {"type": "error", "content": "Не удалось распознать аудио."},
            status=502,
        )
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

    result = await core_api.process_message(
        user_id=user_id,
        text=transcription,
        model=widget.ai_model,
        prompt_source=widget.prompt_source,
        temperature=widget.temperature,
        max_tokens=widget.max_tokens,
        stt_model=widget.stt_model,
        widget_id=widget.id,
        widget_slug=widget.slug,
    )

    if not result or result.get("type") == "error":
        content = (result or {}).get("content", "Не удалось получить ответ от AI.")
        payload = {"type": "error", "content": content}
        status_code = 502
    else:
        ai_text = result.get("content", "")
        payload = {
            "type": "audio_response",
            "transcription": transcription,
            "ai_response": ai_text,
        }
        status_code = 200

    response = web.json_response(payload, status=status_code)
    if is_new:
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response


def setup_widget_routes(app: web.Application) -> None:
    app.router.add_get('/w/{slug}', render_widget)
    app.router.add_post('/w/{slug}/api/send', widget_api_send)
    app.router.add_get('/w/{slug}/api/history', widget_api_history)
    app.router.add_post('/w/{slug}/api/audio', widget_api_audio)
