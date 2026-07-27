import sqlite3
from pathlib import Path

from tools.codex_telegram_bridge.store import BridgeStore


THREAD_ID = "019f9e1b-fb04-7482-b62d-cee4c051131b"


def test_queue_is_fifo_and_deduplicates_telegram_updates(tmp_path: Path) -> None:
    store = BridgeStore(tmp_path / "bridge.sqlite3")

    first = store.enqueue(10, 20, 30, THREAD_ID, "first")
    duplicate = store.enqueue(10, 20, 30, THREAD_ID, "duplicate")
    second = store.enqueue(11, 20, 30, THREAD_ID, "second")

    assert first is True
    assert duplicate is False
    assert second is True
    assert store.claim_next().prompt == "first"
    assert store.claim_next().prompt == "second"
    assert store.claim_next() is None


def test_delivery_is_not_rerun_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "bridge.sqlite3"
    store = BridgeStore(database)
    store.enqueue(10, 20, 30, THREAD_ID, "hello")
    job = store.claim_next()
    store.mark_for_delivery(job.id, "final answer")
    store.close()

    reopened = BridgeStore(database)
    reopened.recover_interrupted()

    assert reopened.claim_next() is None
    delivery = reopened.next_delivery()
    assert delivery.response == "final answer"
    assert delivery.delivery_cursor == 0
    reopened.mark_chunk_delivered(delivery.id, 1)
    assert reopened.next_delivery().delivery_cursor == 1
    reopened.mark_done(delivery.id)
    assert reopened.next_delivery() is None

    with sqlite3.connect(database) as connection:
        prompt, response = connection.execute(
            "SELECT prompt, response FROM jobs WHERE id = ?", (delivery.id,)
        ).fetchone()
    assert prompt == ""
    assert response is None


def test_running_job_returns_to_pending_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "bridge.sqlite3"
    store = BridgeStore(database)
    store.enqueue(10, 20, 30, THREAD_ID, "hello")
    running = store.claim_next()
    store.close()

    reopened = BridgeStore(database)
    assert reopened.recover_interrupted() == 1
    recovered = reopened.claim_next()

    assert recovered.id == running.id
    assert recovered.prompt == "hello"


def test_bindings_and_update_offset_are_persistent(tmp_path: Path) -> None:
    database = tmp_path / "bridge.sqlite3"
    store = BridgeStore(database)
    assert store.ensure_binding(20, THREAD_ID) is True
    assert store.ensure_binding(20, "11111111-1111-1111-1111-111111111111") is False
    store.set_binding(20, "22222222-2222-2222-2222-222222222222")
    store.set_last_update_id(456)
    store.close()

    reopened = BridgeStore(database)
    assert reopened.get_binding(20) == "22222222-2222-2222-2222-222222222222"
    assert reopened.get_last_update_id() == 456


def test_old_completed_jobs_and_processed_updates_are_pruned(tmp_path: Path) -> None:
    database = tmp_path / "bridge.sqlite3"
    store = BridgeStore(database)
    store.enqueue(10, 20, 30, THREAD_ID, "private prompt")
    job = store.claim_next()
    store.mark_failed(job.id, "safe error")
    store.mark_update_processed(11)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE jobs SET updated_at = datetime('now', '-31 days') WHERE id = ?",
            (job.id,),
        )
        connection.execute(
            "UPDATE processed_updates SET processed_at = datetime('now', '-31 days')"
        )

    assert store.prune_history(30) == (1, 1)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM processed_updates").fetchone()[0] == 0
