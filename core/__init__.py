# -*- coding: utf-8 -*-
"""core 包初始化。"""

from core.ai_correction import AICorrector as AICorrector, load_correction_config as load_correction_config
from core.frame_processor import (
    FrameProcessor as FrameProcessor,
    format_time as format_time,
    get_similarity as get_similarity,
)
from core.ocr_engine import (
    BaseOCREngine as BaseOCREngine,
    LlamaCppEngine as LlamaCppEngine,
    OCREngineManager as OCREngineManager,
    OllamaVisionEngine as OllamaVisionEngine,
    OpenAIVisionEngine as OpenAIVisionEngine,
    PaddleOCREngine as PaddleOCREngine,
)
from core.prompt_manager import PromptTemplateManager as PromptTemplateManager
from core.result_processor import (
    GARBAGE_PATTERN as GARBAGE_PATTERN,
    export_results as export_results,
    polish_results as polish_results,
)
