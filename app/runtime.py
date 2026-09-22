from __future__ import annotations

import hashlib
import json
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
    """An expected runtime failure that can be shown directly in the UI."""


class DuplicateKeyError(ValueError):
    """Raised when a JSON file contains duplicate object keys."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_strict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeErrorMessage(f"缺少文件：{path}")

    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise RuntimeErrorMessage(f"JSON 不能带 UTF-8 BOM：{path}")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeErrorMessage(f"JSON 不是 UTF-8：{path}：{exc}") from exc

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateKeyError(f"重复 JSON key：{key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, DuplicateKeyError) as exc:
        raise RuntimeErrorMessage(f"JSON 格式错误：{path}：{exc}") from exc

    if not isinstance(value, dict):
        raise RuntimeErrorMessage(f"JSON 根节点必须是对象：{path}")
    return value


def _normalize_node_ref(value: str) -> str:
    """Strip Maa control prefixes such as [JumpBack] before reference validation."""
    name = value.strip()
    while name.startswith("["):
        end = name.find("]")
        if end < 0:
            break
        name = name[end + 1 :].strip()
    return name


class MaaRuntime:
    """Own the MaaFramework objects used by the application layer."""

    def __init__(
        self,
        app_dir: Path,
        adb_path: Path,
        address: str,
        resource_dir: Path,
    ) -> None:
        self.app_dir = app_dir
        self.adb_path = adb_path
        self.address = address
        self.resource_dir = resource_dir
        self.controller: AdbController | None = None
        self.resource: Resource | None = None
        self.tasker: Tasker | None = None
        self.loaded_resource_hashes: dict[str, str] | None = None
        self.last_adb_connect_result = ""

    @property
    def connected(self) -> bool:
        return self.controller is not None and self.controller.connected

    @property
    def ready(self) -> bool:
        return self.tasker is not None and self.tasker.inited

    def connect(self) -> None:
        if not self.adb_path.exists():
            raise RuntimeErrorMessage(f"找不到 adb：{self.adb_path}")
        if not self.address:
            raise RuntimeErrorMessage("请在 config/settings.json 中填写 address")

        Toolkit.init_option(
            self.app_dir / "runtime",
            {"logging": True, "save_on_error": True, "stdout_level": 2},
        )
        self._ensure_adb_connection()
        device = self._find_device()
        if device is None:
            detail = f"；ADB 返回：{self.last_adb_connect_result}" if self.last_adb_connect_result else ""
            raise RuntimeErrorMessage(
                f"未找到设备 {self.address}。请确认 MuMu 实例已启动并开启 ADB{detail}"
            )

        controller = AdbController(
            adb_path=device.adb_path,
            address=device.address,
            screencap_methods=device.screencap_methods,
            input_methods=device.input_methods,
            config=device.config,
        )
        if not controller.post_connection().wait().succeeded:
            raise RuntimeErrorMessage(f"MaaFramework 无法连接到 {device.address}")

        self.controller = controller

    def validate_resource(self, entry: str) -> dict[str, Any]:
        """Validate the external resource bundle before MaaFramework loads/runs it."""
        if not self.resource_dir.is_dir():
            raise RuntimeErrorMessage(f"找不到资源目录：{self.resource_dir}")

        entry = entry.strip()
        if not entry:
            raise RuntimeErrorMessage("任务入口不能为空")

        default_path = self.resource_dir / "default_pipeline.json"
        _load_json_strict(default_path)

        pipeline_dir = self.resource_dir / "pipeline"
        if not pipeline_dir.is_dir():
            raise RuntimeErrorMessage(f"缺少 Pipeline 目录：{pipeline_dir}")

        pipeline_files = sorted(pipeline_dir.rglob("*.json"))
        if not pipeline_files:
            raise RuntimeErrorMessage(f"Pipeline 目录中没有 JSON：{pipeline_dir}")

        nodes: dict[str, dict[str, Any]] = {}
        node_sources: dict[str, Path] = {}
        hashes: dict[str, str] = {
            default_path.relative_to(self.resource_dir).as_posix(): _sha256(default_path)
        }

        for path in pipeline_files:
            data = _load_json_strict(path)
            hashes[path.relative_to(self.resource_dir).as_posix()] = _sha256(path)
            for node_name, node in data.items():
                if node_name in nodes:
                    raise RuntimeErrorMessage(
                        f"Pipeline 节点重复：{node_name!r}\n"
                        f"{node_sources[node_name]}\n{path}"
                    )
                if not isinstance(node, dict):
                    raise RuntimeErrorMessage(f"Pipeline 节点必须是对象：{path} -> {node_name}")
                nodes[node_name] = node
                node_sources[node_name] = path

        if entry not in nodes:
            raise RuntimeErrorMessage(
                f"任务入口 {entry!r} 不存在于已加载 Pipeline。\n"
                f"当前节点：{', '.join(nodes.keys())}"
            )

        image_root = self.resource_dir / "image"
        referenced_images: set[Path] = set()

        for node_name, node in nodes.items():
            for field in ("next", "on_error"):
                refs = node.get(field)
                if refs is None:
                    continue
                if isinstance(refs, str):
                    refs = [refs]
                if not isinstance(refs, list):
                    raise RuntimeErrorMessage(f"{node_name}.{field} 必须是字符串或数组")
                for ref in refs:
                    if not isinstance(ref, str):
                        raise RuntimeErrorMessage(f"{node_name}.{field} 中存在非字符串节点引用")
                    target = _normalize_node_ref(ref)
                    if target and target not in nodes:
                        raise RuntimeErrorMessage(
                            f"Pipeline 引用了不存在的节点：{node_name}.{field} -> {ref!r}"
                        )

            templates = node.get("template")
            if templates is None:
                continue
            if isinstance(templates, str):
                templates = [templates]
            if not isinstance(templates, list):
                raise RuntimeErrorMessage(f"{node_name}.template 必须是字符串或数组")

            for template in templates:
                if not isinstance(template, str):
                    raise RuntimeErrorMessage(f"{node_name}.template 中存在非字符串模板名")
                image_path = image_root / template
                if not image_path.is_file():
                    raise RuntimeErrorMessage(
                        f"缺少模板图片：{template}\n节点：{node_name}\n目录：{image_root}"
                    )
                referenced_images.add(image_path)

        for image_path in sorted(referenced_images):
            hashes[image_path.relative_to(self.resource_dir).as_posix()] = _sha256(image_path)

        return {
            "entry": entry,
            "node_count": len(nodes),
            "pipeline_files": [p.relative_to(self.resource_dir).as_posix() for p in pipeline_files],
            "hashes": hashes,
        }

    def load(self, entry: str) -> dict[str, Any]:
        if self.controller is None:
            raise RuntimeErrorMessage("请先连接模拟器")

        snapshot = self.validate_resource(entry)

        resource = Resource()
        if not resource.post_bundle(self.resource_dir).wait().succeeded:
            raise RuntimeErrorMessage("资源加载失败，请检查 resource/pipeline 中的 JSON")

        tasker = Tasker()
        if not tasker.bind(resource, self.controller) or not tasker.inited:
            raise RuntimeErrorMessage("Tasker 初始化失败")

        self.resource = resource
        self.tasker = tasker
        self.loaded_resource_hashes = dict(snapshot["hashes"])
        return snapshot

    def screenshot(self) -> np.ndarray:
        if self.controller is None or not self.connected:
            raise RuntimeErrorMessage("模拟器尚未连接")
        image = self.controller.post_screencap().wait().get()
        if image is None or image.size == 0:
            raise RuntimeErrorMessage("截图失败，未返回图像")
        return image

    def run_task(self, entry: str) -> Any:
        if self.tasker is None or not self.ready:
            raise RuntimeErrorMessage("资源尚未加载")
        entry = entry.strip()
        if not entry:
            raise RuntimeErrorMessage("请填写 Pipeline 入口名称")
        if self.tasker.running:
            raise RuntimeErrorMessage("已有任务正在运行")

        current = self.validate_resource(entry)
        if self.loaded_resource_hashes is None:
            raise RuntimeErrorMessage("资源尚未完成一致性校验，请重新加载资源")
        if current["hashes"] != self.loaded_resource_hashes:
            raise RuntimeErrorMessage(
                "资源文件在“加载资源”之后发生了变化。\n"
                "为避免运行旧的内存 Pipeline，请先重新点击“加载资源”。"
            )

        return self.tasker.post_task(entry).wait().get()

    def stop(self) -> None:
        if self.tasker is not None and self.tasker.running:
            self.tasker.post_stop().wait()

    def close(self) -> None:
        self.stop()
        self.tasker = None
        self.resource = None
        self.controller = None
        self.loaded_resource_hashes = None

    def _ensure_adb_connection(self) -> None:
        result = subprocess.run(
            [str(self.adb_path), "connect", self.address],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        self.last_adb_connect_result = (
            f"returncode={result.returncode}, stdout={stdout!r}, stderr={stderr!r}"
        )

        # Some ADB builds use a non-zero code even when a later device query succeeds.
        # The authoritative check is _find_device(), but clearly reject unexpected codes.
        if result.returncode not in (0, 1):
            raise RuntimeErrorMessage(f"ADB 连接命令失败：{self.last_adb_connect_result}")
        time.sleep(0.3)

    def _find_device(self):
        devices = Toolkit.find_adb_devices(self.adb_path)
        for device in devices:
            if device.address == self.address:
                return device
        return None
