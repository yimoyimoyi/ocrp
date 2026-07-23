"""核心工具模块 —— 统一 LLM 调用、重试装饰器。

注意：test_connection() 当前未被 UI 调用（UI 使用 engine.check_availability()），
保留在模块中供后续统一连接检测逻辑时使用。
"""

from .llm_client import _normalize_base_url, ask_llm
from .retry import except_handler

__all__ = ["_normalize_base_url", "ask_llm", "except_handler"]
