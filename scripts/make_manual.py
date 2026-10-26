"""生成《大肥鱼启动器 操作手册》docx。"""

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

CN_FONT = "微软雅黑"
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
GRAY = RGBColor(0x59, 0x59, 0x59)


def set_font(run, size=10.5, bold=False, color=None):
    run.font.name = CN_FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    run._element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)


def para(doc, text="", size=10.5, bold=False, color=None, align=None, space_after=4):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.35
    if align is not None:
        p.alignment = align
    if text:
        set_font(p.add_run(text), size=size, bold=bold, color=color)
    return p


def bullet(doc, text, level=0):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6 + 0.6 * level)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.3
    set_font(p.add_run(("• " if level == 0 else "– ") + text), size=10.5)
    return p


def heading(doc, text, level=1):
    h = doc.add_heading(level=level)
    h.paragraph_format.space_before = Pt(14 if level == 1 else 10)
    h.paragraph_format.space_after = Pt(6)
    set_font(h.add_run(text), size=16 if level == 1 else (13 if level == 2 else 11.5), bold=True, color=ACCENT)
    return h


def table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for index, header in enumerate(headers):
        cell = t.rows[0].cells[index]
        cell.text = ""
        set_font(cell.paragraphs[0].add_run(header), size=9.5, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        cell._tc.get_or_add_tcPr().append(_shade("1F4E79"))
    for row in rows:
        cells = t.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = ""
            set_font(cells[index].paragraphs[0].add_run(str(value)), size=9.5)
    if widths:
        for row in t.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Cm(width)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def _shade(color):
    from docx.oxml import OxmlElement

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    return shd


doc = Document()
section = doc.sections[0]
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.0)
section.bottom_margin = Cm(2.0)
section.left_margin = Cm(2.2)
section.right_margin = Cm(2.2)

style = doc.styles["Normal"]
style.font.name = CN_FONT
style.font.size = Pt(10.5)
style._element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)

# ---------------- 封面 ----------------
for _ in range(6):
    doc.add_paragraph()
