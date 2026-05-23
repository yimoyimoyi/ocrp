<div align="center">

# ORCP

**跨平台视频/图片 OCR 字幕提取工具**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-yellow.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey.svg)]()

PaddleOCR · OpenAI Vision · Ollama · LlamaCpp · WhisperX · AI 纠错

</div>

---

ORCP 是一款功能完整的桌面端字幕提取工具，支持从视频、音频、图片中提取文字内容。集成多引擎 OCR、离线语音识别、LLM 纠错，提供直观的 PyQt5 图形界面。

## 功能特性

### 核心能力

| 功能 | 说明 |
|------|------|
| **多引擎 OCR** | PaddleOCR（本地 GPU/CPU）、OpenAI Vision、Ollama Vision、LlamaCpp，自由切换 |
| **语音识别** | 基于 faster-whisper 的离线 ASR，子进程隔离 CUDA 环境 |
| **AI 纠错** | LLM API 二次校对，支持流式输出和时间轴感知批量纠错 |
| **流式字幕** | 哨兵检测（字数骤降判断台词结束）+ 相似度去重 + 缓冲区 |
| **批量处理** | 多文件队列，自动导出到 `output/` 目录 |

### 界面功能

- 视频/图片预览，拖拽绘制 ROI 区域
- 多区域独立配置引擎和提示词
- 结果表格：可视化编辑、搜索替换、过滤、排序
- 暗色/亮色主题一键切换
- 分层配置面板（字幕/后处理/语音/纠错/模板）

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
├── ocr_gui.py                  # 应用入口（DLL 加载、QApplication）
├── config_manager.py           # 配置管理器（JSON 注释支持）
├── style_loader.py             # QSS 样式表加载
├── pyproject.toml              # 项目配置、依赖、工具链
│
├── config/                     # 配置文件
│   ├── ocr_engines.json        #   OCR 引擎默认参数
│   ├── asr_engines.json        #   ASR 引擎参数
│   ├── ai_correction.json      #   AI 纠错配置
│   ├── api_presets.json        #   API 连接预设
│   ├── prompt_templates.json   #   提示词模板
│   ├── filters.json            #   关键词过滤
│   └── ui_config.json          #   UI 标签与默认值
│
├── core/                       # 核心业务逻辑
│   ├── workflow_manager.py     #   业务流程编排
│   ├── ocr_engine.py           #   OCR 引擎抽象与实现
│   ├── asr_engine.py           #   ASR 引擎（子进程隔离）
│   ├── asr_server.py           #   ASR 子进程服务
│   ├── ai_correction.py        #   LLM 纠错
│   ├── frame_processor.py      #   视频帧解码 + 字幕检测
│   ├── result_processor.py     #   去重、过滤、导出
│   ├── ffmpeg_reader.py        #   FFmpeg 视频解码
│   ├── filter_manager.py       #   关键词过滤
│   ├── prompt_manager.py       #   提示词模板管理
│   └── api_preset_manager.py   #   API 预设管理
│
├── ui/                         # PyQt5 用户界面
│   ├── main_window.py          #   主窗口（菜单、工具栏、布局）
│   ├── config_panel.py         #   参数配置面板
│   ├── video_preview.py        #   视频预览 + ROI 绘制
│   ├── region_manager.py       #   区域管理器
│   ├── result_table.py         #   结果表格
│   ├── settings_dialog.py      #   设置对话框
│   ├── display_dialog.py       #   显示对话框
│   └── workers.py              #   QThread 工作线程
│
├── styles/                     # QSS 样式表
├── tests/                      # 单元测试
├── scripts/                    # 辅助脚本
└── models/                     # ASR 模型（gitignored）
```

## 配置说明

所有 UI 参数自动保存到 `config/settings.json`，下次启动自动恢复。

### 字幕参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 帧间隔 | 每 N 秒采样一帧 | 0.1s |
| 字幕持续时间 | 非 SRT 导出的默认时长 | 3.0s |
| 流式模式 | 哨兵检测 + 去重 + 缓冲区 | 用户配置 |
| 常规模式 | 固定间隔 + 去重 | 用户配置 |

### 后处理参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 置信度过滤 | 删除低置信度结果（仅 PaddleOCR） | 关闭 |
| 去重相似度 | 0~1，越高去重越宽松 | 0.9 |
| 最小文字长度 | 小于此长度的文本被丢弃 | 2 |

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
