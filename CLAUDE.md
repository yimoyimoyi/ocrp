# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ORCP is a cross-platform (Windows/Linux) desktop GUI tool for video/image OCR subtitle extraction, built with PyQt5. It supports PaddleOCR (local GPU/CPU), OpenAI Vision, Ollama Vision, LlamaCpp, plus ASR via faster-whisper and LLM-based OCR correction.

## Commands

```bash
# Run the app
uv run python ocr_gui.py

# Syntax check
uv run python -c "import ast; ast.parse(open('ocr_gui.py').read()); print('OK')"

# Import check
uv run python -c "from core.workflow_manager import WorkflowManager; from ui.main_window import MainWindow; print('OK')"

# Install (Windows)
setup.bat [--cpu|--gpu|--no-ffmpeg]

# Install (Linux)
bash setup.sh [--cpu|--gpu|--no-ffmpeg]

# Diagnostics
diagnose.bat            # Windows
bash diagnose.sh        # Linux
```

## Architecture

### Entry Point → UI → Core data flow

```
ocr_gui.py                    # DLL setup, torch preload, QApplication + MainWindow
  └── ui/main_window.py       # Menus, toolbar, video preview, results table, status bar
        └── core/workflow_manager.py  # All business logic (dependency-injected)
              ├── core/ocr_engine.py        # 4 OCR engines: PaddleOCR, OpenAI, Ollama, LlamaCpp
              ├── core/asr_engine.py        # ASR via subprocess (WhisperXEngine → asr_server.py)
              ├── core/frame_processor.py   # FFmpeg video decoding + subtitle detection
              ├── core/ai_correction.py     # LLM-based OCR correction
              └── core/result_processor.py  # Dedup, filter, export (TXT/JSON/CSV/SRT)
```

### Key architectural decisions

- **ASR process isolation**: `asr_engine.py` spawns `asr_server.py` as a subprocess communicating via stdin/stdout JSON lines. This isolates CUDA/cuDNN DLL environments between PaddleOCR and faster-whisper, which have conflicting CUDA dependencies.
- **DLL preload order in `ocr_gui.py`**: On Windows, torch's `lib/` directory must be added to DLL search path BEFORE PyQt5 imports. This prevents DLL resolution conflicts.
- **Config system**: `config_manager.py` handles all JSON config files with comment support (`//` and `/* */`). Settings auto-migrate between versions. `config/settings.json` is gitignored — it persists UI state (window geometry, last engine, mode params).
- **Worker threads**: All heavy processing runs in `QThread` subclasses defined in `ui/workers.py` — `OCRWorker`, `AICorrectionWorker`, `VideoProcessWorker`. Signals carry results back to the main thread. Never run OCR/ASR/correction on the main thread.

### Two subtitle detection modes

1. **Streaming mode** (`frame_processor.py`): Sentinel-based detection — waits for word count to drop abruptly (end of subtitle line), then deduplicates via similarity threshold and flushes a buffer.
2. **Regular mode**: Fixed-interval frame sampling with optional dedup.

### Config files (in `config/`)

| File | Purpose |
|------|---------|
| `settings.json` | UI state persistence (gitignored) |
| `ocr_engines.json` | 4 OCR engine defaults |
| `asr_engines.json` | ASR model, device, VAD params |
| `ai_correction.json` | LLM correction API config + prompts |
| `api_presets.json` | Shared API connection presets |
| `prompt_templates.json` | User-editable prompt templates |
| `filters.json` | Keyword filter list |
| `ui_config.json` | UI labels and defaults |

## Important constraints

- **Python >= 3.12** required (uses `uv` for package management, lock file is gitignored)
- **torch < 2.11**, **torchaudio < 2.11** (pinned in pyproject.toml)
- **setuptools < 70** (pinned for compatibility)
- PaddleOCR >= 3.0, paddlepaddle >= 3.0
- On Windows, GPU ASR requires cuDNN 8 DLLs manually placed in `models/asr/lib/` (not in repo, gitignored)
- FFmpeg binaries (`core/ffmpeg.exe`, `core/ffprobe.exe`) are downloaded by setup script, not in repo
- ASR model files in `models/asr/` are gitignored — users download separately
