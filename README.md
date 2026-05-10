# ORCP - OCR 处理工具

ORCP（OCR Processing Tool）是一款跨平台（Windows / Linux）的视频/图片 OCR 字幕提取工具，支持多引擎（PaddleOCR / Ollama / OpenAI Vision / LlamaCpp）、语音识别（WhisperX）、AI 纠错、批量处理等功能。

## 功能特性

### 🎯 核心功能
- **多引擎 OCR**：PaddleOCR（本地 GPU/CPU）、OpenAI Vision、Ollama Vision、LlamaCpp，自由切换
- **语音识别 (ASR)**：基于 faster-whisper 的离线语音识别，支持 GPU 加速
- **AI 纠错**：调用 LLM API 对 OCR 结果二次校对，支持时间轴感知的批量纠错
- **字幕模式**：
  - **流式字幕**：基于哨兵检测（字数骤降判断台词结束）+ 去重 + 缓冲区，实时输出
  - **常规字幕**：按固定间隔采样，可选基本去重

### 🖥️ 用户界面
- 视频/图片预览面板，支持拖拽绘制 ROI 区域
- 区域管理器：多区域独立配置引擎/提示词
- 结果表格：可视化编辑、搜索替换、过滤、排序
- 配置面板：分层 Tab 结构（基础/字幕/后处理/排序/语音/纠错/模板）
- 暗色/亮色主题一键切换

### 📦 支持格式
- **输入**：MP4, MKV, AVI, MOV, WebM（视频）；MP3, WAV, FLAC, OGG（音频）；PNG, JPG, BMP（图片）
- **输出**：SRT（字幕）、TXT、JSON、CSV
- **批量处理**：多文件队列，自动导出到 `output/` 目录

### 🔧 高级功能
- 置信度阈值过滤（仅 PaddleOCR）
- 关键词过滤
- 后处理去重（相似度阈值 + 最小文字长度）
- 处理暂停/继续
- 自定义排序规则
- API 预设管理
- 提示词模板管理
- GPU/CPU 硬件加速切换
- 配置自动迁移（`settings.json` 自动生成与兼容）

## 快速开始

### 环境要求
- Python >= 3.12
- 推荐使用 [uv](https://github.com/astral-sh/uv) 包管理器
- FFmpeg（Windows 使用 `core/` 目录下的捆绑二进制；Linux 通过包管理器安装）
- Linux GPU 模式额外需要：NVIDIA 驱动、CUDA Toolkit、cuDNN

### Linux 一键安装

```bash
# 克隆仓库
git clone https://github.com/yourusername/orcp.git
cd orcp

# 运行一键安装脚本（自动检测 GPU、安装系统依赖和 Python 包）
bash setup.sh

# 或指定模式
bash setup.sh --cpu        # 强制纯 CPU 模式
bash setup.sh --gpu        # 强制 GPU 模式（需 NVIDIA 显卡）
bash setup.sh --no-ffmpeg  # 跳过 ffmpeg 安装（使用已安装的系统版）

# 安装完成后启动
bash orcp_gui.sh
```

### Windows 安装

```batch
:: 克隆仓库
git clone https://github.com/yourusername/orcp.git
cd orcp

:: 运行一键安装脚本（自动检测 GPU、安装 FFmpeg + Python 依赖）
setup.bat

:: 或指定模式
setup.bat --cpu          :: 强制纯 CPU 模式
setup.bat --gpu          :: 强制 GPU 模式（需 NVIDIA 显卡）
setup.bat --no-ffmpeg    :: 跳过 FFmpeg 安装（已有则使用）
setup.bat --ffmpeg-only  :: 仅安装 FFmpeg
```

### 启动

```bash
# 方式一：跨平台通用
uv run python ocr_gui.py

# 方式二：使用启动脚本（首次运行会自动检测并提示安装）
orcp_gui.bat          # Windows
bash orcp_gui.sh      # Linux
```

### 首次使用
1. 打开媒体文件：点击 **📂 打开视频/图片** 或拖拽文件到预览区
2. 绘制 OCR 区域：在预览图上拖拽鼠标定义矩形区域
3. 选择引擎：菜单栏 **引擎(&E)** 中选择（默认 PaddleOCR）
4. 开始处理：点击 **▶ 开始处理**

## 项目结构

```
orcp/
├── ocr_gui.py              # 应用入口
├── config_manager.py       # 配置管理器
├── style_loader.py         # 样式表加载
├── pyproject.toml          # 项目配置与依赖
├── setup.sh                # Linux 一键安装脚本
├── orcp_gui.bat            # Windows 启动脚本
├── orcp_gui.sh             # Linux 启动脚本
├── config/                 # 配置文件
│   ├── settings.json       # UI 参数持久化
│   ├── ocr_engines.json    # OCR 引擎配置
│   ├── ai_correction.json  # AI 纠错配置
│   ├── asr_engines.json    # ASR 引擎配置
│   ├── api_presets.json    # API 预设
│   ├── prompt_templates.json  # 提示词模板
│   └── filters.json        # 过滤器
├── core/                   # 核心逻辑
│   ├── workflow_manager.py # 业务流程管理器
│   ├── ocr_engine.py      # OCR 引擎（PaddleOCR / API）
│   ├── asr_engine.py      # ASR 引擎（WhisperX）
│   ├── asr_server.py      # ASR 子进程服务器
│   ├── ai_correction.py   # AI 纠错
│   ├── frame_processor.py # 视频帧处理（哨兵去重）
│   ├── result_processor.py # 结果导出
│   ├── ffmpeg_reader.py   # FFmpeg 视频解码
│   ├── filter_manager.py  # 关键词过滤
│   ├── prompt_manager.py  # 提示词模板管理
│   └── api_preset_manager.py # API 预设管理
├── ui/                     # 用户界面
│   ├── main_window.py     # 主窗口
│   ├── config_panel.py    # 配置面板
│   ├── video_preview.py   # 视频预览
│   ├── region_manager.py  # 区域管理
│   ├── result_table.py    # 结果表格
│   └── workers.py         # 工作线程
├── styles/                 # QSS 样式表
│   ├── dark_style.qss
│   └── light_style.qss
├── scripts/                # 辅助脚本
└── models/                 # ASR 模型
```

## 配置说明

所有 UI 参数自动保存到 `config/settings.json`，下次启动自动恢复。

### OCR 引擎配置
在菜单栏 **引擎(&E) → 引擎配置...** 中设置：
- API Key / Base URL / 模型名
- 超时时间
- GPU 加速

### 字幕参数
配置面板 **字幕设置** Tab：
| 参数 | 说明 | 默认值 |
|------|------|--------|
| 帧间隔 | 每 N 秒采样一帧 | 0.1s |
| 字幕持续时间 | 非 SRT 导出的默认时长 | 3.0s |
| 流式模式 | 哨兵/去重/相似度/缓冲区 | 用户配置 |
| 常规模式 | 去重/间隔/缓冲区 | 用户配置 |

### 后处理参数
配置面板 **后处理** Tab：
| 参数 | 说明 | 默认值 |
|------|------|--------|
| 置信度过滤 | 删除 PaddleOCR 低置信度结果 | 关闭 |
| 去重相似度 | 0~1，越高去重越宽松 | 0.9 |
| 最小文字长度 | 小于此长度的文本被丢弃 | 2 |

## 开发

```bash
# 语法检查
uv run python -c "import ast; ast.parse(open('ocr_gui.py').read()); print('OK')"

# 测试导入
uv run python -c "from core.workflow_manager import WorkflowManager; from ui.main_window import MainWindow; print('OK')"
```

## 许可证

MIT License
