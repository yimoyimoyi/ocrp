<div align="center">

# ORCP

**Cross-Platform Video/Image OCR Subtitle Extraction Tool**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-yellow.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey.svg)]()

PaddleOCR · OpenAI Vision · Ollama · LlamaCpp · WhisperX · LLM Correction · Segmentation

</div>

---

🌐 [中文](../README.md) | [日本語](README_JA.md)

ORCP is a full-featured desktop subtitle extraction tool supporting text extraction from video, audio, and images. It integrates multi-engine OCR, offline speech recognition, LLM correction and sentence segmentation, all within an intuitive PyQt5 GUI.

## Features

### Core Capabilities

| Feature | Description |
|---------|-------------|
| **Multi-Engine OCR** | PaddleOCR (local GPU/CPU), OpenAI Vision, Ollama Vision, LlamaCpp |
| **Speech Recognition** | Offline ASR via faster-whisper, subprocess-isolated CUDA environment |
| **AI Correction + Translation** | Text proofreading & translation via OpenAI-compatible APIs, with streaming + JSON mode |
| **LLM Segmentation** | CoT (Chain-of-Thought) semantic merging of text fragments into complete subtitles, with original-text alignment verification |
| **Proofreading** | Secondary LLM quality check after correction/translation |
| **Streaming Subtitles** | Sentinel detection (word count drop) + similarity dedup + buffer |
| **Batch Processing** | Multi-file queue, auto-export to `output/` |

### LLM Features

| Feature | Description |
|---------|-------------|
| **Unified Gateway** | All LLM calls through single `ask_llm()` entry, exponential backoff retry + response caching |
| **RPM Rate Limiting** | Sliding window rate limiter to prevent API throttling |
| **Multi-Preset Support** | Configure multiple API connections (OpenAI / DeepSeek / Ollama / Volcano Engine etc.), one-click switching |
| **Connection Test** | One-click API connectivity and credential verification |
| **Prompt Templates** | Visual editor with `{raw_text}` `{context}` `{environment}` etc. placeholders |
| **Template/Custom Toggle** | Switch between template override mode and custom reference mode |

### Interface Features

- Video/image preview with drag-to-draw ROI regions
- Multi-region independent engine and prompt configuration
- Result table: visual editing, search & replace, filtering, sorting
- Search bar styling unified with result table
- Dark/light theme one-click switch
- Collapsible settings panels (decode params / VAD / region sorting)
- Auto engine warm-up on processing mode switch

### Supported Formats

| Type | Formats |
|------|---------|
| **Video Input** | MP4, MKV, AVI, MOV, WebM |
| **Audio Input** | MP3, WAV, FLAC, OGG |
| **Image Input** | PNG, JPG, BMP |
| **Subtitle Output** | SRT, TXT, JSON, CSV |

## Quick Start

### Requirements

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager (recommended)
- FFmpeg (auto-installed by setup script)
- GPU mode: NVIDIA driver + CUDA Toolkit

### Installation

**Windows:**
```batch
git clone https://github.com/yimoyimoyi/orcp.git
cd orcp
setup.bat              # Auto-detect GPU, install dependencies
```

**Linux:**
```bash
git clone https://github.com/yimoyimoyi/orcp.git
cd orcp
bash setup.sh          # Auto-detect GPU, install system dependencies
```

Setup script options:
| Option | Description |
|--------|-------------|
| `--cpu` | Force CPU-only mode |
| `--gpu` | Force GPU mode |
| `--no-ffmpeg` | Skip FFmpeg installation |

### Launch

```bash
# Cross-platform
uv run python ocr_gui.py

# Launch scripts
orcp_gui.bat           # Windows
bash orcp_gui.sh       # Linux
```

### GPU ASR Acceleration (cuDNN 8)

> Required only for GPU speech recognition. OCR and CPU ASR are unaffected.

ctranslate2 requires cuDNN 8 DLLs. If missing, ASR automatically falls back to CPU mode.

1. Visit https://developer.nvidia.com/cudnn (free registration required)
2. Download **cuDNN 8.9 for CUDA 12.x**
3. Place DLLs in `models/asr/lib/`:
   - **Windows**: `cudnn_ops_infer64_8.dll`, `cudnn_cnn_infer64_8.dll`, `cudnn64_8.dll`
   - **Linux**: `libcudnn_ops_infer.so.8`, `libcudnn_cnn_infer.so.8`, `libcudnn.so.8`

### First Use

1. Click **📂 Open Video/Image** or drag a file to the preview area
2. Drag on the preview to draw OCR regions
3. Menu bar **Engine(&E)** to select engine (default PaddleOCR)
4. Click **▶ Start Processing**

## Project Structure

```
orcp/
├── ocr_gui.py                  # Application entry point
├── pyproject.toml              # Project config, dependencies
├── setup.bat / setup.sh        # Installation scripts
├── orcp_gui.bat / orcp_gui.sh  # Launch scripts
├── diagnose.bat / diagnose.sh  # Diagnostic scripts
│
├── config/                     # Configuration files (JSON)
├── docs/                       # Documentation
│
├── core/                       # Core business logic
│   ├── llm_utils/              #   Unified LLM gateway (retry, cache, rate limit)
│   ├── config_manager.py       #   Config read/write/validate
│   ├── ocr_engine.py           #   OCR engines
│   ├── asr_engine.py           #   ASR engine (subprocess-isolated)
│   ├── workflow_manager.py     #   Workflow orchestration
│   ├── frame_processor.py      #   Video frame decoding + subtitle detection
│   ├── ai_correction.py        #   LLM correction + segmentation + proofread
│   ├── result_processor.py     #   Dedup, filter, export
│   └── ...
│
├── ui/                         # PyQt5 user interface
│   ├── main_window.py          #   Main window
│   ├── config_panel.py         #   Settings panel
│   ├── settings_dialog.py      #   Advanced settings dialog
│   ├── video_preview.py        #   Video preview + ROI
│   ├── result_table.py         #   Result table + search bar
│   ├── collapsible_group.py    #   Collapsible group widget
│   ├── workers.py              #   QThread workers
│   └── ...
│
├── styles/                     # QSS stylesheets (dark/light)
├── tests/                      # Unit tests
├── scripts/                    # Utility scripts
├── locale/                     # i18n (zh_CN / en_US)
└── models/                     # ASR models (gitignored)
```

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Lint
ruff check .

# Format
ruff format .

# Type check
mypy core/ ui/

# Run tests
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

[MIT License](LICENSE)
