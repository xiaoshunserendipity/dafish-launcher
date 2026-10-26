"""便携化路径：配置、日志、资源全部跟随 exe 所在目录。

- 打包后：`sys.executable` 所在目录
- 源码运行：项目根目录
- 资源（assets）在单文件模式下位于 `sys._MEIPASS`

便携增强：

1. **程序目录不可写时自动降级**
   放进 `C:\\Program Files`、只读 U 盘、或受控目录时，配置与日志改存
   `%LOCALAPPDATA%\\大肥鱼启动器`，避免"改了设置但存不住"。

2. **`${APP}` 变量**
   配置里的路径可以写成 `${APP}\\AstrBot\\xxx.exe`，运行时展开成 exe 所在目录。
   整个文件夹拷到别的电脑，只要相对结构不变就能直接跑。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_VAR = "${APP}"
_FALLBACK_DIR_NAME = "大肥鱼启动器"

_data_dir_cache: Path | None = None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序真实所在目录（exe 同级）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """静态资源目录：单文件模式下是解压出的临时目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent.parent


def _is_writable(directory: Path) -> bool:
    """真实写一个临时文件来判断。

    不用 `os.access`：Windows 上它对 ACL 的判断经常和实际写入结果不一致。
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".write_test_", dir=str(directory), delete=True
        ):
            return True
    except Exception:
        return False


def fallback_dir() -> Path:
    """程序目录不可写时的落点。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / _FALLBACK_DIR_NAME


def data_dir() -> Path:
    """配置与日志的落点：优先程序目录，不可写时降级到用户目录。"""
    global _data_dir_cache
    if _data_dir_cache is not None:
        return _data_dir_cache
    primary = app_dir()
    if _is_writable(primary):
        _data_dir_cache = primary
    else:
        fallback = fallback_dir()
        _data_dir_cache = fallback if _is_writable(fallback) else primary
    return _data_dir_cache


def is_portable() -> bool:
    """配置是否仍然跟着 exe 走（False 表示已降级到用户目录）。"""
    try:
        return data_dir() == app_dir()
    except Exception:
        return True


def reset_cache() -> None:
    """测试用：清掉 data_dir 缓存。"""
    global _data_dir_cache
    _data_dir_cache = None


def config_path() -> Path:
    override = os.environ.get("LAUNCHER_CONFIG")
    if override:
        return Path(override)
    return data_dir() / "config.json"


def log_dir() -> Path:
    path = data_dir() / "logs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        return data_dir()
    return path


def log_file() -> Path:
    return log_dir() / "launcher.log"


def crash_log_file() -> Path:
    return log_dir() / "crash.log"


def asset_path(name: str) -> str:
    return str(bundle_dir() / "assets" / name)


def asset_exists(name: str) -> bool:
    return Path(asset_path(name)).exists()


# ---------------- `${APP}` 变量 ----------------

def expand_vars(text: str) -> str:
    """把 `${APP}` 展开成程序目录的绝对路径。"""
    raw = (text or "").strip()
    if not raw or APP_VAR not in raw:
        return raw
    try:
        return raw.replace(APP_VAR, str(app_dir()))
    except Exception:
        return raw


def shrink_vars(text: str) -> str:
    """把位于程序目录下的绝对路径压成 `${APP}\\...` 形式。

    大小写不敏感比较（Windows 语义），但保留用户原始的大小写。
    """
    raw = (text or "").strip()
    if not raw or APP_VAR in raw:
        return raw
    try:
        base = os.path.normpath(str(app_dir()))
        candidate = os.path.normpath(raw)
        if os.path.normcase(candidate) == os.path.normcase(base):
            return APP_VAR
        prefix = base + os.sep
        if os.path.normcase(candidate).startswith(os.path.normcase(prefix)):
            rest = candidate[len(prefix):]
            return APP_VAR + os.sep + rest
    except Exception:
        return raw
    return raw


def has_app_var(text: str) -> bool:
    return APP_VAR in (text or "")
