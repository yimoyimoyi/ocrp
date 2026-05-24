"""ui 包初始化。"""

from ui.config_panel import ConfigPanel as ConfigPanel
from ui.region_manager import RegionManagerWidget as RegionManagerWidget
from ui.result_table import ResultTableWidget as ResultTableWidget
from ui.video_preview import VideoPreviewWidget as VideoPreviewWidget
from ui.workers import AICorrectionWorker as AICorrectionWorker
from ui.workers import OCRWorker as OCRWorker
from ui.workers import VideoProcessWorker as VideoProcessWorker
from ui.workers import WorkerSignals as WorkerSignals
