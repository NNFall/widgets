from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    """Raised when another bridge process already owns the same data directory."""


class SingleInstanceLock:
    _ERROR_ALREADY_EXISTS = 183

    def __init__(self, data_dir: str | Path) -> None:
        normalized = str(Path(data_dir).resolve()).casefold().encode("utf-8")
        digest = hashlib.sha256(normalized).hexdigest()[:24]
        self.name = f"Global\\KaigoCodexTelegramBridge-{digest}"
        self._handle: int | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        if os.name != "nt":
            raise OSError("The Telegram Codex bridge instance lock requires Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        create_mutex.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_bool
        ctypes.set_last_error(0)
        handle = create_mutex(None, False, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == self._ERROR_ALREADY_EXISTS:
            close_handle(handle)
            raise AlreadyRunningError(
                "Another Telegram Codex bridge process already uses this data directory"
            )
        self._handle = int(handle)

    def release(self) -> None:
        if self._handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_bool
        handle = self._handle
        self._handle = None
        if not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
