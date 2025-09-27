from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class AppConfig:
    database_url: str
    host: str = '0.0.0.0'
    port: int = 8080


def load_config() -> AppConfig:
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL environment variable is required')
    host = os.getenv('APP_HOST', '0.0.0.0')
    port = int(os.getenv('APP_PORT', '8080'))
    return AppConfig(database_url=database_url, host=host, port=port)
