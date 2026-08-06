"""OCR 流程编排 —— 视频/图片 OCR 处理与回调。

方法体从原 workflow_manager.py 原样搬移（AST 提取，零改动）。
未定义的属性/方法自动委托给 WorkflowManager（见 _FlowBase）。
"""

from core.frame_processor import FrameProcessor
from core.i18n import _
from core.logger import get_logger
from core.workers import ImageProcessWorker, VideoProcessWorker

logger = get_logger(__name__)

from core.workflow.base import _FlowBase


class OCRFlow(_FlowBase):
    """OCR 流程编排 —— 视频/图片 OCR 处理与回调。"""

    def _start_ocr_only(self):
        vp = self._get_video_path()
        if not vp:
            self._show_error("提示", "请先加载视频或图片文件。")
            return

        # 释放 ASR 引擎（OCR 与 ASR 互斥）
        if self._asr_mgr:
            self._asr_mgr.release_all_engines()

        regions = [r for r in self._get_regions() if r.get("enabled", True)]
        if not regions:
            regions = [
                {
                    "name": "全帧",
                    "x": 0,
                    "y": 0,
                    "w": 0,
                    "h": 0,
                    "engine": self._get_current_engine(),
                    "prompt": self._get_custom_prompt() or self._get_config_prompt(),
                    "prompt_template": self._get_current_template(),
                    "enabled": True,
                }
            ]
            self._set_regions(regions)

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
        self.status_msg.emit(_("OCR 处理中..."))

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
        has_ocr_regions = any(r.get("name", "") != asr_region_name for r in regions)

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
            self._start_asr_worker(vp)
        elif do_asr and not do_ocr:
            logger.info("流程: 仅 ASR")
            self._start_asr_worker(vp)
        elif not do_asr and do_ocr:
            logger.info("流程: 仅 OCR")
            self._do_ocr_pass(vp)
        else:
            self._show_error("提示", "未启用任何处理（请启用 ASR 或定义 OCR 区域）")
            self._set_buttons(start=True, stop=False)

    def _do_ocr_pass(self, vp: str):
        """启动 OCR 视频处理线程。"""
        regions = self._pending_regions
        ename = self._pending_ename
        mp = self._session_mp or self._get_mode_params()  # 会话快照优先（设置同步 W1）
        hw_accel = self._config_mgr.get_hw_accel() if self._config_mgr else False
        self._frame_processor = FrameProcessor(
            engine_manager=self._engine_mgr,
            regions=regions,
            filter_manager=self._filter_mgr,
            hw_accel=hw_accel,
        )

        if mp:
            fp = self._frame_processor
            fp._subtitle_mode = mp.get("subtitle_mode", "stream")
            fp._sentinel_enabled = mp.get("sentinel_enabled", True)
            fp._s_drop_ratio = mp.get("s_drop_ratio", 0.5)
            fp._s_buffer_size = mp.get("s_buffer_size", 8)
            fp._s_sim_threshold = mp.get("s_sim_threshold", 0.85)
            fp._s_min_text_len = mp.get("s_min_text_len", 2)
            fp._s_ocr_version = mp.get("s_ocr_version", "")
            fp._r_dedup = mp.get("r_dedup", True)
            fp._r_sim_threshold = mp.get("r_sim_threshold", 0.9)
            fp._r_buffer_size = mp.get("r_buffer_size", 5)
            fp._r_min_text_len = mp.get("r_min_text_len", 2)
            fp._r_interval = mp.get("r_interval", 2.0)
            fp._frame_interval = mp.get("frame_interval", 0.1)

        t_start, t_end = self._get_time_range()
        self._video_worker = VideoProcessWorker(
            self._frame_processor,
            vp,
            ename,
            time_start=t_start,
            time_end=t_end,
        )
        self._video_worker.log.connect(lambda m: self.status_msg.emit(m))
        self._video_worker.progress.connect(self._on_process_progress)
        self._video_worker.result_item.connect(self._on_process_result)
        self._video_worker.finished_all.connect(self._on_process_finished)
        self._video_worker.error.connect(self._on_process_error)
        self._video_worker.start()
        self.status_msg.emit(_("OCR 处理中..."))

    def _process_image(self, regions: list):
        frame = self._get_current_frame()
        if frame is None:
            self._show_error("提示", "没有可处理的图片帧。")
            self._set_buttons(start=True, stop=False)
            self.progress_val.emit(0)
            return

        # 回收旧 worker（防止覆盖引用导致运行中线程被 GC 销毁 → QThread destroyed while running）
        old = getattr(self, "_image_worker", None)
        if old and old.isRunning():
            if hasattr(old, "stop"):
                old.stop()
            if not old.wait(2000):
                old.terminate()
                old.wait(1000)

        self._image_worker = ImageProcessWorker(self._engine_mgr, frame, regions)
        self._image_worker.result_item.connect(self._on_process_result)
        self._image_worker.finished_all.connect(self._on_process_finished)
        self._image_worker.error.connect(self._on_process_error)
        self._image_worker.start()

    def _on_process_result(self, ts, t_str, rname, ename, raw, conf: float = 0.0):
        if self._filter_mgr and self._filter_mgr.matches(raw):
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
        try:
            self._set_buttons(
                start=True, stop=False, correction=True, correction_all=True, polish=True, polish_all=True, pause=False
            )
            self.progress_val.emit(0)

            # 排序：先按时间，再按区域顺序模板（如有）
            mp = self._session_mp or self._get_mode_params()  # 会话快照优先（设置同步 W1）
            if not self._get_is_image():
                self._sort_by_time()
            region_order = mp.get("region_order", "")
            if not self._get_is_image() and region_order:
                self._sort_results_table(region_order)

            # 通知 MainWindow 做后处理（end_sec 回填等）
            self.process_finished.emit()

            n = self._get_table_row_count()
            msg = f"✅ 处理完成: {n} 条结果"
            if self._filtered_count > 0:
                msg += f" | 过滤: {self._filtered_count} 条"
            self._filtered_count = 0

            # 全量处理：直接纠错（纠错完成后会自动触发下一个文件）
            corr_enabled = mp.get("corr_enabled", False)
            if corr_enabled and n > 0 and not self._correction_in_progress:
                self._run_full_correction(is_auto=True)
                self.status_msg.emit(f"{msg} | 全量 AI 纠错中...")
            elif corr_enabled and self._correction_in_progress:
                logger.warning("纠错已在运行中，跳过重复触发")
                self.status_msg.emit(msg)
            else:
                self.status_msg.emit(msg)
                # 没有纠错时，直接进入下一个文件
                self._maybe_start_next_batch_file()
        except Exception as e:
            import traceback

            logger.error("_on_process_finished 异常: %s", e)
            traceback.print_exc()
            self.status_msg.emit(f"❌ 处理完成回调异常: {e}")

    def _on_process_error(self, err):
        self._set_buttons(
            start=True, stop=False, correction=True, correction_all=True, polish=True, polish_all=True, pause=False
        )
        self.progress_val.emit(0)
        self.status_msg.emit(f"❌ 处理失败: {err}")
        is_batch = self._batch_worker and self._batch_worker.isRunning()
        if not is_batch:
            self._show_error("处理错误", f"处理失败:\n{err}")
