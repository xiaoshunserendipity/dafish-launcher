"""清理本程序自己的 PyInstaller 单文件临时目录。

onefile 模式每次启动都会把 exe 解压到 `%TEMP%\\_MEIxxxxx`（本程序约 91MB），
正常退出时 PyInstaller 会自己删掉；但被强杀（`Stop-Process -Force`、任务管理器
结束进程、访问违例崩溃）时会残留，越攒越多。

策略（只处理"确认是自己、且属主已退出"的目录）：

1. 启动时在当前解压目录写一个 `.launcher_mei_owner.json` 属主标记（含 pid）；
2. 下次启动扫描 `%TEMP%\\_MEI*`，**只有带本程序标记**的目录才考虑清理；
3. 标记里的 pid 仍存活 → 说明另一个实例在用，跳过；
4. 其余删除，单次最多 12 个，失败静默跳过。

绝不触碰没有标记的目录 —— 那是别的 PyInstaller 程序的解压目录。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

MARKER = ".launcher_mei_owner.json"
OWNER = "AstrBotLauncher"


def _current_meipass() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    try:
        return Path(base)
    except Exception:
        return None


def mark_current() -> None:
    """在当前解压目录留一个属主标记，供下次启动判断。"""
    base = _current_meipass()
    if base is None:
        return
    try:
        (base / MARKER).write_text(
            json.dumps(
                {"app": OWNER, "pid": os.getpid(), "started": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.restype = ctypes.c_void_p
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return bool(ok) and exit_code.value == STILL_ACTIVE
        except Exception:
            return False
    return False


def cleanup(limit: int = 12) -> int:
    """删掉属主已退出的残留解压目录，返回清理个数。"""
    current = _current_meipass()
    try:
        candidates = sorted(Path(tempfile.gettempdir()).glob("_MEI*"))
    except Exception:
        return 0

    removed = 0
    for candidate in candidates:
        if removed >= limit:
            break
        if current is not None and candidate == current:
            continue
        try:
            marker = candidate / MARKER
            if not marker.is_file():
                continue  # 不是本程序留下的，绝对不动
            info = json.loads(marker.read_text(encoding="utf-8"))
            if info.get("app") != OWNER:
                continue
            if _pid_alive(int(info.get("pid") or 0)):
                continue  # 另一个实例还在用
            shutil.rmtree(candidate, ignore_errors=True)
            if not candidate.exists():
                removed += 1
        except Exception:
            continue
    return removed