para(doc, "大肥鱼启动器", size=30, bold=True, color=ACCENT, align=WD_ALIGN_PARAGRAPH.CENTER)
para(doc, "操 作 手 册", size=22, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=24)
para(doc, "版本：v3.3.0（便携免安装版）", size=12, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER)
para(doc, "更新日期：2026-10-26", size=12, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=60)
para(doc, "适用系统：Windows 10 / 11 64 位", size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
para(doc, "无需安装 Python 或任何依赖，双击 exe 即可运行", size=11, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_page_break()

# ---------------- 目录提示 ----------------
heading(doc, "阅读提示", 1)
para(doc, "本手册分三部分：")
bullet(doc, "第 1～6 章：日常怎么用（安装、界面、启动停止、避让、接管、日志、托盘、设置）。")
bullet(doc, "第 7 章：配置文件的保存位置、结构与备份恢复。")
bullet(doc, "第 8～10 章：常见问题、功能实现速查、版本记录。")
para(doc, "遇到问题先看第 8 章；想知道某个功能是怎么做的，直接翻第 9 章。", color=GRAY)

# ---------------- 1 简介 ----------------
heading(doc, "1. 软件简介", 1)
para(doc, "大肥鱼启动器是一个 Windows 本地程序启动管理器，用来把多个需要按顺序启动的本地程序"
          "（例如 AstrBot 桌面版、SnowLuma、GSVI 语音适配器）登记成卡片，一键启停。")
para(doc, "核心特点：", bold=True)
bullet(doc, "绿色便携：配置和日志都放在 exe 同目录，拷走文件夹就能用。")
bullet(doc, "默认不自动启动：打开软件时所有开关都是关的，必须手动操作。")
bullet(doc, "顺序启动：总开关按卡片顺序依次启动，可等上一个真正就绪再启动下一个。")
bullet(doc, "状态感知：全屏、目标进程、已运行服务都能识别，并自动暂停/接管。")
bullet(doc, "单实例：重复双击不会多开，会把已打开的窗口叫到前台。")

# ---------------- 2 安装与首次使用 ----------------
heading(doc, "2. 安装与第一次使用", 1)

heading(doc, "2.1 安装（免安装）", 2)
para(doc, "把「大肥鱼启动器.exe」放到任意文件夹（例如 D:\\Tools\\启动器\\），双击即可。")
para(doc, "首次运行会在同目录自动生成：config.json（配置）和 logs\\（日志）。", color=GRAY)

heading(doc, "2.2 第一次设置路径", 2)
bullet(doc, "方法一：点卡片上的「设置」，选择目标 exe / bat / cmd / lnk。")
bullet(doc, "方法二：点底部「自动探测路径」，它会按名字尝试填充 AstrBot / SnowLuma / GSVI 的常见路径。")
bullet(doc, "路径可以先留空，之后补；未设置路径时点开关会提示“还没有设置路径”。")

heading(doc, "2.3 启动与停止", 2)
bullet(doc, "单个启动：拨卡片右上角的圆开关。")
bullet(doc, "全部启动：拨右上角「全部启动」，会按卡片顺序依次启动。")
bullet(doc, "全部停止：点右下角「全部停止」（注意：会连同接管的进程一起停止）。")

# ---------------- 3 界面说明 ----------------
heading(doc, "3. 界面说明", 1)
table(doc,
      ["区域", "内容", "说明"],
      [
          ["顶部", "标题、日期、状态徽章、总开关", "徽章显示 🟢 监控中 / 🟡 触发避让（全屏或目标进程）"],
          ["卡片", "图标、名称、路径摘要、状态、开关、挂起标签", "拖拽可排序；右键有更多操作"],
          ["底部按钮", "添加启动项、编辑模式、自动探测路径、设置、分组、触发器、日志、全部停止", "常用操作都在这里"],
          ["日志面板", "运行日志、条目过滤、导出诊断包、清空", "点「日志」展开/收起"],
      ],
      widths=[2.6, 7.0, 7.0])

# ---------------- 4 启动项管理 ----------------
heading(doc, "4. 启动项管理", 1)

heading(doc, "4.1 添加与编辑", 2)
para(doc, "每个条目可设置：")
table(doc,
      ["字段", "说明", "默认"],
      [
          ["显示名称", "卡片标题", "必填"],
          ["目标文件", "exe / bat / cmd / lnk / com", "空"],
          ["启动参数", "传给程序的命令行参数", "空"],
          ["工作目录", "留空 = 目标文件所在目录（推荐）", "空"],
          ["窗口模式", "正常窗口 / 最小化", "正常窗口"],
          ["延迟启动", "总开关轮到它后再等 N 秒（0–600）", "0"],
          ["端口预检", "启动前检查 127.0.0.1:端口，占用则询问", "0（不检测）"],
          ["就绪端口", "端口监听成功才算“运行中”，用于级联等待", "0（不检测）"],
          ["就绪超时", "等待就绪的最长秒数", "60"],
          ["分组", "可选，用于按组启动/停止", "空"],
          ["全屏避让", "全屏时是否自动暂停该条目", "开"],
          ["进程避让", "目标进程命中时是否自动暂停该条目", "开"],
          ["自动重启", "异常退出 3 秒后自动重启（最多 5 次）", "关"],
          ["启动接管", "允许启动时接管已在运行的进程", "开"],
      ],
      widths=[2.8, 9.4, 4.0])

heading(doc, "4.2 排序", 2)
para(doc, "按住卡片拖动即可调整顺序，松手后其他卡片平滑让位，顺序自动保存；总开关按这个顺序启动。")
para(doc, "也可以打开「编辑模式」，用卡片上的「设置/删除」按钮管理。")

heading(doc, "4.3 分组", 2)
para(doc, "给条目填「分组」后，点底部「分组」菜单，可以整组启动或停止（组内按 300ms 间隔依次启动）。")

heading(doc, "4.4 卡片右键菜单", 2)
bullet(doc, "启动 / 停止")
bullet(doc, "打开所在目录")
bullet(doc, "复制路径")
bullet(doc, "设置… / 删除")

# ---------------- 5 核心功能 ----------------
heading(doc, "5. 核心功能", 1)

heading(doc, "5.1 总开关与顺序启动", 2)
para(doc, "打开总开关时：所有独立开关先点亮，然后按卡片顺序逐个启动。"
          "启动下一个的条件是“上一个进入运行中”（配置了就绪端口时，以端口监听成功为准），"
          "每步间隔 150ms；单个程序最多等 20 秒，超时会跳过并在日志提示。")
para(doc, "关闭总开关：中止级联队列，并停止由启动器拉起的程序。")

heading(doc, "5.2 延迟启动", 2)
para(doc, "条目设置里的「延迟启动」只影响总开关/分组启动：轮到它之后再等 N 秒，期间界面不卡。")

heading(doc, "5.3 端口预检与就绪检测", 2)
table(doc,
      ["设置", "作用", "典型用法"],
      [
          ["端口预检", "启动前探测端口是否被占用，占用会弹窗询问是否继续", "防止重复启动 AstrBot 导致端口冲突"],
          ["就绪端口", "端口监听成功才把状态置为运行中，总开关接着启动下一个", "AstrBot 填 6185，GSVI 填 8000"],
      ],
      widths=[3.0, 7.6, 5.5])

heading(doc, "5.4 崩溃自动重启", 2)
para(doc, "勾选「自动重启」后，程序不是由你手动停止、也不是系统避让暂停，而是自己退出时，"
          "启动器会在 3 秒后重新拉起；连续退出 5 次后停止自动重启并提示。")

heading(doc, "5.5 全屏与进程避让（System Busy）", 2)
para(doc, "启动器把“系统忙”定义为：前台是全屏/无边框全屏窗口，或者监控目标进程正在运行。")
para(doc, "触发时：正在运行且勾选了对应避让的条目会被自动暂停，卡片显示灰色标签「已挂起 · 全屏 / 进程」。")
para(doc, "解除时：只恢复被系统暂停的那些；你手动关掉的不会被拉起。")
para(doc, "相关设置：")
bullet(doc, "全屏避让总开关（设置页）与每个条目的「全屏避让」。")
bullet(doc, "监控目标（触发器）：在「触发器」里添加 exe 或目录；命中即视为目标进程运行。")
bullet(doc, "进程白名单 / 窗口标题白名单：命中则不触发避让（适合浏览器全屏看视频）。")

heading(doc, "5.6 启动接管", 2)
para(doc, "如果服务已经在运行（例如上次没通过启动器打开），启动器启动 0.8 秒后会自动扫描并接管，"
          "卡片显示「运行中 · 已接管」，可以直接用启动器停止。")
para(doc, "两级开关：")
table(doc,
      ["层级", "位置", "作用"],
      [
          ["全局", "设置 →「启动时自动扫描并接管已在运行的服务」", "关掉后启动时不扫描（托盘手动扫描仍可用）"],
          ["单条目", "条目设置 →「允许启动时自动接管」", "关掉后该条目永远不被接管"],
      ],
      widths=[2.5, 8.5, 5.0])
para(doc, "托盘右键「扫描已运行服务」可随时手动扫描。")

heading(doc, "5.7 日志与诊断", 2)
bullet(doc, "点底部「日志」展开面板；下拉框可按条目过滤。")
bullet(doc, "子进程的 stdout/stderr 会实时显示，并同时写入 exe 同目录的 logs\\launcher.log。")
bullet(doc, "「导出诊断包」生成一个 zip：config.json + 全部日志 + 系统信息，方便排障。")

heading(doc, "5.8 托盘与单实例", 2)
bullet(doc, "点 X 默认只是收进托盘；托盘右键可单独启停、扫描已运行、全部停止并退出。")
bullet(doc, "重复双击 exe 不会多开：第二个实例静默退出，已打开的窗口自动弹到前台。")
bullet(doc, "右键托盘「全部停止并退出」会结束启动器拉起和接管的全部进程。")

heading(doc, "5.9 开机自启、主题、窗口记忆", 2)
table(doc,
      ["功能", "位置", "说明"],
      [
          ["开机自启", "设置 →「开机自动启动」", "写 HKCU Run，值为当前 exe 路径 + --minimized；移动 exe 后需重新勾选"],
          ["界面主题", "设置 →「界面主题」", "跟随系统 / 浅色 / 深色，保存后立即生效"],
          ["窗口记忆", "自动", "记住上次位置和大小，多屏也会校验可见区域"],
      ],
      widths=[2.8, 5.2, 8.0])

# ---------------- 6 设置项清单 ----------------
heading(doc, "6. 设置项清单", 1)
table(doc,
      ["设置", "默认", "说明"],
      [
          ["开机自动启动", "关", "登录后最小化到托盘"],
          ["全屏时暂停参与避让的程序", "开", "总开关；关闭后不检测全屏"],
          ["界面主题", "跟随系统", "自动 / 浅色 / 深色"],
          ["从备份恢复配置", "—", "选择 config.bak-*.json 恢复，恢复前自动备份当前配置"],
          ["监控白名单（进程）", "空", "进程名，逗号分隔，如 chrome.exe,obs64.exe"],
          ["监控白名单（标题）", "空", "窗口标题关键字，逗号分隔，如 Bilibili,腾讯视频"],
          ["启动时自动扫描并接管", "开", "关闭后仅手动扫描"],
          ["点关闭按钮最小化到托盘", "开", "关闭后点 X 会询问是否退出"],
      ],
      widths=[5.6, 2.4, 8.0])

# ---------------- 7 配置 ----------------
heading(doc, "7. 配置文件", 1)
para(doc, "位置：与 exe 同目录的 config.json；日志在 logs\\launcher.log。")
para(doc, "包含三部分：")
bullet(doc, "items：启动条目（路径、参数、窗口模式、延迟、端口、分组、避让、接管等）。")
bullet(doc, "targets：监控触发器（exe 或目录）。")
bullet(doc, "settings：全局设置（主题、托盘、自启、白名单、接管开关等）。")
para(doc, "安全机制：")
bullet(doc, "写入采用临时文件 + 原子替换，避免写一半损坏。")
bullet(doc, "保存前自动备份，保留最近 5 份 config.bak-时间.json。")
bullet(doc, "旧版本配置（v1/v2/v3）会自动补字段迁移到 v4。")
bullet(doc, "配置损坏时不会覆盖原文件，先另存 config.invalid-时间.json。")
para(doc, "换电脑：把 exe + config.json 一起拷走；只拷 exe 会变成空白配置。")

# ---------------- 8 FAQ ----------------
heading(doc, "8. 常见问题", 1)
faq = [
    ("双击没反应 / 打不开", "先看任务栏和托盘是否已有实例（单实例会静默退出）。若被 SmartScreen 拦截，点「更多信息 → 仍要运行」；杀毒软件需加白名单。"),
    ("卡片显示“尚未设置路径”", "点卡片「设置」选择程序；或点「自动探测路径」。"),
    ("总开关打开后没全部启动", "检查是否缺路径、路径失效，或系统正忙（全屏/目标进程）导致延后；日志面板会有提示。"),
    ("服务在跑但卡片显示“未运行”", "说明它不是在启动器里启动的。等 0.8 秒自动接管，或在托盘点「扫描已运行服务」；也可检查该条目是否关闭了「启动接管」。"),
    ("接管不生效", "确认条目路径正确；同名程序较多时建议填完整路径或配置“就绪端口”。"),
    ("全屏时没有暂停服务", "检查设置里“全屏时暂停”是否开启、条目是否勾选「全屏避让」；某些无边框全屏由标题白名单/进程白名单排除。"),
    ("退出全屏后没有恢复", "只会恢复“被系统暂停”的条目；如果你在全屏期间手动关掉过，它不会被拉起。"),
    ("端口被占用弹窗", "说明已有实例在跑。选“否”取消启动，或先结束残留进程。"),
    ("开机自启没生效", "移动过 exe 位置会导致注册表指向旧路径，去设置里重新勾选一次。"),
    ("日志面板没有输出", "只捕获子进程的标准输出；GUI 程序（如 AstrBot 桌面版）通常不输出到控制台。"),
    ("停止后仍有残留进程", "启动器会提示残留 PID；可再点一次停止，或手动结束。极少数受保护进程需要管理员权限。"),
    ("换电脑后配置全空", "配置跟着 exe 走；请把 config.json 一起拷过去，或重新设置路径。"),
    ("桌面出现了两份配置", "不同文件夹各有一份独立配置；建议只保留 启动器\\ 下这一份。"),
    ("想恢复默认设置", "退出启动器，把 config.json 改名备份，重新启动会生成默认配置。"),
    ("内存占用高", "主要来自被它启动的程序（如 GSVI 语音模型常驻约 2–3GB），启动器本身约 50–60MB。"),
]
for question, answer in faq:
    para(doc, "Q：" + question, bold=True, space_after=1)
    para(doc, "A：" + answer, space_after=6)

# ---------------- 9 实现速查 ----------------
heading(doc, "9. 附录 A：功能与实现方式速查", 1)
table(doc,
      ["功能", "实现方式"],
      [
          ["便携配置", "app/paths.py：frozen 时取 sys.executable 父目录；资源取 sys._MEIPASS"],
          ["原子保存 / 备份 / 迁移", "app/config.py：tmp+replace、_backup_current(保留5份)、migrate_payload(v1→v4)"],
          ["启动 / 停止", "app/processes.py：subprocess.Popen；psutil 递归 + taskkill /T /F 兜底"],
          ["窗口模式", "STARTUPINFO.wShowWindow = SW_SHOWNORMAL / SW_SHOWMINIMIZED"],
          ["工作目录隔离", "LaunchItem.effective_cwd()：显式优先，否则目标目录，永远绝对路径"],
          ["顺序级联", "MainWindow._on_master_toggled → _start_next_in_chain；等待 RUNNING，20s 超时"],
          ["延迟启动", "QTimer.singleShot(delay*1000)，不阻塞 UI"],
          ["就绪检测", "ProcessManager._wait_ready：每 400ms 探测端口，成功才置 RUNNING"],
          ["端口预检", "socket.connect_ex(127.0.0.1, port) == 0 判占用"],
          ["自动重启", "_handle_exit：3s 后重启，连续 5 次停止，用户 stop 不触发"],
          ["全屏检测", "app/fullscreen.py：DWM 扩展边框 + GWL_STYLE 无边框 + 覆盖比例 + 2 次去抖"],
          ["进程监控", "app/monitor.py：独立 QThread，3s psutil.process_iter(['name','exe'])"],
          ["避让暂停/恢复", "paused_by_system 字典；QTimer 250ms 递增恢复；用户覆盖集合"],
          ["启动接管", "app/adopt.py：路径 → 命令行 → 端口 → 名称 四级匹配；adopt() 登记 PID"],
          ["日志读取", "app/logreader.py：daemon 线程 readline，UTF-8 失败转系统编码"],
          ["单实例", "app/single_instance.py：独占文件锁 + QLocalSocket SHOW + QLocalServer"],
          ["托盘唤醒", "showNormal + raise + SetForegroundWindow + QApplication.alert"],
          ["开机自启", "app/autostart.py：winreg HKCU Run，值 = 当前 exe + --minimized"],
          ["主题", "app/theme.py：读注册表 AppsUseLightTheme；两套 QSS + setStyleSheet"],
          ["拖拽排序", "app/flow.py：手写定位计算行列；QPropertyAnimation 位移动画"],
          ["动画", "QPropertyAnimation：悬浮 200ms、开关 180ms、重排 220ms、日志面板 220ms"],
          ["单文件打包", "PyInstaller --onefile --noconsole --icon --version-file --add-data assets"],
      ],
      widths=[4.2, 11.5])

# ---------------- 10 版本记录 ----------------
heading(doc, "10. 附录 B：版本记录", 1)
table(doc,
      ["版本", "主要内容"],
      [
          ["v1.0", "基础卡片、总开关、窗口模式、便携配置"],
          ["v2.0", "托盘、开机自启、自动重启、延迟启动、端口预检、日志面板"],
          ["v2.1", "全屏检测避让（最小化启动）"],
          ["v2.2", "单实例防多开与唤醒；全屏暂停/恢复状态机"],
          ["v3.0", "多维度 System Busy（全屏 OR 目标进程）、触发器、状态徽章、卡片挂起标签"],
          ["v3.1", "就绪端口、配置备份/迁移、残留检测、提权、路径巡检、主题、分组、诊断包"],
          ["v3.2", "启动时接管已运行服务"],
          ["v3.3", "接管可配置（全局 + 单条目）、build.bat 一键构建并更新运行目录"],
      ],
      widths=[2.2, 13.5])

para(doc, "—— 手册结束 ——", color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER)

import sys

out = sys.argv[1] if len(sys.argv) > 1 else "操作手册.docx"
doc.save(out)
print("saved:", out)
