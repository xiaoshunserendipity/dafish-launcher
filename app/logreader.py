"""读取子进程 stdout/stderr 的后台线程（不阻塞 UI）。

用 Python 的 daemon 线程而不是 QThread：
- 退出时不需要 join，也不会触发 “QThread destroyed while running” 的 abort；
- 跨线程通过 QObject 的信号桥接回主线程（Qt 会自动排队）。
"""

from __future__ import annotations

import locale
import threading

from PySide6.QtCore import QObject, Signal


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    encoding = locale.getpreferredencoding(False) or "gbk"
    try:
        return raw.decode(encoding, errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace")


class LogBridge(QObject):
    """把后台线程的输出转成 Qt 信号（主线程安全）。"""

    line_ready = Signal(str, str)  # item_id, line


class LogReader(threading.Thread):
    def __init__(self, item_id: str, stream, bridge: LogBridge) -> None:
        super().__init__(name=f"log-reader-{item_id}", daemon=True)
        self._item_id = item_id
        self._stream = stream
        self._bridge = bridge
        self._closed = False

    def run(self) -> None:  # noqa: D401
        stream = self._stream
        if stream is None:
            return
        while not self._closed:
            try:
                raw = stream.readline()
            except Exception:
                break
            if not raw:
                break
            text = _decode(raw).rstrip("\r\n")
            if text:
                try:
                    self._bridge.line_ready.emit(self._item_id, text)
                except RuntimeError:
                    # 窗口已销毁，安静退出
                    break

    def stop(self) -> None:
        """停止读取。

        **不能**在读线程可能阻塞于 `readline()` 时调用 `stream.close()`：
        `close()` 要拿文件对象的锁，而阻塞中的 `readline()` 正持有它 ——
        两边互相等待，界面直接卡死。

        正确顺序是：先结束子进程（管道写端关闭 → `readline()` 返回 EOF），
        等读线程自己退出后再关管道。
        """
        self._closed = True
        try:
            self.join(timeout=0.3)
        except Exception:
            pass
        if self.is_alive():
            # 进程还没结束、读线程仍阻塞：宁可让 GC 回收，也不能冒险 close
            return
        try:
            self._stream.close()
        except Exception:
            pass
