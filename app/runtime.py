from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
from maa.controller import AdbController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit


class RuntimeErrorMessage(RuntimeError):
    pass


class MaaRuntime:
    def __init__(self, app_dir: Path, adb_path: Path, address: str, resource_dir: Path):
        self.app_dir = app_dir
        self.adb_path = adb_path
        self.address = address
        self.resource_dir = resource_dir
        self.controller = None
        self.resource = None
        self.tasker = None
        self.last_screenshot_time = 0.0

    @property
    def connected(self):
        return self.controller is not None and self.controller.connected

    @property
    def ready(self):
        return self.tasker is not None and self.tasker.inited

    def connect(self):
        if not self.adb_path.exists():
            raise RuntimeErrorMessage(f"找不到 adb：{self.adb_path}")
        Toolkit.init_option(self.app_dir / "runtime", {"logging": True, "save_on_error": True, "stdout_level": 2})
        self._ensure_adb_connection()
        device = next((d for d in Toolkit.find_adb_devices(self.adb_path) if d.address == self.address), None)
        if device is None:
            raise RuntimeErrorMessage(f"未找到设备 {self.address}")
        controller = AdbController(
            adb_path=device.adb_path,
            address=device.address,
            screencap_methods=device.screencap_methods,
            input_methods=device.input_methods,
            config=device.config,
        )
        if not controller.post_connection().wait().succeeded:
            raise RuntimeErrorMessage("MaaFramework 连接失败")
        self.controller = controller

    def load(self):
        if not self.controller:
            raise RuntimeErrorMessage("请先连接模拟器")
        resource = Resource()
        if not resource.post_bundle(self.resource_dir).wait().succeeded:
            raise RuntimeErrorMessage("资源加载失败")
        tasker = Tasker()
        if not tasker.bind(resource, self.controller) or not tasker.inited:
            raise RuntimeErrorMessage("Tasker 初始化失败")
        self.resource = resource
        self.tasker = tasker

    def screenshot(self):
        if not self.controller or not self.connected:
            raise RuntimeErrorMessage("模拟器未连接")
        image = self.controller.post_screencap().wait().get()
        if image is None or image.size == 0:
            raise RuntimeErrorMessage("截图失败")
        self.last_screenshot_time = time.time()
        return image

    def health_check(self):
        if not self.connected:
            return False, "ADB 未连接"
        if self.tasker and not self.tasker.inited:
            return False, "Tasker 未初始化"
        return True, "正常"

    def run_task(self, entry: str) -> Any:
        if not self.ready:
            raise RuntimeErrorMessage("资源尚未加载")
        return self.tasker.post_task(entry.strip()).wait().get()

    def stop(self):
        if self.tasker and self.tasker.running:
            self.tasker.post_stop().wait()

    def close(self):
        self.stop()
        self.tasker = None
        self.resource = None
        self.controller = None

    def _ensure_adb_connection(self):
        subprocess.run([str(self.adb_path), "connect", self.address], capture_output=True, text=True, timeout=10)
        time.sleep(0.5)
