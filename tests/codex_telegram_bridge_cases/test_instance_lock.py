from pathlib import Path

import pytest

from tools.codex_telegram_bridge.instance_lock import AlreadyRunningError, SingleInstanceLock


def test_only_one_bridge_instance_can_hold_the_same_data_directory(tmp_path: Path) -> None:
    first = SingleInstanceLock(tmp_path)
    second = SingleInstanceLock(tmp_path)
    assert first.name.startswith("Global\\")

    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
