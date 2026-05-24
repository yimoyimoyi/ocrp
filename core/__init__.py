# -*- coding: utf-8 -*-
"""core 包初始化。"""

from core.ocr_engine import (
    OCREngineManager,
    BaseOCREngine,
    PaddleOCREngine,
    OpenAIVisionEngine,
    OllamaVisionEngine,
    LlamaCppEngine,
)
from core.ai_correction import AICorrector, load_correction_config
from core.frame_processor import FrameProcessor, get_similarity, format_time
from core.result_processor import (
    polish_results,
    export_results,
    GARBAGE_PATTERN,
)
from core.prompt_manager import PromptTemplateManager
