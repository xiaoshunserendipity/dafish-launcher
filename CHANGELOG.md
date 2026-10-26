# 变更记录

> v3.3 及更早的详细开发记录见文末「历史记录」。
> 从 v3.4 起按版本倒序记录。

---

## v4.1.1（2026-10-26）

**修：开屏动画缩在窗口中间、四周一圈黑边**

- 根因：`QMovie` **只要读过一帧，之后再 `setScaledSize()` 对 `currentPixmap()`
  完全不起作用**（实测 CacheAll / CacheNone、start 前/后共 6 种顺序，
  只有「先 setScaledSize 再读帧」有效）。v4.1.0 为了修启动闪烁把
  `_relayout()` 挪到 `movie_duration_ms()` 之后，而算时长要逐帧 `jumpToFrame()`，
  等于先把 900×506 的原始帧读进缓存，缩放就再也改不动了 ——
  画布只画出中间那块，周围因为 `WA_OpaquePaintEvent` 呈黑色
- 修法：**缩放改到绘制时做**。新增 `cover_rect()`，`_MovieCanvas.paintEvent()`
  用 `drawPixmap(QRect, QPixmap)` + `SmoothPixmapTransform` 等比放大铺满，
  彻底绕开 QMovie 的缓存坑，还顺带自适应窗口尺寸变化
- 移除 `setScaledSize` 相关逻辑，`_relayout()` 只负责画布尺寸与提示位置
- 自检 94 → 95 项（新增「开屏动画铺满整个窗口」几何覆盖断言）

---

## v4.1.0（2026-10-26）

**启动闪烁修复 + 评审遗留四项**

- **修：启动时主界面会闪一下** —— `SplashWindow` 原本在 `window.show()` 之后创建，
  而 `QMovie.CacheAll` 解 48 帧要 235–343ms，加上创建无边框窗口本身 ~90ms，
  这段时间主界面已经画完，用户看到「主界面闪一下 → 变黑播动画」。
  改为：开屏对象提前构建 → 主窗口先以全透明 `show()` → 遮罩就位后恢复不透明。
  新增 `SplashWindow.retarget()`，处理「show 前 `geometry()` 是 frame 矩形、
  差一个标题栏高度」这个坑（实测差 30px，直接拿来建遮罩会跳一下）
- **自动重启改真「连续」** —— 新增 `_started_at` 记录每次启动时刻，
  稳定运行 ≥ `STABLE_UPTIME_SEC`（60 秒）后退出就把计数清零。
  原来只在用户主动停止时清零，实际是「累计」：长期服务崩 6 次后永久失去自动重启
- **psutil 全表扫描移出 UI 线程** —— 新增 `app/scan.py`（`AsyncRunner`：`QThreadPool` +
  强制 `QueuedConnection` 回调）。接管探测与残留检测都挪到后台，
  实测一次三条目扫描 109ms，不再阻塞界面；扫描进行中重复触发会被忽略
- **全屏进程名单有了 UI 入口** —— 设置里新增「额外判定为全屏的进程」
  「不计入全屏的进程」，原来只能手改 `config.json`
- **避让状态改为原因集合** —— `_system_busy` 由 bool 改为 `_busy_reasons: set[str]`，
  每次变化做增量 diff。修复「全屏 + 目标进程同时触发、只退出全屏」时，
  被挂起条目永远不恢复的问题（原来 busy 仍为 True 就整段跳过）。
  新增 `_restore_released()` 按条目自身开关 + 当前原因重算；
  `_restore_one(allow_busy=True)` 支持系统仍忙时恢复单个条目
- 便携包补回 `config.json`（v4.0.3 漏了）
- 自检 89 → 94 项；专项验证 34 项

---

## v4.0.3（2026-10-26）

**评审修复：设置保存 / 备份恢复 / 小屏适配**

- **修：设置对话框点「保存」抛 `NameError`**（`fullscreen_avoid` 未定义）——
  出错行之后的代码被静默跳过：全屏避让开关不生效、监控白名单与触发器不刷新，
  且 PySide6 会吞掉槽内异常（不崩溃、无提示）。改为直接读 `self._settings.fullscreen_avoid`
- **修：「从备份恢复配置」后 `order_changed` 重复连接** —— `_populate_cards()`
  每次都重新 connect，一次拖动排序会触发多次配置写盘；改为只在构建时连接一次，
  重建卡片期间用 `_loading` 屏蔽排序信号
- **改：日志面板渲染合并为 200ms 一次** —— 原来每收到一行子进程输出就全量重建
  一次文本（最多 400 行），刷屏日志会卡 UI；并且只有本来停在底部才自动跟随滚动，
  往上翻阅时不被拽回底部
