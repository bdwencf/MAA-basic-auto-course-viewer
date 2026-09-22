from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import (
    BOTH,
    DISABLED,
    END,
    LEFT,
    NORMAL,
    X,
    Button,
    Entry,
    Frame,
    Label,
    StringVar,
    Tk,
    messagebox,
)
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk

try:
    from app.runtime import MaaRuntime, RuntimeErrorMessage
except ModuleNotFoundError:  # Running as ``python app/main.py`` from the template root.
    from runtime import MaaRuntime, RuntimeErrorMessage


STATE_DISCONNECTED = "DISCONNECTED"
STATE_CONNECTING = "CONNECTING"
STATE_CONNECTED = "CONNECTED"
STATE_LOADING = "LOADING"
STATE_READY = "READY"
STATE_CAPTURING = "CAPTURING"
STATE_RUNNING = "RUNNING"
STATE_STOPPING = "STOPPING"

_SINGLE_INSTANCE_HANDLE = None


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def load_settings(app_dir: Path) -> dict:
    path = app_dir / "config" / "settings.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeErrorMessage(f"settings.json 格式错误：{exc}") from exc


def resolve_path(app_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else app_dir / path


def acquire_single_instance() -> bool:
    """Prevent two GUI instances from controlling the same external resource/ADB state."""
    global _SINGLE_INSTANCE_HANDLE

    if os.name != "nt":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(
        None,
        False,
        r"Local\MaaAutomationBlank.SingleInstance",
    )
    if not handle:
        return True

    ERROR_ALREADY_EXISTS = 183
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                "MaaAutomation 已经在运行。\n请关闭已有窗口后再启动。",
                "Maa 自动化模板",
                0x30,
            )
        except Exception:
            pass
        return False

    _SINGLE_INSTANCE_HANDLE = handle
    return True


def release_single_instance() -> None:
    global _SINGLE_INSTANCE_HANDLE
    if os.name == "nt" and _SINGLE_INSTANCE_HANDLE:
        try:
            ctypes.windll.kernel32.CloseHandle(_SINGLE_INSTANCE_HANDLE)
        finally:
            _SINGLE_INSTANCE_HANDLE = None


def report_startup_error(app_dir: Path, exc: Exception) -> None:
    runtime_dir = app_dir / "runtime"
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "startup_error.log").write_text(
            f"{datetime.now().isoformat()}\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except Exception:
        pass

    text = f"启动失败：{exc}"
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, "Maa 自动化模板", 0x10)
            return
        except Exception:
            pass

    if sys.stderr is not None:
        print(text, file=sys.stderr)


