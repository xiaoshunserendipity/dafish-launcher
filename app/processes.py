"""进程管理：启动 / 停止 / 状态轮询 / 崩溃自动重启 / 日志读取。

要点：
- 用 STARTUPINFO + STARTF_USESHOWWINDOW 控制子进程窗口（正常 / 最小化）；
- .bat/.cmd 通过 cmd.exe /c 启动，并显式传入目标目录作为 cwd（不继承启动器环境）；
- 停止时按进程树处理（psutil 递归，taskkill 兜底）；
- 只管理“由本启动器拉起”的进程，绝不碰别人启动的程序；
- 子进程输出通过 QThread 读取，不阻塞 UI。
"""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .config import SCRIPT_SUFFIXES, LaunchItem, WINDOW_MINIMIZED
from .logreader import LogBridge, LogReader

try:  # psutil 用于递归终止进程树 / 自动重启判断
    import psutil  # type: ignore
except Exception:  # pragma: no cover - 兜底
    psutil = None  # type: ignore

IS_WINDOWS = sys.platform.startswith("win")

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"
STATE_ERROR = "error"

SCRIPT_EXTS = set(SCRIPT_SUFFIXES)
LINK_EXTS = {".lnk"}
ALLOWED_EXTS = {".exe", ".bat", ".cmd", ".lnk", ".com"}

# Win32 ShowWindow 常量（subprocess 并没有导出 SW_SHOWMINIMIZED）
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2

AUTO_RESTART_DELAY_MS = 3000
AUTO_RESTART_MAX = 5  # 连续崩溃最多重试次数，避免死循环
# 稳定运行超过这个时长，下次退出时把"连续崩溃"计数清零。
# 没有这一条，计数就是"累计"：一个每天崩一次的服务，第 6 次崩溃后会永久失去自动重启。
STABLE_UPTIME_SEC = 60.0


def psutil_available() -> bool:
    return psutil is not None


