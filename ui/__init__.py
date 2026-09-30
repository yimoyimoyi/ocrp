"""ui 包初始化。"""

from core.workers import OCRWorker as OCRWorker
from core.workers import VideoProcessWorker as VideoProcessWorker
from core.workers import WorkerSignals as WorkerSignals
from ui.config_panel import ConfigPanel as ConfigPanel
from ui.region_manager import RegionManagerWidget as RegionManagerWidget
from ui.result_table import ResultTableWidget as ResultTableWidget
from ui.video_preview import VideoPreviewWidget as VideoPreviewWidget
