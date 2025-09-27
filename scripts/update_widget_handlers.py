from pathlib import Path
import re
import textwrap

path = Path('wrappers/web_app_aiohttp.py')
text = path.read_text(encoding='utf-8')

api_send_pattern = r"async def api_send\(request\):\n.*?return response\n"
api_history_pattern = r"async def api_history\(request\):\n.*?return response\n"
api_audio_pattern = r"async def api_audio\(request\):\n.*?return response\n"

api_send_replacement = textwrap.dedent('''
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
        if not result:
            payload = {"type": "error", "content": "Нет ответа."}
            status_code = 502
        elif result.get("type") == "error":
            payload = result
            status_code = 500
        else:
            payload = result
            status_code = 200

    response = web.json_response(payload, status=status_code)
    if is_new:
        response.set_cookie(COOKIE_NAME, uid, max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response
''')

api_history_replacement = textwrap.dedent('''
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
''')

api_audio_replacement = textwrap.dedent('''
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
        transcription = await asyncio.to_thread(_transcribe_audio_bytes, audio_data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("api_audio: ошибка распознавания аудио")
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

    try:
        ai_result = await core_api.process_message(user_id=user_id, text=transcription)
    except Exception as exc:  # noqa: BLE001
        logger.exception("api_audio: ошибка обработки транскрипции")
        payload = {"type": "error", "content": "Не удалось обработать транскрипцию."}
        status_code = 500
    else:
        if not ai_result or ai_result.get("type") == "error":
            content = (ai_result or {}).get("content", "Не удалось получить ответ от AI.")
            status_code = 502
            payload = {"type": "error", "content": content}
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
''')

text, count_send = re.subn(api_send_pattern, api_send_replacement, text, flags=re.S)
text, count_history = re.subn(api_history_pattern, api_history_replacement, text, flags=re.S)
text, count_audio = re.subn(api_audio_pattern, api_audio_replacement, text, flags=re.S)

if not all([count_send, count_history, count_audio]):
    raise SystemExit(f"Replacement counts: send={count_send}, history={count_history}, audio={count_audio}")

path.write_text(text, encoding='utf-8')
