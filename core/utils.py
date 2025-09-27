from __future__ import annotations

import json
import logging
import re
from typing import Tuple, List, Dict, Any
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build

from . import config

# --- КЭШ ДЛЯ ПРОМПТА (Улучшенная структура) ---
_prompt_cache = {
    "system_prompt": None,
    "tools": None,
    "last_updated": 0  # Время последнего обновления в секундах (timestamp)
}

# --- Конфигурация автообновления ---
CACHE_LIFETIME_SECONDS = 3600

# Настраиваем доступ к API Google
SCOPES = ['https://www.googleapis.com/auth/documents.readonly']
creds = service_account.Credentials.from_service_account_file(
    config.settings.SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
service = build('docs', 'v1', credentials=creds)

logger = logging.getLogger(__name__)

def _extract_doc_id_from_url(url: str) -> str | None:
    match = re.search(r'/document/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None

async def fetch_google_doc_content(url: str) -> str:
    doc_id = _extract_doc_id_from_url(url)
    if not doc_id:
        logger.error("Не удалось извлечь ID из URL: %s", url)
        return ""
    try:
        logger.info("Загрузка содержимого Google Doc с ID: %s", doc_id)
        document = service.documents().get(documentId=doc_id).execute()
        doc_content = document.get('body').get('content')
        text_content = ""
        for element in doc_content:
            if 'paragraph' in element:
                for sub_element in element.get('paragraph').get('elements'):
                    if 'textRun' in sub_element:
                        text_content += sub_element.get('textRun').get('content')
        return text_content
    except Exception as e:  # noqa: BLE001
        logger.error("Ошибка при доступе к Google Docs API: %s", e)
        return ""

def parse_prompt_and_tools(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    tools_separator = "---TOOLS---"
    if tools_separator in content:
        prompt_part, tools_part = content.split(tools_separator, 1)
        try:
            tools_part_no_comments = re.sub(r'//.*?\n|/\*.*?\*/', '', tools_part, flags=re.S)
            tools = json.loads(tools_part_no_comments)
            return prompt_part.strip(), tools
        except json.JSONDecodeError as e:
            logger.error("Ошибка парсинга JSON для инструментов: %s. Используется только промпт.", e)
            return prompt_part.strip(), []
    return content.strip(), []

def _default_prompt_url() -> str:
    return config.settings.PROMPT_GOOGLE_DOC_URL

async def get_prompt_and_tools(prompt_url: str | None = None) -> tuple[str, list]:
    prompt_url = prompt_url or _default_prompt_url()
    if prompt_url == _default_prompt_url():
        current_time = time.time()
        if (current_time - _prompt_cache["last_updated"]) > CACHE_LIFETIME_SECONDS:
            logger.info("Кэш промпта устарел (прошло > %s сек). Обновляем...", CACHE_LIFETIME_SECONDS)
            await update_prompt_cache()
        return _prompt_cache["system_prompt"], _prompt_cache["tools"]
    content = await fetch_google_doc_content(prompt_url)
    if not content:
        logger.warning("Не удалось загрузить промпт по адресу %s. Используем дефолтный.", prompt_url)
        return await get_prompt_and_tools()
    return parse_prompt_and_tools(content)

async def update_prompt_cache(force: bool = False):
    if not force and (time.time() - _prompt_cache["last_updated"]) <= CACHE_LIFETIME_SECONDS:
        return
    logger.info("Обновление кэша промпта из Google Doc...")
    content = await fetch_google_doc_content(_default_prompt_url())
    if content:
        system_prompt, tools = parse_prompt_and_tools(content)
        _prompt_cache["system_prompt"] = system_prompt
        _prompt_cache["tools"] = tools
        _prompt_cache["last_updated"] = time.time()
        logger.info("Кэш промпта успешно обновлен.")
    else:
        logger.error("Не удалось обновить кэш промпта: документ пуст или недоступен.")
