import os
import logging
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

class OperationMode(Enum):
    ALL = "all"
    EXISTING = "existing"
    NONE = "none"

class AppSettings:
    def __init__(self):
        # --- API Ключи (неизменяемые) ---
        self.VSEGPT_API_KEY = os.getenv("VSEGPT_API_KEY")
        self.TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
        self.TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
        
        # --- Настройки Admin Bot'а (неизменяемые) ---
        self.ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
        self.ADMIN_CHANNEL_ID = os.getenv("ADMIN_CHANNEL_ID")

        # --- Настройки сессии Telethon (неизменяемые) ---
        self.SESSION_NAME = "my_ai_session"

        # --- Настройки базы данных истории сообщений ---
        self.DATABASE_URL = os.getenv("MESSAGE_DATABASE_URL", "dialogs.db")

        # --- Настройки промптов ---
        self.PROMPT_GOOGLE_DOC_URL = os.getenv("PROMPT_GOOGLE_DOC_URL")
        self.SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "credentials.json")
        self.DEFAULT_SYSTEM_PROMPT = os.getenv(
            "DEFAULT_SYSTEM_PROMPT",
            "Ты полезный AI-ассистент. Отвечай кратко, понятно и по делу.",
        )

        # --- Настройки админки ---
        self.ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
        self.ADMIN_EMAILS = {
            email.strip().lower()
            for email in os.getenv("ADMIN_EMAILS", "").split(",")
            if email.strip()
        }

        # --- НАСТРОЙКИ НЕЙРОСЕТИ (ИЗМЕНЯЕМЫЕ) ---
        # Эти значения будут использоваться как стартовые и могут быть изменены "на лету"
        self.default_model = "google/gemini-2.5-flash"
        self.default_temperature = 0.5
        self.default_max_tokens = 10000
        self.default_stt_model = "stt-openai/whisper-v3-turbo"
        self.operation_mode = OperationMode.ALL

settings = AppSettings()

def set_default_model(new_model: str):
    logging.info(f"Смена модели по умолчанию с '{settings.default_model}' на '{new_model}'")
    settings.default_model = new_model

def set_operation_mode(mode_str: str) -> bool:
    try:
        new_mode = OperationMode(mode_str.lower())
        logging.info(f"Смена режима работы с '{settings.operation_mode.value}' на '{new_mode.value}'")
        settings.operation_mode = new_mode
        return True
    except ValueError:
        logging.warning(f"Попытка установить некорректный режим работы: {mode_str}")
        return False

def get_operation_mode_value() -> str:
    return settings.operation_mode.value
