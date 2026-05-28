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

# ── 日志格式 ──
_LOG_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── 日志文件路径 ──
_BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = _BASE_DIR / "logs"
_LOG_FILE = _LOG_DIR / "orcp.log"

# ── 全局配置（只执行一次） ──
_initialized = False


# ── ANSI 终端颜色 ──
_ANSI = {
    "reset": "\033[0m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "cyan": "\033[96m",
    "grey": "\033[90m",
    "bold": "\033[1m",
}

_LEVEL_COLORS = {
    logging.ERROR: _ANSI["red"] + _ANSI["bold"],
    logging.WARNING: _ANSI["yellow"],
    logging.INFO: _ANSI["green"],
    logging.DEBUG: _ANSI["grey"],
}


class _ColoredFormatter(logging.Formatter):
    """为控制台输出添加 ANSI 颜色的日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, _ANSI["reset"])
        reset = _ANSI["reset"]
        levelname = record.levelname
        # 级别名着色
        record.levelname = f"{color}{levelname}{reset}"
        msg = super().format(record)
        # 根据级别给整行加色
        if record.levelno >= logging.ERROR:
            return f"{_ANSI['red']}{msg}{reset}"
        elif record.levelno >= logging.WARNING:
            return f"{_ANSI['yellow']}{msg}{reset}"
        return msg


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

    # 防止重复添加 handler（例如模块重载或子进程继承时）
    existing_types = {type(h) for h in root.handlers}

    # ── stderr handler（带颜色）──
    if logging.StreamHandler not in existing_types:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(_ColoredFormatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
        root.addHandler(console_handler)

    # ── 文件 handler（不带颜色，纯文本）──
    if logging.handlers.RotatingFileHandler not in existing_types:
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


def get_logger(name: str | None = None) -> logging.Logger:
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
