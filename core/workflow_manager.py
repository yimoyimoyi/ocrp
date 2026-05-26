"""WorkflowManager —— 封装所有业务流程逻辑（处理、纠错、批量、导出）。

通过信号与 MainWindow 通信，通过依赖注入获取 UI 数据，
保持业务逻辑与 UI 的分离。
"""

# [DEBUG] 临时调试日志
import datetime as _wdt
import json
import threading
from collections.abc import Callable
from pathlib import Path
from pathlib import Path as _WPath
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal

from core.frame_processor import FrameProcessor
from core.logger import get_logger
from core.result_processor import export_results
from core.utils import ENGINE_WHISPERX, MODE_ASR_ONLY, MODE_OCR_ASR_FULL, MODE_OCR_ONLY

_WDEBUG = _WPath(__file__).resolve().parent.parent / "logs" / "debug_seg.log"
_WDEBUG.parent.mkdir(parents=True, exist_ok=True)


def _wlog(msg: str):
    with open(_WDEBUG, "a", encoding="utf-8") as f:
        f.write(f"{_wdt.datetime.now().strftime('%H:%M:%S.%f')[:-3]} {msg}\n")
from ui.workers import (
    AICorrectionWorker,
    AudioProcessWorker,
    BatchCorrectionWorker,
    BatchProcessWorker,
    ImageProcessWorker,
    SegmentationWorker,
    VideoProcessWorker,
)

logger = get_logger(__name__)


