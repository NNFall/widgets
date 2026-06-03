from __future__ import annotations

import json
import logging
from typing import Any

from . import ai_service, database as db
from .config import OperationMode, settings
from .utils import get_prompt_and_tools

logger = logging.getLogger(__name__)


async def process_message(
    user_id: int,
    text: str | None = None,
    audio_data: bytes | None = None,
    audio_file_path: str | None = None,
    **options: Any,
) -> dict | None:
    current_mode = settings.operation_mode

    if current_mode == OperationMode.NONE:
        logger.info("режим 'NONE': пропускаем обработку сообщения.")
        return None

    if current_mode == OperationMode.EXISTING:
        user_exists = await db.does_user_exist(user_id)
        if not user_exists:
            logger.info("режим 'EXISTING': игнорируем нового пользователя %s", user_id)
            return None

    user = await db.add_or_get_user(user_id)
    if not user['is_active']:
        logger.info("пользователь %s отключён, сообщение пропущено", user_id)
        return None

    widget_id = options.get("widget_id")
    widget_slug = options.get("widget_slug")

    processed_text = ""
    stt_model = options.get("stt_model") or settings.default_stt_model

    if audio_file_path:
        processed_text = await ai_service.transcribe_audio_file(audio_file_path, model=stt_model)
        await db.add_message(
            user_id,
            "user",
            f"[Голосовое сообщение]: {processed_text}",
            widget_id=widget_id,
            widget_slug=widget_slug,
        )
    elif audio_data:
        processed_text = await ai_service.transcribe_audio(audio_data, model=stt_model)
        await db.add_message(
            user_id,
            "user",
            f"[Голосовое сообщение]: {processed_text}",
            widget_id=widget_id,
            widget_slug=widget_slug,
        )
    elif text:
        processed_text = text
        await db.add_message(
            user_id,
            "user",
            processed_text,
            widget_id=widget_id,
            widget_slug=widget_slug,
        )
    else:
        return {"type": "error", "content": "Не найдено сообщение для обработки."}

    prompt_source = options.get("prompt_source") or None
    system_prompt, tools = await get_prompt_and_tools(prompt_source)

    history = await db.get_history(
        user_id,
        widget_id=widget_id,
        widget_slug=widget_slug,
    )

    model = options.get("model") or settings.default_model
    temperature = options.get("temperature", settings.default_temperature)
    max_tokens = options.get("max_tokens", settings.default_max_tokens)
    logger.info(
        "process_message: user=%s widget=%s model=%s prompt=%s stt=%s temperature=%s max_tokens=%s history=%s",
        user_id,
        widget_slug or widget_id or "-",
        model,
        prompt_source or "default",
        stt_model,
        temperature,
        max_tokens,
        len(history),
    )

    try:
        ai_response_message = await ai_service.get_ai_response(
            history=history,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except ai_service.AIServiceError as exc:
        logger.error("AI provider failed [%s]: %s", exc.code, exc.detail)
        return {"type": "error", "code": exc.code, "content": exc.public_message}

    if not ai_response_message:
        return {"type": "error", "content": "Не удалось получить ответ от AI."}

    if ai_response_message.tool_calls:
        logger.info("AI вернул tool_calls: %s", ai_response_message.tool_calls)
        db_content = json.dumps(ai_response_message.model_dump())
        await db.add_message(
            user_id,
            "assistant",
            db_content,
            widget_id=widget_id,
            widget_slug=widget_slug,
        )
        return {"type": "tool_calls", "content": ai_response_message.tool_calls}

    ai_text = ai_response_message.content or ""
    logger.info("AI ответил: %s", ai_text[:120])
    await db.add_message(
        user_id,
        "assistant",
        ai_text,
        widget_id=widget_id,
        widget_slug=widget_slug,
    )
    return {"type": "text", "content": ai_text}
