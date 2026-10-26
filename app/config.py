"""配置与数据模型：所有可持久化内容都在这里。

设计约束：
- 启动器启动时**绝对不会**因为配置里的路径而启动任何程序；
- 只持久化条目列表、条目设置与少量偏好，开关状态永远不落盘；
- 配置固定写在 exe 同目录（便携 / 绿色软件）。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import app_dir, config_path, expand_vars, has_app_var, shrink_vars
from .weblink import normalize_url

CONFIG_NAME = "config.json"
CONFIG_VERSION = 6

WINDOW_NORMAL = "normal"
WINDOW_MINIMIZED = "minimized"
WINDOW_HIDDEN = "hidden"
WINDOW_MODES = (WINDOW_NORMAL, WINDOW_MINIMIZED, WINDOW_HIDDEN)
WINDOW_LABELS = {
    WINDOW_NORMAL: "正常窗口",
    WINDOW_MINIMIZED: "最小化到任务栏",
    WINDOW_HIDDEN: "后台运行（不弹窗）",
}

# 批处理类目标：默认让它们用自己的控制台显示输出，而不是重定向到日志面板
SCRIPT_SUFFIXES = (".bat", ".cmd")

DEFAULT_ENTRIES = (
    ("AstrBot 桌面版", "🤖"),
    ("SnowLuma（QQ 桥）", "🐋"),
    ("GSVI 语音适配器", "🎙️"),
)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def migrate_payload(data: dict[str, Any]) -> dict[str, Any]:
    """把旧版本配置补齐到当前版本；只补字段、不改用户数据。"""
    if not isinstance(data, dict):
        return {"version": CONFIG_VERSION, "items": [], "settings": {}, "targets": []}
    version = _as_int(data.get("version"), 1, 1, 999)
    if version < 5:
        # v3.4.x 的 capture_output 与 window 合并成三态 window：
        # capture_output=True（捕获到日志面板）等价于现在的「后台运行」
        for entry in data.get("items") or []:
            if isinstance(entry, dict):
                if entry.pop("capture_output", None) is True:
                    entry["window"] = WINDOW_HIDDEN
    if version < 4:
        data.setdefault("targets", [])
        settings = data.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        settings.setdefault("adopt_on_startup", True)
        data["settings"] = settings
        for entry in data.get("items") or []:
            if isinstance(entry, dict):
                entry.setdefault("allow_adopt", True)
    if version < 3:
        data.setdefault("targets", [])
        settings = data.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        settings.setdefault("theme", "auto")
        settings.setdefault("window_geometry", "")
        data["settings"] = settings
        for entry in data.get("items") or []:
            if isinstance(entry, dict):
                entry.setdefault("avoid_fullscreen", True)
                entry.setdefault("avoid_process", True)
                entry.setdefault("ready_port", 0)
                entry.setdefault("ready_timeout", 60)
                entry.setdefault("group", "")
    data["version"] = CONFIG_VERSION
    return data


@dataclass
class LaunchItem:
    name: str
    path: str = ""
    args: str = ""
    cwd: str = ""
    window: str = WINDOW_NORMAL
    icon: str = "🚀"
    icon_path: str = ""
    delay_seconds: int = 0
    auto_restart: bool = False
    check_port: int = 0
    avoid_fullscreen: bool = True
    avoid_process: bool = True
    ready_port: int = 0
    ready_timeout: int = 60
    # 启动后自动用默认浏览器打开服务地址
    auto_open: bool = False
    open_url: str = ""
    group: str = ""
    allow_adopt: bool = True
    id: str = field(default_factory=new_id)
    # 运行期字段（不落盘）
    running: bool = False

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            # 落在程序目录下的路径统一存成 ${APP}\...，换电脑也能用
            "path": shrink_vars(self.path),
            "args": self.args,
            "cwd": shrink_vars(self.cwd),
            "window": self.window if self.window in WINDOW_MODES else WINDOW_NORMAL,
            "icon": self.icon or "🚀",
            "icon_path": shrink_vars(self.icon_path),
            "delay_seconds": max(0, int(self.delay_seconds or 0)),
            "auto_restart": bool(self.auto_restart),
            "check_port": max(0, min(65535, int(self.check_port or 0))),
            "avoid_fullscreen": bool(self.avoid_fullscreen),
            "avoid_process": bool(self.avoid_process),
            "ready_port": max(0, min(65535, int(self.ready_port or 0))),
            "ready_timeout": max(5, min(600, int(self.ready_timeout or 60))),
            "auto_open": bool(self.auto_open),
            "open_url": self.open_url.strip(),
            "group": self.group.strip(),
            "allow_adopt": bool(self.allow_adopt),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LaunchItem":
        window = str(data.get("window") or WINDOW_NORMAL)
        if window not in WINDOW_MODES:
            window = WINDOW_NORMAL
        # 兼容 v3.4.x：capture_output=True（捕获到日志面板）等价于「后台运行」
        if data.get("capture_output") is True:
            window = WINDOW_HIDDEN
        try:
            delay = max(0, int(data.get("delay_seconds") or 0))
        except (TypeError, ValueError):
            delay = 0
        try:
            port = max(0, min(65535, int(data.get("check_port") or 0)))
        except (TypeError, ValueError):
            port = 0
        return cls(
            id=str(data.get("id") or new_id()),
            name=str(data.get("name") or "未命名启动项"),
            path=str(data.get("path") or ""),
            args=str(data.get("args") or ""),
            cwd=str(data.get("cwd") or ""),
            window=window,
            icon=str(data.get("icon") or "🚀"),
            icon_path=str(data.get("icon_path") or "").strip(),
            delay_seconds=delay,
            auto_restart=bool(data.get("auto_restart", False)),
            check_port=port,
            avoid_fullscreen=bool(data.get("avoid_fullscreen", True)),
            avoid_process=bool(data.get("avoid_process", True)),
            ready_port=_as_int(data.get("ready_port"), 0, 0, 65535),
            ready_timeout=_as_int(data.get("ready_timeout"), 60, 5, 600),
            auto_open=bool(data.get("auto_open", False)),
            open_url=str(data.get("open_url") or "").strip(),
            group=str(data.get("group") or "").strip(),
            allow_adopt=bool(data.get("allow_adopt", True)),
        )

    # ---------- 便捷 ----------
    @property
    def window_label(self) -> str:
        return WINDOW_LABELS.get(self.window, "正常窗口")

    def is_script(self) -> bool:
        """目标是不是批处理类脚本。"""
        suffix = Path(self.resolved_path()).suffix.lower()
        return suffix in SCRIPT_SUFFIXES

    def is_hidden(self) -> bool:
        """是否后台运行：不弹窗，输出捕获到日志面板。"""
        return self.window == WINDOW_HIDDEN

    def resolve_open_url(self) -> str:
        """启动后要打开的地址；返回空字符串表示"没有固定地址"。

        优先级：**就绪端口 > 手填地址**。
        两者都没有时返回空，由主窗口在运行时从输出里抓（仅后台模式有输出）。
        """
        if self.ready_port > 0:
            return f"http://127.0.0.1:{self.ready_port}/"
        return normalize_url(self.open_url)

    def resolved_path(self) -> str:
        """展开 `${APP}` 后的绝对路径（空字符串表示未设置）。"""
        return expand_vars(self.path)

    def resolved_cwd(self) -> str:
        return expand_vars(self.cwd)

    def resolved_icon_path(self) -> str:
        """展开 `${APP}` 后的图标图片路径（空字符串表示没设置）。"""
        return expand_vars(self.icon_path)

    def is_portable_path(self) -> bool:
        """路径是否以 `${APP}` 形式保存（换电脑后仍然有效）。"""
        return has_app_var(self.path) or has_app_var(self.cwd)

    def effective_cwd(self) -> str:
        """子进程工作目录：显式配置优先，否则用目标文件所在目录。

        永远返回绝对路径；目标不存在时回退到程序目录，绝不继承启动器的 cwd。
        """
        raw = self.resolved_cwd()
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.exists():
                return str(candidate.resolve())
        target = self.resolved_path()
        if target:
            parent = Path(target).expanduser().resolve().parent
            if parent.exists():
                return str(parent)
        return str(app_dir())

    def has_path(self) -> bool:
        raw = self.resolved_path()
        return bool(raw) and Path(raw).exists()

    def path_summary(self) -> str:
        raw = self.resolved_path()
        if not raw:
            return "尚未设置路径"
        name = Path(raw).name
        parent = str(Path(raw).parent)
        if len(parent) > 42:
            parent = "…" + parent[-41:]
        return f"{name}  ·  {parent}"

    def meta_summary(self) -> str:
        parts: list[str] = []
        if self.is_portable_path():
            parts.append("便携路径")
        if self.window == WINDOW_HIDDEN:
            parts.append("后台运行")
        elif self.window == WINDOW_MINIMIZED:
            parts.append("最小化")
        if self.auto_open:
            parts.append("自动打开")
        if self.group.strip():
            parts.append(f"#{self.group.strip()}")
        if self.delay_seconds > 0:
            parts.append(f"延迟 {self.delay_seconds}s")
        if self.auto_restart:
            parts.append("自动重启")
        if self.check_port > 0:
            parts.append(f"端口 {self.check_port}")
        if not self.avoid_fullscreen:
            parts.append("全屏不避让")
        if not self.allow_adopt:
            parts.append("不接管")
        return " · ".join(parts)


@dataclass
class MonitorTarget:
    """监控目标：exe（具体程序）或 dir（目录下的任何程序）。"""

    kind: str = "exe"          # exe | dir
    path: str = ""
    enabled: bool = True
    note: str = ""
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": "dir" if self.kind == "dir" else "exe",
            "path": shrink_vars(self.path),
            "enabled": bool(self.enabled),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitorTarget":
        kind = "dir" if str(data.get("kind") or "").lower() == "dir" else "exe"
        return cls(
            id=str(data.get("id") or new_id()),
            kind=kind,
            path=str(data.get("path") or ""),
            enabled=bool(data.get("enabled", True)),
            note=str(data.get("note") or ""),
        )

    def resolved_path(self) -> str:
        return expand_vars(self.path)

    def label(self) -> str:
        prefix = "目录" if self.kind == "dir" else "程序"
        return f"[{prefix}] {self.resolved_path() or '（未设置）'}"


@dataclass
class AppSettings:
    autostart: bool = False
    close_to_tray: bool = True
    fullscreen_avoid: bool = True
    fullscreen_block_processes: str = ""
    fullscreen_allow_processes: str = ""
    monitor_whitelist_processes: str = ""
    monitor_whitelist_titles: str = ""
    theme: str = "auto"
    window_geometry: str = ""
    adopt_on_startup: bool = True
    show_splash: bool = True
    hotkeys_enabled: bool = False
    hotkey_start: str = ""   # 空 = 用内置候选
    hotkey_stop: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "autostart": bool(self.autostart),
            "close_to_tray": bool(self.close_to_tray),
            "fullscreen_avoid": bool(self.fullscreen_avoid),
            "fullscreen_block_processes": self.fullscreen_block_processes,
            "fullscreen_allow_processes": self.fullscreen_allow_processes,
            "monitor_whitelist_processes": self.monitor_whitelist_processes,
            "monitor_whitelist_titles": self.monitor_whitelist_titles,
            "theme": self.theme if self.theme in ("auto", "light", "dark") else "auto",
            "window_geometry": self.window_geometry,
            "adopt_on_startup": bool(self.adopt_on_startup),
            "show_splash": bool(self.show_splash),
            "hotkeys_enabled": bool(self.hotkeys_enabled),
            "hotkey_start": self.hotkey_start.strip(),
            "hotkey_stop": self.hotkey_stop.strip(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AppSettings":
        if not isinstance(data, dict):
            return cls()
        return cls(
            autostart=bool(data.get("autostart", False)),
            close_to_tray=bool(data.get("close_to_tray", True)),
            fullscreen_avoid=bool(data.get("fullscreen_avoid", True)),
            fullscreen_block_processes=str(data.get("fullscreen_block_processes") or ""),
            fullscreen_allow_processes=str(data.get("fullscreen_allow_processes") or ""),
            monitor_whitelist_processes=str(data.get("monitor_whitelist_processes") or ""),
            monitor_whitelist_titles=str(data.get("monitor_whitelist_titles") or ""),
            theme=str(data.get("theme") or "auto"),
            window_geometry=str(data.get("window_geometry") or ""),
            adopt_on_startup=bool(data.get("adopt_on_startup", True)),
            show_splash=bool(data.get("show_splash", True)),
            hotkeys_enabled=bool(data.get("hotkeys_enabled", False)),
            hotkey_start=str(data.get("hotkey_start") or "").strip(),
            hotkey_stop=str(data.get("hotkey_stop") or "").strip(),
        )


@dataclass
class ConfigDocument:
    items: list[LaunchItem] = field(default_factory=list)
    settings: AppSettings = field(default_factory=AppSettings)
    targets: list[MonitorTarget] = field(default_factory=list)


def default_items() -> list[LaunchItem]:
    return [LaunchItem(name=name, icon=icon) for name, icon in DEFAULT_ENTRIES]


class ConfigStore:
    """config.json 读写；文件不存在或损坏时回退到默认三条目。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else config_path()
        self._load_failed = False

    def load(self) -> ConfigDocument:
        self._load_failed = False
        try:
            # utf-8-sig：兼容记事本等编辑器写入的 BOM
            raw = self.path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
        except FileNotFoundError:
            return ConfigDocument(items=default_items())
        except Exception:
            # 配置损坏时不覆盖用户文件，仅本次回退默认值
            self._load_failed = True
            return ConfigDocument(items=default_items())

        if not isinstance(data, dict):
            return ConfigDocument(items=default_items())
        data = migrate_payload(data)

        settings = AppSettings.from_dict(data.get("settings"))
        targets: list[MonitorTarget] = []
        for entry in data.get("targets") or []:
            if isinstance(entry, dict):
                targets.append(MonitorTarget.from_dict(entry))
        items_raw = data.get("items")
        if not isinstance(items_raw, list):
            # 字段缺失或类型不对：用默认条目
            return ConfigDocument(items=default_items(), settings=settings, targets=targets)
        if not items_raw:
            # 用户主动删光了所有条目 —— 尊重这个选择，不再塞回默认值
            return ConfigDocument(items=[], settings=settings, targets=targets)

        items: list[LaunchItem] = []
        for entry in items_raw:
            if isinstance(entry, dict):
                items.append(LaunchItem.from_dict(entry))
        return ConfigDocument(
            items=items or default_items(),
            settings=settings,
            targets=targets,
        )

    # ---------------- 备份 / 恢复 ----------------
    def list_backups(self, limit: int = 5) -> list[Path]:
        try:
            files = sorted(
                self.path.parent.glob("config.bak-*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            return []
        return files[:limit]

    def restore_backup(self, backup: Path) -> ConfigDocument:
        data = json.loads(Path(backup).read_text(encoding="utf-8-sig"))
        data = migrate_payload(data)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.load()

    def _backup_current(self) -> None:
        """保存前留一份快照，最多保留 5 份。"""
        if not self.path.exists():
            return
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = self.path.with_name(f"config.bak-{stamp}.json")
            target.write_bytes(self.path.read_bytes())
            for old in sorted(self.path.parent.glob("config.bak-*.json"))[:-5]:
                try:
                    old.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def save(self, document: ConfigDocument) -> None:
        payload = {
            "version": CONFIG_VERSION,
            "items": [item.to_dict() for item in document.items],
            "settings": document.settings.to_dict(),
            "targets": [t.to_dict() for t in document.targets],
        }
        # 内容有变化才备份，避免每次启动都刷一堆快照
        try:
            if self.path.exists():
                current = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if current != payload:
                    self._backup_current()
        except Exception:
            pass
        # 上一次读取失败（文件损坏）时，先留一份备份再覆盖
        if self._load_failed and self.path.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            try:
                self.path.replace(self.path.with_name(f"config.invalid-{stamp}.json"))
            except Exception:
                pass
            self._load_failed = False
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)
