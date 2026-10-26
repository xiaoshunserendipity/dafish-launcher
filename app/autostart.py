"""开机自启：只写 HKCU\\...\\Run，路径用当前 exe 的真实位置，不写死。"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg
except Exception:  # 非 Windows 环境
    winreg = None  # type: ignore

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "大肥鱼启动器"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}" --minimized'
    # 源码运行：用 pythonw 避免黑框
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(__file__).resolve().parent.parent / "launcher.py"
    return f'"{interpreter}" "{script}" --minimized'


def is_autostart_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart(enabled: bool) -> tuple[bool, str]:
    if winreg is None:
        return False, "当前系统不支持注册表自启（非 Windows）。"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
                return True, "已设置开机自启（当前用户）。"
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
            return True, "已关闭开机自启。"
    except PermissionError:
        return False, "没有权限写入注册表（可能被安全软件拦截）。"
    except Exception as exc:
        return False, f"设置开机自启失败：{exc}"
