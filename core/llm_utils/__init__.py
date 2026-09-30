"""核心工具模块 —— 统一 LLM 调用、重试装饰器。

``test_connection()`` 由设置对话框的「测试连接」按钮经后台线程调用
（见 ``ui/settings_dialog.SettingsDialog._on_test_connection``，R12 起）。
"""

from .llm_client import _normalize_base_url, ask_llm, test_connection
from .retry import except_handler

__all__ = ["_normalize_base_url", "ask_llm", "except_handler", "test_connection"]
