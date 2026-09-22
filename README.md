# MAA Basic Auto Course Viewer

一个基于 **MaaFramework 应用层框架设计** 的自动化课程查看工具。

本项目参考并采用了 [MaaXYZ/MaaFramework](https://github.com/MaaXYZ/MaaFramework) 项目的整体架构思路，特别参考其自动化框架设计、Pipeline 工作流思想以及资源组织方式。

> 本项目不是 MaaFramework 官方项目，也不包含 MaaFramework 的完整运行资源。
> 本项目作为独立应用层实现，仅参考其框架结构进行二次开发。

---

## 项目简介

MAA Basic Auto Course Viewer 是一个基于模拟器连接与 Pipeline 自动化流程的课程查看辅助工具。

项目通过：

- ADB 连接模拟器
- 屏幕截图获取
- Pipeline 节点定义
- 模板图片识别
- 自动执行任务流程

实现可配置化的自动化操作。

---

## 架构说明

本项目采用类似 MaaFramework 的模块化设计：

```text
MAA Basic Auto Course Viewer
│
├── Application Layer
│   └── MaaAutomation.exe
│
├── Configuration
│   └── config/settings.json
│
├── Resource Layer
│   ├── Pipeline
│   └── Image Templates
│
└── Runtime
    ├── Emulator Connection
    └── Task Execution
```

核心设计参考：

- MaaFramework Pipeline 工作流思想
- 节点化任务描述
- 图片模板识别流程
- 配置驱动执行方式

---

# 使用方法

## 1. 准备模拟器

启动 MuMu 模拟器目标实例。

确认：

```text
config/settings.json
```

中的 `address` 与当前模拟器 ADB 地址一致。

---

## 2. 启动程序

运行：

```text
MaaAutomation.exe
```

操作流程：

1. 点击「连接模拟器」
2. 确认设备连接成功
3. 点击「截图」
4. 查看生成：

```text
captures/latest.png
```

---

## 3. 配置自动化任务

将 Pipeline 节点放入：

```text
resource/pipeline/
```

模板图片放入：

```text
resource/image/
```

然后：

1. 加载资源
2. 选择任务入口
3. 执行 Pipeline 流程

---

# 资源结构

```text
resource/
├── default_pipeline.json
├── image/
└── pipeline/
    └── main.json
```

---

# Pipeline 示例

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

---

# 图片资源制作

推荐流程：

1. 使用模拟器截图
2. 使用 `tools/ImageCropper` 裁剪目标区域
3. 保存 PNG 图片
4. 放入 `resource/image/`

---

# OCR 支持

OCR 模型需要自行准备：

```text
resource/model/ocr/
```

本项目不会复制 MaaFramework 或其他项目中的资源文件。

---

# 配置文件

主要配置：

```text
config/settings.json
```

参数：

| 参数 | 说明 |
|-|-|
| adb_path | MuMu 自带 adb.exe 路径 |
| address | 模拟器 ADB 地址 |
| resource | 资源目录 |
| entry | Pipeline 入口节点 |

---

# 调试模式

支持：

```bash
MaaAutomation.exe --probe
```

结果保存：

```text
captures/probe.png
```

---

# 致谢

特别感谢：

[MaaXYZ/MaaFramework](https://github.com/MaaXYZ/MaaFramework)

本项目参考其：

- 自动化框架设计
- Pipeline 流程思想
- 资源组织方式

进行应用层开发。

---

# License

本项目仅用于学习与研究用途。
