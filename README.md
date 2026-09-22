# MaaAutomationBlank

这是一个独立的 MaaFramework 应用层模板。它只读取 MuMu 和 MAA 参考目录，不会修改它们。

## 运行

1. 启动 MuMu 的目标实例，并确认 `config/settings.json` 中的 `address` 与实例一致。
2. 运行 `MaaAutomation.exe`。
3. 点击“连接模拟器”确认可以看到设备。
4. 点击“截图”查看画面，截图会保存到 `captures/latest.png`。
5. 把自己的 Pipeline 节点写入 `resource/pipeline/`，把模板图片放到 `resource/image/`，然后填写任务入口并点击“加载资源”“执行任务”。

当前 JSON 和图片是空模板，不包含任何目标软件流程。

## 资源目录

```text
resource/
├── default_pipeline.json
├── image/                  # 你放置 PNG 模板图片的位置
└── pipeline/
    └── main.json           # 你填写节点和 next/on_error 的位置
```

模板图片路径要相对于 `resource/image/`，例如：

```json
{
  "Start": {
    "recognition": "TemplateMatch",
    "template": "buttons/start.png",
    "action": "Click",
    "next": ["Next"]
  }
}
```

建议先用仓库中的 `tools/ImageCropper` 从模拟器截图裁剪小区域，再把图片放入资源目录。OCR 需要另行准备 `resource/model/ocr/` 模型文件；本模板不会复制 MAA 的游戏资源。

## 配置

只需要修改 `config/settings.json`：

- `adb_path`：MuMu 自带的 `shell/adb.exe` 路径。
- `address`：MuMu 实例 ADB 地址。当前机器已验证实例 #1 为 `127.0.0.1:16416`。
- `resource`：资源包目录。
- `entry`：Pipeline 的入口节点名称。

程序还提供 `--probe` 参数，用于只连接并截图一次：

```text
MaaAutomation.exe --probe
```

结果保存为 `captures/probe.png`。
