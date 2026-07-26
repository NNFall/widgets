import json
from pathlib import Path

from tools.codex_telegram_bridge.post_history import PublishedPostHistory


def _write_jsonl(path: Path, records: list[object]) -> None:
    path.write_text(
        "\n".join(
            record if isinstance(record, str) else json.dumps(record, ensure_ascii=False)
            for record in records
        ),
        encoding="utf-8",
    )


def test_latest_message_ids_reads_last_published_post(tmp_path: Path) -> None:
    path = tmp_path / "published.jsonl"
    _write_jsonl(
        path,
        [
            {"record_type": "registry", "published_count": 2},
            {"record_type": "post", "telegram_message_id": 3},
            {"record_type": "post", "telegram_message_id": 4},
        ],
    )

    assert PublishedPostHistory(path).latest_message_ids() == (4,)


def test_latest_message_ids_preserves_album_order(tmp_path: Path) -> None:
    path = tmp_path / "published.jsonl"
    _write_jsonl(
        path,
        [{"record_type": "post", "telegram_message_ids": [11, 12, 13]}],
    )

    assert PublishedPostHistory(path).latest_message_ids() == (11, 12, 13)


def test_latest_message_ids_skips_broken_and_invalid_records(tmp_path: Path) -> None:
    path = tmp_path / "published.jsonl"
    _write_jsonl(
        path,
        [
            {"record_type": "post", "telegram_message_id": 7},
            "not-json",
            {"record_type": "post", "telegram_message_ids": [8, "bad"]},
            {"record_type": "note", "telegram_message_id": 9},
        ],
    )

    assert PublishedPostHistory(path).latest_message_ids() == (7,)


def test_latest_message_ids_returns_none_for_missing_or_empty_history(tmp_path: Path) -> None:
    assert PublishedPostHistory(tmp_path / "missing.jsonl").latest_message_ids() is None
    path = tmp_path / "published.jsonl"
    path.write_text("", encoding="utf-8")
    assert PublishedPostHistory(path).latest_message_ids() is None
