"""大肥鱼启动器 —— 入口。

用法：
    python launcher.py                 正常启动
    python launcher.py --minimized     开机自启：只进托盘，不弹窗
    python launcher.py --selftest      无界面自检（CI / 打包后验证用）
    python launcher.py --screenshot x  截图（验收界面用）
"""

from __future__ import annotations

import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path  # noqa: E402

from PySide6.QtGui import QFont, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.assets import asset_path  # noqa: E402
from app.theme import QSS  # noqa: E402


def main() -> int:
    # 尽早安装：异常/硬崩溃落盘 + 清理自己上次残留的解压目录
    from app.crashlog import install as install_crash_handlers
    from app.housekeeping import cleanup as cleanup_stale_mei
    from app.housekeeping import mark_current

    mark_current()
    install_crash_handlers()
    try:
        cleanup_stale_mei()
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("大肥鱼启动器")
    app.setOrganizationName("local")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(QSS)
    # 关掉最后一个窗口不退出：托盘模式下窗口只是隐藏
    app.setQuitOnLastWindowClosed(False)

    icon_file = asset_path("logo.png")
    if Path(icon_file).exists():
        app.setWindowIcon(QIcon(icon_file))

    if "--selftest" in sys.argv:
        from app.selftest import run_selftest

        return run_selftest(app)

    # 单实例探针（自检用）：试拿锁，拿不到就发唤醒并返回 3
    if "--single-probe" in sys.argv:
        from app.single_instance import SingleInstance

        probe = SingleInstance()
        got_lock = probe.try_start()
        print("PRIMARY" if got_lock else "SECONDARY")
        probe.close()
        return 0 if got_lock else 3

    # 单实例：第二个实例静默退出，并唤醒第一个
    from app.single_instance import SingleInstance

    instance = SingleInstance()
    if not instance.try_start():
        return 0

    from app.mainwindow import MainWindow

    window = MainWindow()
    instance.show_requested.connect(window.show_main_window)

    # 截图模式（用于验收界面，不进入事件循环）
    if "--screenshot" in sys.argv:
        index = sys.argv.index("--screenshot")
        target = sys.argv[index + 1] if index + 1 < len(sys.argv) else "screenshot.png"
        window.resize(1140, 800)
        window.show()
        app.processEvents()
        pixmap = window.grab()
        pixmap.save(target, "PNG")
        print("screenshot saved:", target)
        # 不进事件循环，必须手动把后台线程/定时器停干净，否则 Qt 析构期可能 abort
        window.prepare_headless_exit()
        instance.close()
        return 0

    # 开屏遮罩：和主窗口客户区**完全重合**，底色同主界面渐变，
    # 所以淡出时下面就是主界面，不会跳、也没有大黑边。
    #
    # 必须在 window.show() **之前**把 SplashWindow 建好：QMovie.CacheAll 要把
    # 所有帧解出来，实测 235–343ms。如果放在 show() 之后，这段时间主界面已经
    # 绘制完成，用户会看到「主界面闪一下 → 变黑播动画」。
    # 建的时候拿到的是 show 前的 frame 几何，show 之后再用 retarget() 对齐客户区。
    # 任何异常都静默跳过 —— 开屏不能成为启动失败的原因。
    splash = None
    if not {"--minimized", "--no-splash"} & set(sys.argv):
        try:
            from app.splash import SplashWindow, find_splash_file, splash_enabled

            if splash_enabled():
                splash_path = find_splash_file()
                if splash_path is not None:
                    splash = SplashWindow(splash_path, target=window.geometry())
        except Exception:
            splash = None

    # 遮罩就位之前不能让主界面露出来：
    #   1. QMovie.CacheAll 解帧 235–343ms —— 已提前到 show() 之前付掉；
    #   2. 创建一个无边框 Tool 窗口本身还要 ~90ms —— 这段时间主窗口若已映射到屏幕，
    #      就会看到主界面闪一下。所以先 show() 成**全透明**，遮罩就位后再恢复。
    # 遮罩的几何用 show 之后的客户区矩形，在它第一次绘制前就定好，不会跳动。
    if splash is not None:
        try:
            window.setWindowOpacity(0.0)
        except Exception:
            splash = None

    window.show()

    if splash is not None:
        try:
            splash.retarget(window.geometry())   # show 之后才是客户区矩形
            splash.show()
            splash.raise_()
            splash.start()
        except Exception:
            splash = None
        finally:
            try:
                window.setWindowOpacity(1.0)
            except Exception:
                pass

    if "--minimized" in sys.argv:
        window.start_minimized()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