- **改：启动项 / 全局设置对话框主体套滚动区并按屏幕可用高度限高** ——
  1366×768 小屏上「保存」按钮不再被顶出屏幕，标题与按钮始终可见
- **修：`restart_now()` 的隐藏竞态** —— 原实现 stop（异步杀树）后立刻 start，
  会因 stopping 状态被 `is_active` 拒绝；改为轮询等旧进程真正停下后再拉起；
  顺带把误写在 return 之后的 docstring 挪回函数头
- 清理：删除 ItemDialog 里一段无效的路径扩展名校验（把同样的文本写了回去，
  什么都不做）及随之无用的 `ALLOWED_EXTS` 导入

---

## v4.0.2（2026-10-26）

**热键启动 / 停止改为各自独立**

- 之前的规则是「两个都填才生效，只填一个仍走候选」—— 现在改成**各管各的**：
  填了就用你填的，留空就从对应候选里挑第一个没被占用的
- 支持「启动自定义 + 停止用默认」这类混搭
- `START_CANDIDATES` / `STOP_CANDIDATES` 拆成两个独立候选列表；
  新增 `_first_available()` 用临时 ID 探测组合是否可用
- 自定义组合注册失败时不再自动关闭开关，只提示「换个组合试试」
- 专项验证 28 项

---

## v4.0.1（2026-10-26）

**全局热键支持自定义**

- 设置里新增「启动 / 停止」两个 `QKeySequenceEdit`，点进去直接按组合键即可
- 新增 `parse_hotkey()` / `format_hotkey()`：支持 `Ctrl/Alt/Shift/Win` + 字母/数字/F1–F24/方向键/空格等
- **强制要求修饰键** —— 纯字母键会被拒绝，避免抢走正常输入
- 留空则继续走内置候选组合；只填一个也走候选（两个都填才启用自定义）
- 自定义组合被占用时明确提示「自定义热键注册失败」，不会偷偷换成别的组合
- `SettingsDialog.result_settings()` 从 10 项元组改成 **dict**，避免以后加字段时位置写错
- 新增 `AppSettings.hotkey_start` / `hotkey_stop`
- 专项验证 20 项

---

## v4.0.0（2026-10-26）

**四项增强**

- **全局热键**（`app/hotkey.py`）：`RegisterHotKey` + `QAbstractNativeEventFilter` 捕获 `WM_HOTKEY`，Ctrl+Alt+S 全部启动 / Ctrl+Alt+X 全部停止。热键系统独占，按候选列表依次尝试（S/X → S/F10 → Ctrl+Shift+S/Q → F9/F10），全被占用则自动关闭开关并提示。默认关闭，设置里开启
- **端口占用可视化处理**：`_port_owner()` 用 `psutil.net_connections` 找出监听进程，弹窗显示进程名 + PID，提供「结束它并启动 / 强行启动 / 取消」三个选项；结束后台等端口真正释放
- **卡片显示 CPU / 内存**：运行中卡片右下角显示 `CPU% · 内存M`（含子进程），3 秒刷新；用缓存的 `psutil.Process` 对象保证 `cpu_percent` 有真实基线
- **深色主题真正适配卡片**：卡片底从「更不透明的白色」改成深灰 `rgb(40,47,58)`，文字色按主题切换（名字 / 路径 / meta / 资源标签），边框也换成半透明白；`refresh_theme()` 在切主题时重新套用
- 新增 `AppSettings.hotkeys_enabled`
- 自检 89 项；专项验证 25 项

---

## v3.9.0（2026-10-26）

**五项体验改进**

- **托盘状态**：有服务在跑时图标右下角显示绿点，tooltip 变成「大肥鱼启动器 · N 个运行中」
- **拖拽添加**：把 exe/bat/cmd/lnk 拖进窗口直接建卡片（名字取文件名、路径和目录自动填），支持多选
- **单条导入导出**：卡片右键「复制配置（JSON）」/「从剪贴板添加条目」，支持数组批量导入，导入时重新生成 id
- **日志搜索**：日志面板加关键字搜索框，内容或条目名都能搜，与条目过滤叠加生效
- **重启失败通知**：连续异常退出 5 次放弃自动重启时，弹托盘气泡（8 秒）+ 卡片红色「已停止（重启失败）」，下次启动自动清除
- 抽出 `_append_card()` 统一「新建 / 拖拽 / 导入」三条建卡路径
- 修：`_paths_from_mime` 里 `ALLOWED_EXTS` 未导入，异常被 `except: continue` 吞掉导致拖拽永远无效 —— 已把扩展名判断移出 try 块，避免同类静默失败
- 自检 89 项；专项验证 22 项

