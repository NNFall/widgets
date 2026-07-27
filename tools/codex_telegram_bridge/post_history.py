from __future__ import annotations

import json
from pathlib import Path


class PublishedPostHistory:
    def __init__(self, path: Path) -> None:
        self.path = path

    def latest_message_ids(self) -> tuple[int, ...] | None:
        latest: tuple[int, ...] | None = None
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("record_type") != "post":
                continue
            raw_ids = record.get("telegram_message_ids")
            if raw_ids is None:
                raw_ids = [record.get("telegram_message_id")]
            if not isinstance(raw_ids, list) or not raw_ids:
                continue
            if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in raw_ids):
                continue
            latest = tuple(raw_ids)
        return latest
