"""批量文件队列与批量 worker 管理。

方法体从原 workflow_manager.py 原样搬移（AST 提取，零改动）。
未定义的属性/方法自动委托给 WorkflowManager（见 _FlowBase）。
"""

from pathlib import Path

from PySide6.QtCore import QTimer

from core.asr_engine import SUPPORTED_AUDIO_EXTS
from core.logger import get_logger
from core.utils import ENGINE_WHISPERX
from core.workers import BatchProcessWorker

logger = get_logger(__name__)

from core.workflow.base import _FlowBase


class BatchFlow(_FlowBase):
    """批量文件队列与批量 worker 管理。"""

    def _export_current_results(self, file_path: str | None, results: list):
        """将当前文件结果导出到 output/ 目录（批量切换时自动调用）。"""
        if not results or not file_path:
            return
        try:
            from core.result_processor import export_results, polish_results

            output_dir = Path(__file__).resolve().parent.parent / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(file_path).stem

            # 统一转为 tuple 格式
            raw_list = []
            for r in results:
                if isinstance(r, dict):
                    raw_list.append(
                        (
                            r.get("time_sec", 0.0) or 0.0,
                            r.get("time_str", "") or r.get("time", ""),
                            r.get("region", "unknown"),
                            r.get("engine", ""),
                            r.get("raw", ""),
                        )
                    )
                else:
                    raw_list.append(r)
            polished = polish_results(raw_list)
            txt_path = output_dir / f"{stem}.txt"
            export_results(polished, str(txt_path), "txt", False, {})
            logger.info("批量导出: %s", txt_path.name)
        except Exception as e:
            logger.warning("批量导出失败: %s", e)

    def _maybe_start_next_batch_file(self):
        """如果有批量队列，处理下一个文件（信号驱动，不阻塞主线程）。

        流程：导出当前结果到 output/ → 清空表格 → 加载下一个文件
        P1-6：统一使用真源 _get_batch_files()（此前读 manager 空列表导致队列永不推进）
        """
        files = self._get_batch_files()
        if not files or len(files) <= 1:
            return
        # 保存当前文件结果到缓存 + 导出到 output/
        current_results = self._get_results()
        current_vp = self._get_video_path()
        if current_results:
            if current_vp:
                self._save_asr_cache(current_vp, current_results)
            # 导出当前文件结果到 output/ 目录
            self._export_current_results(current_vp, current_results)
        # 移除已处理的第一个文件
        files.pop(0)
        self._update_batch_label()
        if not files:
            self._set_buttons(start=True, stop=False)
            self.status_msg.emit("✅ 所有文件处理完成")
            return
        # 清空结果表格（导出已完成）
        self._clear_results_table()
        # 加载下一个文件
        next_file = files[0]
        ext = Path(next_file).suffix.lower()

        if self._video_preview is None:
            logger.error("video_preview 未初始化，无法加载批量文件")
            return

        # 连接加载完成信号
        self._video_preview.video_loaded.connect(self._on_batch_file_loaded)
        # 超时保护（30s）

        self._batch_load_timer = QTimer()
        self._batch_load_timer.setSingleShot(True)
        self._batch_load_timer.timeout.connect(self._on_batch_load_timeout)
        self._batch_load_timer.start(30000)

        if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
            self._video_preview.load_video(next_file)
        elif ext in (".png", ".jpg", ".jpeg", ".bmp"):
            self._video_preview.load_image(next_file)
        elif ext in SUPPORTED_AUDIO_EXTS:
            self._video_preview.load_audio(next_file)
        else:
            self._disconnect_batch_load()
            logger.warning("不支持的文件格式: %s, 跳过", ext)
            self._maybe_start_next_batch_file()

    def _on_batch_file_loaded(self, path: str):
        """批量文件加载完成 → 启动处理。"""
        self._disconnect_batch_load()

        QTimer.singleShot(500, lambda: self.start_processing())

    def _on_batch_load_timeout(self):
        """批量文件加载超时 → 跳过该文件。"""
        logger.warning("批量文件加载超时 (30s)，跳过")
        self._disconnect_batch_load()
        self._maybe_start_next_batch_file()

    def _disconnect_batch_load(self):
        """断开批量加载信号连接，取消超时定时器。"""
        try:
            self._video_preview.video_loaded.disconnect(self._on_batch_file_loaded)
        except (TypeError, RuntimeError):
            pass
        if hasattr(self, "_batch_load_timer") and self._batch_load_timer:
            self._batch_load_timer.stop()
            self._batch_load_timer = None

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
        mode_params = self._get_mode_params()
        # 会话配置快照：整个批量队列使用同一份参数（设置同步 W1/W6 修复）
        self._session_mp = dict(mode_params)
        asr_region = mode_params.get("asr_region_name", "语音")
        self._clear_results_by_type(asr_region, ENGINE_WHISPERX)
        for r in regions:
            self._clear_results_by_type(r.get("name", ""), r.get("engine", ""))

        self._correction_pending.clear()

        self._set_buttons(start=False, stop=True, correction=False)
        self.progress_val.emit(0)

        hw_accel = self._config_mgr.get_hw_accel() if self._config_mgr else False
        self._batch_worker = BatchProcessWorker(
            engine_manager=self._engine_mgr,
            file_list=list(batch_files),
            regions=regions,
            mode_params=self._session_mp,
            output_dir=str(output_dir),
            hw_accel=hw_accel,
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
        # 切换到下一个文件时清理结果
        self._clear_results_table()

    def _on_batch_finished_all(self, _=None):
        self._set_buttons(start=True, stop=False, correction=True)
        self.progress_val.emit(0)
        files = self._get_batch_files()
        n = len(files)  # P1-6：读真源（此前读假源恒 0 → "0 个文件"）
        msg = f"✅ 批量处理完成: {n} 个文件 → output/"

        # 设置同步 R6 修复：批量 corr_enabled 自动纠错（此前仅单文件路径生效）
        mp = self._session_mp or self._get_mode_params()
        corr_enabled = mp.get("corr_enabled", False)
        row_count = self._get_table_row_count()
        if corr_enabled and row_count > 0 and not self._correction_in_progress:
            self._run_full_correction(is_auto=True)
            self.status_msg.emit(f"{msg} | 全量 AI 纠错中...")
        else:
            self.status_msg.emit(msg)
        files.clear()  # P1-6：清真源（此前清假源 → 队列标签不归零）
        self.batch_all_done.emit()