---

## v3.8.0（2026-10-26）

**新增：每个卡片可自定义图标图片**

- 启动项设置里新增「图标图片」：选择图片 / 清除，带实时预览
- 支持 `png` / `jpg` / `webp` / `bmp` / `ico` / `svg`
- `load_icon_pixmap()` 用 `QPainterPath` 裁圆角 + `KeepAspectRatioByExpanding` 居中裁切
- **图片优先于 emoji**：设了图片显示图片，没设或文件丢失回落 emoji 字符
- `icon_path` 走 `shrink_vars`，启动器目录内的图片存成 `${APP}\...`，换机仍有效
- 新增 `LaunchItem.icon_path` / `resolved_icon_path()`
- 自检 84 → 89 项

---

## v3.7.2（2026-10-26）

**开屏动画改为铺满整个窗口**

- 动画按「等比放大 + 居中裁切」填满客户区，不再留渐变边、不再做圆角卡片
- 900×506 的源在 1140×800 窗口里放大到 1423×800，左右各裁 141px
- 跳过提示改成深色胶囊底 + 白字，压在动画上也能看清
- 窗口设 `WA_OpaquePaintEvent`，全幅绘制更省

---

## v3.7.1（2026-10-26）

**开屏改为覆盖主窗口客户区，去掉全屏大黑边**

- 开屏窗口尺寸和位置 = 主窗口客户区，底色复用主界面的 `#E6F2FF → #FFFFFF` 渐变
- 动画改为圆角卡片 + 柔和阴影居中，淡出时下面就是主界面，尺寸和底色都对得上，不会跳
- 标题栏保持可见，不被遮挡
- 动画宽于窗口时自动等比缩小

---

## v3.7.0（2026-10-26）

**新增：开机动画（可自定义替换）+ 启动项脉冲**

- 开屏动画：全屏暗色遮罩 + 居中动画，点任意处可跳过
- **外部文件优先**：`<exe 目录>\boot.webp` / `boot.gif` 覆盖内置那份，删掉即回落，无需重新打包
- 内置 `assets/boot.webp`（0–6s 加速到 2s，900px，959 KB）
- 设置里新增「启动时显示开机动画」开关
- 时长从动画帧延迟自动算出，换任何文件都自适应；任何解码异常都静默跳过
- 启动项动画：点开关启动时卡片描边呼吸一次（760 ms），不缩放不闪烁
- 新增 `app/splash.py`
- 自检 80 → 84 项

> 注意：`--onefile` 解压约 1.1 秒，这段时间没有界面，动画只能在之后出现。

---

## v3.6.0（2026-10-26）

**新增：启动后自动打开服务地址**

- 每张卡片可单独开启，支持三级兜底：就绪端口 → 手填地址 → 从控制台输出抓取
- 只放行本机 / 局域网地址（`localhost`、`127.x`、`10.x`、`172.16–31.x`、`192.168.x`、`::1`），
  输出里的外部网址会被拒绝并记入日志 —— 批处理输出不可信
- 进程进入运行中后延迟 1.5 秒打开；每轮启动只开一次，自动重启不重复弹浏览器
- 卡片右键新增「打开网页」
- 新增 `app/weblink.py`
- 配置版本 5 → 6

---

## v3.5.0（2026-10-26）

**窗口形态合并为三态，修掉后台模式 `pause` 卡死**

- 原来的「窗口模式」+「输出方式」两个字段互相冲突（最小化 + 捕获 = 最小化了个空窗口），
  合并成单一 `window` 三态：`normal` / `minimized` / `hidden`
- 卡片右键新增「窗口形态」快捷切换
- 后台运行模式下给子进程 `stdin=DEVNULL`，批处理结尾的 `pause` 会被自动跳过，
  不再永久阻塞（此前会变成僵尸进程，卡片一直显示运行中）
- 配置版本 4 → 5，旧的 `capture_output=True` 自动迁移为 `window=hidden`

---

## v3.4.2（2026-10-26）

**修复：托盘「全部停止并退出」卡死**

- 根因：`shutdown()` 先关日志读取器、后杀进程。读线程阻塞在 `readline()` 时持有文件对象锁，
  `stream.close()` 与之互等形成死锁；进程未结束导致管道永远没有 EOF，锁无法解开
- 修复：改为**先杀进程、再关读取器**；`LogReader.stop()` 在确认线程已退出后才关闭管道
- `_quit_app()` 先隐藏窗口和托盘再执行停止，避免等待期看起来像卡死
- 自检新增回归用例「子进程无输出时退出不会卡死」

---

## v3.4.1（2026-10-26）

**修复：批处理输出被日志面板吞掉**

