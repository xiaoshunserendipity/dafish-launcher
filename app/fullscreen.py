"""全屏检测与避让（状态机版）。

判断前台窗口是否为“全屏/无边框全屏”：
- 用 DWM 的扩展边框（DWMWA_EXTENDED_FRAME_BOUNDS）做精确比较，
  避免窗口化全屏因为不可见边框差几个像素被漏判；
- 用 GWL_STYLE 判断是否无边框（无 WS_CAPTION / WS_THICKFRAME）；
- 用进程名做过滤：屏蔽 shell 类进程，可选“精确触发名单”；
- 带 2 次采样去抖，避免 alt-tab 瞬间抖动导致反复暂停/恢复。

对外只暴露 FullScreenDetector.changed(bool, str)，由主窗口驱动“暂停/恢复”状态机。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal

IS_WINDOWS = sys.platform.startswith("win")

# 这些是桌面外壳/输入法/任务栏，永远不算全屏
DEFAULT_BLOCKED_PROCESSES = {
    "explorer.exe",
    "searchapp.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "textinputhost.exe",
    "applicationframehost.exe",
    "widgets.exe",
    "lockapp.exe",
    "dwm.exe",
}

_SHELL_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
}

_user32 = ctypes.windll.user32 if IS_WINDOWS else None
_dwmapi = ctypes.windll.dwmapi if IS_WINDOWS else None

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
MONITOR_DEFAULTTONEAREST = 2


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    try:
        _user32.GetClassNameW(hwnd, buf, 256)
        return buf.value or ""
    except Exception:
        return ""


def _title(hwnd: int) -> str:
    try:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    try:
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def _process_name(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        import psutil

        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def _extended_bounds(hwnd: int) -> _RECT | None:
    """DWM 扩展边框，比 GetWindowRect 更接近“肉眼看到的窗口边缘”。"""
    if _dwmapi is None:
        return None
    rect = _RECT()
    try:
        result = _dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if result == 0:
            return rect
    except Exception:
        pass
    return None


def _title_blocked(title: str, blocked_titles: list[str]) -> bool:
    if not title:
        return False
    lowered = title.lower()
    for word in blocked_titles:
        token = word.strip().lower()
        if token and token in lowered:
            return True
    return False


def probe_fullscreen(
    blocked_processes: set[str] | None = None,
    allow_processes: set[str] | None = None,
    exclude_hwnds: tuple[int, ...] = (),
    blocked_titles: list[str] | None = None,
) -> tuple[bool, str, str]:
    """返回 (是否全屏, 窗口标题, 进程名)。任何异常都按“不是全屏”处理。"""
    if not IS_WINDOWS:
        return False, "", ""
    blocked = blocked_processes or DEFAULT_BLOCKED_PROCESSES
    allow = allow_processes or set()
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False, "", ""
        if exclude_hwnds and int(hwnd) in {int(h) for h in exclude_hwnds if h}:
            return False, "", ""
        if _user32.IsIconic(hwnd):
            return False, "", ""
        if _class_name(hwnd) in _SHELL_CLASSES:
            return False, "", ""

        pid = _pid_of(hwnd)
        name = _process_name(pid)
        if name and name in blocked:
            return False, _title(hwnd), name

        rect = _extended_bounds(hwnd)
        if rect is None:
            rect = _RECT()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False, "", name

        monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return False, "", name
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False, "", name

        screen = info.rcMonitor
        screen_w = max(1, screen.right - screen.left)
        screen_h = max(1, screen.bottom - screen.top)

        width = rect.right - rect.left
        height = rect.bottom - rect.top
        covers_strict = (
            width >= screen_w - 2
            and height >= screen_h - 2
            and rect.left <= screen.left + 2
            and rect.top <= screen.top + 2
        )
        covers_loose = width >= screen_w * 0.96 and height >= screen_h * 0.96

        style = _user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        borderless = not (style & WS_CAPTION) or not (style & WS_THICKFRAME)

        title = _title(hwnd)
        if _title_blocked(title, blocked_titles or []):
            return False, title, name

        # 精确触发名单：即使有标题栏，只要盖满 ≥96% 就认为在“全屏做事”
        if name and name in allow:
            return (covers_loose or covers_strict), title, name

        # 普通窗口：必须无边框 + 盖满（允许 2px 误差）
        if borderless and covers_strict:
            return True, title, name

        # 无边框但差几个像素（某些无边框全屏会留 1~4px）
        if borderless and covers_loose and not _user32.IsZoomed(hwnd):
            return True, title, name

        return False, title, name
    except Exception:
        return False, "", ""


def foreground_fullscreen(exclude_hwnds: tuple[int, ...] = ()) -> tuple[bool, str]:
    """兼容旧接口：只要 (是否全屏, 标题)。"""
    flag, title, _name = probe_fullscreen(exclude_hwnds=exclude_hwnds)
    return flag, title


class FullScreenDetector(QObject):
    """定时采样前台窗口，状态变化时发信号（带 2 次采样去抖）。"""

    changed = Signal(bool, str)  # is_fullscreen, title

    def __init__(
        self,
        parent: QObject | None = None,
        interval_ms: int = 1200,
        blocked_processes: set[str] | None = None,
        allow_processes: set[str] | None = None,
        exclude_hwnds: tuple[int, ...] = (),
        blocked_titles: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._blocked = blocked_processes or set(DEFAULT_BLOCKED_PROCESSES)
        self._allow = allow_processes or set()
        self._exclude = exclude_hwnds
        self._blocked_titles = list(blocked_titles or [])
        self._state = False
        self._streak = 0
        self._last_title = ""
        self._timer = QTimer(self)
        self._timer.setInterval(max(400, int(interval_ms)))
        self._timer.timeout.connect(self._tick)

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def configure(
        self,
        *,
        blocked_processes: set[str] | None = None,
        allow_processes: set[str] | None = None,
        exclude_hwnds: tuple[int, ...] | None = None,
        blocked_titles: list[str] | None = None,
    ) -> None:
        if blocked_processes is not None:
            self._blocked = blocked_processes
        if allow_processes is not None:
            self._allow = allow_processes
        if exclude_hwnds is not None:
            self._exclude = exclude_hwnds
        if blocked_titles is not None:
            self._blocked_titles = list(blocked_titles)

    @property
    def is_fullscreen(self) -> bool:
        return self._state

    # ---------------- 采样 ----------------
    def _tick(self) -> None:
        flag, title, _name = probe_fullscreen(
            self._blocked, self._allow, self._exclude, self._blocked_titles
        )
        if flag == self._state:
            self._streak = 0
            return
        # 进入全屏要求连续 2 次；退出只要 1 次（用户切出来应当尽快恢复）
        if flag:
            self._streak += 1
            if self._streak < 2:
                return
        self._streak = 0
        self._state = flag
        self._last_title = title if flag else self._last_title
        self.changed.emit(flag, title or self._last_title)