class WorkflowManager(QObject):
    """封装所有业务流程逻辑。"""

    # ── 信号（UI 更新） ──
    status_msg = pyqtSignal(str)                                    # 状态栏文本
    progress_val = pyqtSignal(int)                                  # 进度条 0-100
    time_display = pyqtSignal(str)                                  # 时间标签
    buttons_enabled = pyqtSignal(dict)                              # 按钮启用状态
    error_dialog = pyqtSignal(str, str)                             # 错误弹窗 (标题, 消息)
    info_dialog = pyqtSignal(str, str)                              # 提示弹窗 (标题, 消息)
    result_row = pyqtSignal(float, str, str, str, str, float, float)       # (ts, t_str, rname, ename, raw, conf, end_sec)
    process_finished = pyqtSignal()                                 # 处理完成后通知 MainWindow 做后处理
    correction_updated = pyqtSignal(int, str, str)                  # (row, raw, corrected)
    correction_stream_updated = pyqtSignal(int, str)                # (row, partial_text) 流式增量更新
    segmentation_updated = pyqtSignal(int, str)                     # (row, segmented_text) 分句结果更新
    batch_progress = pyqtSignal(str, int, int)                      # (fname, idx, total)
    batch_file_done = pyqtSignal(str, list)                         # (file_path, results)
    batch_all_done = pyqtSignal()                                   # 批量全部完成
    batch_error = pyqtSignal(str)                                   # 批量出错

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)

        # ── 管理器（通过 configure() 注入） ──
        self._engine_mgr = None
        self._asr_mgr = None
        self._corrector = None
        self._filter_mgr = None
        self._config_mgr = None

        # ── 工作线程 ──
        self._video_worker: VideoProcessWorker | None = None
        self._audio_worker: AudioProcessWorker | None = None
        self._image_worker: ImageProcessWorker | None = None
        self._batch_worker: BatchProcessWorker | None = None
        self._frame_processor: FrameProcessor | None = None
        self._correction_workers: list[AICorrectionWorker] = []
        self._correction_workers_lock = threading.Lock()
        self._batch_correction_worker: BatchCorrectionWorker | None = None
        self._segmentation_worker: SegmentationWorker | None = None
        self._segmentation_range_map: dict[str, tuple[int, int]] = {}
        self._seg_cache_path = Path(__file__).resolve().parent.parent / "output" / "log" / "segmentation.json"

        # ── 结果状态 ──
        self._correction_results: dict[int, str] = {}
        self._correction_pending: set = set()
        self._filtered_count: int = 0

        # ── 批量状态 ──
        self._batch_files: list[str] = []

        # ── 串行状态（ASR → OCR） ──
        self._pending_vp: str = ""
        self._pending_regions: list = []
        self._pending_ename: str = ""

        # ── UI 访问器（通过 configure() 注入） ──
        self._get_video_path: Callable[[], str | None] = lambda: None
        self._get_is_image: Callable[[], bool] = lambda: False
        self._get_regions: Callable[[], list] = lambda: []
        self._get_batch_files: Callable[[], list] = lambda: []
        self._set_regions: Callable[[list], None] = lambda r: None
        self._get_time_range: Callable[[], tuple] = lambda: (0, 0)
        self._get_current_frame: Callable[[], Any] = lambda: None
        self._get_roi_image: Callable[[int], Any] = lambda ri: None
        self._get_current_engine: Callable[[], str] = lambda: "paddleocr"
        self._get_current_template: Callable[[], str] = lambda: ""
        self._get_custom_prompt: Callable[[], str] = lambda: ""
        self._get_config_prompt: Callable[[], str] = lambda: ""
        self._get_mode_params: Callable[[], dict] = lambda: {}
        self._get_is_audio_file: Callable[[], bool] = lambda: False
        self._add_result: Callable = lambda ts, ts_str, rname, ename, raw, end_sec=0.0: 0
        self._get_results: Callable[[], list] = lambda: []
        self._update_correction_cell: Callable[[int, str], None] = lambda row, text: None
        self._clear_results_table: Callable[[], None] = lambda: None
        self._clear_results_by_type: Callable[[str, str], None] = lambda rgn, eng: None
        self._sort_results_table: Callable[[str], None] = lambda _: None
        self._get_polished_results: Callable[[float, int, bool], list] = lambda sim, ml, dedup=True: []
        self._get_table_row_count: Callable[[], int] = lambda: 0

    # ═══════════════════════════════════════════════════════════════
    # 依赖注入
    # ═══════════════════════════════════════════════════════════════

    def configure(self, **kwargs):
        """一次性注入所有依赖和 UI 访问器。"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            elif hasattr(self, f"_{key}"):
                setattr(self, f"_{key}", value)

    # ═══════════════════════════════════════════════════════════════
    # 快捷 UI 操作
    # ═══════════════════════════════════════════════════════════════

    def _set_buttons(self, **states):
        """发射按钮状态信号。"""
        self.buttons_enabled.emit(states)

    def _show_error(self, title: str, message: str):
        self.error_dialog.emit(title, message)

    def _show_info(self, title: str, message: str):
        self.info_dialog.emit(title, message)

    # ═══════════════════════════════════════════════════════════════
    # 结果过滤
    # ═══════════════════════════════════════════════════════════════

    def _matches_filter(self, raw: str) -> bool:
        """检查文本是否匹配过滤器关键词。"""
        if self._filter_mgr:
            return self._filter_mgr.matches(raw)
        return False

    # ═══════════════════════════════════════════════════════════════
    # 配置热加载
    # ═══════════════════════════════════════════════════════════════

    def _reload_all_config(self):
        """每次操作前从 JSON 文件重新读取所有配置。"""
        if self._corrector and hasattr(self._corrector, 'reload_config'):
            self._corrector.reload_config()
        if self._asr_mgr and hasattr(self._asr_mgr, 'reload_config'):
            self._asr_mgr.reload_config()
        if self._engine_mgr and hasattr(self._engine_mgr, 'reload_config'):
            self._engine_mgr.reload_config()
        if self._config_mgr and hasattr(self._config_mgr, 'reload'):
            self._config_mgr.reload()

    # ═══════════════════════════════════════════════════════════════
    # 单文件处理入口
    # ═══════════════════════════════════════════════════════════════

    def start_processing(self):
        """单文件处理入口（对应 MainWindow._on_start_processing）。"""
        self._reload_all_config()
        vp = self._get_video_path()
        if not vp:
            self._show_error("提示", "请加载视频或图片文件。")
            return
        logger.info("开始处理: %s", Path(vp).name)

        mode_params = self._get_mode_params()
        mode = mode_params.get("process_mode", MODE_OCR_ASR_FULL)
        logger.info("处理模式: %s", mode)
        is_audio = self._get_is_audio_file()

        # 音频文件只能 ASR
        if is_audio and mode != MODE_ASR_ONLY:
            mode = MODE_ASR_ONLY

        if mode == MODE_ASR_ONLY:
            self._start_asr_only()
            return

        # OCR 类模式
        if self._get_is_image():
            self._start_ocr_only()
            return

        # 视频 OCR 流程
        regions = [r for r in self._get_regions() if r.get("enabled", True)]
        if not regions:
            regions = [{
                "name": "全帧", "x": 0, "y": 0, "w": 0, "h": 0,
                "engine": self._get_current_engine(),
                "prompt": self._get_custom_prompt() or self._get_config_prompt(),
                "prompt_template": self._get_current_template(),
                "enabled": True,
            }]
            self._set_regions(regions)

        self._correction_results.clear()
        self._correction_pending.clear()

        if mode == MODE_OCR_ONLY:
            self._pending_vp = vp
            self._pending_regions = regions
            self._pending_ename = self._get_current_engine()
            self._do_ocr_pass(vp)
        else:  # MODE_OCR_ASR_FULL
            self._process_video(vp, regions)

        self._set_buttons(start=False, correction=False, correction_all=False, pause=True, stop=True)
        self.progress_val.emit(0)
        self.status_msg.emit("处理中...")

    # ═══════════════════════════════════════════════════════════════
    # ASR only
    # ═══════════════════════════════════════════════════════════════

    def _start_asr_only(self):
        vp = self._get_video_path()
        if not vp:
            self._show_error("提示", "请先加载视频或音频文件。")
            return
        is_audio = self._get_is_audio_file()

        # 🔥 仅清除 ASR 结果
        asr_region = self._get_mode_params().get("asr_region_name", "语音")
        self._clear_results_by_type(asr_region, ENGINE_WHISPERX)
        self._correction_results.clear()
        self._correction_pending.clear()

        self._pending_vp = vp
        self._pending_regions = []
        self._pending_ename = self._get_current_engine()

        asr_engine = self._asr_mgr.get_engine() if self._asr_mgr else None
        if not asr_engine:
            self._show_error("提示", "未检测到 ASR 引擎，请先在「语音识别」标签页启用。")
            return

        mp = self._get_mode_params()
        region_name = mp.get("asr_region_name", "语音")
        t_start, t_end = self._get_time_range()

        self._audio_worker = AudioProcessWorker(
            asr_engine, vp, is_video=not is_audio,
            time_start=t_start, time_end=t_end,
            asr_region_name=region_name,
        )
        self._audio_worker.progress.connect(lambda m: self.status_msg.emit(m))
        self._audio_worker.result_item.connect(self._on_asr_result)
        self._audio_worker.finished_all.connect(self._on_asr_finished)
        self._audio_worker.error.connect(self._on_asr_error)
        self._audio_worker.start()

        self._set_buttons(start=False, correction=False, pause=True, stop=True)
        self.progress_val.emit(0)
        self.status_msg.emit("语音识别中...")

    # ═══════════════════════════════════════════════════════════════
    # OCR only
    # ═══════════════════════════════════════════════════════════════

    def _start_ocr_only(self):
        vp = self._get_video_path()
        if not vp:
            self._show_error("提示", "请先加载视频或图片文件。")
            return

        regions = [r for r in self._get_regions() if r.get("enabled", True)]
        if not regions:
            regions = [{
                "name": "全帧", "x": 0, "y": 0, "w": 0, "h": 0,
                "engine": self._get_current_engine(),
                "prompt": self._get_custom_prompt() or self._get_config_prompt(),
                "prompt_template": self._get_current_template(),
                "enabled": True,
            }]
            self._set_regions(regions)

        # 🔥 仅清除 OCR 区域结果
        for r in regions:
            self._clear_results_by_type(r.get("name", ""), r.get("engine", ""))
        self._correction_results.clear()
        self._correction_pending.clear()

        self._pending_vp = vp
        self._pending_regions = regions
        self._pending_ename = self._get_current_engine()

        if self._get_is_image():
            self._process_image(regions)
        else:
            self._do_ocr_pass(vp)

        self._set_buttons(start=False, correction=False, pause=True, stop=True)
        self.progress_val.emit(0)
        self.status_msg.emit("OCR 处理中...")

    # ═══════════════════════════════════════════════════════════════
    # 视频处理（ASR + OCR 串行）
    # ═══════════════════════════════════════════════════════════════

    def _process_video(self, vp: str, regions: list):
        self._pending_vp = vp
        self._pending_regions = regions
        self._pending_ename = self._get_current_engine()

        mp = self._get_mode_params()
        mode = mp.get("process_mode", "OCR + ASR（完整流程）")
        asr_enabled = mp.get("asr_enabled", False)
        asr_region_name = mp.get("asr_region_name", "语音")

        # 完整流程模式 → ASR 默认启用（UI 复选框仅对"仅OCR"/"仅ASR"分组生效）
        if mode == "OCR + ASR（完整流程）":
            asr_enabled = True

        # 判断是否有 OCR 区域（排除纯 ASR 区域）
        has_ocr_regions = any(
            r.get("name", "") != asr_region_name
            for r in regions
        )

        # 根据模式和实际可用性决定流程
        do_asr = False
        do_ocr = False

        if mode == "仅语音识别 (ASR)":
            do_asr = True
        elif mode == "仅 OCR":
            do_ocr = True
        elif mode == "OCR + ASR（完整流程）":
            do_asr = asr_enabled
            do_ocr = has_ocr_regions

        # 执行流程
        if do_asr and do_ocr:
            logger.info("流程: ASR + OCR 串行")
            self._clear_results_by_type(asr_region_name, ENGINE_WHISPERX)
            for r in regions:
                self._clear_results_by_type(r.get("name", ""), r.get("engine", ""))
            self._start_asr_worker(vp)
        elif do_asr and not do_ocr:
            logger.info("流程: 仅 ASR")
            self._clear_results_by_type(asr_region_name, ENGINE_WHISPERX)
            self._start_asr_worker(vp)
        elif not do_asr and do_ocr:
            logger.info("流程: 仅 OCR")
            for r in regions:
                self._clear_results_by_type(r.get("name", ""), r.get("engine", ""))
            self._do_ocr_pass(vp)
        else:
            self._show_error("提示", "未启用任何处理（请启用 ASR 或定义 OCR 区域）")
            self._set_buttons(start=True, stop=False)

    def _do_ocr_pass(self, vp: str):
        """启动 OCR 视频处理线程。"""
        regions = self._pending_regions
        ename = self._pending_ename
        self._frame_processor = FrameProcessor(
            engine_manager=self._engine_mgr, regions=regions
        )

        mp = self._get_mode_params()
        if mp:
            fp = self._frame_processor
            fp._subtitle_mode = mp.get("subtitle_mode", "流式字幕（去重）")
            fp._sentinel_enabled = mp.get("sentinel_enabled", True)
            fp._s_drop_ratio = mp.get("s_drop_ratio", 0.5)
            fp._s_buffer_size = mp.get("s_buffer_size", 8)
            fp._s_sim_threshold = mp.get("s_sim_threshold", 0.85)
            fp._s_min_text_len = mp.get("s_min_text_len", 2)
            fp._s_filter_keywords = mp.get("s_filter_keywords", "")
            fp._s_ocr_version = mp.get("s_ocr_version", "")
            fp._r_dedup = mp.get("r_dedup", True)
            fp._r_sim_threshold = mp.get("r_sim_threshold", 0.9)
            fp._r_buffer_size = mp.get("r_buffer_size", 5)
            fp._r_min_text_len = mp.get("r_min_text_len", 2)
            fp._r_filter_keywords = mp.get("r_filter_keywords", "")
            fp._r_interval = mp.get("r_interval", 2.0)
            fp._frame_interval = mp.get("frame_interval", 0.1)

        t_start, t_end = self._get_time_range()
        self._video_worker = VideoProcessWorker(
            self._frame_processor, vp, ename,
            time_start=t_start, time_end=t_end,
        )
        self._video_worker.log.connect(lambda m: self.status_msg.emit(m))
        self._video_worker.progress.connect(self._on_process_progress)
        self._video_worker.result_item.connect(self._on_process_result)
        self._video_worker.finished_all.connect(self._on_process_finished)
        self._video_worker.error.connect(self._on_process_error)
        self._video_worker.start()
        self.status_msg.emit("OCR 处理中...")

    def _start_asr_worker(self, video_path: str):
        mp = self._get_mode_params()
        asr_engine = self._asr_mgr.get_engine() if self._asr_mgr else None
        if not asr_engine:
            self.status_msg.emit("⚠ ASR 引擎未加载")
            return

        region_name = mp.get("asr_region_name", "语音")
        t_start, t_end = self._get_time_range()
        self._audio_worker = AudioProcessWorker(
            asr_engine, video_path, is_video=True,
            time_start=t_start, time_end=t_end,
            asr_region_name=region_name,
        )
        self._audio_worker.progress.connect(lambda m: self.status_msg.emit(m))
        self._audio_worker.result_item.connect(self._on_asr_result)
        self._audio_worker.finished_all.connect(self._on_asr_finished)
        self._audio_worker.error.connect(self._on_asr_error)
        self._audio_worker.start()
        self.status_msg.emit("语音识别中...")

    # ═══════════════════════════════════════════════════════════════
    # ASR 回调
    # ═══════════════════════════════════════════════════════════════

    def _on_asr_result(self, ts, t_str, rname, ename, raw, end_sec: float = 0.0):
        if self._matches_filter(raw):
            self._filtered_count += 1
            return
        self.result_row.emit(ts, t_str, rname, ename, raw, 0.0, end_sec)

    def _on_asr_finished(self, results):
        n = len(results)
        ocr_regions = [r for r in self._pending_regions if r.get("enabled", True)]
        if ocr_regions:
            self.status_msg.emit(f"✅ 语音识别完成: {n} 段，开始 OCR...")
            self._do_ocr_pass(self._pending_vp)
        else:
            self.status_msg.emit(f"✅ 语音识别完成: {n} 段")
            # 延迟触发完成
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._on_process_finished([]))

    def _on_asr_error(self, err):
        ocr_regions = [r for r in self._pending_regions if r.get("enabled", True)]
        if ocr_regions:
            self.status_msg.emit(f"⚠ 语音识别失败: {err}，继续 OCR...")
            self._do_ocr_pass(self._pending_vp)
        else:
            self.status_msg.emit(f"⚠ 语音识别失败: {err}")
            self._set_buttons(start=True, stop=False)

    # ═══════════════════════════════════════════════════════════════
    # 图片处理
    # ═══════════════════════════════════════════════════════════════

    def _process_image(self, regions: list):
        frame = self._get_current_frame()
        if frame is None:
            self._show_error("提示", "没有可处理的图片帧。")
            self._set_buttons(start=True, stop=False)
            self.progress_val.emit(0)
            return

        self._image_worker = ImageProcessWorker(self._engine_mgr, frame, regions)
        self._image_worker.result_item.connect(self._on_process_result)
        self._image_worker.finished_all.connect(self._on_process_finished)
        self._image_worker.error.connect(self._on_process_error)
        self._image_worker.start()

    # ═══════════════════════════════════════════════════════════════
    # OCR 回调
    # ═══════════════════════════════════════════════════════════════

    def _on_process_result(self, ts, t_str, rname, ename, raw, conf: float = 0.0):
        if self._matches_filter(raw):
            self._filtered_count += 1
            return
        self.result_row.emit(ts, t_str, rname, ename, raw, conf, 0.0)

    def _on_process_progress(self, cur, total, qs, sentinel):
        if total > 0:
            self.progress_val.emit(min(100, int(cur * 100 / total)))
        m1, s1 = divmod(int(cur), 60)
        m2, s2 = divmod(int(total), 60)
        self.time_display.emit(f" {m1:02d}:{s1:02d} / {m2:02d}:{s2:02d} ")
        self.status_msg.emit(f"处理中... {cur}s / {total}s | 哨兵: {sentinel}")

    def _on_process_finished(self, _):
        self._set_buttons(start=True, stop=False, correction=True, correction_all=True, pause=False)
        self.progress_val.emit(0)

        # 排序
        mp = self._get_mode_params()
        region_order = mp.get("region_order", "")
        if not self._get_is_image():
            self._sort_results_table(region_order)

        # 通知 MainWindow 做后处理（end_sec 回填等）
        self.process_finished.emit()

        n = self._get_table_row_count()
        msg = f"✅ 处理完成: {n} 条结果"
        if self._filtered_count > 0:
            msg += f" | 过滤: {self._filtered_count} 条"
        self._filtered_count = 0

        # 全量 AI 纠错
        corr_enabled = mp.get("corr_enabled", False)
        if corr_enabled and n > 0:
            self._run_full_correction()
            self.status_msg.emit(f"{msg} | 全量 AI 纠错中...")
        else:
            # 自动 LLM 分句（仅在未启用全量纠错时触发，避免冲突）
            seg_enabled = self._corrector and self._corrector.sentence_segmentation_enabled
            if seg_enabled and n > 0:
                self.segment_sentences()
                self.status_msg.emit(f"{msg} | LLM 分句中...")
            else:
                self.status_msg.emit(msg)

    def _on_process_error(self, err):
        self._set_buttons(start=True, stop=False, correction=True, correction_all=True, pause=False)
        self.progress_val.emit(0)
        self.status_msg.emit(f"❌ 处理失败: {err}")
        is_batch = self._batch_worker and self._batch_worker.isRunning()
        if not is_batch:
            self._show_error("处理错误", f"处理失败:\n{err}")

    # ═══════════════════════════════════════════════════════════════
    # 停止处理
    # ═══════════════════════════════════════════════════════════════

    def stop_processing(self):
        """停止所有正在进行的处理（OCR / ASR / 纠错 / 批量）。"""
        logger.info("停止处理")

        # 阻止分句批处理链继续
        self._seg_stop_requested = True

        # 视频帧处理器（sentinel / OCR 循环）
        if self._frame_processor:
            self._frame_processor.stop()

        # 各独立 worker
        workers = [
            ("视频", self._video_worker),
            ("图片", self._image_worker),
            ("音频", self._audio_worker),
            ("批量", self._batch_worker),
            ("批量纠错", self._batch_correction_worker),
            ("分句", self._segmentation_worker),
        ]
        for name, w in workers:
            if w and w.isRunning():
                if hasattr(w, 'stop'):
                    w.stop()
                else:
                    w.quit()
                logger.info("已停止: %s", name)

        # 单个 AI 纠错线程
        with self._correction_workers_lock:
            for w in self._correction_workers:
                if w.isRunning():
                    if hasattr(w, 'stop'):
                        w.stop()
                    else:
                        w.quit()

        self.status_msg.emit("已停止")
        self._set_buttons(start=True, stop=False, correction=True, correction_all=True)
        self.progress_val.emit(0)

    def pause_processing(self):
        """暂停当前处理（视频 OCR / 音频 ASR / 批量）。"""
        logger.info("暂停处理")
        paused = False
        if self._frame_processor and hasattr(self._frame_processor, '_pause_flag'):
            self._frame_processor.pause()
            paused = True
        if self._video_worker and self._video_worker.isRunning():
            if hasattr(self._video_worker, 'pause'):
                self._video_worker.pause()
                paused = True
        if self._audio_worker and self._audio_worker.isRunning():
            if hasattr(self._audio_worker, 'pause'):
                self._audio_worker.pause()
                paused = True
        if self._batch_worker and self._batch_worker.isRunning():
            if hasattr(self._batch_worker, 'pause'):
                self._batch_worker.pause()
                paused = True
        if paused:
            self.status_msg.emit("已暂停")
        else:
            self.status_msg.emit("当前无正在运行的任务")

    def resume_processing(self):
        """继续当前处理。"""
        logger.info("继续处理")
        resumed = False
        if self._frame_processor and hasattr(self._frame_processor, '_pause_flag'):
            self._frame_processor.resume()
            resumed = True
        if self._video_worker and self._video_worker.isRunning():
            if hasattr(self._video_worker, 'resume'):
                self._video_worker.resume()
                resumed = True
        if self._audio_worker and self._audio_worker.isRunning():
            if hasattr(self._audio_worker, 'resume'):
                self._audio_worker.resume()
                resumed = True
        if self._batch_worker and self._batch_worker.isRunning():
            if hasattr(self._batch_worker, 'resume'):
                self._batch_worker.resume()
                resumed = True
        if resumed:
            self.status_msg.emit("继续处理")
        else:
            self.status_msg.emit("当前无暂停的任务")

    # ═══════════════════════════════════════════════════════════════
    # AI 纠错 —— 选中行
    # ═══════════════════════════════════════════════════════════════

    def correct_selected(self, selected_rows: set):
        """对选中的表格行进行批量 AI 纠错（一次性发送所有选中条目）。"""
        self._reload_all_config()
        if not selected_rows:
            self._show_error("提示", "请先在表格中选中需要纠错的行（可多选）。")
            return
        logger.info("批量纠错选中: %d 行", len(selected_rows))

        # 构建选中行的 texts 列表（有分句则仅取分句文本）
        results = self._get_results()
        has_segmented = any(r.get("segmented", "").strip() for r in results)
        texts = []
        for row in sorted(selected_rows):
            if row < len(results):
                r = results[row]
                if has_segmented:
                    raw = r.get("segmented", "").strip()
                    if not raw:
                        continue
                else:
                    raw = r.get("raw", "")
                if raw.strip():
                    ts = r.get("time_sec", 0.0) or 0.0
                    te = r.get("end_sec", 0.0) or 0.0
                    texts.append((row, raw, ts, te))

        if not texts:
            self.status_msg.emit("⚠ 选中的行无有效文本可纠错")
            return

        mp = self._get_mode_params()
        self._sync_corrector_modes(mp)
        self._maybe_extract_env(results, mp)

        context_window = mp.get("corr_context_window", 3)
        max_retries = mp.get("corr_retry", 3)
        batch_size = mp.get("corr_batch_size", 5)

        total_batches = (len(texts) + batch_size - 1) // batch_size
        self._set_buttons(correction_all=False, correction=False)
        self._total_correction_batches = total_batches
        self._is_auto_correction = False

        raw_reference = [r.get("raw", "") for r in results if r.get("raw", "").strip()] if has_segmented else None
        self._submit_correction_batch(texts, batch_size, 0, context_window, max_retries,
                                       total_batches, raw_reference=raw_reference)
        self.status_msg.emit(f"已提交 {len(texts)} 条批量纠错 [{total_batches} 批]")

    # ═══════════════════════════════════════════════════════════════
    # AI 纠错 —— 全部
    # ═══════════════════════════════════════════════════════════════

    def correct_all(self):
        """对全部结果行进行 AI 纠错（使用 BatchCorrectionWorker，按 batch_size 分批）。"""
        self._reload_all_config()
        results = self._get_results()
        if not results:
            self._show_error("提示", "暂无识别结果可纠错。")
            return

        self._start_batch_correction(results, is_auto=False)

    def _build_correction_texts(self, results: list, prefer_segmented: bool = False) -> list:
        """从结果列表提取有效文本条目。

        Args:
            results: 结果列表
            prefer_segmented: True=仅提取分句文本（已分句时使用），False=使用原始文本
        """
        texts = []
        _wlog(f"=== _build_correction_texts prefer_segmented={prefer_segmented} total_rows={len(results)} ===")
        for row, r in enumerate(results):
            if prefer_segmented:
                raw = r.get("segmented", "").strip()
                if not raw:
                    _wlog(f"  row[{row}] SKIP (no segmented)")
                    continue
            else:
                raw = r.get("raw", "")
            if raw.strip():
                ts = r.get("time_sec", 0.0) or 0.0
                te = r.get("end_sec", 0.0) or 0.0
                texts.append((row, raw, ts, te))
                _wlog(f"  row[{row}] INCLUDE text={raw[:40]} ts={ts:.3f}-{te:.3f}")
        _wlog(f"  → {len(texts)} texts extracted")
        return texts

    def _sync_corrector_modes(self, mp: dict):
        """同步 UI 模式到 corrector 实例。"""
        if self._corrector:
            self._corrector.translate_mode = mp.get("corr_translate", False)
            self._corrector.stream_mode = mp.get("corr_stream", False)
            self._corrector.json_mode = mp.get("corr_json", False)

    def _maybe_extract_env(self, results: list, mp: dict):
        """如果配置启用，提取全文环境上下文（后台线程，不阻塞 UI）。"""
        if mp.get("corr_extract_env", False) and self._corrector:
            self.status_msg.emit("⏳ AI 纠错: 提取全文环境中...")
            all_texts = [r.get("raw", "") for r in results if r.get("raw", "").strip()]
            if all_texts:
                import threading
                threading.Thread(
                    target=lambda: self._corrector.extract_environment(all_texts),
                    daemon=True,
                ).start()

    def _start_batch_correction(self, results: list, is_auto: bool = False):
        """内部：启动批量纠错（自动全量或手动全量）。

        Args:
            results: 结果列表
            is_auto: True=处理完成后自动触发, False=手动点击"纠正全部"
        """
        mp = self._get_mode_params()
        self._sync_corrector_modes(mp)
        self._maybe_extract_env(results, mp)

        texts = self._build_correction_texts(results, prefer_segmented=True)
        if not texts:
            self.status_msg.emit(f"✅ 完成: {len(results)} 条结果 | 无有效文本可纠错")
            return

        # 构建原文参考（给 LLM 对照用）
        raw_reference = [r.get("raw", "") for r in results if r.get("raw", "").strip()]

        context_window = mp.get("corr_context_window", 3)
        max_retries = mp.get("corr_retry", 3)
        batch_size = mp.get("corr_batch_size", 5)

        # 按 batch_size 分批提交
        total_batches = (len(texts) + batch_size - 1) // batch_size
        self._set_buttons(correction_all=False, correction=False)
        self._total_correction_batches = total_batches
        self._is_auto_correction = is_auto

        self._submit_correction_batch(texts, batch_size, 0, context_window, max_retries,
                                       total_batches, raw_reference=raw_reference)

    def _submit_correction_batch(self, texts: list, batch_size: int, offset: int,
                                  context_window: int, max_retries: int,
                                  total_batches: int = 1,
                                  raw_reference: list[str] | None = None):
        """递归分批提交批量纠错。"""
        batch_num = offset // batch_size + 1
        # 🔥 实时显示批次进度
        self.status_msg.emit(f"⏳ AI 纠错: 正在批量纠错 [{batch_num}/{total_batches}] 批...")
        batch = texts[offset:offset + batch_size]

        self._batch_correction_worker = BatchCorrectionWorker(
            self._corrector, batch,
            context_window=context_window,
            max_retries=max_retries,
            raw_reference=raw_reference,
        )
        self._batch_correction_worker.correction_ready.connect(self._on_correction_ready)

        # 判断是否是最后一批
        is_last_batch = (offset + batch_size >= len(texts))
        if is_last_batch:
            if self._is_auto_correction:
                self._batch_correction_worker.batch_finished.connect(self._on_full_correction_finished)
            else:
                self._batch_correction_worker.batch_finished.connect(self._on_batch_correction_finished)
        else:
            self._batch_correction_worker.batch_finished.connect(
                lambda: self._submit_correction_batch(
                    texts, batch_size, offset + batch_size, context_window, max_retries,
                    total_batches, raw_reference=raw_reference))

        self._batch_correction_worker.batch_error.connect(self._on_batch_correction_error)
        self._batch_correction_worker.start()

    def _run_full_correction(self):
        """处理完成后全量提交所有结果进行 AI 纠错。"""
        results = self._get_results()
        self._start_batch_correction(results, is_auto=True)

    def _on_full_correction_finished(self):
        """全量纠错（流程结束后自动触发）全部完成。"""
        n = self._get_table_row_count()
        self.status_msg.emit(f"✅ 完成: {n} 条结果 | 全量纠错完成")
        self._batch_correction_worker = None

    def _on_batch_correction_finished(self):
        """批量纠错完成（来自 correct_all）。"""
        self._set_buttons(correction_all=True, correction=True)
        n = self._get_table_row_count()
        self.status_msg.emit(f"✅ 完成: {n} 条结果 | 批量纠错完成")
        self._batch_correction_worker = None

    def _on_batch_correction_error(self, err):
        """批量纠错出错。"""
        self._set_buttons(correction_all=True, correction=True)
        self.status_msg.emit(f"⚠ 批量纠错出错: {err}")
        self._batch_correction_worker = None

    # ═══════════════════════════════════════════════════════════════
    # LLM 分句
    # ═══════════════════════════════════════════════════════════════

    def segment_sentences(self):
        """对当前全部结果进行 LLM 语义分句（分批提交）。"""
        self._reload_all_config()
        self._seg_stop_requested = False
        results = self._get_results()
        if not results:
            self._show_error("提示", "暂无识别结果可分句。")
            return

        texts = self._build_correction_texts(results)
        if not texts:
            self.status_msg.emit("⚠ 无有效文本可分句")
            return

        mp = self._get_mode_params()
        batch_size = mp.get("corr_batch_size", 5)
        max_retries = mp.get("corr_retry", 3)

        self._seg_texts = texts
        self._seg_batch_size = batch_size
        self._seg_max_retries = max_retries
        self._seg_offset = 0
        self._seg_merged_range_map: dict[str, tuple[int, int]] = {}
        self._seg_total_batches = (len(texts) + batch_size - 1) // batch_size
        self._seg_cache_path = Path(__file__).resolve().parent.parent / "output" / "log" / "segmentation.json"

        # ── P4: 断点续跑 ──
        if self._seg_cache_path.exists():
            try:
                with open(self._seg_cache_path, encoding="utf-8") as f:
                    cache = json.load(f)
                cached_offset = cache.get("offset", 0)
                cached_map = cache.get("range_map", {})
                if cached_offset > 0 and cached_map:
                    self._seg_offset = cached_offset
                    self._seg_merged_range_map = cached_map
                    logger.info("分句缓存恢复: 已完成 %d 条, %d 句",
                                cached_offset, len(cached_map))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("分句缓存读取失败: %s", e)

        self._set_buttons(correction_all=False, correction=False)
        self._submit_segmentation_batch()

    def _submit_segmentation_batch(self):
        """提交一批分句任务。"""
        texts = self._seg_texts
        offset = self._seg_offset
        batch = texts[offset:offset + self._seg_batch_size]
        batch_num = offset // self._seg_batch_size + 1
        total = self._seg_total_batches

        self.status_msg.emit(f"⏳ LLM 分句 [{batch_num}/{total}] 批...")
        self._segmentation_worker = SegmentationWorker(
            self._corrector, batch, max_retries=self._seg_max_retries,
        )
        self._segmentation_worker.segmentation_ready.connect(self._on_segmentation_ready)
        self._segmentation_worker.finished_all.connect(self._on_segmentation_batch_done)
        self._segmentation_worker.error.connect(self._on_segmentation_error)
        self._segmentation_worker.start()

    def _on_segmentation_batch_done(self, batch_range_map: dict):
        """一批分句完成，合并 range_map 并启动下一批。"""
        offset = self._seg_offset
        # range 索引从 batch-relative 转为全局 table row index
        for seg_text, (start, end) in batch_range_map.items():
            global_start = self._seg_texts[offset + start][0]
            global_end = self._seg_texts[offset + end][0]
            self._seg_merged_range_map[seg_text] = (global_start, global_end)

        self._seg_offset += self._seg_batch_size

        # ── P4: 保存断点缓存 ──
        try:
            cache = {
                "offset": self._seg_offset,
                "range_map": self._seg_merged_range_map,
            }
            self._seg_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._seg_cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("分句缓存写入失败: %s", e)

        if self._seg_offset < len(self._seg_texts) and not getattr(self, '_seg_stop_requested', False):
            self._submit_segmentation_batch()
        else:
            self._on_segmentation_finished(self._seg_merged_range_map)

    def _on_segmentation_ready(self, row: int, segmented_text: str):
        """分句结果逐行更新。"""
        self.segmentation_updated.emit(row, segmented_text)

    def _on_segmentation_finished(self, range_map: dict):
        """分句完成。"""
        self._segmentation_range_map = range_map
        self._set_buttons(correction_all=True, correction=True)
        self._segmentation_worker = None

        # ── P4: 清理分句缓存 ──
        try:
            if self._seg_cache_path.exists():
                self._seg_cache_path.unlink()
        except OSError:
            pass
        n = self._get_table_row_count()
        merged_count = len(range_map)
        if merged_count:
            self.status_msg.emit(f"✅ 分句完成: {n} 条碎片 → {merged_count} 句")
        else:
            self.status_msg.emit(f"✅ 分句完成: {n} 条结果")

    def _on_segmentation_error(self, err: str):
        """分句出错。"""
        self._set_buttons(correction_all=True, correction=True)
        self.status_msg.emit(f"⚠ 分句失败: {err}")
        self._segmentation_worker = None

    # ═══════════════════════════════════════════════════════════════
    # AI 纠错 —— 单条提交
    # ═══════════════════════════════════════════════════════════════

    def _submit_correction(self, row, raw, region_corr_prompt: str = ""):
        mp = self._get_mode_params()
        ctx_window = mp.get("corr_context_window", 3)
        results = self._get_results()

        ctx = []
        for i in range(max(0, row - ctx_window), min(len(results), row + ctx_window + 1)):
            if i != row:
                ctx.append(results[i].get("segmented", "").strip() or results[i].get("raw", ""))

        # 获取 ROI 图像（本地引擎纠错需要）
        image = None
        if self._corrector and self._corrector._is_local_engine():
            result_region_name = results[row].get("region", "") if row < len(results) else ""
            regions = self._get_regions()
            for ri, r in enumerate(regions):
                if r.get("name", "") == result_region_name:
                    try:
                        image = self._get_roi_image(ri)
                    except Exception as e:
                        logger.warning("获取 ROI 图片失败: %s", e)
                        image = None
                    break

        w = AICorrectionWorker(
            self._corrector, row, raw, ctx, image=image,
            region_correction_prompt=region_corr_prompt,
        )
        w.correction_ready.connect(self._on_correction_ready)
        w.correction_failed.connect(self._on_correction_failed)
        w.correction_stream.connect(self._on_correction_stream)
        # 线程安全的 worker 列表管理
        w.finished.connect(lambda ww=w: self._remove_correction_worker(ww))
        with self._correction_workers_lock:
            self._correction_workers.append(w)
        w.start()

    def _remove_correction_worker(self, w):
        with self._correction_workers_lock:
            if w in self._correction_workers:
                self._correction_workers.remove(w)

    def _on_correction_ready(self, row, raw, corrected):
        self.correction_updated.emit(row, raw, corrected)

    def _on_correction_failed(self, row, _):
        self._correction_pending.discard(row)

    def _on_correction_stream(self, row, partial_text):
        """流式增量更新 —— 实时更新表格中的纠错文本。"""
        self.correction_stream_updated.emit(row, partial_text)

    # ═══════════════════════════════════════════════════════════════
    # 批量文件处理
    # ═══════════════════════════════════════════════════════════════

    def add_batch_files(self, files: list):
        self._batch_files.extend(files)

    def get_batch_file_count(self) -> int:
        return len(self._batch_files)

    def clear_batch_files(self):
        self._batch_files.clear()

    def start_batch(self):
        """启动批量处理。"""
        batch_files = self._get_batch_files()
        if not batch_files:
            self._show_error("提示", "批量队列为空，请先添加文件。")
            return
        logger.info("批量处理: %d 个文件", len(batch_files))

        regions = [r for r in self._get_regions() if r.get("enabled", True)]
        if not regions:
            self._show_error("提示", "没有启用的区域，请先在预览图上定义区域。")
            return

        output_dir = Path(__file__).resolve().parent.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        # 批量处理：清除所有 OCR 区域 + ASR 区域结果
        asr_region = self._get_mode_params().get("asr_region_name", "语音")
        self._clear_results_by_type(asr_region, ENGINE_WHISPERX)
        for r in regions:
            self._clear_results_by_type(r.get("name", ""), r.get("engine", ""))
        self._correction_results.clear()
        self._correction_pending.clear()

        self._set_buttons(start=False, stop=True, correction=False)
        self.progress_val.emit(0)

        self._batch_worker = BatchProcessWorker(
            engine_manager=self._engine_mgr,
            file_list=list(batch_files),
            regions=regions,
            mode_params=self._get_mode_params(),
            output_dir=str(output_dir),
            corrector=self._corrector,
        )
        self._batch_worker.progress_file.connect(self._on_batch_progress_file)
        self._batch_worker.log.connect(lambda m: self.status_msg.emit(m))
        self._batch_worker.result_item.connect(self._on_process_result)
        self._batch_worker.finished_one.connect(self._on_batch_finished_one)
        self._batch_worker.finished_all.connect(self._on_batch_finished_all)
        self._batch_worker.error.connect(self._on_process_error)
        self._batch_worker.start()

    def _on_batch_progress_file(self, fname: str, idx: int, total: int):
        self.progress_val.emit(int(idx * 100 / total))
        self.status_msg.emit(f"批量处理 [{idx}/{total}]: {fname}")

    def _on_batch_finished_one(self, file_path: str, results: list):
        self.status_msg.emit(f"✅ 完成: {Path(file_path).name} ({len(results)} 条)")

    def _on_batch_finished_all(self, _=None):
        self._set_buttons(start=True, stop=False, correction=True)
        self.progress_val.emit(0)
        n = self.get_batch_file_count()
        self.status_msg.emit(f"✅ 批量处理完成: {n} 个文件 → output/")
        self._batch_files.clear()
        self.batch_all_done.emit()

    # ═══════════════════════════════════════════════════════════════
    # 导出
    # ═══════════════════════════════════════════════════════════════

    def export(self, fmt: str, path: str) -> tuple:
        """导出结果。返回 (polished, cmap) 或通过异常抛出。"""
        self._reload_all_config()
        mp = self._get_mode_params()
        results = self._get_results()

        # 构建分句映射 {row_index: segmented_text}
        smap: dict[int, str] = {}
        _wlog(f"=== export ({fmt}) ===  results count={len(results)}")
        for i, r in enumerate(results):
            seg = r.get("segmented", "").strip()
            if seg:
                smap[i] = seg
                _wlog(f"  smap[{i}] = {seg[:40]}")
            raw = r.get("raw", "")[:40]
            _wlog(f"  row[{i}] raw={raw} seg={seg[:40]} time={r.get('time_sec',0):.3f}-{r.get('end_sec',0):.3f}")

        # 从 range_map 计算精确时间轴: {segmented_text: (start_sec, end_sec)}
        seg_time_map: dict[str, tuple[float, float]] = {}
        _wlog(f"  _segmentation_range_map: {dict(self._segmentation_range_map)}")
        if self._segmentation_range_map:
            sub_dur = mp.get("subtitle_duration", 3.0)
            for seg_text, (start_idx, end_idx) in self._segmentation_range_map.items():
                if 0 <= start_idx < len(results) and 0 <= end_idx < len(results):
                    ts = results[start_idx].get("time_sec", 0.0) or 0.0
                    te = results[end_idx].get("end_sec", 0.0) or 0.0
                    if te <= ts:
                        te = ts + sub_dur
                    _wlog(f"  seg_time_map['{seg_text[:30]}'] = ({ts:.3f}, {te:.3f})  idx_range=[{start_idx},{end_idx}]")
                    seg_time_map[seg_text] = (ts, te)
                else:
                    _wlog(f"  SKIP range [{start_idx},{end_idx}] out of bounds (total={len(results)})")

        if fmt == "srt":
            sub_dur = mp.get("subtitle_duration", 3.0)
            polished = []
            for i, r in enumerate(results):
                ts = r.get("time_sec", 0.0) or 0.0
                end = r.get("end_sec", ts + sub_dur) or (ts + sub_dur)
                polished.append({
                    "time_sec": ts,
                    "end_sec": end,
                    "time": r.get("time", "--:--"),
                    "region": r.get("region", ""),
                    "engine": r.get("engine", ""),
                    "speaker": "NONE",
                    "content": r.get("raw", ""),
                    "raw": r.get("raw", ""),
                })
            cmap = {i: v for i, v in self._correction_results.items() if i < len(polished)}
        else:
            polished = self._get_polished_results(
                post_sim_dedup=mp.get("post_sim_dedup", True),
                post_sim_threshold=mp.get("post_sim_threshold", 0.9),
                post_min_text_len=mp.get("post_min_text_len", 2),
            )
            if not polished:
                return None, None
            cmap = {}
            if self._correction_results:
                for pi in range(min(len(polished), len(results))):
                    if pi in self._correction_results:
                        cmap[pi] = self._correction_results[pi]

        if not polished:
            self._show_info("提示", "无有效结果可导出。")
            return None, None

        srt_mode_map = {"仅纠正结果": "corrected", "仅原文": "original", "双语对照（原文+纠正）": "dual"}
        srt_mode = srt_mode_map.get(mp.get("srt_export_mode", "仅纠正结果"), "corrected")
        export_results(polished, path, fmt, bool(cmap), cmap,
                       keep_original=mp.get("export_keep_original", False),
                       srt_mode=srt_mode, segmented_map=smap,
                       seg_time_map=seg_time_map)
        self.status_msg.emit(f"✅ 已导出: {Path(path).name}")
        return polished, cmap

    # ═══════════════════════════════════════════════════════════════
    # 环境提取
    # ═══════════════════════════════════════════════════════════════

    def extract_environment(self, summary_prompt_setter: Callable[[str], None] | None = None):
        """手动提取全文环境。"""
        results = self._get_results()
        if not results:
            self._show_error("提示", "暂无识别结果可提取环境。")
            return None

        self.status_msg.emit("⏳ 正在提取全文环境...")
        all_texts = [r.get("raw", "") for r in results if r.get("raw", "").strip()]
        if not all_texts:
            self.status_msg.emit("⚠ 无有效文本可提取环境")
            return None

        if self._corrector:
            env = self._corrector.extract_environment(all_texts)
            if env:
                if summary_prompt_setter:
                    summary_prompt_setter(env)
                self.status_msg.emit("✅ 全文环境已提取")
            else:
                self.status_msg.emit("⚠ 环境提取失败，请检查 API 配置")
            return env
        return None

    # ═══════════════════════════════════════════════════════════════
    # 关闭清理
    # ═══════════════════════════════════════════════════════════════

    def cleanup(self):
        """快速清理所有线程 —— 立即 terminate，不阻塞 UI 关闭。"""
        try:
            logger.info("开始快速清理...")
        except UnicodeEncodeError:
            pass

        # 停止视频帧处理器（关闭 FFmpeg reader）
        if self._frame_processor:
            try:
                self._frame_processor.stop()
            except Exception as e:
                logger.warning("FrameProcessor 停止失败: %s", e)

        # 收集所有活跃的 worker（不等待，直接收集）
        workers = []
        if self._batch_correction_worker and self._batch_correction_worker.isRunning():
            workers.append(self._batch_correction_worker)
        with self._correction_workers_lock:
            workers.extend([w for w in self._correction_workers if w.isRunning()])
            self._correction_workers.clear()
        if self._video_worker and self._video_worker.isRunning():
            workers.append(self._video_worker)
        if self._audio_worker and self._audio_worker.isRunning():
            workers.append(self._audio_worker)
        if self._batch_worker and self._batch_worker.isRunning():
            workers.append(self._batch_worker)
        if self._segmentation_worker and self._segmentation_worker.isRunning():
            workers.append(self._segmentation_worker)
        if self._image_worker and self._image_worker.isRunning():
            workers.append(self._image_worker)

        # ── 第一步：对所有线程发 stop 信号 + 立即 terminate ──
        for w in workers:
            try:
                if hasattr(w, 'stop'):
                    w.stop()
            except Exception as e:
                logger.warning("工作线程清理异常: %s", e)
        # 给 100ms 让它们有机会自行退出
        for w in workers:
            try:
                w.wait(100)
            except Exception as e:
                logger.warning("工作线程清理异常: %s", e)
        # 直接 terminate 所有仍未退出的
        for w in workers:
            try:
                if w.isRunning():
                    w.terminate()
            except Exception as e:
                logger.warning("工作线程清理异常: %s", e)

        # ── ASR 子进程：kill 而非优雅 shutdown ──
        if self._asr_mgr:
            try:
                engine = self._asr_mgr.get_engine()
                if engine and hasattr(engine, '_stop_server'):
                    engine._stop_server()
            except Exception as e:
                logger.warning("ASR 引擎关闭异常: %s", e)

        # ── 后台线程静默等待 terminate 完成 ──
        def _wait_workers():
            for w in workers:
                try:
                    w.wait(5000)  # 最长等 5 秒（后台，不影响 UI）
                except Exception as e:
                    logger.debug("工作线程等待超时: %s", e)
            logger.info("后台清理完成")

        if workers:
            threading.Thread(target=_wait_workers, daemon=True).start()

        logger.info("清理信号已发出")