- 根因：无条件 `stdout=PIPE`。`cmd.exe` 是控制台程序，父进程无控制台时 Windows 会新建窗口，
  但输出全进了管道 —— 窗口一片空白，看起来像「点了开关什么都没发生」；`pause` 的提示也被吞掉
- 修复：非捕获模式不再重定向，让子进程拿到真实控制台句柄；捕获模式加 `CREATE_NO_WINDOW` 不再弹空窗口
- 新增「输出方式」下拉（v3.5.0 已合并进「窗口形态」）

---

## v3.4.0（2026-10-26）

**便携化改造 + 5 个 P0 修复**

便携化：

- `app/paths.py` 重写：`data_dir()` 真实写文件探测目录可写性，不可写时降级到 `%LOCALAPPDATA%\大肥鱼启动器`
- `${APP}` 变量：程序目录内的路径自动存成相对形式，整个文件夹搬走仍有效
- 新增 `app/crashlog.py`：`sys.excepthook` + `threading.excepthook` + `faulthandler` + Qt 消息处理器 → `logs/crash.log`
- 新增 `app/detect.py` + `detect_rules.json`：探测规则外置，支持 `${APP}`、`${APP}/..` 搜索根
- 新增 `app/housekeeping.py`：清理 PyInstaller 遗留的 `%TEMP%\_MEI*`，靠属主标记 + PID 存活判断，只删自己的
- 诊断包改为包含 `crash.log`、轮转日志、数据目录与运行模式

P0 修复：

1. **单实例锁失效** —— `CreateFileW` 未声明 `restype`，失败返回 `-1` 与 64 位 `INVALID_HANDLE_VALUE` 比较恒为假
2. **日志轮转写错文件** —— 轮转后句柄未重开，`launcher.log` 永久停更；改为按序号轮转并先关句柄
3. **删光卡片会复活默认条目** —— `items: []` 被当成字段缺省
4. **带空格的启动参数被引号污染** —— `shlex.split(posix=False)` 保留引号
5. **退出时接管进程杀不干净** —— daemon 线程未 join

另修：按 PID 结束进程前校验创建时间，防止 PID 复用导致误杀无关进程。

---

# 历史记录（v2 → v3.3）

## 一、v2 新增功能

| 功能 | 说明 |
|---|---|
| **便携化** | 配置/日志固定在 exe 同级目录；不写 AppData、不写注册表（除非你开启开机自启） |
| **托盘常驻** | 点 X 默认隐藏到托盘；托盘右键：显示主界面 / 全部停止并退出；双击图标打开 |
| **开机自启** | 设置里勾选，写 `HKCU\...\Run`，路径是当前 exe 的真实位置，带 `--minimized` |
| **崩溃自动重启** | 每个条目可开 `auto_restart`；非用户主动关闭的退出，3 秒后自动拉起，最多连续 5 次 |
| **延迟启动** | 每个条目可设 `delay_seconds`；总开关级联时用 `QTimer.singleShot` 排队，不阻塞 UI |
| **端口预检** | 每个条目可设 `check_port`；启动前探测 `127.0.0.1:端口`，被占用会弹窗询问是否强行启动 |
| **日志面板** | 底部可折叠面板，捕获子进程 stdout/stderr（UTF-8/GBK 自适应），同时写入 `logs/launcher.log` |
| **单实例保护** | 重复双击 exe 不会多开：第二个实例静默退出，并唤醒第一个（从托盘/最小化恢复并置顶） |
| **全屏避让状态机** | 全屏时暂停勾选了「参与全屏避让」的条目；退出全屏后**只恢复被暂停的那些**，用户手动关掉的不会被拉起 |
| **目标进程避让** | 可配置 `monitor_targets`（exe / 目录）；命中时暂停勾选了「进程避让」的条目，目标退出后自动恢复；白名单可排除 |
| **System Busy 徽章** | 顶部实时显示 🟢 监控中 / 🟡 触发避让：全屏运行中 / 🟡 触发避让：目标进程运行中 |
| **工作目录隔离** | 子进程 `cwd` 永远是目标文件所在目录（或你显式指定），不继承启动器环境 |

## 二、界面

- 浅蓝到白的垂直渐变背景，左上角你的图标
- 圆角 22px 半透明卡片，阴影改为**自绘**（不再用 `QGraphicsDropShadowEffect`，见“已知问题”）
- 悬浮透明度 0.60 → 0.86（200ms）
- 开关滑块 180ms + 背景色渐变；忙碌时开关上有旋转圆弧
- 拖拽排序：蓝色插入指示条 + 220ms 位移动画
- 底部按钮：添加启动项 / 编辑模式 / 自动探测路径 / 设置 / 日志 / 全部停止

