"""日志模块单元测试。"""

import importlib.util
import logging
import sys
from pathlib import Path

# 直接加载 logger 模块文件，避免触发 core/__init__.py（会加载 torch）
_ROOT = Path(__file__).resolve().parent.parent
_logger_path = _ROOT / "core" / "logger.py"
_spec = importlib.util.spec_from_file_location("core.logger", _logger_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["core.logger"] = _mod
_spec.loader.exec_module(_mod)


class TestLogger:
    """测试 core.logger 模块。"""

    def test_get_logger_default(self):
        log = _mod.get_logger()
        assert log.name == "orcp"
        assert log.level <= logging.INFO

    def test_get_logger_named(self):
        log = _mod.get_logger("core.ocr_engine")
        assert log.name == "orcp.core.ocr_engine"

    def test_get_logger_auto_prefix(self):
        log = _mod.get_logger("my_module")
        assert log.name == "orcp.my_module"

    def test_logger_singleton(self):
        log1 = _mod.get_logger("test_module")
        log2 = _mod.get_logger("test_module")
        assert log1 is log2

    def test_logger_has_handler(self):
        assert len(_mod.logger.handlers) >= 1

    def test_log_levels(self):
        """各日志级别不应抛出异常。"""
        log = _mod.get_logger("test_levels")
        log.debug("debug message")
        log.info("info message")
        log.warning("warning message")
        log.error("error message")