class AutomationWindow:
    def __init__(self, root: Tk, app_dir: Path, settings: dict) -> None:
        self.root = root
        self.app_dir = app_dir
        self.settings = settings
        self.runtime: MaaRuntime | None = None
        self.preview_image: ImageTk.PhotoImage | None = None

        self.state = STATE_DISCONNECTED
        self._closing = False
        self._stop_requested = False
        self._heartbeat_job: str | None = None
        self._task_started_at = 0.0

        self.adb_var = StringVar(value=str(resolve_path(app_dir, settings.get("adb_path", ""))))
        self.address_var = StringVar(value=settings.get("address", "127.0.0.1:16416"))
        self.entry_var = StringVar(value=settings.get("entry", ""))
        self.resource_var = StringVar(value=str(resolve_path(app_dir, settings.get("resource", "resource"))))
        self.status_var = StringVar(value="未连接")

        root.title("Maa 自动化模板")
        root.geometry("980x760")
        root.minsize(760, 560)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self._apply_state(STATE_DISCONNECTED, "未连接")
        self._log("程序已启动；请按“连接模拟器 → 加载资源 → 执行任务”的顺序操作")

    def _build_ui(self) -> None:
        control = Frame(self.root, padx=10, pady=10)
        control.pack(fill=X)
        self._row(control, 0, "ADB 路径", self.adb_var)
        self._row(control, 1, "设备地址", self.address_var)
        self._row(control, 2, "资源目录", self.resource_var)
        self._row(control, 3, "任务入口", self.entry_var)

        buttons = Frame(control, pady=8)
        buttons.grid(row=4, column=0, columnspan=3, sticky="w")

        self.connect_button = Button(buttons, text="连接模拟器", width=14, command=self.connect)
        self.connect_button.pack(side=LEFT, padx=(0, 6))

        self.load_button = Button(buttons, text="加载资源", width=14, command=self.load_resource)
        self.load_button.pack(side=LEFT, padx=6)

        self.capture_button = Button(buttons, text="截图", width=10, command=self.capture)
        self.capture_button.pack(side=LEFT, padx=6)

        self.run_button = Button(buttons, text="执行任务", width=14, command=self.run_task)
        self.run_button.pack(side=LEFT, padx=6)

        self.stop_button = Button(buttons, text="停止", width=10, command=self.stop)
        self.stop_button.pack(side=LEFT, padx=6)

        Label(control, textvariable=self.status_var, anchor="w", fg="#155724").grid(
            row=5, column=0, columnspan=3, sticky="we", pady=(2, 0)
        )

        view = Frame(self.root, padx=10, pady=10)
        view.pack(fill=BOTH, expand=True)
        self.preview = Label(
            view,
            text="连接后点击“截图”查看 MuMu 画面",
            anchor="center",
            bg="#202124",
            fg="white",
        )
        self.preview.pack(fill=BOTH, expand=True)

        log_frame = Frame(self.root, padx=10, pady=10)
        log_frame.pack(fill=X)
        self.log = ttk.Treeview(log_frame, columns=("message",), show="headings", height=6)
        self.log.heading("message", text="运行记录")
        self.log.column("message", stretch=True, width=800)
        self.log.pack(fill=X)

    def _row(self, parent: Frame, row: int, label: str, variable: StringVar) -> None:
        Label(parent, text=label, width=10, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
        Entry(parent, textvariable=variable, width=92).grid(
            row=row, column=1, columnspan=2, sticky="we", pady=3
        )

    def _apply_state(self, state: str, status: str | None = None) -> None:
        self.state = state
        if status is not None:
            self.status_var.set(status)

        enabled = {
            STATE_DISCONNECTED: {"connect"},
            STATE_CONNECTED: {"connect", "load", "capture"},
            STATE_READY: {"connect", "load", "capture", "run"},
            STATE_RUNNING: {"stop"},
        }.get(state, set())

        self.connect_button.configure(state=NORMAL if "connect" in enabled else DISABLED)
        self.load_button.configure(state=NORMAL if "load" in enabled else DISABLED)
        self.capture_button.configure(state=NORMAL if "capture" in enabled else DISABLED)
        self.run_button.configure(state=NORMAL if "run" in enabled else DISABLED)
        self.stop_button.configure(state=NORMAL if "stop" in enabled else DISABLED)

    def _start_operation(
        self,
        *,
        allowed_states: set[str],
        busy_state: str,
        busy_status: str,
        func,
        on_success,
    ) -> None:
        if self.state not in allowed_states:
            self._log(f"当前状态不允许此操作：{self.state}")
            return

        previous_state = self.state
        self._apply_state(busy_state, busy_status)

        def worker() -> None:
            try:
                result = func()
            except Exception as exc:
                self.root.after(
                    0,
                    lambda e=exc, prev=previous_state: self._finish_operation_error(e, prev),
                )
            else:
                self.root.after(0, lambda r=result: on_success(r))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_operation_error(self, exc: Exception, previous_state: str) -> None:
        if self._closing:
            return
        self._apply_state(previous_state)
        self._handle_error(exc)

    def connect(self) -> None:
        self._start_operation(
            allowed_states={STATE_DISCONNECTED, STATE_CONNECTED, STATE_READY},
            busy_state=STATE_CONNECTING,
            busy_status="正在连接模拟器...",
            func=self._connect,
            on_success=self._connect_succeeded,
        )

    def _connect(self) -> MaaRuntime:
        runtime = MaaRuntime(
            self.app_dir,
            resolve_path(self.app_dir, self.adb_var.get().strip()),
            self.address_var.get().strip(),
            resolve_path(self.app_dir, self.resource_var.get().strip()),
        )
        runtime.connect()

        old_runtime = self.runtime
        self.runtime = runtime
        if old_runtime is not None:
            old_runtime.close()
        return runtime

    def _connect_succeeded(self, runtime: MaaRuntime) -> None:
        if self._closing:
            return
        self._apply_state(STATE_CONNECTED, f"已连接：{self.address_var.get().strip()}")
        self._log("模拟器连接成功")
        if runtime.last_adb_connect_result:
            self._log(f"ADB：{runtime.last_adb_connect_result}")

    def load_resource(self) -> None:
        self._start_operation(
            allowed_states={STATE_CONNECTED, STATE_READY},
            busy_state=STATE_LOADING,
            busy_status="正在校验并加载 Pipeline 资源...",
            func=self._load_resource,
            on_success=self._load_resource_succeeded,
        )

    def _load_resource(self) -> dict:
        if self.runtime is None:
            raise RuntimeErrorMessage("请先连接模拟器")
        self.runtime.resource_dir = resolve_path(self.app_dir, self.resource_var.get().strip())
        return self.runtime.load(self.entry_var.get().strip())

    def _load_resource_succeeded(self, snapshot: dict) -> None:
        if self._closing:
            return
        self._apply_state(STATE_READY, "资源已加载，可以执行任务")
        self._log(
            f"Pipeline 资源加载成功：entry={snapshot['entry']}，节点数={snapshot['node_count']}"
        )
        self._log(f"资源目录：{self.resource_var.get().strip()}")

    def capture(self) -> None:
        self._start_operation(
            allowed_states={STATE_CONNECTED, STATE_READY},
            busy_state=STATE_CAPTURING,
            busy_status="正在截图...",
            func=self._capture,
            on_success=self._capture_succeeded,
        )

    def _capture(self) -> tuple[np.ndarray, Path, bool]:
        if self.runtime is None:
            raise RuntimeErrorMessage("请先连接模拟器")
        was_ready = self.runtime.ready
        image = self.runtime.screenshot()
        capture_dir = self.app_dir / "captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        path = capture_dir / "latest.png"
        self._save_bgr(image, path)
        return image, path, was_ready

    def _capture_succeeded(self, result: tuple[np.ndarray, Path, bool]) -> None:
        image, path, was_ready = result
        self._show_bgr(image)
        self._apply_state(
            STATE_READY if was_ready else STATE_CONNECTED,
            f"截图已保存：{path}",
        )
        self._log(f"截图完成：{path.name}")

    def run_task(self) -> None:
        if self.state != STATE_READY:
            self._log(f"当前状态不能执行任务：{self.state}")
            return
        if self.runtime is None:
            self._handle_error(RuntimeErrorMessage("请先连接模拟器"))
            return

        entry = self.entry_var.get().strip()
        if not entry:
            self._handle_error(RuntimeErrorMessage("请填写 Pipeline 入口名称"))
            return

        self._stop_requested = False
        self._task_started_at = time.monotonic()
        self._apply_state(STATE_RUNNING, f"任务运行中：{entry}")
        self._log(f"任务已启动：{entry}")
        self._log("弹窗守卫持续运行中；只有点击“停止”才应结束")
        self._start_heartbeat()

        def worker() -> None:
            try:
                detail = self.runtime.run_task(entry)
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._finish_task(error=e))
            else:
                self.root.after(0, lambda d=detail: self._finish_task(detail=d))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_task(self, detail=None, error: Exception | None = None) -> None:
        if self._closing:
            return

        self._stop_heartbeat()

        if error is not None:
            if self._stop_requested:
                self._apply_state(STATE_READY, "任务已停止")
                self._log(f"任务已停止：{error}")
            else:
                self._apply_state(STATE_READY, f"任务失败：{error}")
                self._log(f"任务异常结束：{error}")
                messagebox.showerror("Maa 自动化模板", str(error), parent=self.root)
            self._stop_requested = False
            return

        status = getattr(detail, "status", "完成")
        if self._stop_requested:
            self._apply_state(STATE_READY, "任务已停止")
            self._log("任务已停止（用户主动停止）")
        else:
            self._apply_state(STATE_READY, f"任务自行结束：{status}")
            self._log(
                f"任务自行结束：{status}。弹窗守卫本应持续运行，请查看 runtime/debug/maafw.log"
            )
        self._stop_requested = False

    def stop(self) -> None:
        if self.state != STATE_RUNNING or self.runtime is None:
            self._log("当前没有正在运行的任务")
            return

        self._stop_requested = True
        self._stop_heartbeat()
        self._apply_state(STATE_STOPPING, "正在停止任务...")
        self._log("正在请求停止任务...")

        def worker() -> None:
            try:
                self.runtime.stop()
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._stop_request_failed(e))
            else:
                self.root.after(0, self._stop_request_succeeded)

        threading.Thread(target=worker, daemon=True).start()

    def _stop_request_succeeded(self) -> None:
        if self._closing:
            return
        # Usually the run-task worker returns immediately after post_stop().
        if self.state == STATE_STOPPING:
            self.status_var.set("停止请求已发送，等待任务结束...")
            self._log("停止请求已发送")

    def _stop_request_failed(self, exc: Exception) -> None:
        if self._closing:
            return
        self._stop_requested = False
        self._apply_state(STATE_RUNNING, f"停止失败：{exc}")
        self._log(f"停止失败：{exc}")
        self._start_heartbeat()
        messagebox.showerror("Maa 自动化模板", str(exc), parent=self.root)

    def _start_heartbeat(self) -> None:
        self._stop_heartbeat()
        self._heartbeat_job = self.root.after(10000, self._heartbeat_tick)

    def _heartbeat_tick(self) -> None:
        self._heartbeat_job = None
        if self.state != STATE_RUNNING or self._closing:
            return
        elapsed = max(0, int(time.monotonic() - self._task_started_at))
        self._log(f"弹窗守卫运行中... 已运行 {elapsed} 秒")
        self._heartbeat_job = self.root.after(10000, self._heartbeat_tick)

    def _stop_heartbeat(self) -> None:
        if self._heartbeat_job is not None:
            try:
                self.root.after_cancel(self._heartbeat_job)
            except Exception:
                pass
            self._heartbeat_job = None

    def _handle_error(self, exc: Exception) -> None:
        self.status_var.set(f"失败：{exc}")
        self._log(f"失败：{exc}")
        messagebox.showerror("Maa 自动化模板", str(exc), parent=self.root)

    def _log(self, text: str) -> None:
        if self._closing:
            return

        def append() -> None:
            if self._closing:
                return
            stamp = datetime.now().strftime("%H:%M:%S")
            item = self.log.insert("", END, values=(f"[{stamp}] {text}",))
            self.log.see(item)

        self.root.after(0, append)

    def _save_bgr(self, image: np.ndarray, path: Path) -> None:
        Image.fromarray(image[:, :, ::-1]).save(path)

    def _show_bgr(self, image: np.ndarray) -> None:
        def update() -> None:
            if self._closing:
                return
            pil = Image.fromarray(image[:, :, ::-1])
            pil.thumbnail((920, 560), Image.Resampling.LANCZOS)
            self.preview_image = ImageTk.PhotoImage(pil)
            self.preview.configure(image=self.preview_image, text="")

        self.root.after(0, update)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._stop_heartbeat()
        try:
            if self.runtime is not None:
                self.runtime.close()
        except Exception:
            pass
        self.root.destroy()


