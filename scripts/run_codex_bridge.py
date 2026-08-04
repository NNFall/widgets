from __future__ import annotations

import asyncio
import os
import signal
import stat
from pathlib import Path

from aiohttp import web

from tools.kaigo_codex_bridge.config import CodexBridgeConfig
from tools.kaigo_codex_bridge.runner import CodexRunner
from tools.kaigo_codex_bridge.service import create_app
from tools.kaigo_codex_bridge.state import BridgeStateStore


async def run_service() -> None:
    config = CodexBridgeConfig.from_env()
    _prepare_socket_parent(config.socket_path, gid=config.socket_gid)
    _remove_stale_socket(config.socket_path)
    state = BridgeStateStore(config.state_root)
    codex_runner = CodexRunner(config=config, state=state)
    application = create_app(config=config, runner=codex_runner)
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    site = web.UnixSite(runner, str(config.socket_path))
    await site.start()
    os.chmod(config.socket_path, 0o660)
    os.chown(config.socket_path, -1, config.socket_gid)

    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for watched_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(watched_signal, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        _remove_stale_socket(config.socket_path)


def _prepare_socket_parent(socket_path: Path, *, gid: int) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(socket_path.parent, 0o770)
    os.chown(socket_path.parent, -1, gid)


def _remove_stale_socket(socket_path: Path) -> None:
    try:
        mode = socket_path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"refusing to remove non-socket path: {socket_path}")
    socket_path.unlink()


def main() -> None:
    asyncio.run(run_service())


if __name__ == "__main__":
    main()
