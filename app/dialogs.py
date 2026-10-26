"""添加 / 编辑启动项对话框 + 全局设置对话框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .autostart import is_autostart_enabled
from .config import (
    AppSettings,
    LaunchItem,
    MonitorTarget,
    WINDOW_LABELS,
    WINDOW_MINIMIZED,
    WINDOW_MODES,
    WINDOW_NORMAL,
)
from .paths import expand_vars, shrink_vars

FILE_FILTER = "可执行 / 脚本 (*.exe *.bat *.cmd *.lnk *.com);;所有文件 (*.*)"


class ItemDialog(QDialog):
    def __init__(
        self,
        item: LaunchItem | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self.setWindowTitle("编辑启动项" if item else "添加启动项")
        self.setModal(True)
        # 900px 高度在 1366x768 这类小屏上会把保存按钮顶出屏幕，夹到可用高度
        screen = QGuiApplication.primaryScreen()
        max_height = screen.availableGeometry().height() - 60 if screen else 840
        self.resize(580, min(900, max_height))
        self.setMinimumWidth(500)

        # 主体放进滚动区：小屏上滚动查看字段，标题和保存按钮始终可见
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 20)
        outer.setSpacing(12)

        title = QLabel("编辑启动项" if item else "添加启动项")
        title.setObjectName("DialogTitle")
        outer.addWidget(title)

        form_host = QWidget()
        root = QVBoxLayout(form_host)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(form_host)
        outer.addWidget(form_scroll, 1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.name_edit = QLineEdit(item.name if item else "")
        self.name_edit.setPlaceholderText("例如：AstrBot 桌面版")
        form.addRow(self._label("显示名称"), self.name_edit)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.path_edit = QLineEdit(item.path if item else "")
        self.path_edit.setPlaceholderText(r"选择 .exe / .bat / .cmd / .lnk")
        browse = QPushButton("浏览…")
        browse.setObjectName("Ghost")
        browse.clicked.connect(self._browse_file)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        form.addRow(self._label("目标文件"), self._wrap(path_row))

        self.args_edit = QLineEdit(item.args if item else "")
        self.args_edit.setPlaceholderText("可留空，例如：--port 8000")
        form.addRow(self._label("启动参数"), self.args_edit)

        cwd_row = QHBoxLayout()
        cwd_row.setSpacing(8)
        self.cwd_edit = QLineEdit(item.cwd if item else "")
        self.cwd_edit.setPlaceholderText("留空 = 目标文件所在目录（推荐留空）")
        browse_dir = QPushButton("选择…")
        browse_dir.setObjectName("Ghost")
        browse_dir.clicked.connect(self._browse_dir)
        cwd_row.addWidget(self.cwd_edit, 1)
        cwd_row.addWidget(browse_dir)
        form.addRow(self._label("工作目录"), self._wrap(cwd_row))

        self.window_combo = QComboBox()
        for mode in WINDOW_MODES:
            self.window_combo.addItem(WINDOW_LABELS[mode], mode)
        current_mode = item.window if item else WINDOW_NORMAL
        index = self.window_combo.findData(current_mode)
        self.window_combo.setCurrentIndex(max(0, index))
        self.window_combo.setToolTip(
            "正常窗口：显示控制台窗口，输出在里面\n"
            "最小化到任务栏：窗口最小化，点任务栏图标可恢复查看\n"
            "后台运行：完全不弹窗，输出进「日志」面板"
        )
        form.addRow(self._label("窗口形态"), self.window_combo)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 600)
        self.delay_spin.setSuffix(" 秒")
        self.delay_spin.setValue(int(item.delay_seconds) if item else 0)
        self.delay_spin.setToolTip("总开关顺序启动时，轮到它之后再等这么久（不阻塞界面）")
        form.addRow(self._label("延迟启动"), self.delay_spin)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setSpecialValueText("不检测")
        self.port_spin.setValue(int(item.check_port) if item else 0)
        self.port_spin.setToolTip("启动前检查 127.0.0.1:端口 是否已被占用，0 = 不检测")
        form.addRow(self._label("端口预检"), self.port_spin)

        self.auto_restart_check = QCheckBox("异常退出后 3 秒自动重启（最多 5 次）")
        self.auto_restart_check.setChecked(bool(item.auto_restart) if item else False)
        form.addRow(self._label("自动重启"), self.auto_restart_check)

        self.ready_port_spin = QSpinBox()
        self.ready_port_spin.setRange(0, 65535)
        self.ready_port_spin.setSpecialValueText("不检测")
        self.ready_port_spin.setValue(int(item.ready_port) if item else 0)
        self.ready_port_spin.setToolTip("启动后等这个端口监听成功才算“运行中”（用于总开关级联）")
        form.addRow(self._label("就绪端口"), self.ready_port_spin)

        self.ready_timeout_spin = QSpinBox()
        self.ready_timeout_spin.setRange(5, 600)
        self.ready_timeout_spin.setSuffix(" 秒")
        self.ready_timeout_spin.setValue(int(item.ready_timeout) if item else 60)
        form.addRow(self._label("就绪超时"), self.ready_timeout_spin)

        self.auto_open_check = QCheckBox("启动后自动用浏览器打开服务地址")
        self.auto_open_check.setChecked(bool(item.auto_open) if item else False)
        self.auto_open_check.setToolTip(
            "只支持本机/局域网地址（127.0.0.1、localhost、192.168.x.x 等）。\n"
            "填了「就绪端口」时，端口一通就打开 http://127.0.0.1:端口/"
        )
        form.addRow(self._label("自动打开"), self.auto_open_check)

        self.open_url_edit = QLineEdit(item.open_url if item else "")
        self.open_url_edit.setPlaceholderText("可留空；填了就打开它（优先级低于就绪端口）")
        self.open_url_edit.setToolTip("只支持本机/局域网地址，外部网址会被拒绝")
        form.addRow(self._label("打开的地址"), self.open_url_edit)

        self.group_edit = QLineEdit(item.group if item else "")
        self.group_edit.setPlaceholderText("可选，例如：语音 / QQ / 工作")
        form.addRow(self._label("分组"), self.group_edit)

        self.avoid_fullscreen_check = QCheckBox("参与全屏避让（全屏时自动暂停）")
        self.avoid_fullscreen_check.setChecked(bool(item.avoid_fullscreen) if item else True)
        form.addRow(self._label("全屏避让"), self.avoid_fullscreen_check)

        self.avoid_process_check = QCheckBox("参与目标进程避让（触发器命中时自动暂停）")
        self.avoid_process_check.setChecked(bool(item.avoid_process) if item else True)
        form.addRow(self._label("进程避让"), self.avoid_process_check)

        self.allow_adopt_check = QCheckBox("允许启动时自动接管已在运行的进程")
        self.allow_adopt_check.setChecked(bool(item.allow_adopt) if item else True)
        form.addRow(self._label("启动接管"), self.allow_adopt_check)

        self.icon_edit = QLineEdit(item.icon if item else "🚀")
        self.icon_edit.setMaxLength(4)
        self.icon_edit.setFixedWidth(90)
        self.icon_edit.setPlaceholderText("🚀")
        form.addRow(self._label("图标字符"), self.icon_edit)

        self._icon_path = item.icon_path if item else ""
        icon_row = QWidget()
        icon_row_layout = QHBoxLayout(icon_row)
        icon_row_layout.setContentsMargins(0, 0, 0, 0)
        icon_row_layout.setSpacing(8)

        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(40, 40)
        self.icon_preview.setAlignment(Qt.AlignCenter)
        icon_row_layout.addWidget(self.icon_preview)

        icon_pick_btn = QPushButton("选择图片…")
        icon_pick_btn.setObjectName("Ghost")
        icon_pick_btn.clicked.connect(self._choose_icon)
        icon_row_layout.addWidget(icon_pick_btn)

        icon_clear_btn = QPushButton("清除")
        icon_clear_btn.setObjectName("Ghost")
        icon_clear_btn.clicked.connect(self._clear_icon)
        icon_row_layout.addWidget(icon_clear_btn)
        icon_row_layout.addStretch(1)

        form.addRow(self._label("图标图片"), icon_row)
        icon_hint = QLabel("支持 png / jpg / webp / ico / svg；设置后优先于上面的图标字符。")
        icon_hint.setObjectName("Subtitle")
        icon_hint.setWordWrap(True)
        form.addRow("", icon_hint)
        self._refresh_icon_preview()

        root.addLayout(form)

        # 路径落在程序目录下时提示会转成 ${APP} 形式（换电脑仍可用）
        self.portable_hint = QLabel("")
        self.portable_hint.setObjectName("Subtitle")
        self.portable_hint.setWordWrap(True)
        self.portable_hint.hide()
        root.addWidget(self.portable_hint)
        self.path_edit.textChanged.connect(self._refresh_portable_hint)
        self.cwd_edit.textChanged.connect(self._refresh_portable_hint)
        self._refresh_portable_hint()

        root.addStretch(1)

        hint = QLabel("提示：路径可以先留空，之后在卡片上点“设置”补上。")
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._on_accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        outer.addLayout(buttons)

    # ---------------- 内部 ----------------
    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        label.setFont(QFont("Microsoft YaHei UI", 9))
        return label

    @staticmethod
    def _wrap(layout: QHBoxLayout) -> QWidget:
        holder = QWidget()
        holder.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return holder

    def _browse_file(self) -> None:
        start = self.path_edit.text().strip()
        start_dir = str(Path(start).parent) if start else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择启动文件", start_dir, FILE_FILTER
        )
        if path:
            self.path_edit.setText(path)
            if not self.cwd_edit.text().strip():
                self.cwd_edit.setText(str(Path(path).parent))
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(path).stem)

    def _browse_dir(self) -> None:
        start = self.cwd_edit.text().strip() or self.path_edit.text().strip()
        directory = QFileDialog.getExistingDirectory(self, "选择工作目录", start)
        if directory:
            self.cwd_edit.setText(directory)

    def _refresh_portable_hint(self) -> None:
        rows: list[str] = []
        for label, text in (
            ("目标", self.path_edit.text()),
            ("目录", self.cwd_edit.text()),
        ):
            raw = text.strip()
            if not raw:
                continue
            portable = shrink_vars(raw)
            if portable != raw:
                rows.append(f"{label} → {portable}")
        if rows:
            self.portable_hint.setText(
                "✓ 位于启动器目录内，保存时会转成便携路径（换电脑后依然有效）：\n"
                + "\n".join(rows)
            )
            self.portable_hint.show()
        else:
            self.portable_hint.hide()

    def _choose_icon(self) -> None:
        start = ""
        if self._icon_path:
            start = str(Path(expand_vars(self._icon_path)).parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图标图片",
            start,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.ico *.svg);;所有文件 (*.*)",
        )
        if path:
            self._icon_path = shrink_vars(path)
            self._refresh_icon_preview()

    def _clear_icon(self) -> None:
        self._icon_path = ""
        self._refresh_icon_preview()

    def _refresh_icon_preview(self) -> None:
        from .card import load_icon_pixmap

        pixmap = load_icon_pixmap(expand_vars(self._icon_path), 40, 12)
        if pixmap is not None:
            self.icon_preview.setText("")
            self.icon_preview.setPixmap(pixmap)
            self.icon_preview.setStyleSheet("background: transparent;")
            self.icon_preview.setToolTip(self._icon_path)
        else:
            self.icon_preview.setPixmap(QPixmap())
            self.icon_preview.setText(self.icon_edit.text() or "🚀")
            self.icon_preview.setStyleSheet(
                "background: rgba(255,255,255,0.9); border-radius: 12px;"
                " font-size: 20px;"
            )
            self.icon_preview.setToolTip("没有图片，使用图标字符")

    def _on_accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setFocus()
            self.name_edit.setPlaceholderText("请先填写显示名称")
            return
        self.accept()

    def result_item(self) -> LaunchItem:
        name = self.name_edit.text().strip() or "未命名启动项"
        path = shrink_vars(self.path_edit.text().strip())
        args = self.args_edit.text().strip()
        cwd = shrink_vars(self.cwd_edit.text().strip())
        window = self.window_combo.currentData() or WINDOW_NORMAL
        icon = self.icon_edit.text().strip() or "🚀"
        delay = int(self.delay_spin.value())
        port = int(self.port_spin.value())
        auto_restart = bool(self.auto_restart_check.isChecked())
        avoid_fullscreen = bool(self.avoid_fullscreen_check.isChecked())
        avoid_process = bool(self.avoid_process_check.isChecked())
        ready_port = int(self.ready_port_spin.value())
        ready_timeout = int(self.ready_timeout_spin.value())
        group = self.group_edit.text().strip()
        allow_adopt = bool(self.allow_adopt_check.isChecked())
        auto_open = bool(self.auto_open_check.isChecked())
        open_url = self.open_url_edit.text().strip()

        if self._item is not None:
            item = self._item
            item.name = name
            item.path = path
            item.args = args
            item.cwd = cwd
            item.window = window
            item.icon = icon
            item.icon_path = self._icon_path
            item.delay_seconds = delay
            item.check_port = port
            item.auto_restart = auto_restart
            item.avoid_fullscreen = avoid_fullscreen
            item.avoid_process = avoid_process
            item.ready_port = ready_port
            item.ready_timeout = ready_timeout
            item.group = group
            item.allow_adopt = allow_adopt
            item.auto_open = auto_open
            item.open_url = open_url
            return item
        return LaunchItem(
            name=name,
            path=path,
            args=args,
            cwd=cwd,
            window=window,
            icon=icon,
            icon_path=self._icon_path,
            delay_seconds=delay,
            check_port=port,
            auto_restart=auto_restart,
            avoid_fullscreen=avoid_fullscreen,
            avoid_process=avoid_process,
            ready_port=ready_port,
            ready_timeout=ready_timeout,
            group=group,
            allow_adopt=allow_adopt,
            auto_open=auto_open,
            open_url=open_url,
        )


class SettingsDialog(QDialog):
    """全局设置：开机自启 + 关闭行为。"""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("设置")
        self.setModal(True)
        # 同 ItemDialog：小屏上夹到可用高度，主体可滚动
        screen = QGuiApplication.primaryScreen()
        max_height = screen.availableGeometry().height() - 60 if screen else 740
        self.resize(520, min(800, max_height))

        # 主体放进滚动区：小屏上滚动查看设置项，保存按钮始终可见
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 20)
        outer.setSpacing(14)

        title = QLabel("设置")
        title.setObjectName("DialogTitle")
        outer.addWidget(title)

        body_host = QWidget()
        root = QVBoxLayout(body_host)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        body_scroll = QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setWidget(body_host)
        outer.addWidget(body_scroll, 1)

        actual_autostart = is_autostart_enabled()
        self.autostart_check = QCheckBox("开机自动启动（最小化到托盘）")
        self.autostart_check.setChecked(actual_autostart)
        root.addWidget(self.autostart_check)

        autostart_hint = QLabel(
            "写入 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run，\n"
            "路径使用当前 exe 的真实位置（移动文件夹后请重新勾选一次）。"
        )
        autostart_hint.setObjectName("Subtitle")
        autostart_hint.setWordWrap(True)
        root.addWidget(autostart_hint)

        self.fullscreen_check = QCheckBox("全屏时暂停参与避让的程序（退出全屏后自动恢复）")
        self.fullscreen_check.setChecked(bool(settings.fullscreen_avoid))
        root.addWidget(self.fullscreen_check)

        fullscreen_hint = QLabel("适用于游戏/全屏视频：只暂停勾选了「参与全屏避让」的条目，退出全屏后按原顺序恢复。")
        fullscreen_hint.setObjectName("Subtitle")
        fullscreen_hint.setWordWrap(True)
        root.addWidget(fullscreen_hint)

        # 全屏判定的两份进程名单：以前只能手改 config.json，这里补上入口
        root.addWidget(QLabel("额外判定为全屏的进程"))
        self.fullscreen_block_edit = QLineEdit(
            settings.fullscreen_block_processes or ""
        )
        self.fullscreen_block_edit.setPlaceholderText("可选，例如：chrome.exe, firefox.exe")
        root.addWidget(self.fullscreen_block_edit)
        block_hint = QLabel(
            "内置名单（播放器、浏览器、explorer 等）之外，再把这些进程也算作全屏。"
            "逗号分隔，可省略 .exe。"
        )
        block_hint.setObjectName("Subtitle")
        block_hint.setWordWrap(True)
        root.addWidget(block_hint)

        root.addWidget(QLabel("不计入全屏的进程"))
        self.fullscreen_allow_edit = QLineEdit(
            settings.fullscreen_allow_processes or ""
        )
        self.fullscreen_allow_edit.setPlaceholderText("可选，例如：potplayer.exe")
        root.addWidget(self.fullscreen_allow_edit)
        allow_hint = QLabel(
            "优先级最高：即使它在上面的名单或内置名单里，也不会触发暂停。"
            "逗号分隔，可省略 .exe。"
        )
        allow_hint.setObjectName("Subtitle")
        allow_hint.setWordWrap(True)
        root.addWidget(allow_hint)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("跟随系统", "auto")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        index = self.theme_combo.findData(settings.theme)
        self.theme_combo.setCurrentIndex(max(0, index))
        root.addWidget(QLabel("界面主题"))
        root.addWidget(self.theme_combo)

        self.splash_check = QCheckBox("启动时显示开机动画")
        self.splash_check.setChecked(bool(settings.show_splash))
        self.splash_check.setToolTip(
            "把 boot.webp 或 boot.gif 放到启动器同目录即可替换内置动画；\n"
            "删掉外部文件则回落到内置的那份。"
        )
        root.addWidget(self.splash_check)
        splash_hint = QLabel(
            "替换动画：把 boot.webp / boot.gif 放到启动器同目录即可，无需重新打包。"
            "开屏时点任意处可跳过。"
        )
        splash_hint.setObjectName("Subtitle")
        splash_hint.setWordWrap(True)
        root.addWidget(splash_hint)

        self.hotkey_check = QCheckBox("启用全局热键")
        self.hotkey_check.setChecked(bool(settings.hotkeys_enabled))
        self.hotkey_check.setToolTip(
            "Ctrl+Alt+S 全部启动 · Ctrl+Alt+X 全部停止\n"
            "热键是系统独占的，被其它程序占用时会自动换一组候选组合"
        )
        root.addWidget(self.hotkey_check)
        hotkey_hint = QLabel(
            "开启后即使启动器在后台也能用热键启停。留空时自动挑没被占用的组合："
            "启动 Ctrl+Alt+S → Ctrl+Shift+S → Ctrl+Alt+F9；"
            "停止 Ctrl+Alt+X → Ctrl+Alt+F10 → Ctrl+Shift+Q → Ctrl+Shift+X。"
        )
        hotkey_hint.setObjectName("Subtitle")
        hotkey_hint.setWordWrap(True)
        root.addWidget(hotkey_hint)

        hotkey_row = QWidget()
        hotkey_row_layout = QHBoxLayout(hotkey_row)
        hotkey_row_layout.setContentsMargins(0, 0, 0, 0)
        hotkey_row_layout.setSpacing(8)
        hotkey_row_layout.addWidget(QLabel("启动"))
        self.hotkey_start_edit = QKeySequenceEdit()
        self.hotkey_start_edit.setKeySequence(QKeySequence(settings.hotkey_start))
        self.hotkey_start_edit.setClearButtonEnabled(True)
        hotkey_row_layout.addWidget(self.hotkey_start_edit, 1)
        hotkey_row_layout.addWidget(QLabel("停止"))
        self.hotkey_stop_edit = QKeySequenceEdit()
        self.hotkey_stop_edit.setKeySequence(QKeySequence(settings.hotkey_stop))
        self.hotkey_stop_edit.setClearButtonEnabled(True)
        hotkey_row_layout.addWidget(self.hotkey_stop_edit, 1)
        root.addWidget(hotkey_row)
        hotkey_custom_hint = QLabel(
            "启动 / 停止各自独立：填了用你的，留空用候选组合 —— 可以只自定义其中一个。"
            "必须带 Ctrl / Alt / Shift / Win 之一。"
        )
        hotkey_custom_hint.setObjectName("Subtitle")
        hotkey_custom_hint.setWordWrap(True)
        root.addWidget(hotkey_custom_hint)

        self.restore_path = ""
        restore_btn = QPushButton("从备份恢复配置…")
        restore_btn.setObjectName("Ghost")
        restore_btn.clicked.connect(self._choose_backup)
        root.addWidget(restore_btn)

        root.addWidget(QLabel("监控白名单（命中则不触发避让）"))
        self.whitelist_proc_edit = QLineEdit(settings.monitor_whitelist_processes)
        self.whitelist_proc_edit.setPlaceholderText("进程名，逗号分隔：chrome.exe,obs64.exe")
        root.addWidget(self.whitelist_proc_edit)
        self.whitelist_title_edit = QLineEdit(settings.monitor_whitelist_titles)
        self.whitelist_title_edit.setPlaceholderText("窗口标题关键字，逗号分隔：Bilibili,腾讯视频")
        root.addWidget(self.whitelist_title_edit)

        self.adopt_check = QCheckBox("启动时自动扫描并接管已在运行的服务")
        self.adopt_check.setChecked(bool(settings.adopt_on_startup))
        root.addWidget(self.adopt_check)
        adopt_hint = QLabel("关掉后只在你点托盘“扫描已运行服务”时才接管；单个条目还有自己的“启动接管”开关。")
        adopt_hint.setObjectName("Subtitle")
        adopt_hint.setWordWrap(True)
        root.addWidget(adopt_hint)

        self.tray_check = QCheckBox("点关闭按钮时最小化到系统托盘（不退出）")
        self.tray_check.setChecked(bool(settings.close_to_tray))
        root.addWidget(self.tray_check)

        tray_hint = QLabel("取消勾选后，点 X 会直接退出启动器（并询问是否停止已启动的程序）。")
        tray_hint.setObjectName("Subtitle")
        tray_hint.setWordWrap(True)
        root.addWidget(tray_hint)

        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        outer.addLayout(buttons)

    def _choose_backup(self) -> None:
        from .paths import config_path

        folder = str(config_path().parent)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择配置备份", folder, "备份 (*.json);;所有文件 (*.*)"
        )
        if path:
            self.restore_path = path

    def result_settings(self) -> dict:
        """返回所有设置项（用 dict 而不是长元组，避免位置写错）。"""
        return {
            "autostart": bool(self.autostart_check.isChecked()),
            "close_to_tray": bool(self.tray_check.isChecked()),
            "fullscreen_avoid": bool(self.fullscreen_check.isChecked()),
            "fullscreen_block": self.fullscreen_block_edit.text().strip(),
            "fullscreen_allow": self.fullscreen_allow_edit.text().strip(),
            "whitelist_proc": self.whitelist_proc_edit.text().strip(),
            "whitelist_title": self.whitelist_title_edit.text().strip(),
            "theme": self.theme_combo.currentData() or "auto",
            "restore_path": self.restore_path,
            "adopt_on_startup": bool(self.adopt_check.isChecked()),
            "show_splash": bool(self.splash_check.isChecked()),
            "hotkeys_enabled": bool(self.hotkey_check.isChecked()),
            "hotkey_start": self.hotkey_start_edit.keySequence().toString(),
            "hotkey_stop": self.hotkey_stop_edit.keySequence().toString(),
        }


class MonitorTargetsDialog(QDialog):
    """触发器管理：添加 exe / 目录，启用或删除。"""

    def __init__(self, targets: list[MonitorTarget], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("触发器管理")
        self.setModal(True)
        self.resize(560, 460)
        self._targets = [MonitorTarget.from_dict(t.to_dict()) for t in targets]
        self._rows: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(10)

        title = QLabel("监控触发器")
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        hint = QLabel("命中这些程序/目录时，勾选了「进程避让」的服务会被自动挂起，退出后恢复。")
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.setAlignment(Qt.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_host)
        root.addWidget(scroll, 1)

        add_row = QHBoxLayout()
        add_exe = QPushButton("添加程序…")
        add_exe.setObjectName("Ghost")
        add_exe.clicked.connect(self._add_exe)
        add_dir = QPushButton("添加目录…")
        add_dir.setObjectName("Ghost")
        add_dir.clicked.connect(self._add_dir)
        add_row.addWidget(add_exe)
        add_row.addWidget(add_dir)
        add_row.addStretch(1)
        root.addLayout(add_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)

        self._rebuild()

    # ---------------- 列表 ----------------
    def _rebuild(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        if not self._targets:
            empty = QLabel("还没有触发器，点下面的按钮添加。")
            empty.setObjectName("Subtitle")
            self._list_layout.addWidget(empty)
            self._rows.append(empty)
            return
        for target in self._targets:
            row = QWidget()
            row.setFixedHeight(36)
            row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(8)
            check = QCheckBox()
            check.setChecked(target.enabled)
            check.stateChanged.connect(
                lambda state, t=target: setattr(t, "enabled", bool(state))
            )
            label = QLabel(target.label())
            label.setWordWrap(True)
            delete = QPushButton("删除")
            delete.setObjectName("Ghost")
            delete.setFixedWidth(64)
            delete.clicked.connect(lambda _=False, t=target: self._remove(t))
            layout.addWidget(check)
            layout.addWidget(label, 1)
            layout.addWidget(delete)
            self._list_layout.addWidget(row)
            self._rows.append(row)

    def _remove(self, target: MonitorTarget) -> None:
        self._targets = [t for t in self._targets if t.id != target.id]
        self._rebuild()

    def _add_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要监控的程序", "", "程序 (*.exe);;所有文件 (*.*)"
        )
        if path:
            self._targets.append(MonitorTarget(kind="exe", path=path))
            self._rebuild()

    def _add_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要监控的目录")
        if path:
            self._targets.append(MonitorTarget(kind="dir", path=path))
            self._rebuild()

    def result_targets(self) -> list[MonitorTarget]:
        return self._targets