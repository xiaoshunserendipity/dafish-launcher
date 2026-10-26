"""米家风格样式表。"""

FONT_FAMILY = "Microsoft YaHei UI"

QSS = f"""
* {{
    font-family: "{FONT_FAMILY}", "Microsoft YaHei", "Segoe UI";
}}

#Root {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0   #E6F2FF,
        stop:0.42 #F3F9FF,
        stop:0.75 #FBFCFF,
        stop:1   #FFFFFF);
}}

QLabel#Title {{
    font-size: 30px;
    font-weight: 700;
    color: #10233A;
    letter-spacing: 1px;
}}

QLabel#Subtitle {{
    font-size: 12px;
    color: rgba(64, 88, 112, 0.85);
}}

QLabel#MasterLabel {{
    font-size: 13px;
    color: #24476B;
}}

QFrame#MasterCapsule {{
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid rgba(255, 255, 255, 0.95);
    border-radius: 30px;
}}

QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #3B92F6, stop:1 #2F72E8);
    color: white;
    border: none;
    border-radius: 20px;
    padding: 9px 22px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #4B9DF8, stop:1 #3B7FF0);
}}
QPushButton#Primary:pressed {{
    background: #2A68D4;
}}

QPushButton#Ghost {{
    background: rgba(255, 255, 255, 0.65);
    color: #2C4B6E;
    border: 1px solid rgba(178, 205, 232, 0.9);
    border-radius: 20px;
    padding: 9px 18px;
    font-size: 13px;
}}
QPushButton#Ghost:hover {{
    background: rgba(255, 255, 255, 0.92);
}}
QPushButton#Ghost:checked {{
    background: rgba(47, 128, 237, 0.14);
    color: #1F6FE0;
    border-color: rgba(47, 128, 237, 0.55);
}}

QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(120, 160, 200, 0.35);
    border-radius: 4px;
    min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(120, 160, 200, 0.55);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QLabel#Toast {{
    background: rgba(24, 44, 66, 0.88);
    color: white;
    border-radius: 16px;
    padding: 8px 18px;
    font-size: 12px;
}}

QDialog {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #EDF6FF, stop:1 #FFFFFF);
}}
QLineEdit, QComboBox {{
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid rgba(176, 204, 232, 0.9);
    border-radius: 10px;
    padding: 7px 10px;
    font-size: 13px;
    color: #1B324B;
    selection-background-color: #BBDCFF;
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid #2F80ED;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: white;
    border: 1px solid #CFE3F7;
    border-radius: 8px;
    selection-background-color: #E3F0FF;
    color: #1B324B;
}}
QLabel#FieldLabel {{
    font-size: 12px;
    color: #3B5A7A;
}}
QLabel#DialogTitle {{
    font-size: 18px;
    font-weight: 700;
    color: #10233A;
}}
QFrame#LogPanel {{
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid rgba(255, 255, 255, 0.95);
    border-radius: 16px;
}}

QFrame#LogPanel QLabel {{
    color: #3B5A7A;
    font-size: 12px;
}}

QFrame#LogPanel QPushButton {{
    background: rgba(255, 255, 255, 0.85);
    border: 1px solid rgba(178, 205, 232, 0.9);
    border-radius: 10px;
    padding: 1px 12px;
    font-size: 11px;
    color: #2C4B6E;
}}
QFrame#LogPanel QPushButton:hover {{
    background: #FFFFFF;
}}

QTextEdit#LogView {{
    background: rgba(16, 35, 58, 0.92);
    color: #D6E8FF;
    border: none;
    border-radius: 10px;
    padding: 6px 8px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 11px;
    selection-background-color: #2F80ED;
}}
"""


# ================= 主题 =================
CURRENT_THEME = "light"


def is_system_dark() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
    except Exception:
        return False


def resolve_theme(mode: str) -> str:
    mode = (mode or "auto").lower()
    if mode in ("light", "dark"):
        return mode
    return "dark" if is_system_dark() else "light"


def _darken(qss: str) -> str:
    pairs = (
        ("#E6F2FF", "#101A26"),
        ("#F3F9FF", "#16232F"),
        ("#FBFCFF", "#1B2A38"),
        ("#FFFFFF", "#1E2C3A"),
        ("#10233A", "#E8F1FA"),
        ("#24476B", "#CFE0F0"),
        ("#2C4B6E", "#D6E6F5"),
        ("#3B5A7A", "#B7CBDD"),
        ("#1B324B", "#E3EEF8"),
        ("rgba(255, 255, 255, 0.72)", "rgba(30, 44, 58, 0.85)"),
        ("rgba(255, 255, 255, 0.65)", "rgba(36, 52, 68, 0.9)"),
        ("rgba(176, 204, 232, 0.9)", "rgba(80, 110, 140, 0.9)"),
        ("rgba(178, 205, 232, 0.9)", "rgba(80, 110, 140, 0.9)"),
    )
    out = qss
    for old, new in pairs:
        out = out.replace(old, new)
    return out


_DARK_OVERRIDES = """
QPushButton#Ghost {
    background: rgba(44, 62, 80, 0.92);
    color: #DCE9F6;
    border: 1px solid rgba(96, 128, 158, 0.9);
}
QPushButton#Ghost:hover { background: rgba(58, 80, 102, 0.95); }
QPushButton#Ghost:checked { background: rgba(47,128,237,0.30); color: #BFD9FF; }
QComboBox, QLineEdit, QSpinBox {
    background: rgba(30, 44, 58, 0.96);
    color: #E3EEF8;
    border: 1px solid rgba(90, 120, 150, 0.9);
}
QComboBox QAbstractItemView {
    background: #1E2C3A;
    color: #E3EEF8;
    selection-background-color: #2F80ED;
}
QLabel#Subtitle { color: rgba(180, 200, 220, 0.85); }
QFrame#LogPanel QPushButton {
    background: rgba(44, 62, 80, 0.92);
    color: #DCE9F6;
    border: 1px solid rgba(96, 128, 158, 0.9);
}
QMenu { background: #1E2C3A; color: #E3EEF8; border: 1px solid rgba(90,120,150,0.9); }
QMenu::item:selected { background: #2F80ED; }
"""

DARK_QSS = _darken(QSS) + _DARK_OVERRIDES


def apply_theme(app, mode: str = "auto") -> str:
    """按 auto/light/dark 应用 QSS，返回实际使用的主题。"""
    global CURRENT_THEME
    theme = resolve_theme(mode)
    CURRENT_THEME = theme
    app.setStyleSheet(DARK_QSS if theme == "dark" else QSS)
    return theme