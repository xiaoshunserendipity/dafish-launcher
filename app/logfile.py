"""按大小轮转的日志写入器。

关键点：**轮转前必须先关闭旧句柄**。

原实现的顺序是"先改名、再判断句柄是否为 None"：改名之后 `path.exists()` 变 False，
轮转分支再也不会触发，而旧句柄仍然指向被改名的那份文件 —— 结果是
`launcher.log` 永久停更，所有日志无限写进 `.log.old`，诊断包里还恰好漏掉它。

轮转后保留 `launcher.1.log` / `launcher.2.log` / `launcher.3.log` 三份。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_BACKUPS = 3


class RotatingLogWriter:
    """线程安全的按大小轮转写入器。"""

    def __init__(
        self,
        path_getter: Callable[[], Path],
        max_bytes: int = DEFAULT_MAX_BYTES,
        backups: int = DEFAULT_BACKUPS,
    ) -> None:
        self._path_getter = path_getter
        self._max_bytes = max(64 * 1024, int(max_bytes))
        self._backups = max(1, int(backups))
        self._handle = None
        self._lock = threading.Lock()

    # ---------------- 内部 ----------------
    def _close_locked(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        except Exception:
            pass
        self._handle = None

    def _rotate_locked(self, path: Path) -> None:
        self._close_locked()  # 先关句柄，否则改名后写入仍落到旧文件
        try:
            for index in range(self._backups, 1, -1):
                older = path.with_name(f"{path.stem}.{index - 1}{path.suffix}")
                newer = path.with_name(f"{path.stem}.{index}{path.suffix}")
                if older.exists():
                    older.replace(newer)
            if path.exists():
                path.replace(path.with_name(f"{path.stem}.1{path.suffix}"))
        except Exception:
            pass

    # ---------------- 对外 ----------------
    def write(self, line: str) -> None:
        with self._lock:
            try:
                path = self._path_getter()
                try:
                    if path.exists() and path.stat().st_size > self._max_bytes:
                        self._rotate_locked(path)
                except Exception:
                    pass
                if self._handle is None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._handle = path.open("a", encoding="utf-8")
                self._handle.write(line + "\n")
                self._handle.flush()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def path(self) -> Path:
        return self._path_getter()
