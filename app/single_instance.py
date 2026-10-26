"""单实例锁（QLocalSocket 握手 + 独占文件锁 + QLocalServer 唤醒）。

为什么不用 QSharedMemory：
- 程序被 taskkill / 崩溃后，共享内存段可能残留，形成“幽灵锁”，
  下次启动直接判定“已有一个实例”，结果谁都打不开。

为什么不能只靠 QLocalServer::listen 判重：
- 实测（Qt 6.11 / Windows 11）同名管道允许多个 QLocalServer 同时 listen，
  第二个进程的 listen() 也会返回 True，拦不住多开。

所以这里分三层：
1. **独占文件锁**（Windows CreateFileW，dwShareMode=0）作为真正的互斥量；
   进程退出/被杀时句柄由系统回收，不会产生幽灵锁。锁文件放在 %TEMP%，
   不污染配置目录，换电脑/换 U 盘也能生效（每台机器一个实例）。
2. **QLocalSocket 握手**：第二个实例连上第一个的 QLocalServer，发 "SHOW" 唤醒它。
3. **QLocalServer**：只负责接收唤醒消息（QLocalServer + QLocalSocket 负责通信）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

DEFAULT_SERVER_NAME = "AstrLauncher_Single_Instance_v3"
DEFAULT_LOCK_FILE = "AstrLauncher_v3.lock"


def _server_name() -> str:
    return os.environ.get("LAUNCHER_SINGLE_NAME") or DEFAULT_SERVER_NAME


def _lock_name() -> str:
    return os.environ.get("LAUNCHER_SINGLE_LOCK") or DEFAULT_LOCK_FILE
WAKE_MESSAGE = b"SHOW\n"

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    # use_last_error=True 才能用 ctypes.get_last_error() 取错误码
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_HIDDEN = 0x2
    ERROR_SHARING_VIOLATION = 32
    ERROR_ACCESS_DENIED = 5

    # 必须显式声明原型：ctypes 默认 restype 是 32 位 c_int，
    # 失败时返回 -1，与 64 位的 INVALID_HANDLE_VALUE 比较永远为假，
    # 会导致"锁没拿到却判定成功"，进而 removeServer() 拆掉真主实例的管道。
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class _ExclusiveLock:
    """Windows 独占文件锁；非 Windows 用 fcntl。"""

    def __init__(self, name: str | None = None) -> None:
        self.path = Path(tempfile.gettempdir()) / (name or _lock_name())
        self._handle = None
        self._fd = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            handle = _kernel32.CreateFileW(
                str(self.path),
                GENERIC_READ | GENERIC_WRITE,
                0,  # 不共享：第二个进程会失败
                None,
                OPEN_ALWAYS,
                FILE_ATTRIBUTE_HIDDEN,
                None,
            )
            if not handle or handle == INVALID_HANDLE_VALUE:
                # 被占用 / 权限不足，都算没拿到
                return False
            self._handle = handle
            return True
        try:
            import fcntl

            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except Exception:
            return False

    def release(self) -> None:
        if IS_WINDOWS:
            if self._handle:
                try:
                    _kernel32.CloseHandle(self._handle)
                except Exception:
                    pass
                self._handle = None
            return
        if self._fd is not None:
            try:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None


class SingleInstance(QObject):
    show_requested = Signal()

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or _server_name()
        self._lock = _ExclusiveLock(_lock_name())
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

    # ---------------- 启动 ----------------
    def try_start(self) -> bool:
        """成为主实例返回 True；已有实例时发送唤醒消息并返回 False。"""
        # 1) 已有实例在监听 → 直接唤醒并退出
        if self._notify_existing(timeout_ms=500):
            return False

        # 2) 尝试拿独占锁（真正的互斥）
        if not self._lock.acquire():
            # 拿不到锁说明有进程持有，但管道可能还没建好，稍等再试
            for _ in range(10):
                time.sleep(0.1)
                if self._notify_existing(timeout_ms=300):
                    return False
            # 依旧连不上：保守处理，认为已有实例，避免多开
            return False

        # 3) 成为主实例：清理可能残留的管道名后监听
        QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def _notify_existing(self, timeout_ms: int = 600) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(timeout_ms):
            socket.abort()
            return False
        socket.write(WAKE_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return True

    # ---------------- 服务端 ----------------
    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            # 不要在 newConnection 里立刻 readAll：数据通常还没到。
            connection.readyRead.connect(lambda c=connection: self._read(c))
            connection.disconnected.connect(connection.deleteLater)

    def _read(self, connection: QLocalSocket) -> None:
        try:
            data = bytes(connection.readAll()).strip()
        except Exception:
            data = b""
        if not data:
            return
        if b"SHOW" in data:
            self.show_requested.emit()
            try:
                connection.disconnectFromServer()
            except Exception:
                pass

    def close(self) -> None:
        try:
            self._server.close()
            QLocalServer.removeServer(self._name)
        except Exception:
            pass
        self._lock.release()
