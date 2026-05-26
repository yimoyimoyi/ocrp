<div align="center">

# ORCP

**跨平台视频/图片 OCR 字幕提取工具**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-yellow.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey.svg)]()

PaddleOCR · OpenAI Vision · Ollama · LlamaCpp · WhisperX · LLM 纠错 · 分句

</div>

---

🌐 [English](docs/README_EN.md) | [日本語](docs/README_JA.md)

ORCP 是一款功能完整的桌面端字幕提取工具，支持从视频、音频、图片中提取文字内容。集成多引擎 OCR、离线语音识别、LLM 纠错与分句，提供直观的 PyQt5 图形界面。

## 功能特性

### 核心能力

| 功能 | 说明 |
|------|------|
| **多引擎 OCR** | PaddleOCR（本地 GPU/CPU）、OpenAI Vision、Ollama Vision、LlamaCpp |
| **语音识别 (ASR)** | 基于 faster-whisper 的离线 ASR，子进程隔离 CUDA 环境 |
| **AI 纠错 + 翻译** | 支持 OpenAI 兼容 API 的文本校对与翻译，流式输出 + JSON 模式 |
| **LLM 分句** | CoT（思维链）语义合并碎片文本为完整字幕，原文对齐校验 |
| **校对模式** | 纠错/翻译后二次 LLM 检查，修正语法和术语问题 |
| **流式字幕** | 哨兵检测（字数骤降判断台词结束）+ 相似度去重 + 缓冲区 |
| **批量处理** | 多文件队列，自动导出到 `output/` 目录 |

### LLM 调用

| 特性 | 说明 |
|------|------|
| **统一网关** | 所有 LLM 调用经 `ask_llm()` 统一入口，指数退避重试 + 响应缓存 |
| **RPM 速率限制** | 滑动窗口限速，防止 API 限流 |
| **多预设支持** | 可配置多套 API 连接（OpenAI / DeepSeek / Ollama / 火山引擎 等），一键切换 |
| **连接测试** | 一键验证 API 连通性和凭据有效性 |
| **提示词模板** | 可视化编辑器，支持 `{原始结果}` `{上下文}` `{环境信息}` 等占位符 |
| **模板/自定义切换** | 一键切换模板覆盖模式与自定义参考模式 |

### 界面功能

- 视频/图片预览，拖拽绘制 ROI 区域
- 多区域独立配置引擎和提示词
- 结果表格：可视化编辑、搜索替换、过滤、排序
- 搜索栏与结果表格风格统一
- 暗色/亮色主题一键切换
- 可折叠设置面板（解码参数 / VAD / 区域排序）
- 流程模式切换时自动预热对应引擎

### 支持格式

| 类型 | 格式 |
|------|------|
| **视频输入** | MP4, MKV, AVI, MOV, WebM |
| **音频输入** | MP3, WAV, FLAC, OGG |
| **图片输入** | PNG, JPG, BMP |
| **字幕输出** | SRT, TXT, JSON, CSV |

## 快速开始

### 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) 包管理器（推荐）
- FFmpeg（安装脚本自动处理）
- GPU 模式：NVIDIA 驱动 + CUDA Toolkit

### 安装

**Windows：**
```batch
git clone https://github.com/yimoyimoyi/orcp.git
cd orcp
setup.bat              # 自动检测 GPU、安装依赖
```

**Linux：**
```bash
git clone https://github.com/yimoyimoyi/orcp.git
cd orcp
bash setup.sh          # 自动检测 GPU、安装系统依赖
```

安装脚本支持参数：
| 参数 | 说明 |
|------|------|
| `--cpu` | 强制纯 CPU 模式 |
| `--gpu` | 强制 GPU 模式 |
| `--no-ffmpeg` | 跳过 FFmpeg 安装 |

### 启动

```bash
# 方式一：跨平台通用
uv run python ocr_gui.py

# 方式二：启动脚本
orcp_gui.bat           # Windows
bash orcp_gui.sh       # Linux
```

### GPU 语音识别加速（cuDNN 8）

> 仅在使用 GPU 进行语音识别时需要。OCR 和 CPU ASR 不受影响。

ctranslate2 依赖 cuDNN 8 的 `cudnn_ops_infer64_8.dll`（Windows）/ `libcudnn_ops_infer.so.8`（Linux）。若缺失，ASR 自动回退 CPU 模式。