def run_probe(app_dir: Path, settings: dict) -> int:
    """Connect, validate/load resources, capture one frame, and write a JSON result file."""
    runtime = MaaRuntime(
        app_dir,
        resolve_path(app_dir, settings.get("adb_path", "")),
        settings.get("address", "127.0.0.1:16416"),
        resolve_path(app_dir, settings.get("resource", "resource")),
    )
    result_path = app_dir / "runtime" / "probe-result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        runtime.connect()
        snapshot = runtime.load(settings.get("entry", ""))
        image = runtime.screenshot()

        captures = app_dir / "captures"
        captures.mkdir(parents=True, exist_ok=True)
        path = captures / "probe.png"
        Image.fromarray(image[:, :, ::-1]).save(path)

        result = {
            "connected": True,
            "resource_loaded": True,
            "entry": snapshot["entry"],
            "node_count": snapshot["node_count"],
            "resolution": [int(image.shape[1]), int(image.shape[0])],
            "screenshot": str(path),
        }
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception as exc:
        result_path.write_text(
            json.dumps(
                {"connected": False, "error": str(exc), "traceback": traceback.format_exc()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return 1
    finally:
        runtime.close()


def main() -> int:
    app_dir = application_dir()

    if not acquire_single_instance():
        return 2

    try:
        parser = argparse.ArgumentParser(description="MaaFramework automation template")
        parser.add_argument("--probe", action="store_true", help="run deterministic preflight + screenshot, then exit")
        args, unknown = parser.parse_known_args()
        if unknown:
            raise RuntimeErrorMessage(f"未知启动参数：{' '.join(unknown)}")

        settings = load_settings(app_dir)
        if args.probe:
            return run_probe(app_dir, settings)

        root = Tk()
        AutomationWindow(root, app_dir, settings)
        root.mainloop()
        return 0
    except Exception as exc:
        report_startup_error(app_dir, exc)
        return 1
    finally:
        release_single_instance()


if __name__ == "__main__":
    raise SystemExit(main())
