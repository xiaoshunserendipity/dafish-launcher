"""启动项卡片：米家风格圆角半透明卡片 + 独立开关 + 拖拽排序。"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QWidget,
)

from .config import LaunchItem
from .processes import (
    STATE_ERROR,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_STOPPING,
)
from .switch import AnimatedSwitch

CARD_WIDTH = 336
CARD_HEIGHT = 150
RADIUS = 22

STATE_TEXT = {    "stopped": "未运行",
    "starting": "正在启动…",
    "running": "运行中",
    "stopping": "正在停止…",
    "error": "启动失败",
}


def load_icon_pixmap(
    path: str, size: int = 48, radius: int = 16
) -> QPixmap | None:
    """把任意图片裁成圆角正方形图标；失败返回 None（调用方回落到 emoji）。"""
    if not path:
        return None
    try:
        source = QPixmap(path)
    except Exception:
        return None
    if source.isNull():
        return None
    try:
        scaled = source.scaled(
            size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        canvas = QPixmap(size, size)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
        painter.setClipPath(clip)
        painter.drawPixmap(
            (size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled
        )
        painter.end()
        return canvas
    except Exception:
        return None


class LaunchCard(QWidget):
    toggled = Signal(str, bool)
    delete_requested = Signal(str)
    edit_requested = Signal(str)
    drag_started = Signal(object, QPoint)
    drag_moved = Signal(object, QPoint)
    drag_finished = Signal(object)
    menu_requested = Signal(str, QPoint)

    def __init__(self, item: LaunchItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self._hover = 0.0
        self._glow = 0.0
        self._press_pos: QPoint | None = None
        self._dragging = False
        self._edit_mode = False
        self._state = "stopped"
        self._alert = ""
        self._available = True
        self._adopted = False

        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)



        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        # ---- 子控件 ----
        self.icon_label = QLabel(self.item.icon or "🚀", self)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setGeometry(18, 20, 48, 48)
        self.icon_label.setStyleSheet(
            "QLabel { background: rgba(255,255,255,0.75); border-radius: 16px;"
            " font-size: 24px; }"
        )
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._apply_icon()

        self.name_label = QLabel(self.item.name, self)
        self.name_label.setGeometry(78, 20, 186, 26)
        self.name_label.setFont(QFont("Microsoft YaHei UI", 12, QFont.DemiBold))
        self.name_label.setStyleSheet("color: #1F2C3D;")
        self.name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.path_label = QLabel(self)
        self.path_label.setGeometry(78, 48, 240, 20)
        self.path_label.setFont(QFont("Microsoft YaHei UI", 8))
        self.path_label.setStyleSheet("color: rgba(66,88,110,0.72);")
        self.path_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.state_label = QLabel(STATE_TEXT["stopped"], self)
        self.state_label.setGeometry(78, 72, 170, 18)
        self.state_label.setFont(QFont("Microsoft YaHei UI", 8))
        self.state_label.setStyleSheet("color: rgba(47,128,237,0.85);")
        self.state_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.suspend_label = QLabel("", self)
        self.suspend_label.setGeometry(78, 112, 112, 20)
        self.suspend_label.setAlignment(Qt.AlignCenter)
        self.suspend_label.setStyleSheet(
            "QLabel { background: rgba(120,138,158,0.16); color: #5C6B7A;"
            " border-radius: 10px; font-size: 10px; }"
        )
        self.suspend_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.suspend_label.hide()

        self.meta_label = QLabel("", self)
        self.meta_label.setGeometry(78, 92, 160, 16)
        self.meta_label.setFont(QFont("Microsoft YaHei UI", 7))
        self.meta_label.setStyleSheet("color: rgba(47,128,237,0.72);")
        self.meta_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.switch = AnimatedSwitch(False, self)
        self.switch.setGeometry(CARD_WIDTH - 56 - 18, 20, 56, 32)

        self.perf_label = QLabel("", self)
        self.perf_label.setGeometry(CARD_WIDTH - 18 - 88, 120, 88, 14)
        self.perf_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.perf_label.setStyleSheet(
            "color: rgba(120,132,148,0.85); font-size: 10px;"
        )
        self.perf_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.perf_label.hide()
        self.switch.toggled.connect(self._on_switch_toggled)

        self.delete_btn = QPushButton("删除", self)
        self.delete_btn.setGeometry(CARD_WIDTH - 78, 94, 60, 24)
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setStyleSheet(
            "QPushButton { border: none; border-radius: 13px; color: #D9463B;"
            " background: rgba(217,70,59,0.10); font-size: 11px; }"
            "QPushButton:hover { background: rgba(217,70,59,0.18); }"
        )
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.item.id))
        self.delete_btn.hide()

        self.edit_btn = QPushButton("设置", self)
        self.edit_btn.setGeometry(CARD_WIDTH - 148, 94, 60, 24)
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.setStyleSheet(
            "QPushButton { border: none; border-radius: 13px; color: #2F80ED;"
            " background: rgba(47,128,237,0.10); font-size: 11px; }"
            "QPushButton:hover { background: rgba(47,128,237,0.18); }"
        )
        self.edit_btn.clicked.connect(lambda: self.edit_requested.emit(self.item.id))
        self.edit_btn.hide()

        self._refresh_path()

    # ---------------- 属性 ----------------
    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = max(0.0, min(1.0, float(value)))
        self.update()

    hover = Property(float, get_hover, set_hover)

    def get_glow(self) -> float:
        return self._glow

    def set_glow(self, value: float) -> None:
        self._glow = max(0.0, min(1.0, float(value)))
        self.update()

    glow = Property(float, get_glow, set_glow)

    def _launch_pulse(self) -> None:
        """启动时描边呼吸一次 —— 只做这一件事，不缩放、不闪烁。"""
        try:
            anim = QPropertyAnimation(self, b"glow", self)
            anim.setDuration(760)
            anim.setKeyValueAt(0.0, 0.0)
            anim.setKeyValueAt(0.28, 1.0)
            anim.setKeyValueAt(1.0, 0.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start()
            self._glow_anim = anim
        except Exception:
            pass

    # ---------------- 数据 ----------------
    def update_item(self, item: LaunchItem) -> None:
        self.item = item
        self._apply_icon()
        self.name_label.setText(item.name)
        self._refresh_path()

    def _apply_icon(self) -> None:
        """有自定义图片就用图片，否则回落到 emoji。"""
        pixmap = load_icon_pixmap(self.item.resolved_icon_path())
        if pixmap is not None:
            self.icon_label.setText("")
            self.icon_label.setPixmap(pixmap)
            self.icon_label.setStyleSheet(
                "QLabel { background: transparent; border-radius: 16px; }"
            )
            return
        self.icon_label.setPixmap(QPixmap())
        self.icon_label.setText(self.item.icon or "🚀")
        self.icon_label.setStyleSheet(
            "QLabel { background: rgba(255,255,255,0.75); border-radius: 16px;"
            " font-size: 24px; }"
        )

    def _apply_text_colors(self) -> None:
        """按主题 + 路径可用性设置文字颜色。"""
        from .theme import CURRENT_THEME

        dark = CURRENT_THEME == "dark"
        if self._available:
            name_color = "#E8EEF6" if dark else "#1F2C3D"
            path_color = (
                "rgba(178,192,208,0.78)" if dark else "rgba(66,88,110,0.72)"
            )
        else:
            name_color = "#7A8494" if dark else "#9AA7B5"
            path_color = "rgba(130,142,156,0.70)" if dark else "rgba(150,160,172,0.8)"
        self.name_label.setStyleSheet(f"color: {name_color};")
        self.path_label.setStyleSheet(f"color: {path_color};")
        self.meta_label.setStyleSheet(
            "color: rgba(122,178,255,0.85);"
            if dark
            else "color: rgba(47,128,237,0.72);"
        )
        self.perf_label.setStyleSheet(
            "color: rgba(150,162,178,0.85); font-size: 10px;"
            if dark
            else "color: rgba(120,132,148,0.85); font-size: 10px;"
        )

    def refresh_theme(self) -> None:
        """主题切换后重新套用配色。"""
        self._apply_text_colors()
        self.update()

    def set_perf(self, text: str) -> None:
        """右下角显示 CPU / 内存占用；空字符串隐藏。"""
        if text:
            self.perf_label.setText(text)
            self.perf_label.show()
        else:
            self.perf_label.setText("")
            self.perf_label.hide()

    def set_available(self, available: bool) -> None:
        """路径不存在时置灰（U 盘拔出 / 目录改名）。"""
        self._available = bool(available)
        self._apply_text_colors()
        self.setToolTip("" if available else "路径不可用：可能被移动、删除，或 U 盘未插入")
        self.update()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.menu_requested.emit(self.item.id, event.globalPos())
        event.accept()

    def set_system_suspended(self, suspended: bool, reason: str = "") -> None:
        """系统避让导致的挂起：显示灰色标签（与用户手动关闭区分）。"""
        if suspended:
            short = "已挂起"
            if "全屏" in reason and "进程" in reason:
                short = "已挂起 · 全屏/进程"
            elif "全屏" in reason:
                short = "已挂起 · 全屏"
            elif "进程" in reason:
                short = "已挂起 · 进程"
            self.suspend_label.setText(short)
            self.suspend_label.setToolTip(f"已挂起（受{reason or '系统'}影响），退出后自动恢复")
            self.suspend_label.show()
        else:
            self.suspend_label.hide()

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self.delete_btn.setVisible(enabled)
        self.edit_btn.setVisible(enabled)

    def set_adopted(self, adopted: bool) -> None:
        self._adopted = bool(adopted)
        if self._state in ("running", "starting"):
            self.state_label.setText("运行中 · 已接管" if adopted else STATE_TEXT["running"])

    def set_state(self, state: str) -> None:
        self._state = state
        self._alert = ""
        text = STATE_TEXT.get(state, state)
        if state == "running" and self._adopted:
            text = "运行中 · 已接管"
        self.state_label.setText(text)
        busy = state in {STATE_STARTING, STATE_STOPPING}
        self.switch.set_busy(busy)
        self.switch.setChecked(
            state in {STATE_RUNNING, STATE_STARTING}, animate=True
        )
        color = {
            STATE_RUNNING: "rgba(52,199,89,0.9)",
            STATE_STARTING: "rgba(47,128,237,0.9)",
            STATE_STOPPING: "rgba(47,128,237,0.9)",
            STATE_ERROR: "rgba(217,70,59,0.9)",
        }.get(state, "rgba(120,138,158,0.8)")
        self.state_label.setStyleSheet(f"color: {color};")

        if state == STATE_STARTING:
            self._launch_pulse()

    def set_alert(self, message: str = "") -> None:
        """异常停止时的常驻提示（红字）；下次 set_state 会自动清掉。"""
        self._alert = message
        if message:
            self.state_label.setText(message)
            self.state_label.setStyleSheet("color: rgba(217,70,59,0.95);")

    def shutdown(self) -> None:
        try:
            self._anim.stop()
        except Exception:
            pass
        try:
            self.switch.shutdown()
        except Exception:
            pass

    def _refresh_path(self) -> None:
        text = self.item.path_summary()
        metrics = QFontMetrics(self.path_label.font())
        self.path_label.setText(
            metrics.elidedText(text, Qt.ElideMiddle, self.path_label.width())
        )
        meta = self.item.meta_summary()
        self.meta_label.setText(meta)
        self.meta_label.setVisible(bool(meta))

    def _on_switch_toggled(self, checked: bool) -> None:
        self.toggled.emit(self.item.id, checked)

    # ---------------- 悬浮 ----------------
    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def _animate_hover(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    # ---------------- 拖拽 ----------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._edit_mode:
            self._press_pos = event.position().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            return super().mouseMoveEvent(event)
        delta = event.position().toPoint() - self._press_pos
        if not self._dragging and delta.manhattanLength() > 10:
            self._dragging = True
            self.drag_started.emit(self, event.globalPosition().toPoint())
        if self._dragging:
            self.drag_moved.emit(self, event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.drag_finished.emit(self)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    # ---------------- 绘制 ----------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        inner = QRectF(6.0, 4.0, self.width() - 12.0, self.height() - 12.0)

        # 自绘柔和阴影（代替 QGraphicsDropShadowEffect：
        # 后者在本项目的 PyInstaller 单文件运行中偶发崩溃）
        painter.setPen(Qt.NoPen)
        for spread, alpha, dy in ((7.0, 9, 9.0), (4.0, 14, 6.0), (1.5, 20, 4.0)):
            shadow_rect = inner.adjusted(-spread, -spread + dy, spread, spread + dy)
            painter.setBrush(QColor(31, 78, 121, alpha))
            painter.drawRoundedRect(shadow_rect, RADIUS + spread * 0.6, RADIUS + spread * 0.6)

        from .theme import CURRENT_THEME

        if CURRENT_THEME == "dark":
            alpha = int(255 * (0.88 + 0.07 * self._hover))
            painter.setBrush(QColor(40, 47, 58, alpha))
            border = QColor(255, 255, 255, 34)
        else:
            alpha = int(255 * (0.62 + 0.24 * self._hover))
            painter.setBrush(QColor(255, 255, 255, alpha))
            border = QColor(255, 255, 255, 210)
        painter.setPen(QPen(border, 1.2))
        painter.drawRoundedRect(inner, RADIUS, RADIUS)

        # 顶部一条很淡的高光，增加通透感
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, int(70 + 60 * self._hover)))
        painter.drawRoundedRect(
            QRectF(inner.left() + 14, inner.top() + 1, inner.width() - 28, 2.0),
            1.0,
            1.0,
        )

        # 启动脉冲：一圈很淡的主题色描边，只播一次
        if self._glow > 0.01:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(
                QPen(
                    QColor(47, 128, 237, int(130 * self._glow)),
                    1.6 + 1.0 * self._glow,
                )
            )
            painter.drawRoundedRect(
                inner.adjusted(-1.0, -1.0, 1.0, 1.0), RADIUS + 1.0, RADIUS + 1.0
            )

        painter.end()
