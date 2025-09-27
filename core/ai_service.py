# /core/ai_service.py

from openai import AsyncOpenAI, OpenAI
from openai.types.chat import ChatCompletionMessage
import logging

logger = logging.getLogger(__name__)

from . import config
from typing import List, Dict, Any
import io
import os

# Настраиваем логирование для отладки
# --- Конфигурация клиента API ---
# Асинхронный клиент для чата
client = AsyncOpenAI(
    api_key=config.settings.VSEGPT_API_KEY,
    base_url="https://api.vsegpt.ru/v1",
)

# Синхронный клиент для STT (как в примере VseGPT)
sync_client = OpenAI(
    api_key=config.settings.VSEGPT_API_KEY,
    base_url="https://api.vsegpt.ru/v1",
)

async def get_ai_response(
    history: List[Dict[str, Any]],
    system_prompt: str | None,
    tools: List[Dict[str, Any]] | None,
    model: str = config.settings.default_model,
    temperature: float = config.settings.default_temperature,
    max_tokens: int = config.settings.default_max_tokens
) -> ChatCompletionMessage | None:
    """
    Асинхронно отправляет запрос к API нейросети, включая системный промпт и инструменты.
    Возвращает объект сообщения от AI, который может содержать текст или tool_calls.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    
    # Добавляем историю диалога
    messages.extend(history)

    logger.info(f"Отправка запроса к модели {model} с {len(messages)} сообщениями и {len(tools) if tools else 0} инструментами.")

    try:
        # ИЗМЕНЕНИЕ: Передаем `tools` и `tool_choice` в API
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
            temperature=temperature,
            max_tokens=max_tokens,
            n=1
        )
        
        # Возвращаем весь объект message, а не только текст
        ai_message = response.choices[0].message
        logger.info("Ответ от AI успешно получен.")
        return ai_message

    except Exception as e:
        logger.error(f"Произошла ошибка при обращении к API VseGPT: {e}")
        return None
    

async def transcribe_audio(audio_data: bytes, model: str = config.settings.default_stt_model) -> str:
    """
    Асинхронно отправляет аудиоданные в API для распознавания речи.

    :param audio_data: Аудиофайл в виде байтов.
    :param model: ID модели для распознавания речи.
    :return: Распознанный текст или сообщение об ошибке.
    """
    logger.info(f"Отправка аудио на распознавание моделью {model}...")
    try:
        # Как в примере: используем реальный файловый объект. Сохраним байты во временный файл .mp3
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        try:
            tmp.write(audio_data)
            tmp_path = tmp.name
        finally:
            tmp.close()

        import asyncio
        def _do_sync(path: str):
            with open(path, "rb") as f:
                return sync_client.audio.transcriptions.create(
                    model=model,
                    response_format="json",
                    language="ru",
                    file=f
                )
        transcription = await asyncio.to_thread(_do_sync, tmp_path)

        try:
            os.remove(tmp_path)
        except Exception:
            pass

        try:
            text = transcription.text
        except AttributeError:
            try:
                text = transcription.get("text")
            except Exception:
                text = str(transcription)
        text = (text or "").strip()
        logger.info("Аудио успешно распознано.")
        return text
    except Exception as e:
        logger.error(f"Произошла ошибка при распознавании речи: {e}")
        return "Ошибка: не удалось распознать речь."


async def transcribe_audio_file(file_path: str, model: str = config.settings.default_stt_model) -> str:
    """
    Отправляет аудиофайл по пути в API для распознавания речи.
    Это более надежный путь для API, ожидающих файловый дескриптор.
    """
    logger.info(f"Отправка аудио-файла на распознавание моделью {model}: {file_path}")
    try:
        import asyncio
        filename = os.path.basename(file_path) or "speech.mp3"
        def _do_sync(path: str):
            with open(path, "rb") as f:
                return sync_client.audio.transcriptions.create(
                    model=model,
                    response_format="json",
                    language="ru",
                    file=f
                )
        transcription = await asyncio.to_thread(_do_sync, file_path)
        try:
            text = transcription.text
        except AttributeError:
            try:
                text = transcription.get("text")
            except Exception:
                text = str(transcription)
        text = (text or "").strip()
        logger.info("Аудио-файл успешно распознан.")
        return text
    except Exception as e:
        logger.error(f"Ошибка при распознавании файла: {e}")
        # Выбрасываем исключение, чтобы сработал fallback
        raise e