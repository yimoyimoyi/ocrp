# -*- coding: utf-8 -*-
"""统一日志配置模块。

所有模块应通过此模块获取 logger：
    from core.logger import logger
    # 或
    from core.logger import get_logger
    log = get_logger(__name__)

日志级别可通过环境变量 ORCP_LOG_LEVEL 控制（默认 INFO）。
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional

# ── 日志格式 ──
_LOG_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── 日志文件路径 ──
_BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = _BASE_DIR / "logs"
_LOG_FILE = _LOG_DIR / "orcp.log"

# ── 全局配置（只执行一次） ──
_initialized = False


def _setup_root_logger() -> None:
    """初始化根 logger，仅在首次调用时执行。"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    level_name = os.environ.get("ORCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger("orcp")
    root.setLevel(level)

    # ── stderr handler ──
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    root.addHandler(console_handler)

    # ── 文件 handler（带轮转，10MB×5）──
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
        root.addHandler(file_handler)
    except OSError:
        pass  # 文件日志不可用时降级到仅控制台

    # 防止日志向上传播到 Python 根 logger（避免重复输出）
    root.propagate = False


_setup_root_logger()


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取指定名称的 logger（以 'orcp.' 为前缀）。

    Args:
        name: 模块名称，如 __name__。为 None 时返回根 logger。

    Returns:
        logging.Logger 实例
    """
    if name is None:
        return logging.getLogger("orcp")
    # 如果传入的是 __name__ 形式（如 "core.ocr_engine"），自动加前缀
    if not name.startswith("orcp"):
        name = f"orcp.{name}"
    return logging.getLogger(name)


# ── 便捷导出：根 logger ──
logger = get_logger()
