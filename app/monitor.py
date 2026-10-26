"""目标进程监控（独立 QThread，绝不在 UI 线程里跑 psutil）。

- 每 3 秒扫一次进程表（`psutil.process_iter(['name', 'exe'])`）；
- 匹配 monitor_targets：
    * exe：进程名相同，或可执行路径等于 / 以该路径结尾；
    * dir：可执行路径位于该目录下；
- 白名单进程（按进程名）直接跳过；
- 路径比较统一 `normcase` + `normpath`，非 Windows / 权限不足 / 路径失效全部吞异常，
  绝不让 UI 卡死。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import MonitorTarget

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(str(path))).lower()
    except Exception:
        return str(path).lower()


def _split_names(raw: str) -> set[str]:
    names: set[str] = set()
    for part in (raw or "").replace("，", ",").replace(";", ",").split(","):
        name = part.strip().lower()
        if not name:
            continue
        names.add(name)
        if name.endswith(".exe"):
            names.add(name[:-4])
        else:
            names.add(name + ".exe")
    return names


class ProcessMonitorThread(QThread):
    """周期性检查“监控目标是否在运行”。"""

    changed = Signal(bool, str)  # busy, 描述（命中的目标）

    def __init__(self, interval_seconds: float = 3.0, parent=None) -> None:
        super().__init__(parent)
        self._interval = max(1.0, float(interval_seconds))
        self._targets: list[MonitorTarget] = []
        self._whitelist: set[str] = set()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_state: bool | None = None
        self._last_desc = ""

    # ---------------- 配置 ----------------
    def set_targets(self, targets: list[MonitorTarget], whitelist_raw: str) -> None:
        with self._lock:
            self._targets = [t for t in targets if t.enabled and t.resolved_path()]
            self._whitelist = _split_names(whitelist_raw)

    # ---------------- 生命周期 ----------------
    def stop(self) -> None:
        self._stop_event.set()
        try:
            self.wait(3000)
        except Exception:
            pass

    # ---------------- 主循环 ----------------
    def run(self) -> None:  # noqa: D401
        if psutil is None:
            self.changed.emit(False, "")
            return
        while not self._stop_event.is_set():
            try:
                busy, desc = self._scan_once()
            except Exception:
                busy, desc = False, ""
            if busy != self._last_state or (busy and desc != self._last_desc):
                self._last_state = busy
                self._last_desc = desc
                self.changed.emit(busy, desc)

            # 分片等待，保证 stop() 能及时退出
            deadline = time.monotonic() + self._interval
            while time.monotonic() < deadline and not self._stop_event.is_set():
                self.msleep(100)

    # ---------------- 扫描 ----------------
    def _scan_once(self) -> tuple[bool, str]:
        with self._lock:
            targets = list(self._targets)
            whitelist = set(self._whitelist)
        if not targets:
            return False, ""

        for proc in psutil.process_iter(["name", "exe"]):  # type: ignore[union-attr]
            try:
                info = proc.info or {}
                name = (info.get("name") or "").strip()
                exe_path = (info.get("exe") or "").strip()
                if not name and not exe_path:
                    continue
                key = name.lower()
                if key in whitelist or (key.endswith(".exe") and key[:-4] in whitelist):
                    continue

                norm_exe = _norm(exe_path) if exe_path else ""
                for target in targets:
                    if self._matches(target, name, norm_exe):
                        resolved = target.resolved_path()
                        label = (
                            f"{name or Path(exe_path).name}"
                            f"（{Path(resolved).name or resolved}）"
                        )
                        return True, label
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):  # type: ignore[union-attr]
                continue
            except Exception:
                continue
        return False, ""

    @staticmethod
    def _matches(target: MonitorTarget, name: str, norm_exe: str) -> bool:
        resolved = target.resolved_path()
        if not resolved:
            return False
        target_norm = _norm(resolved)
        if target.kind == "dir":
            if not norm_exe:
                return False
            return norm_exe == target_norm or norm_exe.startswith(target_norm + os.sep)

        # exe：先按文件名比，再按完整路径比
        target_name = Path(resolved).name.lower()
        if name and name.lower() == target_name:
            return True
        if norm_exe and (
            norm_exe == target_norm or norm_exe.endswith(os.sep + target_name)
        ):
            return True
        return False
