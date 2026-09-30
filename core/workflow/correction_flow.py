"""AI 纠错/润色/环境提取 —— 含并行批处理调度状态机。

方法体从原 workflow_manager.py 原样搬移（AST 提取，零改动）。
未定义的属性/方法自动委托给 WorkflowManager（见 _FlowBase）。
"""

from collections import deque
from collections.abc import Callable

from core.i18n import _
from core.logger import get_logger
from core.workers import (
    BatchCorrectionWorker,
    BatchPolishWorker,
)

logger = get_logger(__name__)

from core.workflow.base import _FlowBase

# 润色收尾时等待 worker 线程退出的上限（毫秒）。worker 在 run() 返回前就发出
# batch_finished，正常情况下几十毫秒内即退出；超时则转入 draining 列表保引用。
POLISH_DRAIN_WAIT_MS = 2000


class CorrectionFlow(_FlowBase):
    """AI 纠错/润色/环境提取 —— 含并行批处理调度状态机。"""

    def correct_selected(self, selected_rows: set):
        """对选中的表格行进行批量 AI 纠错（一次性发送所有选中条目）。"""
        self._reload_all_config()
        self._correction_pending.clear()

        if not selected_rows:
            self._show_error("提示", "请先在表格中选中需要纠错的行（可多选）。")
            return
        logger.info("批量纠错选中: %d 行", len(selected_rows))

        # 构建选中行的 texts 列表（使用原始文本）
        results = self._get_results()
        texts = []
        for row in sorted(selected_rows):
            if 0 <= row < len(results):
                r = results[row]
                raw = r.get("raw", "")
                if raw.strip():
                    ts = r.get("time_sec", 0.0) or 0.0
                    te = r.get("end_sec", 0.0) or 0.0
                    texts.append((row, raw, ts, te))

        if not texts:
            self.status_msg.emit(_("⚠ 选中的行无有效文本可纠错"))
            return

        if self._correction_in_progress:
            logger.warning("纠错已在运行中，忽略选中行的纠错请求")
            self.status_msg.emit(_("⚠ 纠错进行中，请等待当前纠错完成"))
            return

        self._correction_in_progress = True

        mp = self._get_mode_params()
        self._sync_corrector_modes(mp)
        self._correction_stop_requested = False

        def _do_correction():
            max_retries = mp.get("corr_retry", 2)  # 与 MODE_PARAMS_DEFAULTS 一致
            batch_size = mp.get("corr_batch_size", 5)
            concurrency = mp.get("corr_concurrency", 4)

            total_batches = (len(texts) + batch_size - 1) // batch_size
            self._set_buttons(correction_all=False, correction=False, polish=False, polish_all=False)
            self._total_correction_batches = total_batches
            self._is_auto_correction = False

            self._submit_all_correction_batches(texts, batch_size, max_retries, total_batches, concurrency)
            self.status_msg.emit(f"已提交 {len(texts)} 条批量纠错 [{total_batches} 批]")

        self._maybe_extract_env(results, mp, on_done=_do_correction)

    def correct_all(self):
        """对全部结果行进行 AI 纠错（使用 BatchCorrectionWorker，按 batch_size 分批）。"""
        self._correction_stop_requested = False
        self._reload_all_config()
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
                texts.append((row, raw, r.get("time_sec", 0.0) or 0.0, r.get("end_sec", 0.0) or 0.0))
        return texts

    def _sync_corrector_modes(self, mp: dict):
        """同步 UI 模式到 corrector 实例。"""
        if self._corrector:
            self._corrector.translate_mode = mp.get("corr_translate", False)
            self._corrector.stream_mode = mp.get("corr_stream", False)
            self._corrector.json_mode = mp.get("corr_json", False)

    def _maybe_extract_env(self, results: list, mp: dict, on_done: Callable | None = None):
        """如果配置启用，异步提取全文环境上下文。

        Args:
            results: 结果列表
            mp: 模式参数
            on_done: 提取完成（或无需提取）后的回调
        """
        if mp.get("corr_extract_env", False) and self._corrector and not self._env_extraction_running:
            # 通过公开 API 判断，不再 hasattr 探测私有方法/私有属性
            # （私有成员改名只在运行时炸，且破坏了 AICorrector 的封装）
            if self._corrector.should_skip_env_extraction():
                logger.info(
                    "跳过环境提取: extract_env=%s, env_context=%s",
                    self._corrector.extract_env,
                    bool(self._corrector.env_context),
                )
                if on_done:
                    on_done()
                return
            self._env_extraction_running = True
            self.status_msg.emit(_("⏳ AI 纠错: 提取全文环境中..."))
            all_texts = [r.get("raw", "") for r in results if r.get("raw", "").strip()]
            if all_texts:
                from core.workers import EnvExtractWorker  # workers 已移至 core（4b 重构）

                self._env_worker = EnvExtractWorker(self._corrector, all_texts)

                def _on_env_done(_env_text: str):
                    self._env_extraction_running = False
                    if on_done:
                        on_done()

                def _on_env_error(err: str):
                    logger.error("环境提取失败: %s", err)
                    self._env_extraction_running = False
                    if on_done:
                        on_done()

                self._env_worker.finished.connect(_on_env_done)
                self._env_worker.error.connect(_on_env_error)
                self._env_worker.start()
            else:
                self._env_extraction_running = False
                if on_done:
                    on_done()
        else:
            if on_done:
                on_done()

    def _start_batch_correction(self, results: list, is_auto: bool = False):
        """内部：启动批量纠错/翻译（自动全量或手动全量）。

        Args:
            results: 结果列表
            is_auto: True=处理完成后自动触发, False=手动点击"纠正全部"
        """
        if self._correction_in_progress:
            logger.warning("批量纠错已在运行中，忽略重复请求")
            return
        self._correction_in_progress = True
        self._correction_stop_requested = False

        # 设置同步 W1：自动纠错（处理会话触发）使用会话快照，与处理参数一致；
        # 手动纠错（用户点击）使用实时值（点击前可自由调整）
        mp = self._session_mp if is_auto and self._session_mp else self._get_mode_params()
        self._sync_corrector_modes(mp)

        def _do_correction():
            texts = self._build_correction_texts(results)
            if not texts:
                self._correction_in_progress = False
                self._set_buttons(correction_all=True, correction=True, polish=True, polish_all=True)
                self.status_msg.emit(f"✅ 完成: {len(results)} 条结果 | 无有效文本可纠错")
                return

            max_retries = mp.get("corr_retry", 2)  # 与 MODE_PARAMS_DEFAULTS 一致
            batch_size = mp.get("corr_batch_size", 5)
            concurrency = mp.get("corr_concurrency", 4)

            # 按 batch_size 分批提交
            total_batches = (len(texts) + batch_size - 1) // batch_size
            self._set_buttons(correction_all=False, correction=False, polish=False, polish_all=False)
            self._total_correction_batches = total_batches
            self._is_auto_correction = is_auto

            self._submit_all_correction_batches(texts, batch_size, max_retries, total_batches, concurrency)

        self._maybe_extract_env(results, mp, on_done=_do_correction)

    def _submit_all_correction_batches(
        self,
        texts: list,
        batch_size: int,
        max_retries: int,
        total_batches: int,
        concurrency: int = 4,
    ):
        """并行提交所有批次纠错（滑动窗口并发）。"""
        if self._correction_stop_requested:
            self._on_batch_correction_finished()
            return

        # P1-1 修复：会话代际令牌——新会话 +1，旧会话 worker 的回调全部作废
        self._correction_session_generation += 1
        gen = self._correction_session_generation

        # 预计算所有批次
        batches = []
        for offset in range(0, len(texts), batch_size):
            batches.append(texts[offset : offset + batch_size])

        self._batch_total_count = len(batches)
        self._batch_completed_count = 0
        self._batch_pending_batches = deque(batches)
        with self._batch_correction_workers_lock:
            self._batch_correction_workers.clear()

        # 滑动窗口并发
        concurrency = min(concurrency, len(batches))
        for _i in range(concurrency):
            self._launch_next_correction_batch(max_retries, gen)

    def _launch_next_correction_batch(self, max_retries: int, gen: int):
        """从待处理队列中取下一批并启动 worker。"""
        if not self._batch_pending_batches:
            return
        if self._correction_stop_requested:
            # 所有活跃 worker 停止后检查是否全部完成
            return

        batch = self._batch_pending_batches.popleft()
        worker = BatchCorrectionWorker(
            self._corrector,
            batch,
            max_retries=max_retries,
        )
        worker.correction_ready.connect(self._on_correction_ready)
        worker.batch_finished.connect(lambda: self._on_parallel_batch_done(max_retries, gen))
        worker.batch_error.connect(lambda err: self._on_parallel_batch_error(err, max_retries, gen))

        with self._batch_correction_workers_lock:
            self._batch_correction_workers.append(worker)

        self.status_msg.emit(f"⏳ AI 纠错 [{self._batch_completed_count + 1}/{self._batch_total_count}] ...")
        worker.start()

    def _on_parallel_batch_done(self, max_retries: int, gen: int):
        """单个批次完成 → 启动下一批或检查全部完成。

        P1-1：代际不匹配（旧会话 worker 迟到回调）直接丢弃，不污染新会话计数。
        """
        if gen != self._correction_session_generation:
            logger.debug("忽略旧纠错会话回调 (gen=%d != %d)", gen, self._correction_session_generation)
            return
        with self._batch_completed_count_lock:
            self._batch_completed_count += 1

        if self._correction_stop_requested:
            self._check_all_batches_done()
            return

        if self._batch_pending_batches:
            self._launch_next_correction_batch(max_retries, gen)
        else:
            self._check_all_batches_done()

    def _on_parallel_batch_error(self, err: str, max_retries: int, gen: int):
        """单个批次出错 → 记录错误，继续后续批次。"""
        if gen != self._correction_session_generation:
            logger.debug("忽略旧纠错会话错误回调 (gen=%d != %d)", gen, self._correction_session_generation)
            return
        logger.warning("批次纠错失败: %s", err)
        with self._batch_completed_count_lock:
            self._batch_completed_count += 1

        if self._correction_stop_requested:
            self._check_all_batches_done()
            return

        if self._batch_pending_batches:
            self._launch_next_correction_batch(max_retries, gen)
        else:
            self._check_all_batches_done()

    def _check_all_batches_done(self):
        """检查是否所有批次完成，完成则调用结束回调。"""
        if self._batch_completed_count >= self._batch_total_count:
            if self._is_auto_correction:
                self._on_full_correction_finished()
            else:
                self._on_batch_correction_finished()

    def _run_full_correction(self, is_auto: bool = True):
        """处理完成后全量提交所有结果进行 AI 纠错。"""
        results = self._get_results()
        self._start_batch_correction(results, is_auto=is_auto)

    def _on_full_correction_finished(self):
        """全量纠错/翻译完成。"""
        self._correction_stop_requested = False
        self._correction_in_progress = False
        with self._batch_correction_workers_lock:
            self._batch_correction_workers.clear()
        n = self._get_table_row_count()
        self.status_msg.emit(f"✅ 完成: {n} 条结果 | 全量纠错完成")
        # 纠错完成后，进入下一个文件
        self._maybe_start_next_batch_file()

    def _on_batch_correction_finished(self):
        """批量纠错完成（来自 correct_all）。"""
        self._correction_stop_requested = False
        self._correction_in_progress = False
        self._set_buttons(correction_all=True, correction=True, polish=True, polish_all=True)
        with self._batch_correction_workers_lock:
            self._batch_correction_workers.clear()
        n = self._get_table_row_count()
        self.status_msg.emit(f"✅ 完成: {n} 条结果 | 批量纠错完成")
        # 纠错完成后，进入下一个文件
        self._maybe_start_next_batch_file()

    def _on_correction_ready(self, row, raw, corrected):
        self.correction_updated.emit(row, raw, corrected)

    def _on_correction_stream(self, row, partial_text):
        """流式增量更新 —— 实时更新表格中的纠错文本。"""
        self.correction_stream_updated.emit(row, partial_text)

    def polish_selected(self, selected_rows: set):
        """对选中行进行润色（优先使用纠错结果作为输入）。"""
        self._reload_all_config()
        if not selected_rows:
            self._show_error("提示", "请先在表格中选中需要润色的行（可多选）。")
            return

        results = self._get_results()
        items = self._build_polish_items(results, selected_rows)
        if not items:
            self.status_msg.emit(_("⚠ 选中的行无有效文本可润色"))
            return

        logger.info("润色选中: %d 行", len(items))
        self._start_polish(items)

    def polish_all(self):
        """对全部行进行润色（优先使用纠错结果作为输入）。"""
        self._reload_all_config()
        results = self._get_results()
        if not results:
            self._show_error("提示", "暂无识别结果可润色。")
            return

        all_rows = set(range(len(results)))
        items = self._build_polish_items(results, all_rows)
        if not items:
            self.status_msg.emit(_("⚠ 无有效文本可润色"))
            return

        logger.info("润色全部: %d 行", len(items))
        self._start_polish(items)

    def _build_polish_items(self, results: list, rows: set) -> list[tuple[int, str, str]]:
        """构建润色输入列表：优先使用纠错结果，回退到原始文本。"""
        items = []
        for row in sorted(rows):
            if 0 <= row < len(results):
                r = results[row]
                raw = r.get("raw", "")
                corrected = r.get("segmented", "")  # 纠错结果（col5）
                text_to_polish = corrected if corrected.strip() else raw
                if text_to_polish.strip():
                    items.append((row, raw, text_to_polish))
        return items

    def _start_polish(self, items: list[tuple[int, str, str]]):
        """启动润色（并行批处理，滑动窗口 4 并发）。"""
        if self._polish_in_progress:
            logger.warning("润色已在运行中，忽略重复请求")
            self.status_msg.emit(_("⚠ 润色进行中，请等待当前润色完成"))
            return

        mp = self._get_mode_params()
        self._sync_corrector_modes(mp)
        self._polish_in_progress = True
        self._polish_stop_requested = False

        def _do_polish():
            batch_size = mp.get("corr_batch_size", 5)
            batches = [items[i : i + batch_size] for i in range(0, len(items), batch_size)]

            # P1-1 修复：润色会话代际令牌（与纠错同构）
            self._polish_session_generation += 1
            gen = self._polish_session_generation

            self._set_buttons(polish=False, polish_all=False)
            self._polish_total_batches = len(batches)
            self._polish_completed_batches = 0
            self._polish_pending_batches = deque(batches)
            self._polish_workers: list[BatchPolishWorker] = []

            total = len(items)
            concurrency = mp.get("corr_concurrency", 4)
            logger.info(
                "润色启动: %d 条, %d 批 (batch_size=%d, concurrency=%d)", total, len(batches), batch_size, concurrency
            )
            self.status_msg.emit(f"润色中: {total} 条 [{len(batches)} 批]")

            concurrency = min(concurrency, len(batches))
            for _i in range(concurrency):
                self._launch_next_polish_batch(gen)

        self._maybe_extract_env(self._get_results(), mp, on_done=_do_polish)

    def _launch_next_polish_batch(self, gen: int):
        """从待处理队列中取下一批并启动润色 worker。"""
        if self._polish_stop_requested or not self._polish_pending_batches:
            return

        batch = self._polish_pending_batches.popleft()
        worker = BatchPolishWorker(self._corrector, batch)
        worker.polish_ready.connect(self._on_polish_ready)
        worker.batch_finished.connect(lambda: self._on_polish_batch_done(gen))
        worker.batch_error.connect(lambda err: self._on_polish_batch_error(err, gen))
        with self._polish_workers_lock:
            self._polish_workers.append(worker)
        worker.start()

    def _on_polish_batch_done(self, gen: int):
        """单个润色批次完成，启动下一批或结束。"""
        if gen != self._polish_session_generation:
            logger.debug("忽略旧润色会话回调 (gen=%d != %d)", gen, self._polish_session_generation)
            return
        self._polish_completed_batches += 1
        self.status_msg.emit(f"润色进度: {self._polish_completed_batches}/{self._polish_total_batches} 批")

        if self._polish_pending_batches and not self._polish_stop_requested:
            self._launch_next_polish_batch(gen)
        elif self._polish_completed_batches >= self._polish_total_batches:
            self._on_polish_finished()

    def _on_polish_batch_error(self, err, gen: int):
        """单个润色批次出错，继续后续批次。"""
        if gen != self._polish_session_generation:
            logger.debug("忽略旧润色会话错误回调 (gen=%d != %d)", gen, self._polish_session_generation)
            return
        logger.warning("润色批次失败: %s", err)
        self._polish_completed_batches += 1
        if self._polish_pending_batches and not self._polish_stop_requested:
            self._launch_next_polish_batch(gen)
        elif self._polish_completed_batches >= self._polish_total_batches:
            self._on_polish_finished()

    def _on_polish_ready(self, row, original, polished):
        self.polish_updated.emit(row, original, polished)

    def _on_polish_finished(self):
        self._polish_in_progress = False
        # P1-2 强化：不能 "wait(100) 后无条件 clear()"。worker 在 run() 返回**之前**
        # 就发出 batch_finished，主线程可能先一步恢复；此时若丢掉最后一个 Python
        # 引用，运行中的 QThread 会被 GC，Qt 抛
        # "QThread: Destroyed while thread is still running" 直接 abort。
        # 100ms 只是经验值 —— 改为：等一个宽松上限；仍未退出的移入
        # _polish_draining 保留引用，线程结束回调里再移除。
        still_running: list = []
        with self._polish_workers_lock:
            for w in self._polish_workers:
                if w.isRunning() and not w.wait(POLISH_DRAIN_WAIT_MS):
                    still_running.append(w)
            self._polish_workers = []
            self._polish_draining.extend(still_running)
        for w in still_running:
            # 无 finished 信号的替身（测试/非 QThread）直接丢弃引用即可
            if hasattr(w, "finished"):
                w.finished.connect(lambda w=w: self._discard_draining_polish_worker(w))
        self._set_buttons(polish=True, polish_all=True)
        n = self._get_table_row_count()
        self.status_msg.emit(f"✅ 完成: {n} 条结果 | 润色完成")

    def _discard_draining_polish_worker(self, worker):
        """润色 worker 线程真正结束后从 draining 列表移除并释放。"""
        with self._polish_workers_lock:
            if worker in self._polish_draining:
                self._polish_draining.remove(worker)
        if hasattr(worker, "deleteLater"):
            worker.deleteLater()

    def extract_environment(self, summary_prompt_setter: Callable[[str], None] | None = None):
        """手动提取全文环境。"""
        results = self._get_results()
        if not results:
            self._show_error("提示", "暂无识别结果可提取环境。")
            return None

        self.status_msg.emit(_("⏳ 正在提取全文环境..."))
        all_texts = [r.get("raw", "") for r in results if r.get("raw", "").strip()]
        if not all_texts:
            self.status_msg.emit(_("⚠ 无有效文本可提取环境"))
            return None

        if self._corrector:
            env = self._corrector.extract_environment(all_texts)
            if env:
                if summary_prompt_setter:
                    summary_prompt_setter(env)
                self.status_msg.emit(_("✅ 全文环境已提取"))
            else:
                self.status_msg.emit(_("⚠ 环境提取失败，请检查 API 配置"))
            return env
        return None
