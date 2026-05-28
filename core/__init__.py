"""core 包初始化。"""

from core.ai_correction import AICorrector as AICorrector
from core.ai_correction import load_correction_config as load_correction_config
from core.frame_processor import (
    FrameProcessor as FrameProcessor,
)
from core.frame_processor import (
    format_time as format_time,
)
from core.frame_processor import (
    get_similarity as get_similarity,
)
from core.ocr_engine import (
    BaseOCREngine as BaseOCREngine,
)
from core.ocr_engine import (
    LlamaCppEngine as LlamaCppEngine,
)
from core.ocr_engine import (
    OCREngineManager as OCREngineManager,
)
from core.ocr_engine import (
    OllamaVisionEngine as OllamaVisionEngine,
)
from core.ocr_engine import (
    OpenAIVisionEngine as OpenAIVisionEngine,
)
from core.ocr_engine import (
    PaddleOCREngine as PaddleOCREngine,
)
from core.prompt_manager import PromptTemplateManager as PromptTemplateManager
from core.result_processor import (
    export_results as export_results,
)
from core.result_processor import (
    polish_results as polish_results,
)