## 三、快速开始

```bat
C:\Python\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe launcher.py
```

打包（`build.bat` 会自动跑自检再打包）：

```bat
build.bat
```

手动：

```bat
.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --noconsole ^
    --name "AstrBotLauncher" --icon "assets\app.ico" --add-data "assets;assets" ^
    --hidden-import psutil launcher.py
```

产物：`dist\AstrBotLauncher.exe` → 自动复制为 `dist\大肥鱼启动器.exe`。

> ⚠️ **必须使用 PySide6 6.11.2**（`requirements.txt` 已锁死）。
> 实测 6.12.0 在本项目里退出时会随机崩溃（0xC0000409），降级后 10/10 稳定。

自检（60 项，约 40 秒）：

```bat
.venv\Scripts\python.exe launcher.py --selftest
```

## 四、便携化说明

| 内容 | 位置 |
|---|---|
| 配置 | `<exe 目录>\config.json` |
| 日志 | `<exe 目录>\logs\launcher.log`（超过 2MB 自动转 `.old`） |
| 自检记录 | `<exe 目录>\logs\selftest.log` |
| 图标资源 | 打包进 exe（`assets/`，单文件模式解压到 `_MEIPASS`） |

拷走整个文件夹（或单个 exe + config.json）到任何电脑、任何盘符/U 盘都能用。
`config.json` 里的路径是绝对路径，换电脑后建议点一次「自动探测路径」或重新设置。

## 五、配置结构（version 2）

```json
{
  "version": 2,
  "items": [
    {
      "id": "a1b2c3d4e5f6",
      "name": "SnowLuma（QQ 桥）",
      "path": "D:\\...\\SnowLuma-v2.1.1-win-x64\\launcher.bat",
      "args": "",
      "cwd": "",
      "window": "minimized",
      "icon": "🐋",
      "delay_seconds": 3,
      "auto_restart": true,
      "check_port": 0,
      "avoid_fullscreen": true,
      "avoid_process": true
    }
  ],
  "settings": {
    "autostart": false,
    "close_to_tray": true,
    "fullscreen_avoid": true
  }
}
```

| 字段 | 说明 |
|---|---|
| `delay_seconds` | 0–600；总开关轮到它之后再等这么久 |
| `auto_restart` | 异常退出 3 秒后自动重启，连续 5 次失败则停 |
| `check_port` | 0 = 不检测；否则启动前探测 `127.0.0.1:端口` |
| `avoid_fullscreen` | 该条目是否参与全屏避让（全屏时暂停，退出后恢复） |
| `avoid_process` | 该条目是否参与目标进程避让（触发器命中时暂停） |
| `targets` | 监控触发器数组：`kind` = `exe`/`dir`，`path`、`enabled`、`note` |
| `settings.close_to_tray` | 关闭窗口是否隐藏到托盘 |
| `settings.fullscreen_block_processes` | 额外屏蔽的进程名（逗号分隔，如 `obs64.exe`） |
| `settings.fullscreen_allow_processes` | 精确触发名单（逗号分隔，游戏 exe；命中后即使有边框也按全屏处理） |
| `settings.autostart` | 仅记录状态，真实生效看注册表 |

## 六、代码结构

```
AstrBotLauncher/
├─ launcher.py            入口（--minimized / --selftest / --screenshot）
├─ app/
│  ├─ paths.py            【新增】便携路径：exe 目录 / 配置 / 日志 / 资源
│  ├─ config.py           【改】LaunchItem 新增 3 字段，AppSettings 新增 2 字段
│  ├─ autostart.py        【新增】注册表开机自启（动态 exe 路径）
│  ├─ logreader.py        【重写】daemon 线程 + LogBridge 信号
│  ├─ processes.py        【改】日志管道、自动重启、cwd 隔离、条目缓存
│  ├─ switch.py           【改】新增 shutdown() 停动画
│  ├─ card.py             【改】自绘阴影、meta 徽标、shutdown()
│  ├─ flow.py             【改】新增 shutdown()
│  ├─ dialogs.py          【改】条目新增 3 个选项；新增 SettingsDialog
│  ├─ theme.py            【改】日志面板/日志视图样式
│  ├─ mainwindow.py       【改】托盘、日志面板、端口预检、延迟级联、设置
│  └─ selftest.py         【改】41 项
├─ assets/                 logo.png / app.ico
├─ docs/                   截图
├─ build.bat / requirements.txt
```

## 七、已知问题

