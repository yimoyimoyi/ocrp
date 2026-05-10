# -*- coding: utf-8 -*-
"""WorkflowManager —— 封装所有业务流程逻辑（处理、纠错、批量、导出）。

通过信号与 MainWindow 通信，通过依赖注入获取 UI 数据，
保持业务逻辑与 UI 的分离。
"""

import os, time, threading
from pathlib import Path
from typing import Optional, List, Dict, Callable, Any

from PyQt5.QtCore import QObject, pyqtSignal

from core.frame_processor import FrameProcessor
from core.result_processor import export_results
from ui.workers import (
    VideoProcessWorker, AICorrectionWorker, BatchCorrectionWorker,
    ImageProcessWorker, BatchProcessWorker, AudioProcessWorker,
)


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
    batch_progress = pyqtSignal(str, int, int)                      # (fname, idx, total)
    batch_file_done = pyqtSignal(str, list)                         # (file_path, results)
    batch_all_done = pyqtSignal()                                   # 批量全部完成
    batch_error = pyqtSignal(str)                                   # 批量出错

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

        # ── 管理器（通过 configure() 注入） ──
        self._engine_mgr = None
        self._asr_mgr = None
        self._corrector = None
        self._filter_mgr = None
        self._config_mgr = None

        # ── 工作线程 ──
        self._video_worker: Optional[VideoProcessWorker] = None
        self._audio_worker: Optional[AudioProcessWorker] = None
        self._image_worker: Optional[ImageProcessWorker] = None
        self._batch_worker: Optional[BatchProcessWorker] = None
        self._frame_processor: Optional[FrameProcessor] = None
        self._correction_workers: List[AICorrectionWorker] = []
        self._correction_workers_lock = threading.Lock()
        self._batch_correction_worker: Optional[BatchCorrectionWorker] = None

        # ── 结果状态 ──
        self._correction_results: Dict[int, str] = {}
        self._correction_pending: set = set()
        self._filtered_count: int = 0

        # ── 批量状态 ──
        self._batch_files: List[str] = []

        # ── 串行状态（ASR → OCR） ──
        self._pending_vp: str = ""
        self._pending_regions: list = []
        self._pending_ename: str = ""

        # ── UI 访问器（通过 configure() 注入） ──
        self._get_video_path: Callable[[], Optional[str]] = lambda: None
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
        self._sort_results_table: Callable[[str], None] = lambda order: None
        self._get_polished_results: Callable[[float, int], list] = lambda sim, ml: []
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
    # 单文件处理入口
    # ═══════════════════════════════════════════════════════════════

    def start_processing(self):
        """单文件处理入口（对应 MainWindow._on_start_processing）。"""
        vp = self._get_video_path()
        if not vp:
            self._show_error("提示", "请加载视频或图片文件。")
            return
        print(f"[Workflow] ▶ 开始处理: {Path(vp).name}")

        mode_params = self._get_mode_params()
        mode = mode_params.get("process_mode", "OCR + ASR（完整流程）")
        is_audio = self._get_is_audio_file()

        # 音频文件只能 ASR
        if is_audio and mode != "仅语音识别 (ASR)":
            mode = "仅语音识别 (ASR)"

        if mode == "仅语音识别 (ASR)":
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

        self._clear_results_table()
        self._correction_results.clear()
        self._correction_pending.clear()

        if mode == "仅 OCR":
            self._pending_vp = vp
            self._pending_regions = regions
            self._pending_ename = self._get_current_engine()
            self._do_ocr_pass(vp)
        else:  # "OCR + ASR（完整流程）"
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

        self._clear_results_table()
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

        self._clear_results_table()
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
        asr_enabled = mp.get("asr_enabled", False)
        has_ocr = any(
            r.get("name", "") != mp.get("asr_region_name", "语音")
            for r in regions
        )

        if asr_enabled and has_ocr:
            self._start_asr_worker(vp)
        elif asr_enabled and not has_ocr:
            self._start_asr_worker(vp)
        elif not asr_enabled and has_ocr:
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
        if not mp.get("asr_enabled"):
            return
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
        """停止当前处理线程。"""
        print(f"[Workflow] ⏹ 停止处理")
        if self._video_worker and self._video_worker.isRunning():
            self._video_worker.stop()
            self.status_msg.emit("正在停止...")
        if self._image_worker and self._image_worker.isRunning():
            self._image_worker.quit()
            self._image_worker.wait(2000)
            self.status_msg.emit("正在停止...")
        if self._batch_worker and self._batch_worker.isRunning():
            self._batch_worker.stop()
            self.status_msg.emit("正在停止批量处理...")
        print(f"[Workflow] ✅ 已停止")

    def pause_processing(self):
        """暂停当前处理。"""
        print(f"[Workflow] ⏸ 暂停")
        if self._frame_processor and hasattr(self._frame_processor, '_pause_flag'):
            self._frame_processor.pause()
            self.status_msg.emit("⏸ 已暂停")

    def resume_processing(self):
        """继续当前处理。"""
        print(f"[Workflow] ▶ 继续")
        if self._frame_processor and hasattr(self._frame_processor, '_pause_flag'):
            self._frame_processor.resume()
            self.status_msg.emit("▶ 继续处理")

    # ═══════════════════════════════════════════════════════════════
    # AI 纠错 —— 选中行
    # ═══════════════════════════════════════════════════════════════

    def correct_selected(self, selected_rows: set):
        """对选中的表格行进行 AI 纠错。"""
        if not selected_rows:
            self._show_error("提示", "请先在表格中选中需要纠错的行（可多选）。")
            return
        print(f"[Workflow] ✏ 纠错选中: {len(selected_rows)} 行")

        mp = self._get_mode_params()
        if self._corrector:
            self._corrector.translate_mode = mp.get("corr_translate", False)
            self._corrector.stream_mode = mp.get("corr_stream", False)
            self._corrector.json_mode = mp.get("corr_json", False)
        batch_size = mp.get("corr_batch_size", 5)
        results = self._get_results()
        sorted_rows = sorted(selected_rows)

        submitted = 0
        for row in sorted_rows:
            if row < len(results):
                r = results[row]
                raw = r.get("raw", "")
                self._submit_correction(row, raw, "")
                self._correction_pending.add(row)
                submitted += 1
                if submitted >= batch_size:
                    break

        if submitted:
            self.status_msg.emit(f"✏ 已提交 {submitted} 条纠错")
        else:
            self.status_msg.emit("⚠ 无有效结果可纠错")

    # ═══════════════════════════════════════════════════════════════
    # AI 纠错 —— 全部
    # ═══════════════════════════════════════════════════════════════

    def correct_all(self):
        """对全部结果行进行 AI 纠错（使用 BatchCorrectionWorker，按 batch_size 分批）。"""
        results = self._get_results()
        if not results:
            self._show_error("提示", "暂无识别结果可纠错。")
            return

        self._start_batch_correction(results, is_auto=False)

    def _build_correction_texts(self, results: list) -> list:
        """从结果列表提取有效文本条目。"""
        texts = []
        for row, r in enumerate(results):
            raw = r.get("raw", "")
            if raw.strip():
                ts = r.get("time_sec", 0.0) or 0.0
                te = r.get("end_sec", 0.0) or 0.0
                texts.append((row, raw, ts, te))
        return texts

    def _sync_corrector_modes(self, mp: dict):
        """同步 UI 模式到 corrector 实例。"""
        if self._corrector:
            self._corrector.translate_mode = mp.get("corr_translate", False)
            self._corrector.stream_mode = mp.get("corr_stream", False)
            self._corrector.json_mode = mp.get("corr_json", False)

    def _maybe_extract_env(self, results: list, mp: dict):
        """如果配置启用，提取全文环境上下文。"""
        if mp.get("corr_extract_env", False) and self._corrector:
            self.status_msg.emit("⏳ AI 纠错: 提取全文环境中...")
            all_texts = [r.get("raw", "") for r in results if r.get("raw", "").strip()]
            if all_texts:
                self._corrector.extract_environment(all_texts)

    def _start_batch_correction(self, results: list, is_auto: bool = False):
        """内部：启动批量纠错（自动全量或手动全量）。

        Args:
            results: 结果列表
            is_auto: True=处理完成后自动触发, False=手动点击"纠正全部"
        """
        mp = self._get_mode_params()
        self._sync_corrector_modes(mp)
        self._maybe_extract_env(results, mp)

        texts = self._build_correction_texts(results)
        if not texts:
            finish_label = "全量纠错完成" if is_auto else "批量纠错完成"
            self.status_msg.emit(f"✅ 完成: {len(results)} 条结果 | 无有效文本可纠错")
            return

        context_window = mp.get("corr_context_window", 3)
        max_retries = mp.get("corr_retry", 3)
        batch_size = mp.get("corr_batch_size", 5)

        # 按 batch_size 分批提交
        total_batches = (len(texts) + batch_size - 1) // batch_size
        self.status_msg.emit(f"⏳ AI 纠错: 正在批量纠错 {len(texts)} 条 ({total_batches} 批)...")
        self._set_buttons(correction_all=False, correction=False)
        self._remaining_batches = total_batches
        self._is_auto_correction = is_auto

        self._submit_correction_batch(texts, batch_size, 0, context_window, max_retries)

    def _submit_correction_batch(self, texts: list, batch_size: int, offset: int,
                                  context_window: int, max_retries: int):
        """递归分批提交批量纠错。"""
        batch = texts[offset:offset + batch_size]

        self._batch_correction_worker = BatchCorrectionWorker(
            self._corrector, batch,
            context_window=context_window,
            max_retries=max_retries,
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
                    texts, batch_size, offset + batch_size, context_window, max_retries))

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
    # AI 纠错 —— 单条提交
    # ═══════════════════════════════════════════════════════════════

    def _submit_correction(self, row, raw, region_corr_prompt: str = ""):
        mp = self._get_mode_params()
        ctx_window = mp.get("corr_context_window", 3)
        results = self._get_results()

        ctx = []
        for i in range(max(0, row - ctx_window), min(len(results), row + ctx_window + 1)):
            if i != row:
                ctx.append(results[i].get("raw", ""))

        # 获取 ROI 图像（本地引擎纠错需要）
        image = None
        if self._corrector and self._corrector._is_local_engine():
            result_region_name = results[row].get("region", "") if row < len(results) else ""
            regions = self._get_regions()
            for ri, r in enumerate(regions):
                if r.get("name", "") == result_region_name:
                    try:
                        image = self._get_roi_image(ri)
                    except Exception:
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
        print(f"[Workflow] ▶ 批量处理: {len(batch_files)} 个文件")

        regions = [r for r in self._get_regions() if r.get("enabled", True)]
        if not regions:
            self._show_error("提示", "没有启用的区域，请先在预览图上定义区域。")
            return

        output_dir = Path(__file__).resolve().parent.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        self._clear_results_table()
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
        mp = self._get_mode_params()
        results = self._get_results()

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

        export_results(polished, path, fmt, bool(cmap), cmap)
        self.status_msg.emit(f"✅ 已导出: {Path(path).name}")
        return polished, cmap

    # ═══════════════════════════════════════════════════════════════
    # 环境提取
    # ═══════════════════════════════════════════════════════════════

    def extract_environment(self, summary_prompt_setter: Optional[Callable[[str], None]] = None):
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
                self.status_msg.emit("✅ 全文环境已提取并回填到总结提示词栏")
            else:
                self.status_msg.emit("⚠ 环境提取失败，请检查 API 配置")
            return env
        return None

    # ═══════════════════════════════════════════════════════════════
    # 关闭清理
    # ═══════════════════════════════════════════════════════════════

    def cleanup(self):
        """强制停止所有运行中的线程并等待结束。"""
        print(f"[Workflow] 🧹 开始强制清理所有线程...")
        import signal as _signal

        # ── 1. 停止所有纠错线程（加锁访问） ──
        if self._batch_correction_worker and self._batch_correction_worker.isRunning():
            print(f"[Workflow]   ⏹ 停止批量纠错线程")
            self._batch_correction_worker.stop()
            if not self._batch_correction_worker.wait(2000):
                print(f"[Workflow]   ⚠ 批量纠错线程未退出，强制终止")
                self._batch_correction_worker.terminate()
                self._batch_correction_worker.wait(1000)

        with self._correction_workers_lock:
            workers_snapshot = list(self._correction_workers)
            self._correction_workers.clear()
        for w in workers_snapshot:
            if w.isRunning():
                print(f"[Workflow]   ⏹ 停止纠错线程")
                if not w.wait(500):
                    w.terminate()
                    w.wait(1000)

        # ── 2. 停止视频处理线程 ──
        if self._video_worker and self._video_worker.isRunning():
            print(f"[Workflow]   ⏹ 停止视频处理线程")
            self._video_worker.stop()
            if not self._video_worker.wait(3000):
                print(f"[Workflow]   ⚠ 视频线程未退出，强制终止")
                self._video_worker.terminate()
                self._video_worker.wait(1000)

        # ── 3. 停止音频处理线程 ──
        if self._audio_worker and self._audio_worker.isRunning():
            print(f"[Workflow]   ⏹ 停止音频处理线程")
            self._audio_worker.stop()
            if not self._audio_worker.wait(3000):
                print(f"[Workflow]   ⚠ 音频线程未退出，强制终止")
                self._audio_worker.terminate()
                self._audio_worker.wait(1000)

        # ── 4. 停止批量处理线程 ──
        if self._batch_worker and self._batch_worker.isRunning():
            print(f"[Workflow]   ⏹ 停止批量文件处理线程")
            self._batch_worker.stop()
            if not self._batch_worker.wait(3000):
                print(f"[Workflow]   ⚠ 批量线程未退出，强制终止")
                self._batch_worker.terminate()
                self._batch_worker.wait(1000)

        # ── 5. 停止图片处理线程 ──
        if self._image_worker and self._image_worker.isRunning():
            print(f"[Workflow]   ⏹ 停止图片处理线程")
            self._image_worker.quit()
            if not self._image_worker.wait(2000):
                print(f"[Workflow]   ⚠ 图片线程未退出，强制终止")
                self._image_worker.terminate()
                self._image_worker.wait(1000)

        # ── 6. 停止 ASR 子进程 ──
        if self._asr_mgr:
            try:
                engine = self._asr_mgr.get_engine()
                if engine and hasattr(engine, '_stop_server'):
                    print(f"[Workflow]   ⏹ 停止 ASR 子进程")
                    engine._stop_server()
            except Exception as e:
                print(f"[Workflow]   ⚠ ASR 引擎关闭异常: {e}")

        print(f"[Workflow] ✅ 所有线程清理完成")
