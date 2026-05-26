# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ORCP is a cross-platform (Windows/Linux) desktop GUI tool for video/image OCR subtitle extraction, built with PyQt5. It supports PaddleOCR (local GPU/CPU), OpenAI Vision, Ollama Vision, LlamaCpp, plus ASR via faster-whisper and LLM-based OCR correction/segmentation/proofreading.

## Commands

```bash
# Run the app
uv run python ocr_gui.py

# Install dependencies
uv pip install -e ".[dev]"

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run mypy core/ ui/

# Run tests
uv run pytest

# Quick syntax check
uv run python -c "import ast; ast.parse(open('ocr_gui.py').read()); print('OK')"

# Quick import check
uv run python -c "from core.workflow_manager import WorkflowManager; from ui.main_window import MainWindow; print('OK')"

# Install (Windows)
setup.bat [--cpu|--gpu|--no-ffmpeg]

# Install (Linux)
bash setup.sh [--cpu|--gpu|--no-ffmpeg]
```

## Architecture

### Entry Point → UI → Core

```
ocr_gui.py                    # DLL setup, torch preload, QApplication + MainWindow
  └── ui/main_window.py       # Menus, toolbar, video preview, result table, status bar
        ├── ui/config_panel.py      # Tabbed settings panel (processing, subtitle, sort, ASR, etc.)
        ├── ui/settings_dialog.py   # Advanced settings dialog (internal settings)
        ├── ui/result_table.py      # QTableWidget + search/replace bar
        ├── ui/video_preview.py     # Video playback + ROI drawing
        ├── ui/workers.py           # All QThread workers
        ├── ui/collapsible_group.py # Collapsible section widget
        └── core/workflow_manager.py  # Business logic orchestration (dependency-injected)
              ├── core/ocr_engine.py        # OCR engines + engine manager
              ├── core/asr_engine.py        # ASR via subprocess (WhisperXEngine → asr_server.py)
              ├── core/frame_processor.py   # FFmpeg video decoding + subtitle detection
              ├── core/ai_correction.py     # LLM correction, translation, segmentation, proofreading
              ├── core/result_processor.py  # Dedup, filter, export (TXT/JSON/CSV/SRT)
              ├── core/llm_utils/           # Unified LLM gateway (retry, cache, rate limit)
              ├── core/config_manager.py    # Config read/write/validate, load_key() dot-access
              └── core/utils.py             # FFmpeg lookup, constants (engine names, modes)
```

### LLM Architecture

```
All LLM calls route through: core/llm_utils/llm_client.py → ask_llm()
  - Pure function: all config passed as parameters, no global state
  - Exponential backoff retry (except_handler decorator)
  - File-based response cache (output/llm_log/{title}.json)
  - JSON repair via json_repair for malformed responses
  - Sliding window RPM rate limiter (global instance)
  - Supports stream mode + validation callbacks

Three consumers of ask_llm():
  1. AICorrector (ai_correction.py) — correction, translation, segmentation, proofreading
  2. OCR vision engines (ocr_engine.py) — OpenAIVision, OllamaVision, LlamaCpp
  3. Connection test (config_panel.py) — test_connection()
```

### Key architectural decisions

- **ASR process isolation**: `asr_engine.py` spawns `asr_server.py` as a subprocess communicating via stdin/stdout JSON lines. This isolates CUDA/cuDNN DLL environments between PaddleOCR and faster-whisper, which have conflicting CUDA dependencies.
- **DLL preload order in `ocr_gui.py`**: On Windows, torch's `lib/` directory must be added to DLL search path BEFORE PyQt5 imports. This prevents DLL resolution conflicts.
- **OCR subprocess isolation**: `ocr_server.py` runs PaddleOCR in a child process for environments where DLL conflicts persist.
- **Config system**: JSON config files with comment support (`//` in settings.json). `load_key("mode_params.xxx")` provides dot-notation access via `config_manager.py`. Sensitive config files (containing API keys) are gitignored.
- **Worker threads**: All heavy processing runs in QThread subclasses in `ui/workers.py`. Never run OCR/ASR/correction on the main thread. Signals carry results back.

### Two subtitle detection modes

1. **Streaming mode** (`frame_processor.py`): Sentinel-based detection — waits for word count to drop abruptly (end of subtitle line), then deduplicates via similarity threshold and flushes a buffer.
2. **Regular mode**: Fixed-interval frame sampling with optional dedup via `r_sim_threshold`.

### Config files (in `config/` — all gitignored except `ui_config.json`)

| File | Purpose |
|------|---------|
| `settings.json` | UI state + `mode_params` (window geometry, last engine, all mode settings) |
| `ocr_engines.json` | 4 OCR engine defaults with API keys |
| `asr_engines.json` | ASR model, device, VAD params |
| `ai_correction.json` | LLM correction API config + prompts + template/proofread flags |
| `api_presets.json` | Shared API connection presets with keys |
| `prompt_templates.json` | User-editable prompt templates |
| `filters.json` | Keyword filter list |
| `ui_config.json` | UI labels and defaults (TRACKED, safe) |

## Important constraints

- **Python >= 3.12** required (uses `uv` for package management, lock file is gitignored)
- **torch < 2.11**, **torchaudio < 2.11** (pinned in pyproject.toml)
- **setuptools < 70** (pinned for compatibility)
- PaddleOCR >= 3.0, paddlepaddle >= 3.0
- On Windows, GPU ASR requires cuDNN 8 DLLs manually placed in `models/asr/lib/` (gitignored)
- FFmpeg binaries (`core/ffmpeg.exe`, `core/ffprobe.exe`) are downloaded by setup script, not in repo
- ASR model files in `models/asr/` are gitignored — users download separately
- Config JSON files with API keys are gitignored — `git rm --cached` after adding them to `.gitignore`
- Do not commit `output/` (LLM cache), `*.log`, or `models/asr/` content