1. **PySide6 6.12.0 退出随机崩溃**（0xC0000409/0xC0000005）
   - 现象：界面正常，关闭程序时偶发崩溃（源码与打包版都会）
   - 处理：锁定 **PySide6 6.11.2**，实测 10/10 稳定；`build.bat` 与 `requirements.txt` 已锁版本
2. **`QGraphicsDropShadowEffect` 已弃用**：改为卡片内自绘阴影，少一层图形特效，也规避了一类崩溃
3. **`.lnk` 无法跟踪/停止**：交给系统 Shell，1.2 秒后开关回弹并提示
4. **`.bat` 会带控制台窗口**：选“最小化”只最小化控制台；要完全隐藏需自行加 `CREATE_NO_WINDOW`
5. **自动重启上限 5 次**：防止程序启动即崩导致死循环
6. **开机自启路径**：移动文件夹后需要在设置里重新勾选一次（注册表里存的是旧路径）
7. **单文件冷启动 1–3 秒**：PyInstaller `--onefile` 解压开销
8. **全屏检测避让**：v2.1 已实现。检测到前台全屏时，只把**这一轮**启动的子程序改为最小化，不修改条目自身的窗口设置；托盘气泡在全屏时也不弹。

## 八、常见问题

**Q：点 X 后程序去哪了？**
A：默认最小化到托盘（右下角），双击图标回来；托盘右键可「全部停止并退出」。

**Q：关掉启动器会杀掉我手动开的 AstrBot 吗？**
A：不会。只管理它自己拉起的进程。

**Q：日志面板没内容？**
A：只捕获子进程的 stdout/stderr；GUI 程序（如 AstrBot 桌面版）通常不往控制台输出。

**Q：自动重启会不会一直重启？**
A：连续 5 次后停止，并在界面提示。

**Q：开机自启没生效？**
A：设置里勾选后检查任务管理器→启动项；移动过 exe 需要重新勾选。

---

## 九、v2 实测结果（2026-10-25）

| 测试 | 结果 |
|---|---|
| 自检（源码） | **60 项全部通过**，退出码 0 |
| 自检（打包 exe） | **60 项通过**，退出码 0 |
| 托盘拦截关闭 | 窗口句柄变 0、进程存活 ✅ |
| 端口预检 | 占用/释放/0 三种情况判定正确 |
| 日志捕获 | 子进程 `hello-one` 被读到 |
| 自动重启 | 崩溃一次后 3 秒重启成功，PID 变化 |
| 延迟级联 | 第二个程序在第一个 running 后 1.61s 启动（含 1s 延迟） |
| 退出稳定性（6.11.2） | 10/10 正常退出 |
| 全屏检测（真实全屏窗口） | `(True, 'FULLSCREEN-TEST')`；回到桌面为 `(False, '')` |
| 全屏避让开关持久化 | 通过 |

产物：`dist\大肥鱼启动器.exe`（36.4 MB）

---

## 十一、v2.2：单实例与全屏状态机（2026-10-25）

### 1. 单实例（`app/single_instance.py`）

| 层 | 作用 |
|---|---|
| 独占文件锁（`%TEMP%\AstrLauncher_v2.lock`，CreateFileW 不共享） | 真正的互斥；进程退出/被杀由系统释放，不会幽灵锁 |
| QLocalSocket 握手 | 第二个实例连上第一个的 `QLocalServer`，发送 `SHOW` |
| QLocalServer（`AstrLauncher_Single_Instance_v2`） | 接收唤醒消息，主窗口 `showNormal + raise + SetForegroundWindow` |

> 实测发现：Qt 6.11 在 Windows 上**允许同名管道多个 QLocalServer 同时 listen**，
> 所以不能只用 `listen()` 返回值判重；文件锁是必需的互斥层。
> 未使用 QSharedMemory，避免强杀后的幽灵锁。

### 2. 全屏避让状态机（`app/fullscreen.py` + `MainWindow`）

- `FullScreenDetector`：QTimer 1.2s 采样 + 2 次去抖
- 判定：DWM 扩展边框 + `GWL_STYLE` 无边框判断 + 覆盖 ≥96%/严格覆盖 + 进程名过滤
- 进程过滤：内置 shell 黑名单；设置里可加 `fullscreen_block_processes` / `fullscreen_allow_processes`
- 进入全屏：`paused_by_fullscreen = [勾选了避让且正在运行的条目]`，逐个 `stop()`
- 退出全屏：只遍历该列表恢复；恢复前再次校验路径与运行状态
- 全屏期间用户手动关闭 → 从列表移除；手动启动 → 记入 `user_override`，本轮不再暂停

### 3. 打包注意