def _strip_quotes(token: str) -> str:
    """去掉成对的首尾引号。

    `shlex.split(posix=False)` 会保留引号，直接交给 Popen 会让目标程序
    收到带引号的字面量（例如 `"C:\\my dir\\a.json"`）。
    不能用 posix=True：那会把 Windows 路径里的反斜杠当转义符吃掉。
    """
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _split_args(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        tokens = shlex.split(raw, posix=False)
    except ValueError:
        tokens = raw.split()
    return [_strip_quotes(token) for token in tokens]


def _startup_info(minimized: bool):
    if not IS_WINDOWS:
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = SW_SHOWMINIMIZED if minimized else SW_SHOWNORMAL
    return info


def _port_open(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False


def _proc_create_time(pid: int) -> float | None:
    """进程创建时间，作为 PID 的身份指纹。"""
    if psutil is None or pid <= 0:
        return None
    try:
        return float(psutil.Process(pid).create_time())
    except Exception:
        return None


def _is_same_process(pid: int, created: float | None) -> bool:
    """pid 是否还是当初那个进程（防 PID 复用导致误杀）。"""
    if pid <= 0:
        return False
    if psutil is None:
        return True  # 没有 psutil 就无从校验，按原逻辑继续
    try:
        proc = psutil.Process(pid)
        if created is None:
            return True
        return abs(proc.create_time() - created) < 0.01
    except Exception:
        return False


def _kill_pid_tree(pid: int, created: float | None = None) -> None:
    """按 PID 结束整棵进程树（用于接管的、不是我们 Popen 出来的进程）。"""
    if pid <= 0:
        return
    if not _is_same_process(pid, created):
        return  # PID 已被系统复用，绝不误杀无关进程
    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass
            try:
                parent.terminate()
            except Exception:
                pass
            try:
                _, alive = psutil.wait_procs([parent] + children, timeout=1.5)
            except Exception:
                alive = []
            for leftover in alive:
                try:
                    leftover.kill()
                except Exception:
                    pass
        except Exception:
            pass
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """尽力杀掉整棵进程树（bat → node 这类孙进程也能带走）。"""
    pid = proc.pid
    if proc.poll() is not None:
        # 已经退出且被回收，PID 可能被复用，别再按 PID 下手
        return
    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
        except Exception:
            parent, children = None, []
        for child in children:
            try:
                child.terminate()
            except Exception:
                pass
        if parent is not None:
            try:
                parent.terminate()
            except Exception:
                pass
        try:
            _, alive = psutil.wait_procs(
                ([parent] if parent is not None else []) + children, timeout=1.5
            )
        except Exception:
            alive = []
        for leftover in alive:
            try:
                leftover.kill()
            except Exception:
                pass

    # 兜底：taskkill 整棵树
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
    try:
        proc.wait(timeout=1.5)
    except Exception:
        pass


class ProcessManager(QObject):
    state_changed = Signal(str, str)  # item_id, state
    notice = Signal(str)  # 给界面用的提示语
    output = Signal(str, str)  # item_id, log line
    exited = Signal(str, int)  # item_id, exit code
    needs_admin = Signal(str, str)  # item_id, name（目标需要管理员权限）
    gave_up = Signal(str, str)      # item_id, name（连续退出太多次，放弃自动重启）
    ready = Signal(str)  # item_id 真正就绪（端口已监听 / 窗口已起）

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._procs: dict[str, subprocess.Popen] = {}
        self._items: dict[str, LaunchItem] = {}
        self._external: set[str] = set()
        self._states: dict[str, str] = {}
        self._readers: dict[str, tuple[LogReader, LogBridge]] = {}
        self._user_stopped: set[str] = set()
        self._restart_counts: dict[str, int] = {}
        # 本次启动的单调时钟时间点：用来判断"连续崩溃"——稳定运行超过阈值就清零计数
        self._started_at: dict[str, float] = {}
        self._restart_pending: set[str] = set()
        self._system_busy = False
        self._deferred_restarts: set[str] = set()
        self._adopted: dict[str, tuple[int, float | None]] = {}   # item_id -> (pid, 创建时间)
        self._lock = threading.Lock()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1200)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    # ---------------- 状态 ----------------
    def state(self, item_id: str) -> str:
        return self._states.get(item_id, STATE_STOPPED)

    def is_active(self, item_id: str) -> bool:
        return self.state(item_id) in {STATE_STARTING, STATE_RUNNING, STATE_STOPPING}

    def active_items(self) -> list[str]:
        return [i for i in self._states if self.is_active(i)]

    def pid_of(self, item_id: str) -> int | None:
        with self._lock:
            proc = self._procs.get(item_id)
        if proc is not None:
            return proc.pid
        adopted = self._adopted.get(item_id)
        return adopted[0] if adopted else None

    def is_adopted(self, item_id: str) -> bool:
        return item_id in self._adopted

    def adopt(self, item: LaunchItem, pid: int, name: str = "") -> bool:
        """接管一个已经在运行的进程（不启动、只登记）。"""
        if self.is_active(item.id) or pid <= 0:
            return False
        if psutil is not None:
            try:
                if not psutil.pid_exists(pid):
                    return False
            except Exception:
                return False
        self._adopted[item.id] = (pid, _proc_create_time(pid))
        self._items[item.id] = item
        self._set_state(item.id, STATE_RUNNING)
        self.notice.emit(f"已接管「{item.name}」的已运行进程（PID {pid}{'，' + name if name else ''}）。")
        return True

    def _set_state(self, item_id: str, state: str) -> None:
        self._states[item_id] = state
        self.state_changed.emit(item_id, state)

    # ---------------- 启动 ----------------
    def start(self, item: LaunchItem, force_minimized: bool = False) -> bool:
        if self.is_active(item.id):
            return False
        raw_path = item.resolved_path()
        if not raw_path:
            self.notice.emit(f"「{item.name}」还没有设置路径，请先点“设置路径”。")
            self._set_state(item.id, STATE_STOPPED)
            return False
        path = Path(raw_path)
        if not path.exists():
            self.notice.emit(f"「{item.name}」的路径不存在：{path}")
            self._set_state(item.id, STATE_STOPPED)
            return False

        self._items[item.id] = item
        self._user_stopped.discard(item.id)
        self._restart_pending.discard(item.id)

        ext = path.suffix.lower()
        self._set_state(item.id, STATE_STARTING)

        # .lnk：交给系统 Shell，无法跟踪进程，只做一次性启动
        if ext in LINK_EXTS:
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
                self._external.add(item.id)
                self._set_state(item.id, STATE_RUNNING)
                QTimer.singleShot(1200, lambda: self._finish_external(item.id))
                return True
            except Exception as exc:
                self.notice.emit(f"「{item.name}」启动失败：{exc}")
                self._set_state(item.id, STATE_ERROR)
                QTimer.singleShot(900, lambda: self._set_state(item.id, STATE_STOPPED))
                return False

        args = _split_args(item.args)
        if ext in SCRIPT_EXTS:
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            cmd = [comspec, "/c", str(path), *args]
        else:
            cmd = [str(path), *args]

        cwd = item.effective_cwd()
        if not cwd or not Path(cwd).exists():
            cwd = str(path.parent.resolve())

        hidden = item.is_hidden()
        popen_kwargs: dict = {
            "cwd": cwd,
            "startupinfo": _startup_info(
                force_minimized or item.window == WINDOW_MINIMIZED
            ),
            "close_fds": True,
            "env": os.environ.copy(),
        }
        if hidden:
            popen_kwargs["stdout"] = subprocess.PIPE
            popen_kwargs["stderr"] = subprocess.STDOUT
            popen_kwargs["bufsize"] = 0
            # 后台模式没有窗口，批处理里的 pause 永远等不到按键 ——
            # 给它一个空 stdin，pause 读到 EOF 直接跳过，否则进程会永远挂着
            popen_kwargs["stdin"] = subprocess.DEVNULL
            # 输出已经进日志面板了，不必再弹一个空白的控制台窗口
            # （GUI 程序本来就没有控制台，这个标志对它们无影响）
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        # 非后台模式**不要**指定 stdout/stderr：子进程才能拿到真实控制台句柄，
        # 批处理的输出才会正常显示在它自己的窗口里。
        # （之前无条件 PIPE，导致控制台窗口一片空白，看起来像"什么都没运行"）

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror == 740 or isinstance(exc, PermissionError):
                self.needs_admin.emit(item.id, item.name)
                self.notice.emit(f"「{item.name}」需要管理员权限才能启动。")
            else:
                self.notice.emit(f"「{item.name}」启动失败：{exc}")
            self._set_state(item.id, STATE_ERROR)
            QTimer.singleShot(900, lambda: self._set_state(item.id, STATE_STOPPED))
            return False

        with self._lock:
            self._procs[item.id] = proc
        self._started_at[item.id] = time.monotonic()

        if hidden and proc.stdout is not None:
            bridge = LogBridge(self)
            bridge.line_ready.connect(self.output)
            reader = LogReader(item.id, proc.stdout, bridge)
            reader.start()
            self._readers[item.id] = (reader, bridge)

        self._restart_counts.setdefault(item.id, 0)
        if item.ready_port > 0:
            self._wait_ready(item, proc.pid, item.ready_timeout)
        else:
            # 没有就绪端口：稍等片刻再切 running，让“正在启动”的转圈有存在感
            QTimer.singleShot(260, lambda: self._mark_running(item.id))
        return True

    def _wait_ready(self, item: LaunchItem, pid: int, timeout_seconds: int) -> None:
        """端口就绪才算真正启动（用于总开关级联等待）。"""
        deadline = time.monotonic() + max(5, int(timeout_seconds or 60))

        def probe() -> None:
            if self._states.get(item.id) != STATE_STARTING:
                return
            try:
                if psutil is not None and not psutil.pid_exists(pid):
                    return
            except Exception:
                pass
            if _port_open(item.ready_port):
                self._set_state(item.id, STATE_RUNNING)
                self.ready.emit(item.id)
                return
            if time.monotonic() >= deadline:
                self.notice.emit(
                    f"「{item.name}」端口 {item.ready_port} 在 {timeout_seconds}s 内未就绪，按已启动处理。"
                )
                self._set_state(item.id, STATE_RUNNING)
                return
            QTimer.singleShot(400, probe)

        QTimer.singleShot(300, probe)

    def set_system_busy(self, busy: bool) -> None:
        """系统忙碌期间把自动重启挂起，空闲后统一补。"""
        self._system_busy = bool(busy)
        if self._system_busy:
            return
        pending = list(self._deferred_restarts)
        self._deferred_restarts.clear()
        for index, item_id in enumerate(pending):
            QTimer.singleShot(300 * index, lambda i=item_id: self._do_auto_restart(i))

    def residual_processes(self, item: LaunchItem) -> list[tuple[int, str]]:
        """停止后扫残留（taskkill 偶尔失败时会留下同名进程）。"""
        raw_path = item.resolved_path()
        if psutil is None or not raw_path:
            return []
        target_name = Path(raw_path).name.lower()
        target_path = os.path.normcase(os.path.normpath(raw_path))
        found: list[tuple[int, str]] = []
        try:
            for proc in psutil.process_iter(["name", "exe"]):
                try:
                    info = proc.info or {}
                    name = (info.get("name") or "").lower()
                    exe = os.path.normcase(os.path.normpath(info.get("exe") or ""))
                    if name == target_name or (exe and exe == target_path):
                        found.append((proc.pid, info.get("name") or ""))
                except Exception:
                    continue
        except Exception:
            return found
        return found

    def _mark_running(self, item_id: str) -> None:
        if self._states.get(item_id) == STATE_STARTING:
            self._set_state(item_id, STATE_RUNNING)

    def _finish_external(self, item_id: str) -> None:
        self._external.discard(item_id)
        self._set_state(item_id, STATE_STOPPED)
        self.notice.emit("快捷方式已交给系统启动，启动器无法跟踪或停止它。")

    # ---------------- 停止 ----------------
    def stop(self, item: LaunchItem | str) -> None:
        item_id = item.id if isinstance(item, LaunchItem) else item
        self._user_stopped.add(item_id)
        self._restart_pending.discard(item_id)
        self._restart_counts[item_id] = 0
        self._started_at.pop(item_id, None)

        adopted = self._adopted.pop(item_id, None)
        if adopted is not None:
            pid, created = adopted
            self._set_state(item_id, STATE_STOPPING)
            threading.Thread(
                target=self._stop_adopted_worker,
                args=(item_id, pid, created),
                daemon=True,
            ).start()
            return

        if item_id in self._external:
            self.notice.emit("快捷方式由系统启动，启动器无法停止它。")
            self._external.discard(item_id)
            self._set_state(item_id, STATE_STOPPED)
            return
        with self._lock:
            proc = self._procs.get(item_id)
        if proc is None:
            self._set_state(item_id, STATE_STOPPED)
            return
        if self._states.get(item_id) == STATE_STOPPING:
            return

        self._set_state(item_id, STATE_STOPPING)
        threading.Thread(
            target=self._stop_worker, args=(item_id, proc), daemon=True
        ).start()

    def _close_reader(self, item_id: str) -> None:
        entry = self._readers.pop(item_id, None)
        if entry is not None:
            reader, _bridge = entry
            reader.stop()

    def _stop_adopted_worker(
        self, item_id: str, pid: int, created: float | None = None
    ) -> None:
        _kill_pid_tree(pid, created)
        self._set_state(item_id, STATE_STOPPED)

    def _stop_worker(self, item_id: str, proc: subprocess.Popen) -> None:
        _kill_process_tree(proc)
        self._close_reader(item_id)
        with self._lock:
            self._procs.pop(item_id, None)
        self._set_state(item_id, STATE_STOPPED)

    def stop_all(self, item_ids: list[str]) -> None:
        for item_id in list(item_ids):
            self.stop(item_id)

    def restart_now(self, item: LaunchItem, force_minimized: bool = False) -> bool:
        """手动“重新启动”用：停掉旧进程、等它真正停下后再重新拉起。

        stop() 是异步的（后台线程杀进程树，期间状态是 stopping），
        立刻 start() 会因为 is_active 被拒 —— 所以没停稳之前先排队等。
        """
        if not self.is_active(item.id):
            self._user_stopped.discard(item.id)
            return self.start(item, force_minimized=force_minimized)
        self.stop(item)

        def pull_up_again(tries: int = 80) -> None:
            if self.is_active(item.id):
                if tries > 0:
                    QTimer.singleShot(100, lambda: pull_up_again(tries - 1))
                return
            self._user_stopped.discard(item.id)
            self.start(item, force_minimized=force_minimized)

        pull_up_again()
        return True


    # ---------------- 轮询 ----------------
    def _poll(self) -> None:
        if self._adopted and psutil is not None:
            for item_id, (pid, created) in list(self._adopted.items()):
                try:
                    alive = psutil.pid_exists(pid) and _is_same_process(pid, created)
                except Exception:
                    alive = True
                if not alive:
                    self._adopted.pop(item_id, None)
                    self._set_state(item_id, STATE_STOPPED)
                    item = self._items.get(item_id)
                    if item is not None:
                        self.notice.emit(f"「{item.name}」的接管进程已退出。")
        finished: list[tuple[str, int]] = []
        with self._lock:
            for item_id, proc in list(self._procs.items()):
                code = proc.poll()
                if code is not None:
                    finished.append((item_id, int(code)))
                    self._procs.pop(item_id, None)

        for item_id, code in finished:
            self._close_reader(item_id)
            self._set_state(item_id, STATE_STOPPED)
            self.exited.emit(item_id, code)
            self._handle_exit(item_id, code)

    def _handle_exit(self, item_id: str, code: int) -> None:
        item = self._items.get(item_id)
        user_stopped = item_id in self._user_stopped
        self._user_stopped.discard(item_id)
        started = self._started_at.pop(item_id, None)

        if item is None or not item.auto_restart or user_stopped:
            self._restart_counts[item_id] = 0
            if not user_stopped and item is not None:
                self.notice.emit(f"「{item.name}」已退出（code={code}）。")
            return

        # 稳定运行过一段时间才退出的，算"新的一次意外"而不是连续崩溃：
        # 把计数清零，避免长期服务崩几次后永久失去自动重启。
        if started is not None and (time.monotonic() - started) >= STABLE_UPTIME_SEC:
            self._restart_counts[item_id] = 0

        count = self._restart_counts.get(item_id, 0) + 1
        self._restart_counts[item_id] = count
        if count > AUTO_RESTART_MAX:
            self._restart_counts[item_id] = 0
            self.notice.emit(f"「{item.name}」连续退出 {count} 次，已停止自动重启。")
            self.gave_up.emit(item_id, item.name)
            return

        if item_id in self._restart_pending:
            return
        if self._system_busy:
            self._deferred_restarts.add(item_id)
            self.notice.emit(f"「{item.name}」异常退出，但系统正忙，稍后恢复时再重启。")
            return
        self._restart_pending.add(item_id)
        self.notice.emit(
            f"「{item.name}」异常退出（code={code}），3 秒后自动重启（第 {count} 次）。"
        )
        QTimer.singleShot(
            AUTO_RESTART_DELAY_MS, lambda: self._do_auto_restart(item_id)
        )

    def _do_auto_restart(self, item_id: str) -> None:
        self._restart_pending.discard(item_id)
        item = self._items.get(item_id)
        if item is None or self.is_active(item_id):
            return
        if self.start(item):
            self.notice.emit(f"「{item.name}」已自动重启。")

    # ---------------- 退出 ----------------
    def shutdown(self, timeout_total: float = 6.0) -> None:
        """退出前结束所有由本启动器拉起或接管的进程。

        顺序很重要：**先杀进程，再关日志读取器**。
        反过来的话，读线程还阻塞在 `readline()` 上（子进程没输出时必然如此），
        关管道会和它抢对象锁，直接死锁 —— 表现就是点"全部停止并退出"卡死。

        这里还必须 join：`QApplication.quit()` 之后进程随即退出，daemon 线程
        会被强杀，`terminate()` 之后的 `wait_procs` 与 `kill()` 兜底根本走不完，
        表现为"点了全部停止并退出，接管的进程还在"。
        """
        deadline = time.monotonic() + max(1.0, float(timeout_total))

        with self._lock:
            procs = list(self._procs.values())
            adopted = list(self._adopted.values())

        threads: list[threading.Thread] = []
        for proc in procs:
            thread = threading.Thread(
                target=_kill_process_tree, args=(proc,), daemon=True
            )
            thread.start()
            threads.append(thread)
        for pid, created in adopted:
            thread = threading.Thread(
                target=_kill_pid_tree, args=(pid, created), daemon=True
            )
            thread.start()
            threads.append(thread)

        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                thread.join(timeout=remaining)
            except Exception:
                pass

        # 进程都结束了，管道写端关闭 → 读线程读到 EOF 会自行退出，此时关才安全
        for item_id in list(self._readers.keys()):
            self._close_reader(item_id)

        self._adopted.clear()
        with self._lock:
            self._procs.clear()
