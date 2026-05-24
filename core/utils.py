"""共享工具函数 —— FFmpeg 查找、常量定义等。"""

import os
import shutil
import sys
from pathlib import Path

_BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Windows 包管理器常见 FFmpeg 安装路径 ──
_WIN_FFMPEG_EXTRA_PATHS = [
    r"C:\Program Files\FFmpeg\bin",
    os.path.expanduser(r"~\scoop\apps\ffmpeg\current\bin"),
    r"C:\ProgramData\chocolatey\bin",
    r"C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin",
]


def find_ffmpeg(name: str = "ffmpeg") -> str:
    """查找 ffmpeg 系列工具：优先系统 PATH → 包管理器路径 → core/ 捆绑二进制。"""
    system = shutil.which(name)
    if system:
        return system

    if sys.platform == "win32":
        for base in _WIN_FFMPEG_EXTRA_PATHS:
            candidate = os.path.join(base, f"{name}.exe")
            if os.path.isfile(candidate):
                return candidate
        return str(_BASE_DIR / "core" / f"{name}.exe")
    else:
        return str(_BASE_DIR / "core" / name)


# ── 魔术字符串常量 ──

# 引擎名称
ENGINE_PADDLEOCR = "paddleocr"
ENGINE_WHISPERX = "whisperx"

# 处理模式
MODE_OCR_ONLY = "仅 OCR"
MODE_ASR_ONLY = "仅语音识别 (ASR)"
MODE_OCR_ASR_FULL = "OCR + ASR（完整流程）"

# 默认值
DEFAULT_OCR_TEMPLATE = "通用OCR"
DEFAULT_ASR_MODEL_DIR = str(_BASE_DIR / "models" / "asr")
DEFAULT_SRT_DURATION = 3.0


def fetch_models_from_url(base_url: str, api_key: str = "", timeout: int = 10) -> list:
    """根据 Base URL 自动检测引擎类型并获取可用模型列表。

    Args:
        base_url: API 地址
        api_key: 可选 API Key
        timeout: 请求超时秒数

    Returns:
        模型名称列表，失败返回空列表
    """
    # 延迟导入避免循环依赖
    from core.ocr_engine import OllamaVisionEngine, OpenAIVisionEngine

    # 根据 URL 特征判断引擎类型
    url_lower = base_url.lower()
    if "ollama" in url_lower or ":11434" in base_url:
        engine_cls = OllamaVisionEngine
    elif base_url:
        engine_cls = OpenAIVisionEngine  # OpenAI 兼容接口（含 llamacpp）
    else:
        return []

    cfg = {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": "",
        "timeout": timeout,
    }
    try:
        engine = engine_cls(cfg)
        return engine.get_model_list()
    except Exception:
        return []


def populate_model_combo(combo, models: list) -> None:
    """用模型列表填充 QComboBox（可编辑），保留当前文本"""
    if not models:
        return
    current = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(models)
    combo.setEditText(current if current else models[0])
    combo.blockSignals(False)