| 项 | 说明 |
|---|---|
| 管道名 | 固定字符串，与路径无关；onefile 解压目录变化不影响 |
| 同名冲突 | 两个不同版本的启动器共用同一个名字 → 第二个会被当成“重复实例”。若要做多版本并存，改 `SERVER_NAME` 与 `LOCK_FILE` |
| 锁文件 | 在 `%TEMP%`，不进配置目录；换电脑/换 U 盘均生效（每机一个实例） |
| 防火墙 | 本地命名管道不触发防火墙，无需放行 |
| 异常退出 | 锁句柄随进程回收，残留的管道用 `removeServer()` 清掉，下次可正常启动 |

---

## 十二、v3：多维度状态监控与独立避让（2026-10-25）

### 数据模型

```json
{
  "version": 2,
  "items": [
    { "...": "...", "avoid_fullscreen": true, "avoid_process": true }
  ],
  "targets": [
    { "id": "t1", "kind": "exe", "path": "D:\\Games\\EldenRing\\eldenring.exe", "enabled": true, "note": "" },
    { "id": "t2", "kind": "dir", "path": "D:\\Games\\SteamLibrary", "enabled": false, "note": "" }
  ],
  "settings": {
    "fullscreen_avoid": true,
    "monitor_whitelist_processes": "chrome.exe,obs64.exe",
    "monitor_whitelist_titles": "Bilibili,腾讯视频"
  }
}
```

### System Busy = 全屏 OR 目标进程

- `FullScreenDetector`：1.2s 采样 + 2 次去抖；DWM 扩展边框 + 无边框样式判断；支持窗口标题白名单
- `ProcessMonitorThread`：独立 QThread，3 秒 `psutil.process_iter(['name','exe'])`；
  支持 exe 名 / 完整路径 / 目录前缀匹配，路径 `normcase` 容错，异常全部吞掉
- `MainWindow._refresh_system_busy()`：只在 False↔True 翻转时动作一次（OR 去重）
- 进入忙碌：遍历运行中的条目，按「全屏且 avoid_fullscreen」或「进程且 avoid_process」停止，记入 `paused_by_system[id]=原因`
- 恢复：仅遍历该字典，`QTimer.singleShot(250ms 递增)` 依次拉起；用户手动关掉的已从字典移除，绝不复活
- 状态徽章：🟢 监控中 / 🟡 触发避让：全屏运行中 / 🟡 触发避让：目标进程运行中
- 卡片：被系统挂起时显示灰色胶囊「已挂起 · 全屏 / 进程 / 全屏/进程」

### 新增文件 / 改动

| 文件 | 内容 |
|---|---|
| `app/monitor.py` | **新增** `ProcessMonitorThread` |
| `app/config.py` | `avoid_process`、`MonitorTarget`、`targets`、两条白名单 |
| `app/dialogs.py` | 条目「进程避让」勾选；设置里白名单输入；**新增** `MonitorTargetsDialog` |
| `app/fullscreen.py` | 窗口标题白名单 `blocked_titles` |
| `app/card.py` | 挂起胶囊 `set_system_suspended()` |
| `app/mainwindow.py` | 状态徽章、触发器入口、System Busy 状态机、`paused_by_system` |
| `app/selftest.py` | 60 项（新增触发器持久化、进程监控线程、进程避让恢复、单实例隔离） |

### 打包注意

- `ProcessMonitorThread` 必须在退出前 `stop()`（已在 `_shutdown_ui` / `_force_quit` / 自检里处理），否则 Qt 会报 “QThread: Destroyed while thread is still running” 并 abort
- 单实例名字支持环境变量覆盖（`LAUNCHER_SINGLE_NAME` / `LAUNCHER_SINGLE_LOCK`），自检用它避免和正在运行的正式实例抢锁
- 监控目标路径写死盘符时，换电脑后目录不存在：匹配逻辑会跳过，不会崩

---

## 十三、v3.1：深度打磨（2026-10-25）

### 已实现

| 分类 | 内容 |
|---|---|
| P0 修复 | ① 总开关级联在 System Busy 时延后启动（不再误标为用户覆盖）② 忙碌期间自动重启挂起、空闲后统一补 ③ 恢复队列里用户手动关掉的条目不再复活 |
| 就绪检测 | 每条目可设 `ready_port` + `ready_timeout`；端口监听成功才算“运行中”，总开关级联等待真实就绪 |
| 配置安全 | 保存前自动备份 `config.bak-*.json`（保留 5 份）；设置里可“从备份恢复”；`version 1/2 → 3` 自动迁移 |
| 残留检测 | 停止后扫描同名进程，日志 + toast 提示残留 PID |
| 权限 | 目标需要管理员权限时弹窗，支持一键以管理员身份重启启动器 |
| 路径巡检 | 每 30 秒检查条目路径，失效卡片置灰并记录一次日志（U 盘拔出/目录改名） |
| 窗口记忆 | 记住上次位置与大小，多屏也校验可见区域 |
| 主题 | 跟随系统 / 浅色 / 深色（设置里切换，立即生效） |
| 托盘菜单 | 动态列出每个服务状态，可直接启停 |
| 卡片右键 | 启动/停止、打开所在目录、复制路径、设置、删除 |
| 分组 | 条目可设 `group`，底部“分组”菜单支持整组启动/停止 |
| 日志 | 按条目过滤、导出诊断包（config + logs + 系统信息 zip） |
| 关于 | 托盘/菜单可查看版本与路径；exe 带版本资源（3.0.0.0） |
| 单实例 | 名称升级到 v3，避免与旧版本互相判重 |

