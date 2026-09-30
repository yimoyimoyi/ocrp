"""共享工具函数 —— FFmpeg 查找、常量定义等。"""

import os
import shutil
import sys
from pathlib import Path

from rapidfuzz.fuzz import ratio  # P2-2：硬依赖移顶部导入，去重热路径不再每调用走 import 分支


def get_similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0.0 ~ 1.0），使用 RapidFuzz C++ 实现。"""
    return ratio(a, b) / 100.0 if a and b else 0.0


def format_time(seconds: float) -> str:
    """格式化秒数为 HH:MM:SS,mmm（SRT 标准格式）。"""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


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

# 字幕检测模式（唯一规范 token）
SUBTITLE_MODE_STREAM = "stream"
SUBTITLE_MODE_REGULAR = "regular"

# 旧配置 / 本地化文本 → 规范 token
_SUBTITLE_MODE_ALIASES = {
    "stream": SUBTITLE_MODE_STREAM,
    "streaming": SUBTITLE_MODE_STREAM,
    "流式": SUBTITLE_MODE_STREAM,
    "流式字幕": SUBTITLE_MODE_STREAM,
    "流式字幕（去重）": SUBTITLE_MODE_STREAM,
    "流式字幕(去重)": SUBTITLE_MODE_STREAM,
    "regular": SUBTITLE_MODE_REGULAR,
    "fixed": SUBTITLE_MODE_REGULAR,
    "常规": SUBTITLE_MODE_REGULAR,
    "常规字幕": SUBTITLE_MODE_REGULAR,
    "常规字幕（固定间隔）": SUBTITLE_MODE_REGULAR,
    "常规字幕(固定间隔)": SUBTITLE_MODE_REGULAR,
    "固定间隔": SUBTITLE_MODE_REGULAR,
    # ja_JP 界面标签
    "ストリーミング（重複除去）": SUBTITLE_MODE_STREAM,
    "ストリーミング(重複除去)": SUBTITLE_MODE_STREAM,
    "通常（固定間隔）": SUBTITLE_MODE_REGULAR,
    "通常(固定間隔)": SUBTITLE_MODE_REGULAR,
}


def normalize_subtitle_mode(value: object) -> str:
    """把任意历史/本地化写法归一为规范 token（stream / regular）。

    未知值回落到 stream（历史默认），保证下游只需比较 token。
    """
    text = str(value or "").strip()
    if not text:
        return SUBTITLE_MODE_STREAM
    alias = _SUBTITLE_MODE_ALIASES.get(text)
    if alias is not None:
        return alias
    # 兜底：容忍 "流式字幕（去重）" 之类的变体写法
    if "流式" in text or "stream" in text.lower():
        return SUBTITLE_MODE_STREAM
    if "常规" in text or "固定间隔" in text or "固定間隔" in text or "regular" in text.lower():
        return SUBTITLE_MODE_REGULAR
    return SUBTITLE_MODE_STREAM


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
        engine = engine_cls({"config": cfg})
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
