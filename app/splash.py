"""开屏动画窗口。

查找顺序（**外部优先**，方便用户直接替换）：
  1. `<exe 目录>\\boot.webp` / `boot.gif` / `splash.webp` / `splash.gif`
  2. 内置 `assets/boot.webp` / `assets/boot.gif`（打包进 exe）

视觉设计：开屏窗口**和主窗口客户区完全重合**，动画**等比放大铺满整个窗口**
（超出部分居中裁切），所以没有留白、没有黑边；淡出时下面就是主界面，尺寸一致不会跳。

设计原则：
  - 开屏只是锦上添花，**任何异常都必须静默跳过**，绝不能拖住或搞挂启动；
  - 时长从动画帧延迟算出来，不写死，换任何文件都能自适应。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QGuiApplication, QMovie, QPainter
from PySide6.QtWidgets import QLabel, QWidget

from .paths import app_dir, asset_path, config_path

EXTERNAL_NAMES = ("boot.webp", "boot.gif", "splash.webp", "splash.gif")
BUILTIN_NAMES = ("boot.webp", "boot.gif")

MAX_SHOW_MS = 8000     # 兜底：动画再长也不许挡超过 8 秒
FALLBACK_MS = 1500     # 算不出时长时的默认值
FADE_MS = 260

DEFAULT_SIZE = QSize(1140, 800)


def find_splash_file() -> Path | None:
    """外部文件优先，其次内置资源；都没有返回 None。"""
    for name in EXTERNAL_NAMES:
        candidate = app_dir() / name
        if candidate.is_file():
            return candidate
    for name in BUILTIN_NAMES:
        candidate = Path(asset_path(name))
        if candidate.is_file():
            return candidate
    return None


def splash_enabled() -> bool:
    """读配置里的开关；读不到就当作开启。"""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8-sig"))
    except Exception:
        return True
    settings = data.get("settings")
    if not isinstance(settings, dict):
        return True
    return bool(settings.get("show_splash", True))


def movie_duration_ms(movie: QMovie) -> int:
    """把每帧延迟累加出总时长；算不出来返回 0。"""
    try:
        count = movie.frameCount()
    except Exception:
        return 0
    if count <= 1:
        return 0
    total = 0
    try:
        for index in range(count):
            movie.jumpToFrame(index)
            total += max(10, int(movie.nextFrameDelay()))
        movie.jumpToFrame(0)
    except Exception:
        return 0
    return total


def cover_size(native: QSize, target: QSize) -> QSize:
    """等比放大到铺满 target（会裁切，不拉伸变形）。"""
    if native.width() <= 0 or native.height() <= 0:
        return target
    scale = max(
        target.width() / native.width(),
        target.height() / native.height(),
    )
    return QSize(
        max(1, math.ceil(native.width() * scale)),
        max(1, math.ceil(native.height() * scale)),
    )


def cover_rect(native: QSize, target: QSize) -> QRect:
    """铺满 target 并居中（超出部分靠控件裁剪），返回实际绘制矩形。"""
    covered = cover_size(native, target)
    return QRect(
        (target.width() - covered.width()) // 2,
        (target.height() - covered.height()) // 2,
        covered.width(),
        covered.height(),
    )


class _MovieCanvas(QWidget):
    """把当前帧等比放大铺满整个控件（超出部分裁掉）。

    缩放放在**绘制时**做，而不是用 `QMovie.setScaledSize()`：
    实测 QMovie 只要读过一帧，之后再 setScaledSize 对 `currentPixmap()`
    完全不起作用（CacheAll / CacheNone 都一样），表现就是动画缩在窗口中间、
    四周一圈黑边。绘制时缩放还能顺便自适应窗口尺寸变化。
    """

    def __init__(self, movie: QMovie, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.movie = movie
        self.movie.frameChanged.connect(self.update)

    def paintEvent(self, event) -> None:  # noqa: N802
        pixmap = self.movie.currentPixmap()
        if pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(cover_rect(pixmap.size(), self.size()), pixmap)
        painter.end()


class SplashWindow(QWidget):
    """覆盖主窗口客户区的开屏；点一下即可跳过。"""

    finished = Signal()

    def __init__(self, movie_path: Path, target: QRect | None = None) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setWindowOpacity(1.0)

        self._closing = False
        self._fade: QPropertyAnimation | None = None
        self._timer: QTimer | None = None

        self.movie = QMovie(str(movie_path))
        self.movie.setCacheMode(QMovie.CacheAll)
        if not self.movie.isValid():
            raise ValueError(f"无法解码动画：{movie_path}")

        self.movie.jumpToFrame(0)
        native = self.movie.currentImage().size()
        if native.width() < 16 or native.height() < 16:
            raise ValueError(f"动画尺寸异常：{native}")
        self._native = native

        # 目标区域：优先跟随主窗口客户区
        if target is not None and target.width() > 80 and target.height() > 80:
            self.setGeometry(target)
        else:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                raise ValueError("没有可用的屏幕")
            geo = screen.availableGeometry()
            w = min(DEFAULT_SIZE.width(), int(geo.width() * 0.8))
            h = min(DEFAULT_SIZE.height(), int(geo.height() * 0.8))
            self.setGeometry(geo.center().x() - w // 2, geo.center().y() - h // 2, w, h)

        self.canvas = _MovieCanvas(self.movie, self)
        self.hint = QLabel("点击任意处跳过", self)
        self.hint.setStyleSheet(
            "color: rgba(255,255,255,0.72); font-size: 11px;"
            " background: rgba(0,0,0,0.32); border-radius: 9px;"
            " padding: 3px 10px;"
        )
        self.hint.adjustSize()

        self._relayout()
        self._duration = movie_duration_ms(self.movie)

    def _relayout(self) -> None:
        """按当前窗口尺寸重排画布与提示（缩放由画布绘制时自适应）。"""
        self.canvas.setGeometry(0, 0, self.width(), self.height())
        self.hint.adjustSize()
        self.hint.move(
            max(0, (self.width() - self.hint.width()) // 2),
            max(0, self.height() - self.hint.height() - 18),
        )

    # ---------------- 对外 ----------------
    def retarget(self, rect: QRect) -> None:
        """对齐到新的客户区矩形。

        主窗口 show() 之前的 geometry() 是**含标题栏的 frame 矩形**，
        show() 之后才是客户区，两者差一个标题栏高度。所以先按 show 前的
        几何把窗口建出来（把 QMovie 解帧那 200–350ms 提前付掉），等主窗口
        真正显示后再用这个方法对齐，避免主界面在遮罩出现前先露一下。
        """
        if rect is None or rect.width() <= 80 or rect.height() <= 80:
            return
        if rect == self.geometry():
            return
        self.setGeometry(rect)
        self._relayout()
    def start(self) -> None:
        self.movie.start()
        delay = self._duration or FALLBACK_MS
        delay = max(400, min(MAX_SHOW_MS, delay))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self.finish)
        timer.start(delay)
        self._timer = timer

    def finish(self) -> None:
        """淡出并关闭；可重复调用。"""
        if self._closing:
            return
        self._closing = True
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
        try:
            self.movie.stop()
        except Exception:
            pass
        try:
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(FADE_MS)
            anim.setStartValue(self.windowOpacity())
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.InOutQuad)
            anim.finished.connect(self._on_faded)
            self._fade = anim
            anim.start()
        except Exception:
            self._on_faded()

    def _on_faded(self) -> None:
        try:
            self.close()
        finally:
            self.finished.emit()

    # ---------------- 事件 ----------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.finish()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self.finish()
        super().keyPressEvent(event)
