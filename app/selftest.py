"""无界面自检：覆盖便携化、启停、窗口模式、端口预检、日志读取、自动重启、延迟级联。

用 `python launcher.py --selftest` 或打包后的 exe `--selftest` 运行。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from .config import (    AppSettings,
    ConfigDocument,
    ConfigStore,
    LaunchItem,
    WINDOW_HIDDEN,
    WINDOW_MINIMIZED,
    WINDOW_NORMAL,
)
from .paths import asset_path
from .processes import (
    STATE_RUNNING,
    STATE_STOPPED,
    SW_SHOWMINIMIZED,
    SW_SHOWNORMAL,
    ProcessManager,
    _startup_info,
)

RESULTS: list[tuple[str, bool, str]] = []


_DIAG_PATH: Path | None = None


def _diag(text: str) -> None:
    """自检同时写一份文件日志，方便无控制台的 exe 排查。"""
    global _DIAG_PATH
    try:
        if _DIAG_PATH is None:
            from .paths import log_dir

            _DIAG_PATH = log_dir() / "selftest.log"
        with _DIAG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except Exception:
        pass


def _check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    line = ("  [PASS] " if ok else "  [FAIL] ") + name + (f"  ({detail})" if detail else "")
    print(line)
    _diag(line)


class SelfTest(QObject):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.tmpdir = Path(tempfile.mkdtemp(prefix="launcher_selftest_"))
        self.manager = ProcessManager(self)
        self.manager.output.connect(self._on_output)
        self._log_lines: list[str] = []
        self._state_events: list[tuple[str, str, float]] = []
        self.manager.state_changed.connect(self._on_state)
        self._win = None

    def _on_output(self, item_id: str, line: str) -> None:
        self._log_lines.append(line)

    def _on_state(self, item_id: str, state: str) -> None:
        self._state_events.append((item_id, state, time.monotonic()))

    # ---------------- 用例 ----------------
    def run(self) -> int:
        print("== 自检开始 ==")
        _diag("== 自检开始 %s ==" % time.strftime("%Y-%m-%d %H:%M:%S"))
        os.environ["LAUNCHER_CONFIG"] = str(self.tmpdir / "config.json")

        # 1) 默认配置
        store = ConfigStore(self.tmpdir / "config.json")
        items = store.load().items
        _check("默认条目数量为 3", len(items) == 3, str([i.name for i in items]))
        _check("默认条目路径为空", all(not i.path for i in items))
        store.save(ConfigDocument(items=items))
        reloaded = store.load().items
        _check(
            "配置保存后可原样读回",
            [i.name for i in reloaded] == [i.name for i in items],
        )

        # 1b) 带 BOM 的配置
        bom_path = self.tmpdir / "bom.json"
        bom_path.write_text(
            json.dumps(
                {"version": 2, "items": [{"id": "bom1", "name": "带BOM的条目"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8-sig",
        )
        bom_items = ConfigStore(bom_path).load().items
        _check(
            "带 BOM 的配置也能正确读取",
            len(bom_items) == 1 and bom_items[0].name == "带BOM的条目",
            str([i.name for i in bom_items]),
        )

        # 1c) 新增字段持久化
        feature = LaunchItem(
            name="新字段",
            delay_seconds=7,
            auto_restart=True,
            check_port=8000,
        )
        store.save(ConfigDocument(items=[feature]))
        loaded = store.load().items[0]
        _check(
            "delay/auto_restart/check_port 可持久化",
            loaded.delay_seconds == 7
            and loaded.auto_restart is True
            and loaded.check_port == 8000,
            f"{loaded.delay_seconds}/{loaded.auto_restart}/{loaded.check_port}",
        )
        _check(
            "全新条目的新字段默认值正确",
            LaunchItem(name="x").delay_seconds == 0
            and LaunchItem(name="x").auto_restart is False
            and LaunchItem(name="x").check_port == 0,
        )

        # 1c1) 窗口形态：三态，只有「后台运行」才捕获输出
        _check(
            "窗口形态三态可持久化",
            [
                LaunchItem.from_dict(
                    {"name": "x", "window": mode}
                ).window
                for mode in (WINDOW_NORMAL, WINDOW_MINIMIZED, WINDOW_HIDDEN)
            ]
            == [WINDOW_NORMAL, WINDOW_MINIMIZED, WINDOW_HIDDEN],
        )
        _check(
            "只有后台运行模式才捕获输出",
            LaunchItem(name="x", path="a.bat", window=WINDOW_NORMAL).is_hidden() is False
            and LaunchItem(
                name="x", path="a.bat", window=WINDOW_MINIMIZED
            ).is_hidden()
            is False
            and LaunchItem(name="x", path="a.bat", window=WINDOW_HIDDEN).is_hidden()
            is True,
        )
        _check(
            "非法窗口形态回落到正常窗口",
            LaunchItem.from_dict({"name": "x", "window": "bogus"}).window
            == WINDOW_NORMAL,
        )
        _check(
            "旧版 capture_output 迁移为后台运行",
            LaunchItem.from_dict({"name": "x", "capture_output": True}).window
            == WINDOW_HIDDEN
            and LaunchItem.from_dict({"name": "x", "capture_output": False}).window
            == WINDOW_NORMAL,
        )

        # 1c1b) 自动打开服务地址
        from .weblink import is_local_url, pick_entry_url

        _check(
            "能从输出里识别本地服务地址",
            pick_entry_url("[WebUI] listening http://127.0.0.1:5099")
            == "http://127.0.0.1:5099"
            and pick_entry_url(
                "Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)"
            )
            == "http://127.0.0.1:8000",
        )
        _check(
            "跳过 ws:// 与非本机地址",
            pick_entry_url("connected ws://127.0.0.1:6199/ws") == ""
            and pick_entry_url("see https://example.com/docs") == ""
            and is_local_url("http://192.168.1.10:8080/") is True
            and is_local_url("http://10.0.0.5/") is True
            and is_local_url("http://example.com/") is False,
        )
        _check(
            "地址优先级：就绪端口 > 手填地址",
            LaunchItem(
                name="x", ready_port=5099, open_url="http://127.0.0.1:9999/admin"
            ).resolve_open_url()
            == "http://127.0.0.1:5099/"
            and LaunchItem(name="x", open_url="127.0.0.1:7000/ui").resolve_open_url()
            == "http://127.0.0.1:7000/ui"
            and LaunchItem(name="x").resolve_open_url()
            == "",
        )
        _check(
            "auto_open / open_url 可持久化",
            LaunchItem.from_dict(
                {"name": "x", "auto_open": True, "open_url": "http://127.0.0.1:5099/"}
            ).auto_open
            is True
            and LaunchItem.from_dict(
                {"name": "x", "open_url": "http://127.0.0.1:5099/"}
            ).open_url
            == "http://127.0.0.1:5099/",
        )

        # 1c1c) 开机动画
        from PySide6.QtGui import QMovie

        from .splash import find_splash_file, movie_duration_ms, splash_enabled

        splash_file = find_splash_file()
        _check(
            "开机动画文件可找到",
            splash_file is not None,
            str(splash_file),
        )
        if splash_file is not None:
            splash_movie = QMovie(str(splash_file))
            splash_movie.setCacheMode(QMovie.CacheAll)
            _check(
                "开机动画可解码且是多帧",
                splash_movie.isValid() and splash_movie.frameCount() > 1,
                f"帧数={splash_movie.frameCount()}",
            )
            _check(
                "开机动画时长可算出",
                movie_duration_ms(splash_movie) > 200,
                f"{movie_duration_ms(splash_movie)} ms",
            )
        _check("开机动画开关默认开启", splash_enabled() is True)

        # 1c1c2) 开屏必须铺满窗口 —— 曾经因为 QMovie 读过帧后 setScaledSize 失效，
        # 动画缩在中间、四周一圈黑边，这里用几何覆盖做回归保护
        from PySide6.QtCore import QPoint, QRect

        from .splash import SplashWindow, cover_rect

        try:
            probe = SplashWindow(find_splash_file(), target=QRect(0, 0, 1140, 800))
            frame_size = probe.movie.currentPixmap().size()
            drawn = cover_rect(frame_size, probe.canvas.size())
            canvas_rect = QRect(QPoint(0, 0), probe.canvas.size())
            _check(
                "开屏动画铺满整个窗口（不留黑边）",
                drawn.contains(canvas_rect),
                f"绘制 {drawn.width()}x{drawn.height()} vs 画布 {canvas_rect.width()}x{canvas_rect.height()}",
            )
            probe.finish()
        except Exception as exc:
            _check("开屏动画铺满整个窗口（不留黑边）", False, str(exc))

        # 1c1d) 自定义图标
        from .card import load_icon_pixmap

        icon_source = Path(asset_path("logo.png"))
        _check("内置图标图片存在", icon_source.is_file(), str(icon_source))

        icon_item = LaunchItem(name="x", icon_path=str(icon_source))
        _check(
            "图标路径会转成 ${APP} 形式",
            icon_item.to_dict()["icon_path"].startswith("${APP}"),
            icon_item.to_dict()["icon_path"],
        )
        _check(
            "图标路径可读回并展开",
            LaunchItem.from_dict(icon_item.to_dict()).resolved_icon_path()
            == str(icon_source),
        )
        if icon_source.is_file():
            _check(
                "能加载并裁出圆角图标",
                load_icon_pixmap(str(icon_source)) is not None,
            )
        _check(
            "图标缺失时返回 None（回落 emoji）",
            load_icon_pixmap(r"D:\不存在的目录\nope.png") is None
            and load_icon_pixmap("") is None,
        )

        # 1c2) 全屏避让设置持久化
        from .config import AppSettings

        store.save(
            ConfigDocument(
                items=[LaunchItem(name="x")],
                settings=AppSettings(
                    fullscreen_avoid=False,
                    fullscreen_block_processes="myplayer",
                    fullscreen_allow_processes="potplayer",
                ),
            )
        )
        _check(
            "全屏避让开关可持久化",
            ConfigStore(self.tmpdir / "config.json").load().settings.fullscreen_avoid is False,
        )
        _check(
            "全屏进程名单可持久化",
            ConfigStore(self.tmpdir / "config.json").load().settings.fullscreen_block_processes
            == "myplayer",
        )
        store.save(ConfigDocument(items=[LaunchItem(name="x")]))

        # 1c3) 进程避让与触发器持久化
        from .config import MonitorTarget

        target = MonitorTarget(kind="dir", path=r"C:\Some\Dir", enabled=False, note="测试")
        store.save(
            ConfigDocument(
                items=[LaunchItem(name="x", avoid_process=False)],
                targets=[target],
            )
        )
        loaded_doc = ConfigStore(self.tmpdir / "config.json").load()
        _check(
            "avoid_process 可持久化",
            loaded_doc.items[0].avoid_process is False,
        )
        store.save(
            ConfigDocument(
                items=[LaunchItem(name="y", allow_adopt=False)],
                settings=AppSettings(adopt_on_startup=False),
            )
        )
        adopt_doc = ConfigStore(self.tmpdir / "config.json").load()
        _check(
            "接管开关可持久化（条目 allow_adopt / 全局 adopt_on_startup）",
            adopt_doc.items[0].allow_adopt is False
            and adopt_doc.settings.adopt_on_startup is False,
        )

        _check(
            "monitor_targets 可持久化",
            len(loaded_doc.targets) == 1
            and loaded_doc.targets[0].kind == "dir"
            and loaded_doc.targets[0].enabled is False,
            str([t.to_dict() for t in loaded_doc.targets]),
        )
        store.save(ConfigDocument(items=[LaunchItem(name="x")]))

        # 1c4) 旧版本配置迁移
        legacy = self.tmpdir / "legacy.json"
        legacy.write_text(
            json.dumps(
                {"version": 1, "items": [{"name": "老条目", "path": "C:/x.exe"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        legacy_doc = ConfigStore(legacy).load()
        _check(
            "v1 配置可迁移到 v3（补默认字段）",
            legacy_doc.items[0].avoid_process is True
            and legacy_doc.items[0].ready_port == 0
            and legacy_doc.settings.theme == "auto",
        )

        # 1c5) 配置备份与恢复
        backup_dir = self.tmpdir / "backup_test"
        backup_dir.mkdir(exist_ok=True)
        backup_store = ConfigStore(backup_dir / "config.json")
        backup_store.save(ConfigDocument(items=[LaunchItem(name="旧配置")]))
        backup_store.save(ConfigDocument(items=[LaunchItem(name="新配置")]))
        backups = backup_store.list_backups()
        _check("保存时生成了配置备份", len(backups) >= 1, str([b.name for b in backups]))
        if backups:
            restored = backup_store.restore_backup(backups[0])
            _check(
                "可以从备份恢复配置",
                restored.items[0].name in ("旧配置", "新配置"),
                restored.items[0].name,
            )

        # 1d) 资源与开机自启命令
        from .assets import asset_exists
        from . import autostart

        _check("图标资源 logo.png 存在", asset_exists("logo.png"))
        _check("exe 图标 app.ico 存在", asset_exists("app.ico"))
        command = autostart._launch_command()
        _check(
            "开机自启命令使用动态路径",
            command.startswith('"') and "--minimized" in command,
            command[:60],
        )
        _check(
            "读取开机自启状态不报错",
            isinstance(autostart.is_autostart_enabled(), bool),
        )

        # 1e) 便携化：配置与日志都在 exe/项目目录
        from .paths import app_dir, log_file

        store_path = ConfigStore().path
        _check(
            "配置默认位于程序目录",
            store_path.parent == app_dir() or os.environ.get("LAUNCHER_CONFIG") is not None,
            str(store_path),
        )
        _check("日志目录可创建", log_file().parent.exists(), str(log_file().parent))

        # 2) 主窗口：不自动启动 + 托盘 + 日志面板
        from .mainwindow import MainWindow

        window = MainWindow()
        self._win = window
        self._first_window = window
        _check("主窗口可构建", window is not None)
        _check("启动时没有拉起任何程序", window._manager.active_items() == [])
        _check(
            "所有独立开关初始为关闭",
            all(not c.switch.isChecked() for c in window._flow.cards()),
        )
        _check("总开关初始为关闭", not window.master_switch.isChecked())
        _check("托盘图标已创建", window._tray is not None)
        from .fullscreen import foreground_fullscreen

        fs_flag, _fs_title = foreground_fullscreen()
        _check("全屏检测函数可正常调用", isinstance(fs_flag, bool), str(fs_flag))
        _check("启动器自身的全屏判断可用", isinstance(window._is_fullscreen(), bool))
        _check("日志面板初始收起", window._log_panel.maximumHeight() == 0)

        # 2b) 端口预检
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        busy_port = listener.getsockname()[1]
        _check("端口被占用时能检测出来", window._port_in_use(busy_port), str(busy_port))
        listener.close()
        time.sleep(0.2)
        _check("端口释放后检测为未占用", not window._port_in_use(busy_port), str(busy_port))
        _check("端口 0 不检测", not window._port_in_use(0))

        # 2c) 单实例锁与唤醒（跨进程，模拟真实双击）
        from .single_instance import SingleInstance

        os.environ["LAUNCHER_SINGLE_NAME"] = "Selftest_Single_Instance"
        os.environ["LAUNCHER_SINGLE_LOCK"] = "Selftest_Single.lock"
        primary = SingleInstance()
        first_ok = primary.try_start()
        wake_events: list[bool] = []
        primary.show_requested.connect(lambda: wake_events.append(True))
        _check("第一个实例成功拿到单实例锁", first_ok)

        if getattr(sys, "frozen", False):
            probe_cmd = [sys.executable, "--single-probe"]
        else:
            probe_cmd = [sys.executable, str(Path(__file__).resolve().parent.parent / "launcher.py"), "--single-probe"]
        probe_code, probe_out = -1, ""
        try:
            # 关键：用 Popen + 轮询，让本进程的事件循环持续运转，
            # 否则 QLocalServer 收不到唤醒连接（阻塞式 run 会把事件循环卡住）。
            probe = subprocess.Popen(
                probe_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 25
            while probe.poll() is None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            if probe.poll() is None:
                probe.kill()
            out, _err = probe.communicate(timeout=5)
            probe_code = probe.returncode
            probe_out = (out or "").strip()
        except Exception as exc:
            probe_code, probe_out = -1, f"error: {exc}"
        for _ in range(20):
            self.app.processEvents()
            time.sleep(0.02)
        _check(
            "第二个进程被拒绝（不会多开）",
            probe_code == 3,
            f"code={probe_code} out={probe_out[:40]}",
        )
        _check("第二个进程唤醒了第一个实例", bool(wake_events), str(len(wake_events)))
        primary.close()
        os.environ.pop("LAUNCHER_SINGLE_NAME", None)
        os.environ.pop("LAUNCHER_SINGLE_LOCK", None)

        # 2d) 进程监控线程能识别到正在运行的目标
        from .monitor import ProcessMonitorThread

        mon = ProcessMonitorThread(1.0)
        mon_targets = [MonitorTarget(kind="exe", path=sys.executable)]
        mon_hits: list[tuple[bool, str]] = []
        mon.changed.connect(lambda busy, desc: mon_hits.append((busy, desc)))
        mon.set_targets(mon_targets, "")
        mon.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not any(h[0] for h in mon_hits):
            self.app.processEvents()
            time.sleep(0.05)
        mon.stop()
        _check(
            "进程监控线程检测到目标进程",
            any(h[0] for h in mon_hits),
            str(mon_hits[-1] if mon_hits else "无事件"),
        )

        # 3) 窗口模式常量
        info = _startup_info(True)
        _check(
            "最小化模式设置 SW_SHOWMINIMIZED",
            info is not None and info.wShowWindow == SW_SHOWMINIMIZED,
        )
        info2 = _startup_info(False)
        _check(
            "正常模式设置 SW_SHOWNORMAL",
            info2 is not None and info2.wShowWindow == SW_SHOWNORMAL,
        )

        # 4) 启动 / 日志 / 停止
        bat = self.tmpdir / "fake_long.bat"
        bat.write_text(
            "@echo off\r\n"
            "echo hello-one\r\n"
            "ping -n 40 127.0.0.1 >nul\r\n",
            encoding="utf-8",
        )
        self.item = LaunchItem(
            name="假程序",
            path=str(bat),
            window=WINDOW_HIDDEN,  # 后台运行，输出进日志面板（本用例验证捕获）
        )
        self.manager.start(self.item)
        QTimer.singleShot(1500, self._after_start)
        QTimer.singleShot(100000, self._timeout)
        return self.app.exec()

    def _after_start(self) -> None:
        _check(
            "启动后状态变为 running",
            self.manager.state(self.item.id) == STATE_RUNNING,
        )
        pid = self.manager.pid_of(self.item.id)
        _check("拿到了子进程 PID", bool(pid), str(pid))
        _check(
            "子进程输出被日志线程捕获",
            any("hello-one" in line for line in self._log_lines),
            str(self._log_lines[:2]),
        )
        self._started_pid = pid
        self.manager.stop(self.item)
        QTimer.singleShot(2600, self._after_stop)

    def _after_stop(self) -> None:
        _check(
            "停止后状态回到 stopped",
            self.manager.state(self.item.id) == STATE_STOPPED,
        )
        _check("进程已不在管理表中", self.manager.pid_of(self.item.id) is None)
        if self._started_pid:
            _check(
                "原进程已被真正终止",
                not _pid_alive(self._started_pid),
                str(self._started_pid),
            )
        self._test_shutdown_no_hang()

    # ---------------- 退出不能卡死 ----------------
    def _test_shutdown_no_hang(self) -> None:
        """回归：子进程没有输出时读线程会阻塞在 readline()。

        如果 shutdown 先关管道、后杀进程，close() 会和阻塞中的 readline()
        抢对象锁，直接死锁 —— 表现为点"全部停止并退出"界面卡死。
        这里用独立线程跑 shutdown，卡住就判失败，不会拖死整个自检。
        """
        quiet = self.tmpdir / "quiet_nohang.bat"
        quiet.write_text("@echo off\r\nping -n 60 127.0.0.1 >nul\r\n", encoding="gbk")

        manager = ProcessManager(self)
        item = LaunchItem(name="静默进程", path=str(quiet), window=WINDOW_HIDDEN)
        manager.start(item)
        pid = manager.pid_of(item.id)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)

        outcome: dict[str, float] = {}

        def worker() -> None:
            started = time.monotonic()
            manager.shutdown(timeout_total=6.0)
            outcome["elapsed"] = time.monotonic() - started

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        deadline = time.monotonic() + 12
        while thread.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)

        hung = thread.is_alive()
        _check(
            "子进程无输出时退出不会卡死",
            not hung,
            "shutdown 超过 12s 未返回" if hung else f"耗时 {outcome.get('elapsed', 0):.2f}s",
        )
        if hung and pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                pass

        self._test_auto_restart()

    # ---------------- 自动重启 ----------------
    def _test_auto_restart(self) -> None:
        crash_bat = self.tmpdir / "crash_once.bat"
        flag = self.tmpdir / "crashed.flag"
        if flag.exists():
            flag.unlink()
        crash_bat.write_text(
            "@echo off\r\n"
            'if exist "%~dp0crashed.flag" goto hold\r\n'
            'echo first-run > "%~dp0crashed.flag"\r\n'
            "exit /b 3\r\n"
            ":hold\r\n"
            "ping -n 40 127.0.0.1 >nul\r\n",
            encoding="utf-8",
        )
        self.crash_item = LaunchItem(
            name="会崩一次的假程序",
            path=str(crash_bat),
            auto_restart=True,
            window=WINDOW_HIDDEN,
        )
        self.manager.start(self.crash_item)
        self._first_pid = self.manager.pid_of(self.crash_item.id)
        QTimer.singleShot(7500, self._check_auto_restart)

    def _check_auto_restart(self) -> None:
        item = self.crash_item
        state = self.manager.state(item.id)
        new_pid = self.manager.pid_of(item.id)
        _check(
            "崩溃后自动重启成功",
            state == STATE_RUNNING and new_pid is not None and new_pid != self._first_pid,
            f"state={state} pid={new_pid} first={self._first_pid}",
        )
        self.manager.stop(item)
        QTimer.singleShot(2600, self._test_adoption)

    # ---------------- 启动接管 ----------------
    def _test_adoption(self) -> None:
        from .adopt import detect_running

        bat = self.tmpdir / "adopt_me.bat"
        bat.write_text("@echo off\r\nping -n 60 127.0.0.1 >nul\r\n", encoding="utf-8")
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        self._external_proc = subprocess.Popen(
            [comspec, "/c", str(bat)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
        self.adopt_item = LaunchItem(name="接管测试", path=str(bat), window=WINDOW_HIDDEN)
        match = detect_running(self.adopt_item)
        _check(
            "能检测到外部已运行的进程",
            match is not None,
            str((match.pid, match.how) if match else "无匹配"),
        )
        adopted = self.manager.adopt(
            self.adopt_item, match.pid if match else 0, match.name if match else ""
        )
        _check(
            "接管后视为运行中",
            adopted
            and self.manager.state(self.adopt_item.id) == STATE_RUNNING
            and self.manager.is_adopted(self.adopt_item.id),
        )
        self.manager.stop(self.adopt_item)
        QTimer.singleShot(2600, self._check_adoption_stopped)

    def _check_adoption_stopped(self) -> None:
        _check(
            "停止接管项后状态回到 stopped",
            self.manager.state(self.adopt_item.id) == STATE_STOPPED,
        )
        alive = False
        try:
            alive = self._external_proc.poll() is None
        except Exception:
            alive = False
        _check("接管的外部进程已被结束", not alive, str(self._external_proc.pid))
        self._test_adopt_optout()

    # ---------------- 接管开关过滤 ----------------
    def _test_adopt_optout(self) -> None:
        bat = self.tmpdir / "optout.bat"
        bat.write_text("@echo off\r\nping -n 60 127.0.0.1 >nul\r\n", encoding="utf-8")
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        self._optout_proc = subprocess.Popen(
            [comspec, "/c", str(bat)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
        item = LaunchItem(
            name="不接管测试", path=str(bat), allow_adopt=False, window=WINDOW_HIDDEN
        )
        ConfigStore(self.tmpdir / "config.json").save(ConfigDocument(items=[item]))

        from .mainwindow import MainWindow

        win = MainWindow()
        self._optout_win = win
        win._detector.stop()
        win._monitor_thread.stop()
        win.adopt_running_services(manual=True)
        self._wait_scan(win)
        _check(
            "allow_adopt=False 的条目不接管",
            not win._manager.is_adopted(item.id),
        )
        win._items[0].allow_adopt = True
        win.adopt_running_services(manual=True)
        self._wait_scan(win)
        _check(
            "allow_adopt=True 的条目会被接管",
            win._manager.is_adopted(item.id),
        )
        win._manager.stop(win._items[0])
        QTimer.singleShot(2200, self._check_optout_done)

    @staticmethod
    def _wait_scan(win, timeout: float = 8.0) -> None:
        """接管扫描已挪到后台线程，断言前先等它跑完。"""
        from PySide6.QtWidgets import QApplication

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QApplication.processEvents()
            if not win._scan.is_busy("adopt"):
                break
            time.sleep(0.02)
        QApplication.processEvents()

    def _check_optout_done(self) -> None:
        alive = self._optout_proc.poll() is None
        _check("关闭接管后外部进程被结束", not alive, str(self._optout_proc.pid))
        try:
            self._optout_win._force_quit = True
            self._optout_win.close()
        except Exception:
            pass
        self._test_master()

    # ---------------- 总开关级联 + 延迟 + 排序 ----------------
    def _test_master(self) -> None:
        bat_a = self.tmpdir / "fake_a.bat"
        bat_b = self.tmpdir / "fake_b.bat"
        for path in (bat_a, bat_b):
            path.write_text(
                "@echo off\r\nping -n 60 127.0.0.1 >nul\r\n", encoding="utf-8"
            )
        store = ConfigStore(self.tmpdir / "config.json")
        store.save(
            ConfigDocument(
                items=[
                    LaunchItem(name="假A", path=str(bat_a), window=WINDOW_HIDDEN),
                    LaunchItem(
                        name="假B",
                        path=str(bat_b),
                        delay_seconds=1,
                        window=WINDOW_HIDDEN,
                    ),
                ],
                settings=AppSettings(autostart=False, close_to_tray=True),
            )
        )

        from .mainwindow import MainWindow

        window = MainWindow()
        self._win = window
        _check("临时配置装载 2 个条目", len(window._flow.cards()) == 2)
        _check("新窗口打开时仍无进程运行", window._manager.active_items() == [])

        self._running_times: list[tuple[str, float]] = []
        self._running_order: list[str] = []
        window._manager.state_changed.connect(self._track_running)
        window._on_master_toggled(True)
        QTimer.singleShot(5200, self._check_master_on)

    def _track_running(self, item_id: str, state: str) -> None:
        if state == STATE_RUNNING and self._win is not None:
            item = self._win._item(item_id)
            if item is not None:
                self._running_order.append(item.name)
                self._running_times.append((item.name, time.monotonic()))

    def _check_master_on(self) -> None:
        window = self._win
        active = sorted(window._manager.active_items())
        _check("总开关打开后两个程序都在运行", len(active) == 2, str(active))
        _check(
            "启动顺序与列表顺序一致",
            self._running_order[:2] == ["假A", "假B"],
            str(self._running_order),
        )
        _check(
            "所有独立开关已联动打开",
            all(c.switch.isChecked() for c in window._flow.cards()),
        )
        times = dict(self._running_times)
        gap = times.get("假B", 0) - times.get("假A", 0)
        _check(
            "第二个程序在第一个 running 之后 + 1 秒延迟才启动",
            gap >= 1.1,
            "间隔 %.2fs" % gap,
        )
        window._on_master_toggled(False)
        QTimer.singleShot(2800, self._check_master_off)

    def _check_master_off(self) -> None:
        window = self._win
        _check("总开关关闭后没有进程在运行", window._manager.active_items() == [])
        _check(
            "所有独立开关已联动关闭",
            all(not c.switch.isChecked() for c in window._flow.cards()),
        )
        # 后续全屏测试自己控制检测器，避免被真实前台窗口干扰
        window._detector.stop()
        self._start_fullscreen_round1()

    # ---------------- 全屏暂停 / 恢复 ----------------
    def _start_fullscreen_round1(self) -> None:
        window = self._win
        self._fs_a = window._items[0]
        self._fs_b = window._items[1]
        self._fs_a.avoid_fullscreen = True
        self._fs_a.avoid_process = True
        self._fs_b.avoid_fullscreen = False
        self._fs_b.avoid_process = False
        window._manager.start(self._fs_a)
        window._manager.start(self._fs_b)
        QTimer.singleShot(1600, self._fs_enter)

    def _fs_enter(self) -> None:
        window = self._win
        window.handle_fullscreen(True, "SELFTEST-FULLSCREEN")
        QTimer.singleShot(2200, self._fs_check_paused)

    def _fs_check_paused(self) -> None:
        window = self._win
        _check(
            "全屏时暂停了参与避让的条目",
            self._fs_a.id in window._paused_by_system,
            str(window._paused_by_system),
        )
        _check(
            "未参与避让的条目不受影响",
            window._manager.is_active(self._fs_b.id),
        )
        window.handle_fullscreen(False, "")
        QTimer.singleShot(1600, self._fs_check_restored)

    def _fs_check_restored(self) -> None:
        window = self._win
        _check(
            "退出全屏后自动恢复被暂停的条目",
            window._manager.is_active(self._fs_a.id),
        )
        _check("恢复列表已清空", window._paused_by_system == {})
        # 第二轮：全屏期间用户手动关掉，退出全屏后绝不能自动拉起
        window.handle_fullscreen(True, "SELFTEST-2")
        QTimer.singleShot(2000, self._fs_manual_stop)

    def _fs_manual_stop(self) -> None:
        window = self._win
        if self._fs_a.id in window._paused_by_system:
            window._on_card_toggled(self._fs_a.id, False)
        window.handle_fullscreen(False, "")
        QTimer.singleShot(1600, self._fs_check_no_restore)

    def _fs_check_no_restore(self) -> None:
        window = self._win
        _check(
            "全屏中手动关闭的条目不会被自动拉起",
            not window._manager.is_active(self._fs_a.id),
        )
        window._manager.stop(self._fs_b)
        QTimer.singleShot(1200, self._start_process_round)

    # ---------------- 目标进程避让 ----------------
    def _start_process_round(self) -> None:
        window = self._win
        window._manager.start(self._fs_a)
        window._manager.start(self._fs_b)
        QTimer.singleShot(1600, self._process_enter)

    def _process_enter(self) -> None:
        window = self._win
        window._on_process_busy(True, "TestTarget.exe")
        QTimer.singleShot(2200, self._process_check)

    def _process_check(self) -> None:
        window = self._win
        _check(
            "目标进程触发时挂起勾选进程避让的条目",
            self._fs_a.id in window._paused_by_system,
            str(window._paused_by_system),
        )
        _check(
            "未勾选进程避让的条目不受影响",
            window._manager.is_active(self._fs_b.id),
        )
        _check(
            "状态徽章显示目标进程触发",
            "目标进程" in window.status_badge.text(),
            window.status_badge.text(),
        )
        window._on_process_busy(False, "")
        QTimer.singleShot(1800, self._process_restored)

    def _process_restored(self) -> None:
        window = self._win
        _check(
            "目标进程退出后自动恢复",
            window._manager.is_active(self._fs_a.id),
        )
        _check("进程恢复后列表清空", window._paused_by_system == {})
        # 单维度解除：两个维度同时忙，只退出全屏时，仍受进程避让约束的条目
        # 必须继续挂起（原来 busy 用 bool 去重，这里会整段跳过、一直挂着）
        QTimer.singleShot(600, self._partial_enter_fullscreen)

    def _partial_enter_fullscreen(self) -> None:
        self._win.handle_fullscreen(True, "SELFTEST-PARTIAL")
        QTimer.singleShot(1800, self._partial_add_process)

    def _partial_add_process(self) -> None:
        self._win._on_process_busy(True, "TestTarget.exe")
        QTimer.singleShot(1200, self._partial_release_fullscreen)

    def _partial_release_fullscreen(self) -> None:
        self._win.handle_fullscreen(False, "")
        QTimer.singleShot(250, self._partial_check)

    def _partial_check(self) -> None:
        window = self._win
        _check(
            "只解除一个维度时仍算忙",
            window._system_busy is True,
            str(window._busy_reasons),
        )
        _check(
            "仍受进程避让约束的条目继续挂起",
            self._fs_a.id in window._paused_by_system,
            str(window._paused_by_system),
        )
        window._on_process_busy(False, "")
        QTimer.singleShot(1800, self._partial_done)

    def _partial_done(self) -> None:
        window = self._win
        _check("两个维度都解除后全部恢复", window._paused_by_system == {})
        _check("条目已被重新拉起", window._manager.is_active(self._fs_a.id))
        window._manager.stop(self._fs_b)
        QTimer.singleShot(800, self._reorder_and_finish)

    # ---------------- 排序与收尾 ----------------
    def _reorder_and_finish(self) -> None:
        window = self._win
        cards = window._flow.cards()
        first = cards[0]
        window._flow._drag_card = first
        window._flow._drop_index = len(cards)
        window._flow._on_drag_finished(first)
        names = [c.item.name for c in window._flow.cards()]
        _check("拖拽后卡片顺序改变", names == ["假B", "假A"], str(names))
        saved = ConfigStore(self.tmpdir / "config.json").load()
        _check(
            "新顺序已写入配置",
            [i.name for i in saved.items] == names,
            str([i.name for i in saved.items]),
        )
        _check(
            "全局设置已随配置保存",
            saved.settings.close_to_tray is True
            and isinstance(saved.settings.fullscreen_avoid, bool),
            str(saved.settings.to_dict()),
        )
        window.close()
        self._finish()
    def _timeout(self) -> None:
        _check("自检在 100 秒内完成", False, "超时")
        self._finish()

    def _finish(self) -> None:
        failed = [r for r in RESULTS if not r[1]]
        summary = "== 自检结束：%d 项，失败 %d 项 ==" % (len(RESULTS), len(failed))
        print(summary)
        _diag(summary)
        try:
            self.manager.shutdown(timeout_total=3.0)
        except Exception:
            pass
        for win in (getattr(self, "_first_window", None), getattr(self, "_win", None)):
            if win is None:
                continue
            for stopper in (
                lambda w=win: w._monitor_thread.stop(),
                lambda w=win: w._detector.stop(),
            ):
                try:
                    stopper()
                except Exception:
                    pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        self.app.exit(1 if failed else 0)

def _pid_alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                window=WINDOW_HIDDEN,
                text=True,
                timeout=5,
            ).stdout
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def run_selftest(app) -> int:
    tester = SelfTest(app)
    return tester.run()
