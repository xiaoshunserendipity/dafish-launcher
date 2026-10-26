"""崩溃与异常落盘。

`--noconsole` 打包后 `sys.stderr` 是 None，任何槽函数里的异常都会写进黑洞，
用户只看到"点了没反应"，事后完全无法排查。

这里做三件事：

1. `sys.excepthook` / `threading.excepthook`：捕获未处理异常，写 `logs/crash.log`
2. `faulthandler`：硬崩溃（访问违例 / 0xC0000409）也会留下 Python 栈
3. `qInstallMessageHandler`：把 Qt 自身的警告（QSS 解析失败、QThread 未停止等）
   一并收进同一个文件
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import traceback

from .paths import crash_log_file

_lock = threading.Lock()
_fault_handle = None
_installed = False

# QtMsgType：0=Debug 1=Warning 2=Critical 3=Fatal 4=Info
_QT_LOG_THRESHOLD = 1


def _header(kind: str) -> str:
    try:
        from . import __version__
    except Exception:  # pragma: no cover - 极端兜底
        __version__ = "?"
    return (
        f"\n{'=' * 72}\n"
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {kind}\n"
        f"version={__version__} pid={os.getpid()} "
        f"python={sys.version.split()[0]} platform={sys.platform} "
        f"frozen={getattr(sys, 'frozen', False)}\n"
        f"{'-' * 72}\n"
    )


def _write(text: str) -> None:
    try:
        path = crash_log_file()
        with _lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text.rstrip("\n") + "\n")
    except Exception:
        pass


def log_exception(exc_type, exc_value, exc_tb, *, source: str = "main") -> None:
    if exc_type is None or exc_type is KeyboardInterrupt:
        return
    detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _write(_header(f"未捕获异常（{source}）") + detail)


def log_message(text: str, *, source: str = "note") -> None:
    if not text:
        return
    _write(_header(source) + text)


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    log_exception(exc_type, exc_value, exc_tb, source="main")
    try:
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    except Exception:
        pass


def _thread_excepthook(args) -> None:
    if args.exc_type is SystemExit:
        return
    name = getattr(args.thread, "name", "?") if args.thread is not None else "?"
    log_exception(
        args.exc_type, args.exc_value, args.exc_traceback, source=f"thread:{name}"
    )


def _qt_message_handler(mode, context, message) -> None:
    try:
        if int(mode) < _QT_LOG_THRESHOLD:
            return
        log_message(str(message), source="qt")
    except Exception:
        pass


def install() -> None:
    """安装全局钩子；重复调用无副作用。"""
    global _fault_handle, _installed
    if _installed:
        return
    _installed = True

    sys.excepthook = _excepthook
    try:
        threading.excepthook = _thread_excepthook
    except Exception:
        pass

    # 硬崩溃也要留下栈：句柄必须一直开着，所以存在模块级变量里
    try:
        path = crash_log_file()
        _fault_handle = path.open("a", encoding="utf-8")
        _fault_handle.write(_header("faulthandler 已启用"))
        _fault_handle.flush()
        faulthandler.enable(file=_fault_handle, all_threads=True)
    except Exception:
        _fault_handle = None

    try:
        from PySide6.QtCore import qInstallMessageHandler

        qInstallMessageHandler(_qt_message_handler)
    except Exception:
        pass
