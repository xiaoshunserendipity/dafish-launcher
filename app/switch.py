"""米家风格的圆形拨动开关。

动画：
- 滑动：QPropertyAnimation 驱动 progress(0..1)，180ms OutCubic；
- 忙碌：仅在进行中才启动 16ms 定时器画旋转圆弧，空闲时完全停止。
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

TRACK_OFF = QColor("#D9DEE7")
TRACK_ON = QColor("#34C759")
KNOB = QColor("#FFFFFF")
BORDER_ON = QColor(0, 0, 0, 10)


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class AnimatedSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._progress = 1.0 if checked else 0.0
        self._busy = False
        self._angle = 0.0
        self._enabled = True
        self.setFixedSize(56, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self._on_spin)

    # ---------------- 属性 ----------------
    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = Property(float, get_progress, set_progress)

    # ---------------- 对外 ----------------
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True) -> None:
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        target = 1.0 if checked else 0.0
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._progress)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.set_progress(target)

    def shutdown(self) -> None:
        """退出前停掉所有动画/定时器，避免 Qt 析构时访问已销毁对象。"""
        try:
            self._anim.stop()
        except Exception:
            pass
        try:
            self._spin.stop()
        except Exception:
            pass

    def set_busy(self, busy: bool) -> None:
        busy = bool(busy)
        if busy == self._busy:
            return
        self._busy = busy
        if busy:
            self._spin.start()
        else:
            self._spin.stop()
        self.update()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - Qt 命名
        self._enabled = bool(enabled)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        super().setEnabled(enabled)
        self.update()

    # ---------------- 交互 ----------------
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            self._enabled
            and event.button() == Qt.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        super().mouseReleaseEvent(event)

    # ---------------- 动画 ----------------
    def _on_spin(self) -> None:
        self._angle = (self._angle + 9.0) % 360.0
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(0.5, 3.5, self.width() - 1.0, self.height() - 7.0)
        radius = rect.height() / 2.0

        track = _blend(TRACK_OFF, TRACK_ON, self._progress)
        if not self._enabled:
            track = QColor(track.red(), track.green(), track.blue(), 140)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)

        # 内描边，增加通透感
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(BORDER_ON, 1.0))
        painter.drawRoundedRect(rect, radius, radius)

        # 滑块
        knob_d = rect.height() - 4.0
        travel = rect.width() - knob_d - 4.0
        x = rect.left() + 2.0 + travel * self._progress
        knob_rect = QRectF(x, rect.top() + 2.0, knob_d, knob_d)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 28))
        painter.drawEllipse(knob_rect.translated(0, 1.0))
        painter.setBrush(KNOB)
        painter.drawEllipse(knob_rect)

        # 忙碌：滑块外围的旋转圆弧
        if self._busy:
            painter.setBrush(Qt.NoBrush)
            pen = QPen(QColor("#2F80ED"), 2.0)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            arc_rect = knob_rect.adjusted(-2.5, -2.5, 2.5, 2.5)
            painter.drawArc(
                arc_rect,
                int(-self._angle * 16),
                int(250 * 16),
            )
        painter.end()