### 未实现（原因）

- **数字签名**：需要代码签名证书（个人使用可跳过；SmartScreen 提示用“更多信息→仍要运行”）。
- **在线更新检查**：需要一个发布服务器/固定 URL；目前没有，留了 `__version__` 与诊断包方便手动比对。如果你有 GitHub Releases 或自建地址，我可以加一个可选的检查按钮。

### 依赖说明（发给别人直接可用）

- 单文件 exe 已打包 PySide6 + psutil + Python 运行时，**对方无需安装任何东西**。
- 仅支持 Windows 10/11 x64。
- 已验证：把 exe 单独复制到全新空目录，`--selftest` 通过，只生成 `logs/` 与（保存后）`config.json`。

---

## 十四、v3.2：启动时接管已运行服务（2026-10-25）

### 功能

启动器启动后 0.8 秒自动扫描每个条目，发现已经有进程在跑就“接管”：
卡片显示 **运行中 · 已接管**，开关点亮，可以直接用启动器停止它。

匹配优先级（`app/adopt.py`）：

| 优先级 | 方式 | 适用 |
|---|---|---|
| 1 | 可执行文件完整路径相同 | exe 条目 |
| 2 | 进程命令行包含目标路径 | `.bat` / `.cmd`（`cmd /c xxx.bat`） |
| 3 | 端口监听者校验 | 配置了 `ready_port` 的条目 |
| 4 | 进程名相同（兜底，可能误判） | 同名程序 |

- 只读扫描，不启动也不终止；接管后停止走「按 PID 杀进程树」（psutil + taskkill 兜底）
- 接管进程退出后自动把卡片置回“未运行”
- 托盘右键「扫描已运行服务」可手动重新扫描
- 手动再次启动同一条目时会清除接管标记

### 注意

- 接管只记录“这是谁的进程”，不会把它变成启动器的子进程；启动器退出时默认**不会**杀掉接管进程（除非你点“全部停止”）。
- 若同名程序较多，可能接管到错误的进程；建议给条目填完整路径，或配置 `ready_port` 提高准确性。

---

## 十五、v3.3：接管可配置（2026-10-25）

| 层级 | 开关 | 位置 | 默认 |
|---|---|---|---|
| 全局 | `settings.adopt_on_startup` | 设置 → 「启动时自动扫描并接管已在运行的服务」 | 开 |
| 单条目 | `allow_adopt` | 条目设置 → 「允许启动时自动接管已在运行的进程」 | 开 |

行为：

- 全局关 → 启动时不扫描（托盘「扫描已运行服务」仍可手动触发，忽略全局开关）
- 条目关 → 即使全局开、即使手动扫描，也不会接管这个条目
- 卡片 meta 会显示 `不接管` 标记
- 手动扫描会统计并写日志：`扫描：没有发现可接管的运行中服务。（N 个条目设置为不接管）`

自检增至 **71 项**，覆盖：两个开关的持久化、`allow_adopt=False` 不接管、`allow_adopt=True` 接管、接管后停止仍能杀进程。

### build.bat 现在做什么

1. 检查/创建 `.venv`
2. 跑 71 项自检（失败则停止）
3. PyInstaller 打包，生成 `dist\大肥鱼启动器.exe`，删除重复的 `AstrBotLauncher.exe` 和 `build\` 缓存
4. **同步到运行目录（`build.bat` 里写死的路径），自动停掉旧实例并重启新版本**

> 自动化调用时设置环境变量 `LAUNCHER_NO_PAUSE=1`，可跳过末尾的“按任意键继续”。
> 不想自动同步，删掉 build.bat 里 `rem ===== 4)` 那一段即可。

## 操作手册

完整使用说明见 [docs/用户指南.md](docs/用户指南.md)。
（早期版本另有一份 `操作手册.docx`，内容已并入用户指南，v4.1.1 起不再随仓库分发。）
