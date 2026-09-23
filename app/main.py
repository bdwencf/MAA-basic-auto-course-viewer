from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from tkinter import END, LEFT, BOTH, X, Y, Button, Entry, Frame, Label, StringVar, Tk, messagebox
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk

try:
    from app.runtime import MaaRuntime, RuntimeErrorMessage
except ModuleNotFoundError:  # Running as ``python app/main.py`` from the template root.
    from runtime import MaaRuntime, RuntimeErrorMessage


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


class AutomationWindow:
    def __init__(self, root: Tk, app_dir: Path, settings: dict) -> None:
        self.root = root
        self.app_dir = app_dir
        self.settings = settings
        self.runtime: MaaRuntime | None = None
        self.preview_job: str | None = None
        self.preview_image: ImageTk.PhotoImage | None = None

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

    def _build_ui(self) -> None:
        control = Frame(self.root, padx=10, pady=10)
        control.pack(fill=X)
        self._row(control, 0, "ADB 路径", self.adb_var)
        self._row(control, 1, "设备地址", self.address_var)
        self._row(control, 2, "资源目录", self.resource_var)
        self._row(control, 3, "任务入口", self.entry_var)

        buttons = Frame(control, pady=8)
        buttons.grid(row=4, column=0, columnspan=3, sticky="w")
        Button(buttons, text="连接模拟器", width=14, command=self.connect).pack(side=LEFT, padx=(0, 6))
        Button(buttons, text="加载资源", width=14, command=self.load_resource).pack(side=LEFT, padx=6)
        Button(buttons, text="截图", width=10, command=self.capture).pack(side=LEFT, padx=6)
        Button(buttons, text="执行任务", width=14, command=self.run_task).pack(side=LEFT, padx=6)
        Button(buttons, text="停止", width=10, command=self.stop).pack(side=LEFT, padx=6)

        Label(control, textvariable=self.status_var, anchor="w", fg="#155724").grid(
            row=5, column=0, columnspan=3, sticky="we", pady=(2, 0)
        )

        view = Frame(self.root, padx=10, pady=10)
        view.pack(fill=BOTH, expand=True)
        self.preview = Label(view, text="连接后点击“截图”查看 MuMu 画面", anchor="center", bg="#202124", fg="white")
        self.preview.pack(fill=BOTH, expand=True, pady=(0, 10))

        log_frame = Frame(self.root, padx=10, pady=10, height=180)
        log_frame.pack(fill=X)
        self.log = ttk.Treeview(log_frame, columns=("message",), show="headings", height=5)
        self.log.heading("message", text="运行记录")
        self.log.column("message", stretch=True, width=800)
        self.log.pack(fill=X, expand=False)

    def _row(self, parent: Frame, row: int, label: str, variable: StringVar) -> None:
        Label(parent, text=label, width=10, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
        Entry(parent, textvariable=variable, width=92).grid(row=row, column=1, columnspan=2, sticky="we", pady=3)

    def connect(self) -> None:
        self._run_async(self._connect)

    def _connect(self) -> None:
        if self.runtime is not None:
            self.runtime.close()
        self.runtime = MaaRuntime(
            self.app_dir,
            resolve_path(self.app_dir, self.adb_var.get().strip()),
            self.address_var.get().strip(),
            resolve_path(self.app_dir, self.resource_var.get().strip()),
        )
        self.runtime.connect()
        self._set_status(f"已连接：{self.address_var.get().strip()}")
        self._log("模拟器连接成功")

    def load_resource(self) -> None:
        self._run_async(self._load_resource)

    def _load_resource(self) -> None:
        if self.runtime is None:
            raise RuntimeErrorMessage("请先连接模拟器")
        self.runtime.resource_dir = resolve_path(self.app_dir, self.resource_var.get().strip())
        self.runtime.load()
        self._set_status("资源已加载，可以执行任务")
        self._log("Pipeline 资源加载成功")

    def capture(self) -> None:
        self._run_async(self._capture)

    def _capture(self) -> None:
        if self.runtime is None:
            raise RuntimeErrorMessage("请先连接模拟器")
        image = self.runtime.screenshot()
        capture_dir = self.app_dir / "captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        path = capture_dir / "latest.png"
        self._save_bgr(image, path)
        self._show_bgr(image)
        self._set_status(f"截图已保存：{path}")
        self._log(f"截图完成：{path.name}")

    def run_task(self) -> None:
        self._run_async(self._run_task)

    def _run_task(self) -> None:
        if self.runtime is None:
            raise RuntimeErrorMessage("请先连接模拟器")
        detail = self.runtime.run_task(self.entry_var.get())
        status = getattr(detail, "status", "完成")
        self._set_status(f"任务执行结束：{status}")
        self._log(f"任务 {self.entry_var.get().strip()} 执行结束")

    def stop(self) -> None:
        if self.runtime is None:
            return
        self.runtime.stop()
        self._set_status("已请求停止任务")
        self._log("已请求停止")

    def _run_async(self, func) -> None:
        def worker() -> None:
            try:
                func()
            except Exception as exc:  # UI boundary: show all runtime failures to the user.
                self.root.after(0, lambda: self._handle_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_error(self, exc: Exception) -> None:
        self._set_status(f"失败：{exc}")
        self._log(f"失败：{exc}")
        messagebox.showerror("Maa 自动化模板", str(exc), parent=self.root)

    def _set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(text))

    def _log(self, text: str) -> None:
        self.root.after(0, lambda: self.log.insert("", END, values=(text,)))

    def _save_bgr(self, image: np.ndarray, path: Path) -> None:
        Image.fromarray(image[:, :, ::-1]).save(path)

    def _show_bgr(self, image: np.ndarray) -> None:
        def update() -> None:
            pil = Image.fromarray(image[:, :, ::-1])
            pil.thumbnail((920, 560), Image.Resampling.LANCZOS)
            self.preview_image = ImageTk.PhotoImage(pil)
            self.preview.configure(image=self.preview_image, text="")

        self.root.after(0, update)

    def close(self) -> None:
        if self.preview_job is not None:
            self.root.after_cancel(self.preview_job)
        if self.runtime is not None:
            self.runtime.close()
        self.root.destroy()


def run_probe(app_dir: Path, settings: dict) -> int:
    """Connect, capture one frame, and write a machine-readable result."""
    runtime = MaaRuntime(
        app_dir,
        resolve_path(app_dir, settings.get("adb_path", "")),
        settings.get("address", "127.0.0.1:16416"),
        resolve_path(app_dir, settings.get("resource", "resource")),
    )
    try:
        runtime.connect()
        image = runtime.screenshot()
        captures = app_dir / "captures"
        captures.mkdir(parents=True, exist_ok=True)
        path = captures / "probe.png"
        Image.fromarray(image[:, :, ::-1]).save(path)
        result = {"connected": True, "resolution": [int(image.shape[1]), int(image.shape[0])], "screenshot": str(path)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"connected": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="MaaFramework automation template")
    parser.add_argument("--probe", action="store_true", help="connect and save one screenshot, then exit")
    args = parser.parse_args()
    app_dir = application_dir()
    try:
        settings = load_settings(app_dir)
        if args.probe:
            return run_probe(app_dir, settings)
        root = Tk()
        AutomationWindow(root, app_dir, settings)
        root.mainloop()
        return 0
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