1. 访问 https://developer.nvidia.com/cudnn （需注册免费账号）
2. 下载 **cuDNN 8.9 for CUDA 12.x**
3. 将 DLL 文件放入 `models/asr/lib/`：
   - **Windows**: `cudnn_ops_infer64_8.dll`, `cudnn_cnn_infer64_8.dll`, `cudnn64_8.dll`
   - **Linux**: `libcudnn_ops_infer.so.8`, `libcudnn_cnn_infer.so.8`, `libcudnn.so.8`

> 启动后日志显示 `cuDNN 8 found (lib)` 即表示 GPU ASR 已就绪。

### 首次使用

1. 点击 **📂 打开视频/图片** 或拖拽文件到预览区
2. 在预览图上拖拽鼠标绘制 OCR 区域
3. 菜单栏 **引擎(&E)** 选择引擎（默认 PaddleOCR）
4. 点击 **▶ 开始处理**

## 项目结构

```
orcp/
├── ocr_gui.py                  # 应用入口
├── pyproject.toml              # 项目配置、依赖
├── setup.bat / setup.sh        # 安装脚本
├── orcp_gui.bat / orcp_gui.sh  # 启动脚本
├── diagnose.bat / diagnose.sh  # 诊断脚本
│
├── config/                     # 配置文件 (JSON)
├── docs/                       # 文档（CHANGELOG, CONTRIBUTING 等）
│
├── core/                       # 核心业务逻辑
│   ├── llm_utils/              #   统一 LLM 网关（重试、缓存、限速）
│   ├── config_manager.py       #   配置读/写/校验
│   ├── ocr_engine.py           #   OCR 引擎（PaddleOCR + 3 种 API）
│   ├── ocr_server.py           #   OCR 子进程服务
│   ├── asr_engine.py           #   ASR 引擎（子进程隔离）
│   ├── asr_server.py           #   ASR 子进程服务
│   ├── workflow_manager.py     #   业务流程编排
│   ├── frame_processor.py      #   视频帧解码 + 字幕检测
│   ├── ai_correction.py        #   LLM 纠错 + 分句 + 校对
│   ├── result_processor.py     #   去重、过滤、导出
│   ├── ffmpeg_reader.py        #   FFmpeg 视频解码
│   └── ...
│
├── ui/                         # PyQt5 用户界面
│   ├── main_window.py          #   主窗口
│   ├── config_panel.py         #   参数配置面板
│   ├── settings_dialog.py      #   内部设置对话框
│   ├── video_preview.py        #   视频预览 + ROI
│   ├── result_table.py         #   结果表格 + 搜索栏
│   ├── collapsible_group.py    #   可折叠分组控件
│   ├── workers.py              #   QThread 工作线程
│   └── ...
│
├── styles/                     # QSS 样式表（暗色/亮色）
├── tests/                      # 单元测试
├── scripts/                    # 辅助脚本
├── locale/                     # 国际化 (zh_CN / en_US)
└── models/                     # ASR 模型（gitignored）
```

## 配置说明

所有 UI 参数自动保存到 `config/settings.json`，下次启动自动恢复。

### 处理参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 帧间隔 | 每 N 秒采样一帧 | 0.1s |
| 处理模式 | OCR / ASR / OCR+ASR | 完整流程 |
| 字幕持续时间 | 非 SRT 导出的默认时长 | 3.0s |

### 字幕参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 流式模式 | 哨兵检测 + 去重 + 缓冲区 | 启用 |
| 字数骤降比 | 哨兵模式触发阈值 | 0.5 |
| 相似度阈值 | 去重相似度 | 0.85 |
| 常规模式 | 固定间隔输出 | 用户配置 |

### 后处理参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 置信度过滤 | 删除低置信度结果（仅 PaddleOCR） | 关闭 |
| 去重相似度 | 0~1，越高去重越宽松 | 0.9 |
| 最小文字长度 | 小于此长度的文本被丢弃 | 2 |

### AI 纠错

| 参数 | 说明 |
|------|------|
| API 预设 | 多套连接配置，一键切换 |
| 翻译模式 | 将结果翻译为中文 |
| 流式输出 | 实时逐字显示 API 响应 |
| JSON 模式 | API 返回结构化 JSON |
| 校对模式 | 纠错后二次质量检查 |
| 提示词模板 | 可视化编辑，支持占位符 |
| 环境提取 | 自动分析全文语境提升纠错准确率 |

## 开发

```bash
# 安装开发依赖
uv pip install -e ".[dev]"

# 代码检查
ruff check .

# 格式化
ruff format .

# 类型检查
mypy core/ ui/

# 运行测试
pytest
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT License](LICENSE)
