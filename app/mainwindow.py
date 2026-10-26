"""主窗口：浅蓝渐变 + 流式卡片 + 托盘 + 日志面板 + 延迟级联 + 端口预检。"""

from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path

try:
    import psutil
except Exception:  # psutil 是可选依赖，缺了只是少了资源占用显示
    psutil = None

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import autostart
from .adopt import detect_running
from .assets import asset_path
from .card import LaunchCard
from .config import (
    ConfigDocument,
    ConfigStore,
    LaunchItem,
    MonitorTarget,
    WINDOW_LABELS,
    WINDOW_MODES,
    WINDOW_NORMAL,
)
from .detect import ensure_rules_file, find_for as detect_path_for
from .dialogs import ItemDialog, SettingsDialog
from .flow import CardFlow
from .fullscreen import DEFAULT_BLOCKED_PROCESSES, FullScreenDetector
from .logfile import RotatingLogWriter
from .monitor import ProcessMonitorThread
from .paths import data_dir, is_portable, log_dir, log_file, shrink_vars
from .processes import (
    ALLOWED_EXTS,
    STATE_ERROR,
    STATE_RUNNING,
    STATE_STOPPED,
    ProcessManager,
    psutil_available,
)
from .scan import AsyncRunner
from .switch import AnimatedSwitch
from .weblink import is_local_url, open_in_browser, pick_entry_url

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

