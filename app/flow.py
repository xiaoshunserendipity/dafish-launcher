"""流式布局容器（手写定位，便于做拖拽排序与位移动画）。

不用 QLayout，是因为要在拖拽换位时对每张卡片做 position 动画；
QFlowLayout 会立刻重排，动画会被覆盖。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from .card import CARD_HEIGHT, CARD_WIDTH, LaunchCard

MARGIN = 18
GAP = 18


class CardFlow(QWidget):
    order_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: list[LaunchCard] = []
        self._anims: list[QPropertyAnimation] = []
        self._drag_card: LaunchCard | None = None
        self._drag_offset = QPoint(0, 0)
        self._drop_index = -1
        self._indicator_x = 0
        self._indicator_y = 0
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, False)

    # ---------------- 卡片管理 ----------------
    def cards(self) -> list[LaunchCard]:
        return list(self._cards)

    def add_card(self, card: LaunchCard, animate: bool = False) -> None:
        card.setParent(self)
        card.drag_started.connect(self._on_drag_started)
        card.drag_moved.connect(self._on_drag_moved)
        card.drag_finished.connect(self._on_drag_finished)
        self._cards.append(card)
        card.show()
        self.relayout(animate=animate)
        self.order_changed.emit()

    def remove_card(self, card: LaunchCard) -> None:
        if card in self._cards:
            self._cards.remove(card)
            card.setParent(None)
            card.deleteLater()
            self.relayout(animate=True)
            self.order_changed.emit()

    def clear(self) -> None:
        for card in list(self._cards):
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self.relayout(animate=False)

    # ---------------- 布局 ----------------
    def _positions(self) -> list[QPoint]:
        width = max(self.width(), CARD_WIDTH + MARGIN * 2)
        per_row = max(1, (width - MARGIN * 2 + GAP) // (CARD_WIDTH + GAP))
        points: list[QPoint] = []
        for index in range(len(self._cards)):
            row, col = divmod(index, per_row)
            x = MARGIN + col * (CARD_WIDTH + GAP)
            y = MARGIN + row * (CARD_HEIGHT + GAP)
            points.append(QPoint(int(x), int(y)))
        return points

    def _content_height(self) -> int:
        points = self._positions()
        if not points:
            return CARD_HEIGHT + MARGIN * 2
        bottom = max(p.y() for p in points) + CARD_HEIGHT + MARGIN
        return int(bottom)

    def relayout(self, animate: bool = True) -> None:
        points = self._positions()
        self.setMinimumHeight(self._content_height())
        for anim in self._anims:
            anim.stop()
        self._anims.clear()

        for card, point in zip(self._cards, points):
            if card is self._drag_card:
                continue
            card.resize(CARD_WIDTH, CARD_HEIGHT)
            if animate and card.pos() != point and card.isVisible():
                anim = QPropertyAnimation(card, b"pos", self)
                anim.setDuration(220)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                anim.setStartValue(card.pos())
                anim.setEndValue(point)
                anim.start()
                self._anims.append(anim)
            else:
                card.move(point)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout(animate=False)

    def shutdown(self) -> None:
        for anim in self._anims:
            try:
                anim.stop()
            except Exception:
                pass
        self._anims.clear()
        for card in self._cards:
            card.shutdown()

    # ---------------- 拖拽排序 ----------------
    def _on_drag_started(self, card: LaunchCard, global_pos: QPoint) -> None:
        if card not in self._cards:
            return
        self._drag_card = card
        local = self.mapFromGlobal(global_pos)
        self._drag_offset = local - card.pos()
        card.raise_()
        self._drop_index = self._cards.index(card)
        self._update_indicator(global_pos)

    def _on_drag_moved(self, card: LaunchCard, global_pos: QPoint) -> None:
        if card is not self._drag_card:
            return
        local = self.mapFromGlobal(global_pos)
        card.move(local - self._drag_offset)
        self._update_indicator(global_pos)
        self.update()

    def _on_drag_finished(self, card: LaunchCard) -> None:
        if card is not self._drag_card:
            return
        target = self._drop_index
        old_index = self._cards.index(card)
        if target < 0:
            target = old_index
        if target != old_index:
            self._cards.pop(old_index)
            if target > old_index:
                target -= 1
            target = max(0, min(len(self._cards), target))
            self._cards.insert(target, card)
            self.order_changed.emit()

        self._drag_card = None
        self._drop_index = -1
        self.relayout(animate=True)

    def _update_indicator(self, global_pos: QPoint) -> None:
        local = self.mapFromGlobal(global_pos)
        points = self._positions()
        best_index = len(self._cards)
        best_distance = None
        for index, (card, point) in enumerate(zip(self._cards, points)):
            if card is self._drag_card:
                continue
            center = QPoint(point.x() + CARD_WIDTH // 2, point.y() + CARD_HEIGHT // 2)
            dx = local.x() - center.x()
            dy = local.y() - center.y()
            # 行优先：先比 y，再比 x
            distance = abs(dy) * 1000 + abs(dx)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = index if dx < 0 else index + 1

        self._drop_index = max(0, min(len(self._cards), best_index))
        # 指示条位置：落在目标卡片左边缘
        if self._drop_index < len(points):
            point = points[self._drop_index]
            self._indicator_x = point.x() - GAP // 2
            self._indicator_y = point.y() + 8
        else:
            last = points[-1] if points else QPoint(MARGIN, MARGIN)
            self._indicator_x = last.x() + CARD_WIDTH + GAP // 2
            self._indicator_y = last.y() + 8

    # ---------------- 绘制 ----------------
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._drag_card is None or self._drop_index < 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(47, 128, 237, 170))
        painter.drawRoundedRect(
            QRectF(self._indicator_x - 2, self._indicator_y, 4, CARD_HEIGHT - 16),
            2,
            2,
        )
        painter.end()
