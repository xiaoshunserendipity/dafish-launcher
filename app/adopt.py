"""启动时接管检测：找出已经在运行的条目进程。

匹配优先级：
1. 可执行文件完整路径相同（exe）；
2. 进程命令行里包含条目的目标路径（.bat/.cmd 的 `cmd /c xxx.bat`）；
3. 进程名等于目标文件名（同名不同路径的兜底，可能误判，放在最后）；
4. 条目配置了 ready_port 且端口正在监听 → 取占用端口的进程，
   再用 1/2 校验，避免把别的服务当成自己的。

只读扫描，不启动、不终止任何东西；是否接管由调用方决定。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from .config import LaunchItem


@dataclass
class RunningMatch:
    pid: int
    name: str
    how: str  # path / cmdline / name / port


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.normpath(str(path))).lower()
    except Exception:
        return str(path).lower()


def _iter_processes():
    if psutil is None:
        return
    for proc in psutil.process_iter(["name", "exe", "cmdline"]):
        yield proc


def _cmdline_contains(proc, needle: str) -> bool:
    try:
        cmdline = proc.info.get("cmdline") or []
    except Exception:
        return False
    if not cmdline:
        return False
    joined = " ".join(str(part) for part in cmdline)
    return needle in joined.lower()


def _port_owner_pid(port: int) -> int | None:
    if psutil is None or port <= 0:
        return None
    try:
        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.status == psutil.CONN_LISTEN
                and conn.laddr
                and conn.laddr.port == int(port)
                and conn.pid
            ):
                return int(conn.pid)
    except Exception:
        return None
    return None


def _verify_pid(pid: int, item: LaunchItem) -> RunningMatch | None:
    if psutil is None:
        return None
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        exe = ""
        try:
            exe = proc.exe() or ""
        except Exception:
            exe = ""
    except Exception:
        return None

    target_path = item.resolved_path()
    target_norm = _norm(target_path) if target_path else ""
    target_name = Path(target_path).name.lower() if target_path else ""

    if exe and target_norm and _norm(exe) == target_norm:
        return RunningMatch(pid, name, "path")
    if target_path and _cmdline_contains(proc, target_norm):
        return RunningMatch(pid, name, "cmdline")
    if target_name and name.lower() == target_name:
        return RunningMatch(pid, name, "name")
    return None


def detect_running(item: LaunchItem) -> RunningMatch | None:
    """返回第一个匹配到的运行中进程（只读）。"""
    if psutil is None or not item.resolved_path():
        return None

    target_path = item.resolved_path()
    target_norm = _norm(target_path)
    target_name = Path(target_path).name.lower()

    # 1/2：按路径或命令行精确匹配
    for proc in _iter_processes():
        try:
            info = proc.info or {}
            exe = info.get("exe") or ""
            name = (info.get("name") or "").lower()
            if exe and _norm(exe) == target_norm:
                return RunningMatch(proc.pid, info.get("name") or "", "path")
            if _cmdline_contains(proc, target_norm):
                return RunningMatch(proc.pid, info.get("name") or "", "cmdline")
        except Exception:
            continue

    # 3：端口监听 → 校验占用者
    if item.ready_port > 0:
        pid = _port_owner_pid(item.ready_port)
        if pid is not None:
            match = _verify_pid(pid, item)
            if match is not None:
                match.how = "port"
                return match

    # 4：最后才按文件名兜底
    if target_name:
        for proc in _iter_processes():
            try:
                info = proc.info or {}
                if (info.get("name") or "").lower() == target_name:
                    return RunningMatch(proc.pid, info.get("name") or "", "name")
            except Exception:
                continue
    return None