CHAIN_TIMEOUT_MS = 20000
CHAIN_GAP_MS = 150
LOG_PANEL_HEIGHT = 170
LOG_MAX_LINES = 1200
LOG_FILE_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle("大肥鱼启动器")
        self.resize(1140, 800)
        self.setMinimumSize(760, 560)
        self.setAcceptDrops(True)   # 支持把 exe / bat 拖进窗口直接建卡片

        icon_path = asset_path("logo.png")
        self._app_icon = QIcon(icon_path) if Path(icon_path).exists() else QIcon()
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)

        self._store = ConfigStore()
        self._document = self._store.load()
        self._items: list[LaunchItem] = self._document.items
        self._settings = self._document.settings
        self._manager = ProcessManager(self)
        self._loading = True
        self._syncing_master = False
        self._force_quit = False
        self._tray_tip_shown = False
        # 系统忙碌状态机：全屏 OR 目标进程
        self._fullscreen_busy = False
        self._process_busy = False
        self._process_desc = ""
        self._system_busy = False
        # 忙碌原因集合：{"fullscreen"} / {"process"} / 两者都有。
        # 用集合而不是单个 bool，才能在只解除一个维度时精确恢复对应的条目。
        self._busy_reasons: set[str] = set()
        self._paused_by_system: dict[str, set[str]] = {}   # id -> 触发它的原因集合
        self._user_override_ids: set[str] = set()
        self._restore_token = 0
        self._deferred_start_ids: list[str] = []
        self._missing_paths: set[str] = set()
        self._opened_url: set[str] = set()   # 本轮启动已经打开过网页的条目

        # 总开关级联：等上一个进入 running 再启动下一个
        self._chain_queue: list[LaunchItem] = []
        self._chain_active = False
        self._chain_current: str | None = None
        self._chain_token = 0

        self._chain_timer = QTimer(self)
        self._chain_timer.setSingleShot(True)
        self._chain_timer.setInterval(CHAIN_TIMEOUT_MS)
        self._chain_timer.timeout.connect(self._on_chain_timeout)

        self._log_writer = RotatingLogWriter(
            log_file, LOG_FILE_MAX_BYTES, LOG_BACKUP_COUNT
        )
        self._log_open = False
        self._log_records: list[tuple[str, str, str]] = []  # (时间, 来源, 文本)

        self._build_ui()
        from .theme import apply_theme

        apply_theme(QApplication.instance(), self._settings.theme)
        self._populate_cards()
        self._loading = False
        self._refresh_log_filter()
        self._sync_master()
        self._tick_clock()
        self._center_on_screen()
        self._setup_tray()
        self._check_dependencies()
        self._announce_storage()
        # 首次运行落一份可编辑的探测规则，方便用户自己改
        try:
            ensure_rules_file()
        except Exception:
            pass

        # 时钟：30 秒一次，几乎无开销
        self._clock = QTimer(self)
        self._clock.setInterval(30000)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start()

        # 提示条淡入淡出
        self._toast_effect = QGraphicsOpacityEffect(self._toast)
        self._toast.setGraphicsEffect(self._toast_effect)
        self._toast_effect.setOpacity(0.0)
        self._toast_anim = QPropertyAnimation(self._toast_effect, b"opacity", self)
        self._toast_anim.setDuration(180)
        self._toast_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)

        self._manager.state_changed.connect(self._on_state_changed)
        self._manager.notice.connect(self._toast_message)
        self._manager.output.connect(self._on_process_output)
        self._manager.needs_admin.connect(self._on_needs_admin)
        self._manager.gave_up.connect(self._on_service_gave_up)

        # 全局热键：默认关闭，设置里可开
        from .hotkey import GlobalHotkeys, parse_hotkey

        self._hotkeys = GlobalHotkeys(QApplication.instance(), self)
        self._hotkeys.triggered.connect(self._on_hotkey)
        if self._settings.hotkeys_enabled:
            QTimer.singleShot(600, self._apply_hotkeys)

        # 后台采集：psutil 全表扫描（接管探测 / 残留检测）都放这里跑，别卡 UI
        self._scan = AsyncRunner(self)

        # 路径巡检：U 盘拔出/目录改名后把卡片置灰
        self._path_timer = QTimer(self)
        self._path_timer.setInterval(30000)
        self._path_timer.timeout.connect(self._check_paths)
        self._path_timer.start()
        QTimer.singleShot(1200, self._check_paths)
        QTimer.singleShot(800, self.adopt_running_services)

        # 资源占用采样：卡片右下角显示 CPU / 内存
        self._perf_procs: dict[str, object] = {}
        self._perf_timer = QTimer(self)
        self._perf_timer.setInterval(3000)
        self._perf_timer.timeout.connect(self._tick_perf)
        self._perf_timer.start()

        # 全屏检测器：后台每 1.2 秒采样一次，状态变化时回调
        self._detector = FullScreenDetector(
            self,
            blocked_processes=self._blocked_processes(),
            allow_processes=self._allow_processes(),
            blocked_titles=self._whitelist_titles(),
        )
        self._detector.changed.connect(self._on_fullscreen_changed)
        if self._settings.fullscreen_avoid:
            self._detector.start()

        # 目标进程监控：独立线程，每 3 秒扫一次，绝不在 UI 线程跑 psutil
        self._monitor_thread = ProcessMonitorThread(3.0, self)
        self._monitor_thread.changed.connect(self._on_process_busy)
        self._monitor_thread.set_targets(
            self._document.targets, self._settings.monitor_whitelist_processes
        )
        self._monitor_thread.start()
        self._update_status_badge()

    # ================= UI 构建 =================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 20)
        root.setSpacing(14)

        # ---- 顶部 ----
        header = QHBoxLayout()
        header.setSpacing(14)

        self._logo = QLabel()
        self._logo.setFixedSize(56, 56)
        logo_pix = self._logo_pixmap(56)
        if not logo_pix.isNull():
            self._logo.setPixmap(logo_pix)
        header.addWidget(self._logo)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self._title = QLabel("大肥鱼启动器")
        self._title.setObjectName("Title")
        self._subtitle = QLabel("")
        self._subtitle.setObjectName("Subtitle")
        title_box.addWidget(self._title)
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.status_badge = QLabel("🟢 监控中 (正常)", self)
        self.status_badge.setObjectName("StatusBadge")
        self.status_badge.setAlignment(Qt.AlignCenter)
        self.status_badge.setMinimumWidth(150)
        self.status_badge.setStyleSheet(
            "QLabel { background: rgba(52,199,89,0.14); color: #1E7A3C;"
            " border-radius: 14px; padding: 6px 14px; font-size: 12px; }"
        )
        header.addWidget(self.status_badge)

        capsule = QFrame()
        capsule.setObjectName("MasterCapsule")
        cap_layout = QHBoxLayout(capsule)
        cap_layout.setContentsMargins(20, 8, 12, 8)
        cap_layout.setSpacing(12)
        master_label = QLabel("全部启动")
        master_label.setObjectName("MasterLabel")
        self.master_switch = AnimatedSwitch(False, capsule)
        self.master_switch.toggled.connect(self._on_master_toggled)
        cap_layout.addWidget(master_label)
        cap_layout.addWidget(self.master_switch)
        header.addWidget(capsule)

        root.addLayout(header)

        # ---- 卡片区 ----
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._flow = CardFlow()
        # order_changed 只在这里连一次：_populate_cards 会被备份恢复等场景
        # 反复调用，重复 connect 会让一次重排触发多次保存
        self._flow.order_changed.connect(self._on_order_changed)
        self._scroll.setWidget(self._flow)
        root.addWidget(self._scroll, 1)

        # ---- 日志面板 ----
        self._log_panel = QFrame()
        self._log_panel.setObjectName("LogPanel")
        log_layout = QVBoxLayout(self._log_panel)
        log_layout.setContentsMargins(12, 8, 12, 10)
        log_layout.setSpacing(4)
        log_head = QHBoxLayout()
        log_head.addWidget(QLabel("运行日志"))
        self.log_filter = QComboBox()
        self.log_filter.addItem("全部", "")
        self.log_filter.currentIndexChanged.connect(self._render_log)
        log_head.addWidget(self.log_filter)
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("搜索关键字…")
        self.log_search.setClearButtonEnabled(True)
        self.log_search.setFixedWidth(180)
        self.log_search.setFixedHeight(24)
        self.log_search.textChanged.connect(self._render_log)
        log_head.addWidget(self.log_search)
        log_head.addStretch(1)
        export_btn = QPushButton("导出诊断包")
        export_btn.setObjectName("Ghost")
        export_btn.setFixedHeight(24)
        export_btn.clicked.connect(self._export_diagnostics)
        log_head.addWidget(export_btn)
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("Ghost")
        clear_btn.setFixedHeight(24)
        clear_btn.clicked.connect(lambda: self._log_edit.clear())
        log_head.addWidget(clear_btn)
        log_layout.addLayout(log_head)

        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setObjectName("LogView")
        self._log_edit.setPlaceholderText("子进程的输出会显示在这里…")
        self._log_edit.document().setMaximumBlockCount(LOG_MAX_LINES)
        log_layout.addWidget(self._log_edit, 1)

        self._log_panel.setMaximumHeight(0)
        self._log_panel.setMinimumHeight(0)
        root.addWidget(self._log_panel)

        self._log_anim = QPropertyAnimation(self._log_panel, b"maximumHeight", self)
        self._log_anim.setDuration(220)
        self._log_anim.setEasingCurve(QEasingCurve.OutCubic)

        # 子进程输出可能一秒几十行：渲染合并到 200ms 一次，别每行都重建文本
        self._log_render_timer = QTimer(self)
        self._log_render_timer.setSingleShot(True)
        self._log_render_timer.setInterval(200)
        self._log_render_timer.timeout.connect(self._render_log)

        # ---- 底部 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        self.add_btn = QPushButton("＋  添加启动项")
        self.add_btn.setObjectName("Primary")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._on_add)

        self.edit_btn = QPushButton("编辑模式")
        self.edit_btn.setObjectName("Ghost")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.toggled.connect(self._on_edit_mode)

        self.detect_btn = QPushButton("自动探测路径")
        self.detect_btn.setObjectName("Ghost")
        self.detect_btn.setCursor(Qt.PointingHandCursor)
        self.detect_btn.clicked.connect(self._on_detect)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("Ghost")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.clicked.connect(self._on_settings)

        self.groups_btn = QPushButton("分组")
        self.groups_btn.setObjectName("Ghost")
        self.groups_btn.setCursor(Qt.PointingHandCursor)
        self.groups_btn.clicked.connect(self._show_groups_menu)

        self.triggers_btn = QPushButton("触发器")
        self.triggers_btn.setObjectName("Ghost")
        self.triggers_btn.setCursor(Qt.PointingHandCursor)
        self.triggers_btn.clicked.connect(self._on_manage_targets)

        self.log_btn = QPushButton("日志")
        self.log_btn.setObjectName("Ghost")
        self.log_btn.setCheckable(True)
        self.log_btn.setCursor(Qt.PointingHandCursor)
        self.log_btn.toggled.connect(self._toggle_log_panel)

        self.stop_btn = QPushButton("全部停止")
        self.stop_btn.setObjectName("Ghost")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.clicked.connect(self._on_stop_all)

        bottom.addWidget(self.add_btn)
        bottom.addWidget(self.edit_btn)
        bottom.addWidget(self.detect_btn)
        bottom.addWidget(self.settings_btn)
        bottom.addWidget(self.groups_btn)
        bottom.addWidget(self.triggers_btn)
        bottom.addWidget(self.log_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.stop_btn)
        root.addLayout(bottom)

        # ---- 提示条 ----
        self._toast = QLabel("", self)
        self._toast.setObjectName("Toast")
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.hide()

    def _logo_pixmap(self, size: int) -> QPixmap:
        raw = QPixmap(asset_path("logo.png"))
        if raw.isNull():
            return QPixmap()
        scale = 2  # 高 DPI 也清晰
        target = size * scale
        scaled = raw.scaled(
            target, target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        canvas = QPixmap(target, target)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, target, target), target * 0.28, target * 0.28)
        painter.setClipPath(clip)
        x = (scaled.width() - target) // 2
        y = (scaled.height() - target) // 2
        painter.drawPixmap(-x, -y, scaled)
        painter.end()
        return canvas.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # ================= 托盘 =================
    def _setup_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._app_icon, self)
        self._tray.setToolTip("大肥鱼启动器")
        self._tray_menu = QMenu(self)
        self._tray_menu.aboutToShow.connect(self._rebuild_tray_menu)
        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray_icon_running = self._build_tray_icon(True)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()
        self._refresh_tray_status()

    def _build_tray_icon(self, running: bool) -> QIcon:
        """运行中时在托盘图标右下角点一颗绿点。"""
        try:
            base = self._app_icon.pixmap(32, 32)
        except Exception:
            return self._app_icon
        if base.isNull():
            return self._app_icon
        if not running:
            return QIcon(base)
        canvas = QPixmap(base.size())
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.drawPixmap(0, 0, base)
        radius = 5.5
        center = QPointF(
            canvas.width() - radius - 1.5, canvas.height() - radius - 1.5
        )
        painter.setPen(QPen(QColor(255, 255, 255, 235), 1.6))
        painter.setBrush(QColor(52, 199, 89))
        painter.drawEllipse(center, radius, radius)
        painter.end()
        return QIcon(canvas)

    def _refresh_tray_status(self) -> None:
        """托盘图标 + 提示随运行数变化。"""
        tray = getattr(self, "_tray", None)
        if tray is None:
            return
        try:
            active = sum(1 for item in self._items if self._manager.is_active(item.id))
        except Exception:
            active = 0
        running_icon = getattr(self, "_tray_icon_running", self._app_icon)
        tray.setIcon(running_icon if active else self._app_icon)
        suffix = f"{active} 个运行中" if active else "未运行"
        tray.setToolTip(f"大肥鱼启动器 · {suffix}")

    def _rebuild_tray_menu(self) -> None:
        menu = self._tray_menu
        menu.clear()
        menu.addAction("显示主界面", self.show_main_window)
        menu.addAction("扫描已运行服务", lambda: self.adopt_running_services(manual=True))
        menu.addAction("关于", self._about)
        menu.addSeparator()
        for item in self._items:
            running = self._manager.is_active(item.id)
            action = menu.addAction(("● " if running else "○ ") + item.name)
            action.triggered.connect(
                lambda _=False, i=item, r=running: self._on_card_toggled(i.id, not r)
            )
        if self._items:
            menu.addSeparator()
        menu.addAction("全部停止并退出", self._quit_app)

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_main_window()

    def show_main_window(self) -> None:
        """从托盘/最小化唤醒，并尽量抢到前台。"""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        try:
            import ctypes

            ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        except Exception:
            pass
        try:
            QApplication.alert(self, 0)
        except Exception:
            pass

    def start_minimized(self) -> None:
        """--minimized：开机自启时只进托盘，不弹窗。"""
        if not self._tray.isVisible():
            # 没有托盘可用时保持窗口显示，避免“隐形进程”
            return
        self.hide()
        if self._tray.isVisible() and not self._is_fullscreen():
            self._tray.showMessage(
                "大肥鱼启动器", "已在托盘运行，双击图标打开主界面。",
                QSystemTrayIcon.Information, 2500,
            )

    # ================= 依赖检查 =================
    def _is_fullscreen(self) -> bool:
        return bool(self._settings.fullscreen_avoid and self._system_busy)

    def _check_dependencies(self) -> None:
        if not psutil_available():
            QTimer.singleShot(
                600,
                lambda: QMessageBox.warning(
                    self,
                    "缺少可选依赖",
                    "没有找到 psutil，进程树清理和自动重启会降级为 taskkill。\n"
                    "建议重新安装：pip install psutil",
                ),
            )

    def _announce_storage(self) -> None:
        """程序目录不可写时，明确告诉用户配置被改存到哪了。"""
        if is_portable():
            return
        self._append_log_line(
            f"程序所在目录不可写，配置与日志已重定向到：{data_dir()}"
        )
        self._toast_message("程序目录不可写，配置已改存到用户目录（详见日志）")

    # ================= 资源占用 =================
    def _tick_perf(self) -> None:
        """每 3 秒采一次运行中条目的 CPU / 内存（含子进程）。"""
        if psutil is None:
            return
        for item in self._items:
            card = self._card(item.id)
            if card is None:
                continue
            pid = self._manager.pid_of(item.id)
            if not pid or not self._manager.is_active(item.id):
                card.set_perf("")
                self._perf_procs.pop(item.id, None)
                continue
            try:
                proc = self._perf_procs.get(item.id)
                if proc is None or getattr(proc, "pid", None) != pid:
                    proc = psutil.Process(pid)
                    proc.cpu_percent(None)      # 建立基线，下次才有真实值
                    self._perf_procs[item.id] = proc
                    continue
                cpu = proc.cpu_percent(None)
                rss = proc.memory_info().rss
                for child in proc.children(recursive=True):
                    try:
                        rss += child.memory_info().rss
                    except Exception:
                        continue
                card.set_perf(f"{cpu:.0f}% · {rss / 1024 / 1024:.0f}M")
            except Exception:
                card.set_perf("")
                self._perf_procs.pop(item.id, None)

    # ================= 拖拽添加 =================
    @staticmethod
    def _paths_from_mime(mime) -> list[str]:
        """从拖拽数据里挑出可启动的文件。"""
        try:
            if not mime.hasUrls():
                return []
            urls = mime.urls()
        except Exception:
            return []
        paths: list[str] = []
        for url in urls:
            try:
                if not url.isLocalFile():
                    continue
                candidate = Path(url.toLocalFile())
            except Exception:
                continue
            # 扩展名判断放在 try 外面：别把编程错误也一起吞掉
            if candidate.is_file() and candidate.suffix.lower() in ALLOWED_EXTS:
                paths.append(str(candidate))
        return paths

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = self._paths_from_mime(event.mimeData())
        if not paths:
            return
        event.acceptProposedAction()
        for raw in paths:
            path = Path(raw)
            self._append_card(
                LaunchItem(
                    name=path.stem,
                    path=shrink_vars(str(path)),
                    cwd=shrink_vars(str(path.parent)),
                )
            )
        self._save()
        self._refresh_log_filter()
        self._toast_message(f"已添加 {len(paths)} 个启动项")

    # ================= 卡片 =================
    def _append_card(self, item: LaunchItem, animate: bool = True) -> LaunchCard:
        """把一个条目接到列表末尾并生成卡片（拖拽 / 导入 / 新建共用）。"""
        self._items.append(item)
        card = LaunchCard(item)
        card.toggled.connect(self._on_card_toggled)
        card.delete_requested.connect(self._on_delete)
        card.edit_requested.connect(self._on_edit)
        card.menu_requested.connect(self._show_card_menu)
        self._flow.add_card(card, animate=animate)
        return card

    def _populate_cards(self) -> None:
        # 重建期间屏蔽 order_changed：add_card 每次都会 emit，
        # 否则备份恢复时会把半成品顺序反复写盘
        self._loading = True
        try:
            self._flow.clear()
            for item in self._items:
                card = LaunchCard(item)
                card.toggled.connect(self._on_card_toggled)
                card.delete_requested.connect(self._on_delete)
                card.edit_requested.connect(self._on_edit)
                card.menu_requested.connect(self._show_card_menu)
                self._flow.add_card(card, animate=False)
        finally:
            self._loading = False

    def _card(self, item_id: str) -> LaunchCard | None:
        for card in self._flow.cards():
            if card.item.id == item_id:
                return card
        return None

    def _item(self, item_id: str) -> LaunchItem | None:
        for item in self._items:
            if item.id == item_id:
                return item
        return None

    def _on_order_changed(self) -> None:
        if self._loading:
            return
        self._items = [card.item for card in self._flow.cards()]
        self._save()

    def _save(self) -> None:
        try:
            self._document = ConfigDocument(
                items=self._items,
                settings=self._settings,
                targets=self._document.targets,
            )
            self._store.save(self._document)
        except Exception as exc:
            self._toast_message(f"配置保存失败：{exc}")

    # ================= 启停 =================
    @staticmethod
    def _port_owner(port: int) -> tuple[int, str] | None:
        """找出监听该端口的进程；查不到返回 None（可能权限不足）。"""
        if psutil is None or port <= 0:
            return None
        try:
            connections = psutil.net_connections(kind="inet")
        except Exception:
            return None
        for conn in connections:
            try:
                if not conn.laddr or conn.laddr.port != port:
                    continue
                if conn.status != psutil.CONN_LISTEN or not conn.pid:
                    continue
                proc = psutil.Process(conn.pid)
                return conn.pid, proc.name()
            except Exception:
                continue
        return None

    def _ask_port_conflict(self, item: LaunchItem) -> bool:
        """端口被占用：显示占用者，提供「结束它并启动」。返回是否继续启动。"""
        port = item.check_port
        owner = self._port_owner(port)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("端口已被占用")
        box.setText(f"127.0.0.1:{port} 已被占用")
        if owner is not None:
            box.setInformativeText(
                f"占用进程：{owner[1]}（PID {owner[0]}）\n"
                f"「{item.name}」可能已经在运行，或上次退出后有残留进程。"
            )
        else:
            box.setInformativeText(
                "没能查到占用进程（可能需要管理员权限）。\n"
                f"「{item.name}」可能已经在运行，或上次退出后有残留进程。"
            )
        kill_btn = (
            box.addButton("结束它并启动", QMessageBox.DestructiveRole)
            if owner is not None
            else None
        )
        force_btn = box.addButton("强行启动", QMessageBox.AcceptRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        clicked = box.clickedButton()

        if kill_btn is not None and clicked is kill_btn:
            self._kill_port_owner(item, owner)
            return True
        if clicked is force_btn:
            return True
        self._toast_message(f"已取消启动「{item.name}」（端口 {port} 被占用）。")
        return False

    def _kill_port_owner(self, item: LaunchItem, owner: tuple[int, str]) -> None:
        """结束占用端口的进程，并等端口真正释放。"""
        pid, name = owner
        if psutil is None:
            return
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=2.5)
            except Exception:
                proc.kill()
                proc.wait(timeout=1.5)
            self._append_log_line(
                f"已结束占用 {item.check_port} 端口的进程：{name}（PID {pid}）"
            )
            self._toast_message(f"已结束 {name}（PID {pid}）")
        except Exception as exc:
            self._append_log_line(f"结束占用进程失败：{exc}")
            self._toast_message(f"结束进程失败：{exc}")
            return
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not self._port_in_use(item.check_port):
                return
            QApplication.processEvents()
            time.sleep(0.05)

    def _port_in_use(self, port: int) -> bool:
        if port <= 0:
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.35)
        try:
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
        except Exception:
            return False
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _try_start(self, item: LaunchItem, source: str = "user") -> bool:
        """统一的启动入口：路径校验 → 端口预检 → 真正启动。"""
        target = item.resolved_path()
        if not target:
            self._toast_message(f"「{item.name}」还没有设置路径，点卡片上的“设置”补上。")
            return False
        if not Path(target).exists():
            self._toast_message(f"路径不存在：{target}")
            return False

        if item.check_port > 0 and self._port_in_use(item.check_port):
            if not self._ask_port_conflict(item):
                return False

        # 系统忙碌期间的启动策略：手动启动尊重用户，链条启动延迟，恢复动作跳过
        if self._system_busy:
            if source == "chain":
                if item.id not in self._deferred_start_ids:
                    self._deferred_start_ids.append(item.id)
                self._append_log_line(f"系统正忙，「{item.name}」延后到空闲后启动。")
                return False
            if source == "restore":
                return False
            self._user_override_ids.add(item.id)
            self._paused_by_system.pop(item.id, None)
            card = self._card(item.id)
            if card is not None:
                card.set_system_suspended(False)

        card = self._card(item.id)
        if card is not None:
            card.set_adopted(False)
        self._opened_url.discard(item.id)   # 新一轮启动，允许再打开一次网页
        return self._manager.start(item)

    # ---------------- 系统忙碌状态机（全屏 OR 目标进程） ----------------
    def _blocked_processes(self) -> set[str]:
        blocked = set(DEFAULT_BLOCKED_PROCESSES)
        raw = (self._settings.fullscreen_block_processes or "").replace("，", ",")
        for part in raw.replace(";", ",").split(","):
            name = part.strip().lower()
            if name:
                blocked.add(name if name.endswith(".exe") else f"{name}.exe")
        return blocked

    def _allow_processes(self) -> set[str]:
        allow: set[str] = set()
        raw = (self._settings.fullscreen_allow_processes or "").replace("，", ",")
        for part in raw.replace(";", ",").split(","):
            name = part.strip().lower()
            if name:
                allow.add(name if name.endswith(".exe") else f"{name}.exe")
        return allow

    def _whitelist_titles(self) -> list[str]:
        raw = (self._settings.monitor_whitelist_titles or "").replace("，", ",")
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]

    # ---- 两个维度的输入 ----
    def _on_fullscreen_changed(self, is_fullscreen: bool, title: str = "") -> None:
        if not self._settings.fullscreen_avoid:
            return
        if is_fullscreen == self._fullscreen_busy:
            return
        self._fullscreen_busy = is_fullscreen
        if is_fullscreen:
            self._append_log_line(f"检测到全屏窗口「{title or '未命名'}」。")
        else:
            self._append_log_line("已退出全屏。")
        self._refresh_system_busy()

    def _on_process_busy(self, busy: bool, desc: str = "") -> None:
        if busy == self._process_busy and desc == self._process_desc:
            return
        self._process_busy = busy
        self._process_desc = desc if busy else ""
        if busy:
            self._append_log_line(f"监控目标正在运行：{desc}")
        else:
            self._append_log_line("监控目标已全部退出。")
        self._refresh_system_busy()

    # ---- 合并成 System Busy ----
    REASON_TEXT = {"fullscreen": "全屏", "process": "目标进程"}

    def _current_reasons(self) -> set[str]:
        reasons: set[str] = set()
        if self._fullscreen_busy:
            reasons.add("fullscreen")
        if self._process_busy:
            reasons.add("process")
        return reasons

    @classmethod
    def _reasons_text(cls, reasons) -> str:
        order = ("fullscreen", "process")
        return "、".join(cls.REASON_TEXT[r] for r in order if r in reasons)

    def _refresh_system_busy(self) -> None:
        reasons = self._current_reasons()
        self._update_status_badge()
        if reasons == self._busy_reasons:
            return               # 状态没变，不重复挂起/恢复
        released = self._busy_reasons - reasons
        self._busy_reasons = reasons
        busy = bool(reasons)
        self._system_busy = busy
        self._manager.set_system_busy(busy)

        if not busy:
            self._restore_paused()
            self._flush_deferred_starts()
            return

        # 有维度解除了（例如退出全屏，但目标进程还在跑）：
        # 只把"触发原因已经全部消失"的条目恢复，其余的继续挂起。
        # 原来这里用 bool 去重，busy 仍为 True 就整段跳过，那些条目会被一直挂着。
        if released:
            self._restore_released()
        self._pause_services()

    def _restore_released(self) -> None:
        """按当前忙碌原因重新评估每个挂起条目。

        注意不能只看"当初是什么原因把它挂起来的"：一个同时勾了全屏避让和
        进程避让的条目，可能在全屏阶段就被挂起（记录只有 fullscreen），
        之后进程也忙起来；此时退出全屏，它仍然该因为"进程还在跑"保持挂起。
        所以这里按条目自身的开关 + 当前忙碌原因重新算一遍。
        """
        if not self._paused_by_system:
            return
        ready: list[LaunchItem] = []
        for item_id, recorded in list(self._paused_by_system.items()):
            item = self._item(item_id)
            if item is None:
                self._paused_by_system.pop(item_id, None)
                continue
            active: set[str] = set()
            if "fullscreen" in self._busy_reasons and item.avoid_fullscreen:
                active.add("fullscreen")
            if "process" in self._busy_reasons and item.avoid_process:
                active.add("process")
            if active:
                # 还有原因适用：更新记录与卡片文案，继续挂起
                if active != recorded:
                    self._paused_by_system[item_id] = active
                    card = self._card(item_id)
                    if card is not None:
                        card.set_system_suspended(True, self._reasons_text(active))
                continue
            self._paused_by_system.pop(item_id, None)
            if item_id in self._user_override_ids:
                continue
            if self._manager.is_active(item_id):
                continue
            ready.append(item)
        if not ready:
            return
        self._restore_token += 1
        token = self._restore_token
        scheduled: list[str] = []
        step = 0
        for item in ready:
            target = item.resolved_path()
            if not target or not Path(target).exists():
                self._append_log_line(f"「{item.name}」恢复时发现路径失效，跳过。")
                continue
            step += 1
            QTimer.singleShot(
                step * 250,
                lambda target=item, expect=token: self._restore_one(
                    target, expect, allow_busy=True
                ),
            )
            scheduled.append(item.name)
        if scheduled:
            self._append_log_line(
                f"避让条件部分解除（{self._status_text()}），恢复：{'、'.join(scheduled)}。"
            )

    def _status_text(self) -> str:
        if self._fullscreen_busy and self._process_busy:
            return "全屏 + 目标进程运行中"
        if self._fullscreen_busy:
            return "全屏运行中"
        if self._process_busy:
            return f"目标进程运行中（{self._process_desc}）"
        return ""

    def _pause_services(self) -> None:
        if not self._system_busy:
            return
        paused_names: list[str] = []
        for item in self._items:
            if not self._manager.is_active(item.id):
                continue
            if item.id in self._user_override_ids:
                continue
            reasons: set[str] = set()
            if self._fullscreen_busy and item.avoid_fullscreen:
                reasons.add("fullscreen")
            if self._process_busy and item.avoid_process:
                reasons.add("process")
            if not reasons:
                continue
            self._manager.stop(item)
            reason_text = self._reasons_text(reasons)
            self._paused_by_system[item.id] = reasons
            card = self._card(item.id)
            if card is not None:
                card.set_system_suspended(True, reason_text)
            paused_names.append(item.name)
        if paused_names:
            self._append_log_line(
                f"触发避让（{self._status_text()}），已挂起：{'、'.join(paused_names)}。"
            )
            self._toast_message(f"触发避让，已挂起 {len(paused_names)} 个程序。")

    def _restore_paused(self) -> None:
        """只恢复因系统忙碌被挂起的条目；用 QTimer 排队，不 sleep。"""
        if not self._paused_by_system:
            return
        pending = list(self._paused_by_system.items())
        self._paused_by_system.clear()
        self._restore_token += 1
        token = self._restore_token
        scheduled: list[str] = []
        step = 0
        for item_id, _reason in pending:
            if item_id in self._user_override_ids:
                continue
            item = self._item(item_id)
            if item is None or self._manager.is_active(item_id):
                continue
            target = item.resolved_path()
            if not target or not Path(target).exists():
                self._append_log_line(f"「{item.name}」恢复时发现路径失效，跳过。")
                continue
            step += 1
            QTimer.singleShot(
                step * 250,
                lambda target=item, expect=token: self._restore_one(target, expect),
            )
            scheduled.append(item.name)
        self._user_override_ids.clear()
        if scheduled:
            self._append_log_line(f"系统恢复空闲，自动恢复：{'、'.join(scheduled)}。")

    def _restore_one(self, item: LaunchItem, token: int, allow_busy: bool = False) -> None:
        if token != self._restore_token:
            return
        # allow_busy：只解除一个维度时，系统整体仍算忙，但这一条该恢复
        if self._system_busy and not allow_busy:
            return
        if item.id in self._paused_by_system:
            return
        card = self._card(item.id)
        if card is not None:
            card.set_system_suspended(False)
        if self._manager.start(item):
            self._toast_message(f"已恢复「{item.name}」")

    def _flush_deferred_starts(self) -> None:
        if not self._deferred_start_ids:
            return
        pending = list(self._deferred_start_ids)
        self._deferred_start_ids.clear()
        for index, item_id in enumerate(pending):
            item = self._item(item_id)
            if item is None or self._manager.is_active(item_id):
                continue
            QTimer.singleShot(200 * index, lambda i=item: self._try_start(i, source="chain"))

    def _update_status_badge(self) -> None:
        badge = getattr(self, "status_badge", None)
        if badge is None:
            return
        if self._fullscreen_busy:
            text, color, bg = "🟡 触发避让：全屏运行中", "#8A5A00", "rgba(255,193,7,0.20)"
        elif self._process_busy:
            text, color, bg = "🟡 触发避让：目标进程运行中", "#8A5A00", "rgba(255,193,7,0.20)"
        else:
            text, color, bg = "🟢 监控中 (正常)", "#1E7A3C", "rgba(52,199,89,0.14)"
        badge.setText(text)
        badge.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {color};"
            " border-radius: 14px; padding: 6px 14px; font-size: 12px; }"
        )
        badge.setToolTip(self._process_desc or "")

    # 兼容旧接口（测试/外部调用）
    def handle_fullscreen(self, is_fullscreen: bool, title: str = "") -> None:
        self._on_fullscreen_changed(is_fullscreen, title)

    def restore_paused_apps(self) -> None:
        self._restore_paused()
    def _on_card_toggled(self, item_id: str, checked: bool) -> None:
        item = self._item(item_id)
        card = self._card(item_id)
        if item is None or card is None:
            return
        if checked:
            if not self._try_start(item):
                card.switch.setChecked(False)
        else:
            self._paused_by_system.pop(item_id, None)
            card.set_system_suspended(False)
            self._manager.stop(item)
        self._sync_master()

    # ---------------- 总开关级联 ----------------
    def _on_master_toggled(self, checked: bool) -> None:
        if self._syncing_master:
            return
        if checked:
            for card in self._flow.cards():
                card.switch.setChecked(True)
            self._abort_chain()
            self._chain_queue = list(self._items)
            self._chain_active = True
            self._start_next_in_chain()
        else:
            self._abort_chain()
            for card in self._flow.cards():
                card.switch.setChecked(False)
            self._manager.stop_all([c.item.id for c in self._flow.cards()])
        self._sync_master()

    def _start_next_in_chain(self) -> None:
        """按顺序启动下一个：先等它的 delay_seconds，再等它进入 running。"""
        if not self._chain_active:
            return
        while self._chain_queue:
            item = self._chain_queue.pop(0)
            card = self._card(item.id)
            target = item.resolved_path()
            if not target or not Path(target).exists():
                if card is not None:
                    card.switch.setChecked(False)
                self._toast_message(f"「{item.name}」缺少路径或路径不存在，已跳过。")
                continue

            token = self._chain_token
            delay_ms = max(0, int(item.delay_seconds)) * 1000

            def launch(target: LaunchItem = item, expect: int = token) -> None:
                if not self._chain_active or expect != self._chain_token:
                    return
                self._chain_current = target.id
                self._chain_timer.start(CHAIN_TIMEOUT_MS)
                if not self._try_start(target, source="chain"):
                    self._chain_current = None
                    self._chain_timer.stop()
                    QTimer.singleShot(0, self._start_next_in_chain)

            if delay_ms > 0:
                self._chain_current = item.id
                self._chain_timer.start(delay_ms + CHAIN_TIMEOUT_MS)
                self._toast_message(f"「{item.name}」将在 {item.delay_seconds} 秒后启动…")
                QTimer.singleShot(delay_ms, launch)
            else:
                launch()
            return

        # 队列清空
        self._chain_active = False
        self._chain_current = None
        self._chain_timer.stop()

    def _advance_chain(self) -> None:
        self._chain_current = None
        self._chain_timer.stop()
        QTimer.singleShot(CHAIN_GAP_MS, self._start_next_in_chain)

    def _on_chain_timeout(self) -> None:
        if not self._chain_active or self._chain_current is None:
            return
        item = self._item(self._chain_current)
        name = item.name if item else "上一个程序"
        self._toast_message(f"「{name}」20 秒内未进入运行状态，继续启动下一个。")
        self._advance_chain()

    def _abort_chain(self) -> None:
        self._chain_token += 1
        self._chain_queue.clear()
        self._chain_active = False
        self._chain_current = None
        self._chain_timer.stop()

    def _on_stop_all(self) -> None:
        self.master_switch.setChecked(False)
        self._abort_chain()
        for card in self._flow.cards():
            card.switch.setChecked(False)
        self._manager.stop_all([c.item.id for c in self._flow.cards()])

    # ================= 全局热键 =================
    def _apply_hotkeys(self) -> None:
        """按设置开关注册 / 注销全局热键。"""
        if not self._settings.hotkeys_enabled:
            self._hotkeys.unregister()
            return
        if self._hotkeys.is_registered:
            return
        start = parse_hotkey(self._settings.hotkey_start)
        stop = parse_hotkey(self._settings.hotkey_stop)
        if self._hotkeys.register(start, stop):
            self._append_log_line(f"全局热键已启用：{self._hotkeys.describe()}")
            self._toast_message(f"全局热键已启用：{self._hotkeys.describe()}")
        else:
            self._append_log_line("全局热键注册失败：组合可能被其它程序占用")
            self._toast_message("全局热键注册失败：组合可能被占用，换个组合试试")

    def _on_hotkey(self, action: str) -> None:
        if action == "start_all":
            if self.master_switch.isChecked():
                self._on_master_toggled(True)
            else:
                self.master_switch.setChecked(True)
            self._toast_message("热键：全部启动")
        else:
            self._on_stop_all()
            self._toast_message("热键：全部停止")

    def _on_state_changed(self, item_id: str, state: str) -> None:
        card = self._card(item_id)
        if card is not None:
            card.set_state(state)

        if state == STATE_RUNNING:
            self._maybe_open_url(item_id)
        self._refresh_tray_status()

        if self._chain_active and item_id == self._chain_current:
            if state == STATE_RUNNING:
                self._advance_chain()
            elif state in {STATE_STOPPED, STATE_ERROR}:
                self._advance_chain()

        if state in {STATE_STOPPED, STATE_ERROR}:
            if card is not None:
                card.set_adopted(False)
            if card is not None and item_id not in self._paused_by_system:
                card.set_system_suspended(False)
            self._sync_master()
            QTimer.singleShot(900, lambda i=item_id: self._check_residual(i))

    # ---------------- P1：残留 / 提权 / 路径 ----------------
    def _check_residual(self, item_id: str) -> None:
        item = self._item(item_id)
        if item is None or item_id in self._paused_by_system:
            return
        # 残留检测要遍历全系统进程表（100–500ms），放后台线程，别卡 UI
        self._scan.run(
            f"residual:{item_id}",
            lambda target=item: self._manager.residual_processes(target),
            lambda leftovers, target=item: self._apply_residual(target, leftovers),
        )

    def _apply_residual(self, item: LaunchItem, leftovers) -> None:
        if not leftovers or self._item(item.id) is None:
            return
        pids = "、".join(str(pid) for pid, _name in leftovers[:5])
        self._append_log_line(
            f"「{item.name}」停止后仍有残留进程（PID {pids}），可手动结束或点“全部停止”再试。"
        )
        self._toast_message(f"「{item.name}」可能有残留进程，详见日志。")

    def _on_service_gave_up(self, item_id: str, name: str) -> None:
        """连续异常退出、放弃自动重启时，用托盘气泡提醒（只写日志太容易漏看）。"""
        card = self._card(item_id)
        if card is not None:
            card.set_alert("已停止（重启失败）")
        self._append_log_line(f"「{name}」连续异常退出，已放弃自动重启，请查看日志")
        self._toast_message(f"「{name}」反复退出，已停止自动重启")
        try:
            if self._tray is not None and self._tray.isVisible():
                self._tray.showMessage(
                    "服务已停止",
                    f"「{name}」连续异常退出，已放弃自动重启。\n点开卡片看看「日志」。",
                    QSystemTrayIcon.Warning,
                    8000,
                )
        except Exception:
            pass

    def _on_needs_admin(self, item_id: str, name: str) -> None:
        answer = QMessageBox.question(
            self,
            "需要管理员权限",
            f"「{name}」需要管理员权限才能启动。\n是否以管理员身份重新启动本启动器？\n（重启后需要你手动再点一次启动）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._relaunch_as_admin()

    def _relaunch_as_admin(self) -> None:
        import ctypes
        import sys as _sys

        try:
            if getattr(_sys, "frozen", False):
                params = "--elevated"
                target = _sys.executable
            else:
                params = f'"{Path(_sys.argv[0]).resolve()}" --elevated'
                target = _sys.executable
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", target, params, None, 1
            )
            if result <= 32:
                self._toast_message("提权被取消或失败。")
                return
            self._quit_app()
        except Exception as exc:
            self._toast_message(f"提权失败：{exc}")

    def adopt_running_services(self, manual: bool = False) -> None:
        """接管已经在运行的条目（按路径/命令行/端口/名称）。

        manual=True 来自托盘菜单，忽略“启动时自动扫描”开关；
        每个条目仍受它自己的 allow_adopt 控制。

        `detect_running()` 要遍历全系统进程表，逐条目跑在主线程会让启动后
        0.8 秒那一下明显卡顿，所以扫描放后台线程，结果回主线程再接管。
        """
        if not manual and not self._settings.adopt_on_startup:
            self._append_log_line("启动接管已关闭（设置里可开启）。")
            return
        if self._scan.is_busy("adopt"):
            return

        skipped = 0
        targets: list[LaunchItem] = []
        for item in self._items:
            if not item.resolved_path() or self._manager.is_active(item.id):
                continue
            if not item.allow_adopt:
                skipped += 1
                continue
            targets.append(item)

        if not targets:
            extra = f"（{skipped} 个条目设置为不接管）" if skipped else ""
            self._append_log_line(f"扫描：没有发现可接管的运行中服务。{extra}")
            return

        def work() -> list[tuple[str, int, str, str]]:
            found: list[tuple[str, int, str, str]] = []
            for item in targets:
                try:
                    match = detect_running(item)
                except Exception:
                    match = None
                if match is not None:
                    found.append(
                        (item.id, int(match.pid), match.name or "", match.how or "")
                    )
            return found

        self._scan.run(
            "adopt", work, lambda found: self._apply_adopt(found, skipped)
        )

    def _apply_adopt(self, found, skipped: int) -> None:
        adopted = 0
        for item_id, pid, name, how in found or []:
            item = self._item(item_id)
            if item is None or self._manager.is_active(item_id):
                continue
            if self._manager.adopt(item, pid, name):
                card = self._card(item_id)
                if card is not None:
                    card.set_adopted(True)
                adopted += 1
                self._append_log_line(
                    f"接管已运行服务：「{item.name}」PID {pid}（匹配方式：{how}）"
                )
        if adopted:
            self._toast_message(f"已接管 {adopted} 个正在运行的服务")
        else:
            extra = f"（{skipped} 个条目设置为不接管）" if skipped else ""
            self._append_log_line(f"扫描：没有发现可接管的运行中服务。{extra}")

    def _check_paths(self) -> None:
        missing: set[str] = set()
        for item in self._items:
            target = item.resolved_path()
            if not target:
                continue
            exists = Path(target).exists()
            card = self._card(item.id)
            if card is not None:
                card.set_available(exists)
            if not exists:
                missing.add(item.id)
                if item.id not in self._missing_paths:
                    self._append_log_line(f"「{item.name}」路径不可用：{target}")
        self._missing_paths = missing

    def _sync_master(self) -> None:
        if self._chain_active:
            return
        cards = self._flow.cards()
        if not cards:
            self.master_switch.setChecked(False)
            return
        all_on = all(card.switch.isChecked() for card in cards)
        if self.master_switch.isChecked() != all_on:
            self.master_switch.setChecked(all_on, animate=True)

    # ================= 日志 =================
    def _toggle_log_panel(self, checked: bool) -> None:
        self._log_open = checked
        start = self._log_panel.maximumHeight()
        end = LOG_PANEL_HEIGHT if checked else 0
        self._log_anim.stop()
        self._log_anim.setStartValue(start)
        self._log_anim.setEndValue(end)
        self._log_anim.start()

    def _on_process_output(self, item_id: str, line: str) -> None:
        item = self._item(item_id)
        name = item.name if item else item_id
        stamp = datetime.now().strftime("%H:%M:%S")
        self._log_records.append((stamp, name, line))
        if len(self._log_records) > 2000:
            self._log_records = self._log_records[-1200:]
        self._schedule_log_render()
        self._append_log_file(f"[{stamp}] [{name}] {line}")
        if item is not None:
            self._watch_output_url(item, line)

    # ---------------- 自动打开服务地址 ----------------
    def _maybe_open_url(self, item_id: str) -> None:
        """进程进入运行中：如果有固定地址（就绪端口 / 手填），安排打开。"""
        item = self._item(item_id)
        if item is None or not item.auto_open or item_id in self._opened_url:
            return
        url = item.resolve_open_url()
        if url:
            self._schedule_open(item, url)

    def _watch_output_url(self, item: LaunchItem, line: str) -> None:
        """没有固定地址时，从输出里找（只有「后台运行」形态才有输出）。"""
        if not item.auto_open or item.id in self._opened_url:
            return
        if item.resolve_open_url():
            return   # 已经有固定地址，不用看输出
        url = pick_entry_url(line)
        if url:
            self._schedule_open(item, url)

    def _schedule_open(self, item: LaunchItem, url: str) -> None:
        if item.id in self._opened_url:
            return
        self._opened_url.add(item.id)
        QTimer.singleShot(
            1500,
            lambda: (
                self._open_url_now(item, url)
                if self._manager.is_active(item.id)
                else None
            ),
        )

    def _copy_item_json(self, item: LaunchItem) -> None:
        """把单个条目导出成 JSON，方便发给别人。"""
        payload = item.to_dict()
        payload.pop("id", None)   # id 由导入方重新生成
        try:
            QApplication.clipboard().setText(
                json.dumps(payload, ensure_ascii=False, indent=2)
            )
            self._toast_message("配置已复制到剪贴板，可以直接发给别人")
        except Exception as exc:
            self._toast_message(f"复制失败：{exc}")

    def _import_item_from_clipboard(self) -> None:
        """从剪贴板导入一个或多个条目（支持对象或数组）。"""
        text = QApplication.clipboard().text().strip()
        if not text:
            self._toast_message("剪贴板是空的")
            return
        try:
            data = json.loads(text)
        except Exception:
            self._toast_message("剪贴板内容不是有效的 JSON 配置")
            return
        if isinstance(data, dict):
            entries = [data]
        elif isinstance(data, list):
            entries = [entry for entry in data if isinstance(entry, dict)]
        else:
            entries = []
        entries = [
            entry for entry in entries if "name" in entry or "path" in entry
        ]
        if not entries:
            self._toast_message("剪贴板里没有可导入的启动项")
            return
        for entry in entries:
            entry.pop("id", None)
            self._append_card(LaunchItem.from_dict(entry))
        self._save()
        self._refresh_log_filter()
        self._toast_message(f"已从剪贴板导入 {len(entries)} 个启动项")

    def _open_url_now(self, item: LaunchItem, url: str) -> None:
        if not is_local_url(url):
            self._append_log_line(
                f"「{item.name}」的地址不在本机/局域网范围，已拒绝打开：{url}"
            )
            return
        if open_in_browser(url):
            self._append_log_line(f"已打开「{item.name}」的地址：{url}")
            self._toast_message(f"已打开 {url}")
        else:
            self._append_log_line(f"打开地址失败：{url}")

    def _render_log(self) -> None:
        current = self.log_filter.currentData() if hasattr(self, "log_filter") else ""
        keyword = ""
        if hasattr(self, "log_search"):
            keyword = self.log_search.text().strip().lower()
        rows = [
            r
            for r in self._log_records
            if (not current or r[1] == current)
            and (not keyword or keyword in r[2].lower() or keyword in r[1].lower())
        ][-400:]
        scrollbar = self._log_edit.verticalScrollBar()
        stick_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        saved_value = scrollbar.value()
        self._log_edit.setPlainText(
            "\n".join(f"[{stamp}] [{name}] {text}" for stamp, name, text in rows)
        )
        # 只有本来就在底部才跟随滚动，否则保持用户的阅读位置不被拽走
        if stick_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(saved_value, scrollbar.maximum()))

    def _schedule_log_render(self) -> None:
        """200ms 合并窗口：期间来多少行都只重建一次日志文本。"""
        if not self._log_render_timer.isActive():
            self._log_render_timer.start()

    def _refresh_log_filter(self) -> None:
        if not hasattr(self, "log_filter"):
            return
        current = self.log_filter.currentData()
        self.log_filter.blockSignals(True)
        self.log_filter.clear()
        self.log_filter.addItem("全部", "")
        for item in self._items:
            self.log_filter.addItem(item.name, item.name)
        index = self.log_filter.findData(current)
        self.log_filter.setCurrentIndex(max(0, index))
        self.log_filter.blockSignals(False)
        self._render_log()

    def _append_log_line(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self._log_records.append((stamp, "启动器", text))
        self._schedule_log_render()
        self._append_log_file(f"[{stamp}] [启动器] {text}")

    def _append_log_file(self, text: str) -> None:
        self._log_writer.write(text)

    # ================= 增删改 =================
    def _on_add(self) -> None:
        dialog = ItemDialog(None, self)
        if dialog.exec() != ItemDialog.Accepted:
            return
        item = dialog.result_item()
        self._append_card(item, animate=True)
        self._save()
        self._refresh_log_filter()
        self._toast_message(f"已添加「{item.name}」")

    def _on_edit(self, item_id: str) -> None:
        item = self._item(item_id)
        card = self._card(item_id)
        if item is None or card is None:
            return
        dialog = ItemDialog(item, self)
        if dialog.exec() != ItemDialog.Accepted:
            return
        updated = dialog.result_item()
        card.update_item(updated)
        self._save()
        self._refresh_log_filter()
        self._toast_message(f"已更新「{updated.name}」")

    def _on_delete(self, item_id: str) -> None:
        item = self._item(item_id)
        card = self._card(item_id)
        if item is None or card is None:
            return
        answer = QMessageBox.question(
            self,
            "删除启动项",
            f"确定要删除「{item.name}」吗？\n只会从列表移除，不会删除磁盘上的文件。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self._manager.is_active(item_id):
            self._manager.stop(item)
        self._items = [i for i in self._items if i.id != item_id]
        self._paused_by_system.pop(item_id, None)
        self._user_override_ids.discard(item_id)
        self._flow.remove_card(card)
        self._save()
        self._refresh_log_filter()
        self._sync_master()

    def _on_edit_mode(self, enabled: bool) -> None:
        for card in self._flow.cards():
            card.set_edit_mode(enabled)
        self._toast_message(
            "编辑模式：可拖动卡片排序，或点卡片上的按钮。" if enabled else "已退出编辑模式"
        )

    def _on_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec() != SettingsDialog.Accepted:
            return
        values = dialog.result_settings()
        ok, message = autostart.set_autostart(values["autostart"])
        if not ok:
            QMessageBox.warning(self, "开机自启", message)
        self._settings.autostart = autostart.is_autostart_enabled()
        self._settings.close_to_tray = values["close_to_tray"]
        self._settings.fullscreen_avoid = values["fullscreen_avoid"]
        self._settings.fullscreen_block_processes = values["fullscreen_block"]
        self._settings.fullscreen_allow_processes = values["fullscreen_allow"]
        self._settings.monitor_whitelist_processes = values["whitelist_proc"]
        self._settings.monitor_whitelist_titles = values["whitelist_title"]
        self._settings.theme = values["theme"]
        self._settings.adopt_on_startup = values["adopt_on_startup"]
        self._settings.show_splash = values["show_splash"]
        restore_path = values["restore_path"]
        theme = values["theme"]
        hotkeys_enabled = values["hotkeys_enabled"]
        from .theme import apply_theme

        apply_theme(QApplication.instance(), theme)
        for card in self._flow.cards():
            card.refresh_theme()
        self._settings.hotkeys_enabled = hotkeys_enabled
        self._settings.hotkey_start = values["hotkey_start"]
        self._settings.hotkey_stop = values["hotkey_stop"]
        self._hotkeys.unregister()          # 组合可能变了，先注销再按新设置注册
        self._apply_hotkeys()
        self._save()

        if restore_path:
            self._restore_config_backup(Path(restore_path))
        self._detector.configure(
            blocked_processes=self._blocked_processes(),
            allow_processes=self._allow_processes(),
            blocked_titles=self._whitelist_titles(),
        )
        if self._settings.fullscreen_avoid:
            self._detector.start()
        else:
            self._detector.stop()
            self._fullscreen_busy = False
        self._monitor_thread.set_targets(
            self._document.targets, self._settings.monitor_whitelist_processes
        )
        self._refresh_system_busy()
        self._toast_message(message if ok else "设置已保存（开机自启未生效）")

    # ---------------- 卡片右键 / 分组 / 关于 ----------------
    def _show_card_menu(self, item_id: str, global_pos) -> None:
        item = self._item(item_id)
        if item is None:
            return
        menu = QMenu(self)
        running = self._manager.is_active(item_id)
        act_start = menu.addAction("停止" if running else "启动")
        act_dir = menu.addAction("打开所在目录")
        act_copy = menu.addAction("复制路径")
        act_copy_cfg = menu.addAction("复制配置（JSON）")
        act_paste_cfg = menu.addAction("从剪贴板添加条目")
        mode_menu = menu.addMenu("窗口形态")
        for mode in WINDOW_MODES:
            mode_action = mode_menu.addAction(WINDOW_LABELS[mode])
            mode_action.setCheckable(True)
            mode_action.setChecked(item.window == mode)
            mode_action.triggered.connect(
                lambda _=False, m=mode, i=item_id: self._set_window_mode(i, m)
            )
        open_target = item.resolve_open_url()
        act_web = menu.addAction("打开网页") if open_target else None
        menu.addSeparator()
        act_edit = menu.addAction("设置…")
        act_delete = menu.addAction("删除")
        chosen = menu.exec(global_pos)
        if chosen is act_start:
            self._on_card_toggled(item_id, not running)
        elif chosen is act_dir:
            try:
                target_path = item.resolved_path()
                folder = Path(target_path).parent if target_path else Path.home()
                os.startfile(str(folder))  # type: ignore[attr-defined]
            except Exception as exc:
                self._toast_message(f"打开目录失败：{exc}")
        elif chosen is act_copy:
            QApplication.clipboard().setText(item.resolved_path())
            self._toast_message("路径已复制")
        elif act_web is not None and chosen is act_web:
            self._open_url_now(item, open_target)
        elif chosen is act_copy_cfg:
            self._copy_item_json(item)
        elif chosen is act_paste_cfg:
            self._import_item_from_clipboard()
        elif chosen is act_edit:
            self._on_edit(item_id)
        elif chosen is act_delete:
            self._on_delete(item_id)

    def _set_window_mode(self, item_id: str, mode: str) -> None:
        """卡片右键快捷切换窗口形态。运行中的条目下次启动才生效。"""
        item = self._item(item_id)
        card = self._card(item_id)
        if item is None or card is None:
            return
        target = mode if mode in WINDOW_MODES else WINDOW_NORMAL
        if item.window == target:
            return
        item.window = target
        card.update_item(item)
        self._save()
        suffix = "（当前正在运行，重启该条目后生效）" if self._manager.is_active(item_id) else ""
        self._toast_message(
            f"「{item.name}」窗口形态：{WINDOW_LABELS.get(target, target)}{suffix}"
        )

    def _show_groups_menu(self) -> None:
        groups = sorted({i.group.strip() for i in self._items if i.group.strip()})
        menu = QMenu(self)
        if not groups:
            action = menu.addAction("还没有分组（在条目设置里填写）")
            action.setEnabled(False)
        for group in groups:
            sub = menu.addMenu(group)
            sub.addAction(
                "启动本组",
                lambda g=group: self._start_group(g),
            )
            sub.addAction(
                "停止本组",
                lambda g=group: self._stop_group(g),
            )
        menu.exec(self.groups_btn.mapToGlobal(self.groups_btn.rect().bottomLeft()))

    def _group_items(self, group: str):
        return [i for i in self._items if i.group.strip() == group]

    def _start_group(self, group: str) -> None:
        items = self._group_items(group)
        for index, item in enumerate(items):
            QTimer.singleShot(index * 300, lambda i=item: self._try_start(i, source="chain"))

    def _stop_group(self, group: str) -> None:
        for item in self._group_items(group):
            self._manager.stop(item)

    def _about(self) -> None:
        from . import __version__

        mode = "便携模式（配置跟随 exe）" if is_portable() else "配置已重定向到用户目录"
        QMessageBox.information(
            self,
            "关于",
            f"大肥鱼启动器 v{__version__}\n\n"
            "本地程序启动管理器（便携版）\n"
            f"运行模式：{mode}\n"
            f"配置：{self._store.path}\n"
            f"日志：{log_file()}\n\n"
            "单实例运行 · 托盘常驻 · 全屏/进程避让",
        )

    def _export_diagnostics(self) -> None:
        default = data_dir() / f"diagnostics-{datetime.now():%Y%m%d-%H%M%S}.zip"
        target, _ = QFileDialog.getSaveFileName(
            self, "导出诊断包", str(default), "Zip (*.zip)"
        )
        if not target:
            return
        try:
            import platform
            import zipfile

            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
                if self._store.path.exists():
                    bundle.write(self._store.path, "config.json")
                # *.log* 覆盖 launcher.log、launcher.log.old、crash.log
                for log in sorted(log_dir().glob("*.log*")):
                    bundle.write(log, f"logs/{log.name}")
                info = [
                    f"version={__import__('app').__version__}",
                    f"python={platform.python_version()}",
                    f"platform={platform.platform()}",
                    f"frozen={getattr(__import__('sys'), 'frozen', False)}",
                    f"data_dir={data_dir()}",
                    f"portable={is_portable()}",
                    f"items={len(self._items)} targets={len(self._document.targets)}",
                    f"busy={self._system_busy} fullscreen={self._fullscreen_busy} process={self._process_busy}",
                    f"paused={self._paused_by_system}",
                ]
                bundle.writestr("system.txt", "\n".join(info))
            self._toast_message("诊断包已导出")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _on_manage_targets(self) -> None:
        from .dialogs import MonitorTargetsDialog

        dialog = MonitorTargetsDialog(self._document.targets, self)
        if dialog.exec() != MonitorTargetsDialog.Accepted:
            return
        self._document.targets = dialog.result_targets()
        self._monitor_thread.set_targets(
            self._document.targets, self._settings.monitor_whitelist_processes
        )
        self._save()
        enabled = len([t for t in self._document.targets if t.enabled])
        self._toast_message(f"触发器已保存：{enabled} 个启用")
        self._append_log_line(f"触发器配置更新，启用 {enabled} 个。")

    def _restore_config_backup(self, backup: Path) -> None:
        answer = QMessageBox.question(
            self,
            "恢复配置备份",
            f"用这个备份覆盖当前配置？\n{backup.name}\n\n当前配置会先自动备份一份。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self._save()  # 先留一份当前快照
            document = self._store.restore_backup(backup)
        except Exception as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))
            return
        self._document = document
        self._items = document.items
        self._settings = document.settings
        self._populate_cards()
        self._refresh_log_filter()
        self._detector.configure(
            blocked_processes=self._blocked_processes(),
            allow_processes=self._allow_processes(),
            blocked_titles=self._whitelist_titles(),
        )
        self._monitor_thread.set_targets(
            self._document.targets, self._settings.monitor_whitelist_processes
        )
        self._toast_message("配置已从备份恢复")
        self._append_log_line(f"已从备份恢复配置：{backup.name}")

    def _on_detect(self) -> None:
        """按 detect_rules.json 里的规则给空路径条目填上目标文件。"""
        filled = 0
        for item in self._items:
            if item.resolved_path():
                continue
            found = detect_path_for(item.name)
            if found is None:
                continue
            item.path = shrink_vars(str(found))
            if not item.cwd.strip():
                item.cwd = shrink_vars(str(found.parent))
            card = self._card(item.id)
            if card is not None:
                card.update_item(item)
            filled += 1
        if filled:
            self._save()
            self._check_paths()
            self._toast_message(f"已探测并填入 {filled} 个路径，启动前请再核对一遍。")
        else:
            self._toast_message("没有可探测的新路径；已设置的条目不会被覆盖。")

    # ================= 提示 =================
    def _toast_message(self, text: str) -> None:
        if not text:
            return
        self._toast.setText(text)
        self._toast.adjustSize()
        self._place_toast()
        self._toast.show()
        self._toast.raise_()
        self._toast_anim.stop()
        self._toast_anim.setStartValue(self._toast_effect.opacity())
        self._toast_anim.setEndValue(1.0)
        self._toast_anim.start()
        self._toast_timer.start(2800)

    def _hide_toast(self) -> None:
        self._toast_anim.stop()
        self._toast_anim.setStartValue(self._toast_effect.opacity())
        self._toast_anim.setEndValue(0.0)
        self._toast_anim.start()
        QTimer.singleShot(220, self._toast.hide)

    def _place_toast(self) -> None:
        x = (self.width() - self._toast.width()) // 2
        y = self.height() - self._toast.height() - 84
        self._toast.move(QPoint(max(12, x), max(12, y)))

    # ================= 其它 =================
    def _restore_geometry(self) -> bool:
        raw = (self._settings.window_geometry or "").strip()
        if not raw:
            return False
        try:
            x, y, w, h = (int(v) for v in raw.split(","))
        except Exception:
            return False
        if w < 600 or h < 400:
            return False
        for screen in QApplication.screens():
            if screen.availableGeometry().intersects(QRect(x, y, w, h)):
                self.setGeometry(x, y, w, h)
                return True
        return False

    def _save_geometry(self) -> None:
        try:
            g = self.geometry()
            self._settings.window_geometry = f"{g.x()},{g.y()},{g.width()},{g.height()}"
        except Exception:
            pass

    def _center_on_screen(self) -> None:
        """启动时恢复上次位置，否则居中。"""
        if self._restore_geometry():
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = min(self.width(), available.width() - 40)
        height = min(self.height(), available.height() - 40)
        self.resize(width, height)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + (available.height() - height) // 2,
        )

    def _tick_clock(self) -> None:
        now = datetime.now()
        self._subtitle.setText(
            f"{now:%Y-%m-%d %H:%M}  ·  {WEEKDAYS[now.weekday()]}  ·  "
            f"共 {len(self._items)} 个启动项"
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._toast.isVisible():
            self._place_toast()

    # ================= 退出 =================
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._force_quit:
            try:
                self._monitor_thread.stop()
            except Exception:
                pass
            try:
                self._detector.stop()
            except Exception:
                pass
            self._clock.stop()
            event.accept()
            return

        if self._settings.close_to_tray and self._tray.isVisible():
            event.ignore()
            self.hide()
            if not self._tray_tip_shown:
                self._tray_tip_shown = True
                if not self._is_fullscreen():
                    self._tray.showMessage(
                        "大肥鱼启动器",
                        "已最小化到托盘，右键图标可“全部停止并退出”。",
                        QSystemTrayIcon.Information,
                        2500,
                    )
            return

        # 未启用托盘：沿用“退出前询问是否停止进程”
        active = self._manager.active_items()
        if active:
            box = QMessageBox(self)
            box.setWindowTitle("退出启动器")
            box.setText(f"还有 {len(active)} 个由启动器拉起的程序在运行。")
            box.setInformativeText("要同时停止它们吗？（选择“仅退出”则它们会继续在后台运行）")
            stop_btn = box.addButton("停止并退出", QMessageBox.DestructiveRole)
            keep_btn = box.addButton("仅退出", QMessageBox.AcceptRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is None or clicked not in (stop_btn, keep_btn):
                event.ignore()
                return
            if clicked is stop_btn:
                self._manager.shutdown()

        self._shutdown_ui()
        QApplication.quit()
        event.accept()

    def _shutdown_ui(self) -> None:
        """退出前统一停掉定时器 / 动画 / 托盘，避免 Qt 析构期崩溃。"""
        try:
            self._detector.stop()
        except Exception:
            pass
        try:
            self._scan.shutdown()
        except Exception:
            pass
        try:
            self._monitor_thread.stop()
        except Exception:
            pass
        for timer in (
            getattr(self, "_clock", None),
            getattr(self, "_toast_timer", None),
            getattr(self, "_log_render_timer", None),
            getattr(self, "_chain_timer", None),
            getattr(self, "_manager", None) and self._manager._poll_timer,
        ):
            try:
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        for anim in (getattr(self, "_toast_anim", None), getattr(self, "_log_anim", None)):
            try:
                if anim is not None:
                    anim.stop()
            except Exception:
                pass
        try:
            self._flow.shutdown()
        except Exception:
            pass
        try:
            self._toast.hide()
        except Exception:
            pass
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.hide()
            except Exception:
                pass
        try:
            self._log_writer.close()
        except Exception:
            pass
        try:
            self._hotkeys.unregister()
        except Exception:
            pass
        try:
            QApplication.processEvents()
        except Exception:
            pass

    def prepare_headless_exit(self) -> None:
        """给没有事件循环的场景（--screenshot）用：停掉后台线程与定时器再退出。

        否则 QThread 在运行中被销毁，Qt 会报
        "QThread: Destroyed while thread is still running" 并可能直接 abort。
        """
        self._force_quit = True
        self._shutdown_ui()
        try:
            self.close()
        except Exception:
            pass

    def _quit_app(self) -> None:
        self._save_geometry()
        self._force_quit = True
        # 先把窗口和托盘收掉：结束进程最多要等几秒，界面留在那儿会像卡死
        try:
            self.hide()
        except Exception:
            pass
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.hide()
            except Exception:
                pass
        try:
            self._manager.shutdown()
        except Exception:
            pass
        self._shutdown_ui()
        self.close()
        QApplication.quit()
