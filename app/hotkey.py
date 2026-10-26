"""全局热键（Windows RegisterHotKey）。

Qt 本身没有全局热键能力，这里直接用 Win32 `RegisterHotKey` 注册，
再通过 `QAbstractNativeEventFilter` 捕获 `WM_HOTKEY` 消息。

注意：
  - 热键是**系统级独占**的，如果被别的程序占用，注册会失败 —— 调用方要处理返回值；
  - 只在 Windows 上可用，其它平台 `register()` 直接返回 False。
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

IS_WINDOWS = sys.platform.startswith("win")

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_S = 0x53
VK_X = 0x58

HOTKEY_START_ALL = 1
HOTKEY_STOP_ALL = 2

VK_Q = 0x51
VK_F9 = 0x78
VK_F10 = 0x79

# 候选组合：启动 / 停止各自独立挑选，这样支持"一个自定义、一个用默认"的混搭
# （热键是系统独占的，Ctrl+Alt+X/Q/E/Z 这类常被输入法或其它工具占走）
START_CANDIDATES: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Ctrl+Alt+S", (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_S)),
    ("Ctrl+Shift+S", (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_S)),
    ("Ctrl+Alt+F9", (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_F9)),
)

STOP_CANDIDATES: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Ctrl+Alt+X", (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_X)),
    ("Ctrl+Alt+F10", (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_F10)),
    ("Ctrl+Shift+Q", (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_Q)),
    ("Ctrl+Shift+X", (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_X)),
)

DEFAULT_START_ALL = START_CANDIDATES[0][1]
DEFAULT_STOP_ALL = STOP_CANDIDATES[0][1]

_PROBE_ID = 99   # 只用于探测某个组合是否可用

_MOD_TOKENS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
    "cmd": MOD_WIN,
}

_KEY_TOKENS = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "insert": 0x2D,
    "delete": 0x2E,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """把 "ctrl+alt+s" 解析成 (modifiers, vk)；不合法返回 None。

    必须带修饰键 —— 否则会抢走普通按键，属于危险配置。
    """
    if not text:
        return None
    parts = [p for p in re.split(r"[\s+]+", text.strip().lower()) if p]
    if not parts:
        return None
    mods = 0
    key: int | None = None
    for part in parts:
        if part in _MOD_TOKENS:
            mods |= _MOD_TOKENS[part]
            continue
        if part in _KEY_TOKENS:
            key = _KEY_TOKENS[part]
            continue
        if len(part) == 1 and (part.isalpha() or part.isdigit()):
            key = ord(part.upper())
            continue
        match = re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part)
        if match:
            key = 0x6F + int(match.group(1))
            continue
        return None
    if key is None or mods == 0:
        return None
    return mods | MOD_NOREPEAT, key


def _vk_name(vk: int) -> str:
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    for name, code in _KEY_TOKENS.items():
        if code == vk and len(name) > 1:
            return name.capitalize()
    return f"0x{vk:02X}"


def format_hotkey(combo: tuple[int, int]) -> str:
    mods, vk = combo
    names: list[str] = []
    if mods & MOD_CONTROL:
        names.append("Ctrl")
    if mods & MOD_ALT:
        names.append("Alt")
    if mods & MOD_SHIFT:
        names.append("Shift")
    if mods & MOD_WIN:
        names.append("Win")
    names.append(_vk_name(vk))
    return "+".join(names)


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.RegisterHotKey.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, eventType, message):  # noqa: N802
        try:
            if bytes(eventType) != b"windows_generic_MSG":
                return False
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            if msg.message == WM_HOTKEY:
                self._callback(int(msg.wParam))
        except Exception:
            pass
        return False


class GlobalHotkeys(QObject):
    """Ctrl+Alt+S 全部启动、Ctrl+Alt+X 全部停止。"""

    triggered = Signal(str)   # "start_all" / "stop_all"

    def __init__(self, app, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._filter = _HotkeyFilter(self._on_hotkey)
        self._installed = False
        self._registered = False

    # ---------------- 内部 ----------------
    def _on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_START_ALL:
            self.triggered.emit("start_all")
        elif hotkey_id == HOTKEY_STOP_ALL:
            self.triggered.emit("stop_all")

    # ---------------- 对外 ----------------
    def register(self, start=None, stop=None) -> bool:
        """注册热键。

        `start` / `stop` 各自独立：传了就用指定的组合，没传就从对应候选里挑
        第一个没被占用的 —— 所以支持"启动自定义、停止用默认"这种混搭。
        """
        if not IS_WINDOWS:
            return False
        if self._registered:
            return True

        if start is not None:
            start_combo, start_label = start, format_hotkey(start)
        else:
            picked = self._first_available(START_CANDIDATES)
            if picked is None:
                return False
            start_label, start_combo = picked

        if stop is not None:
            stop_combo, stop_label = stop, format_hotkey(stop)
        else:
            picked = self._first_available(STOP_CANDIDATES, exclude={start_combo})
            if picked is None:
                return False
            stop_label, stop_combo = picked

        if not self._try_pair(start_combo, stop_combo):
            return False
        self._label = f"{start_label} / {stop_label}"
        return True

    @staticmethod
    def _first_available(candidates, exclude=()):
        """探测候选里第一个没被占用的组合（注册后立刻注销，只为试探）。"""
        for label, combo in candidates:
            if combo in exclude:
                continue
            try:
                ok = _user32.RegisterHotKey(None, _PROBE_ID, *combo)
            except Exception:
                continue
            if ok:
                _user32.UnregisterHotKey(None, _PROBE_ID)
                return label, combo
        return None

    def _try_pair(self, start_combo: tuple[int, int], stop_combo: tuple[int, int]) -> bool:
        try:
            ok_start = _user32.RegisterHotKey(None, HOTKEY_START_ALL, *start_combo)
            ok_stop = _user32.RegisterHotKey(None, HOTKEY_STOP_ALL, *stop_combo)
        except Exception:
            return False
        if not (ok_start and ok_stop):
            if ok_start:
                _user32.UnregisterHotKey(None, HOTKEY_START_ALL)
            if ok_stop:
                _user32.UnregisterHotKey(None, HOTKEY_STOP_ALL)
            return False
        try:
            if not self._installed:
                self._app.installNativeEventFilter(self._filter)
                self._installed = True
        except Exception:
            _user32.UnregisterHotKey(None, HOTKEY_START_ALL)
            _user32.UnregisterHotKey(None, HOTKEY_STOP_ALL)
            return False
        self._registered = True
        return True

    def unregister(self) -> None:
        if not IS_WINDOWS or not self._registered:
            return
        try:
            _user32.UnregisterHotKey(None, HOTKEY_START_ALL)
            _user32.UnregisterHotKey(None, HOTKEY_STOP_ALL)
        except Exception:
            pass
        self._registered = False
        self._label = ""

    @property
    def is_registered(self) -> bool:
        return self._registered

    def describe(self) -> str:
        """返回实际生效的组合描述；未注册时返回空。"""
        return getattr(self, "_label", "") or ""

    @staticmethod
    def hint_text() -> str:
        return "（按优先级尝试，被占用会自动换下一组）"
