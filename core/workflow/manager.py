"""WorkflowManager 门面 —— 封装所有业务流程入口。

职责：信号定义、状态持有、依赖注入、入口方法（start/stop/pause/cleanup）。
各子域流程（OCR/ASR/纠错/批量）在 core/workflow/ 下，通过委托机制协作。
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.frame_processor import FrameProcessor
from core.i18n import _
from core.logger import get_logger
from core.utils import MODE_ASR_ONLY, MODE_OCR_ASR_FULL, MODE_OCR_ONLY
from core.workers import (
    AudioProcessWorker,
    BatchCorrectionWorker,
    BatchPolishWorker,
    BatchProcessWorker,
    ImageProcessWorker,
    VideoProcessWorker,
)
from core.workflow.asr_flow import ASRFlow
from core.workflow.batch_flow import BatchFlow
from core.workflow.correction_flow import CorrectionFlow
from core.workflow.ocr_flow import OCRFlow

logger = get_logger(__name__)

#: terminate() 后仍未退出的线程引用 —— 见 WorkflowManager.cleanup()
_ZOMBIE_WORKERS: list = []

#: terminate() 之后回收线程的上限（毫秒）。terminate 是异步的，这里只做有界回收，
#: 仍不退出则保留引用到进程结束（见 _ZOMBIE_WORKERS）。
TERMINATE_REAP_MS = 500

#: cleanup() 第一阶段（等待线程自行退出）的默认**全局**预算（毫秒）
DEFAULT_CLEANUP_WAIT_BUDGET_MS = 3000

#: 空闲态按钮状态（单一来源）。
#: 处理结束/出错后应恢复到此状态；此前 ui/main_window._on_process_error 手工逐个
#: setEnabled，漏掉了 polish/polish_all —— 处理出错后润色按钮会一直保持禁用。
IDLE_BUTTON_STATES: dict[str, bool] = {
    "start": True,
    "stop": False,
    "pause": False,
    "correction": True,
    "correction_all": True,
    "polish": True,
    "polish_all": True,
}


class WorkflowManager(QObject):
    """封装所有业务流程逻辑的门面。"""

    status_msg = Signal(str)  # 状态栏文本
    progress_val = Signal(int)  # 进度条 0-100
    time_display = Signal(str)  # 时间标签
    buttons_enabled = Signal(dict)  # 按钮启用状态
    error_dialog = Signal(str, str)  # 错误弹窗 (标题, 消息)
    info_dialog = Signal(str, str)  # 提示弹窗 (标题, 消息)
    result_row = Signal(float, str, str, str, str, float, float)  # (ts, t_str, rname, ename, raw, conf, end_sec)
    process_finished = Signal()  # 处理完成后通知 MainWindow 做后处理
    correction_updated = Signal(int, str, str)  # (row, raw, corrected)
    correction_stream_updated = Signal(int, str)  # (row, partial_text) 流式增量更新
    polish_updated = Signal(int, str, str)  # (row, original, polished)
    batch_progress = Signal(str, int, int)  # (fname, idx, total)
    batch_file_done = Signal(str, list)  # (file_path, results)
    batch_all_done = Signal()  # 批量全部完成
    batch_error = Signal(str)  # 批量出错

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)

        # ── 管理器（通过 configure() 注入） ──
        self._engine_mgr = None
        self._asr_mgr = None
        self._corrector = None
        self._filter_mgr = None
        self._config_mgr = None

        # ── UI 组件引用 ──
        self._video_preview = None

        # ── 工作线程 ──
        self._video_worker: VideoProcessWorker | None = None
        self._audio_worker: AudioProcessWorker | None = None
        self._image_worker: ImageProcessWorker | None = None
        self._batch_worker: BatchProcessWorker | None = None
        self._frame_processor: FrameProcessor | None = None
        self._batch_correction_workers: list[BatchCorrectionWorker] = []
        self._batch_correction_workers_lock = threading.Lock()
        self._batch_completed_count: int = 0
        self._batch_completed_count_lock = threading.Lock()
        self._batch_total_count: int = 0
        self._batch_pending_batches: deque = deque()
        # P1-1：会话代际令牌——停止/重启后旧 worker 回调作废
        self._correction_session_generation: int = 0
        self._polish_session_generation: int = 0

        # ── ASR 缓存 ──
        self._asr_cache_dir = Path(__file__).resolve().parent.parent / "output" / "llm_log"
        self._asr_cache_file = self._asr_cache_dir / "asr_cache.json"

        # ── 结果状态 ──
        self._correction_pending: set = set()
        self._filtered_count: int = 0

        # ── 批处理控制 ──
        self._correction_stop_requested: bool = False
        self._correction_in_progress: bool = False  # 防止重复提交
        self._env_extraction_running: bool = False
        self._polish_in_progress: bool = False
        self._polish_stop_requested: bool = False
        self._polish_total_batches: int = 0
        self._polish_completed_batches: int = 0
        self._polish_pending_batches: deque = deque()
        self._polish_workers: list[BatchPolishWorker] = []
        # 已完成会话但线程尚未真正退出的润色 worker（见 _on_polish_finished）：
        # 必须保留 Python 引用直到 QThread 结束，否则会被 GC 掉并触发
        # "QThread: Destroyed while thread is still running" 崩溃。
        self._polish_draining: list[BatchPolishWorker] = []
        self._polish_workers_lock = threading.Lock()

        # ── 批量状态（P1-6：删除假源 _batch_files——批量队列统一由
        # main_window._batch_files 经 _get_batch_files() 访问器提供）──
        self._batch_load_timer = None

        # ── 串行状态（ASR → OCR） ──
        self._pending_vp: str = ""
        self._pending_regions: list = []
        self._pending_ename: str = ""

        # ── 会话配置快照（设置同步 W1 修复）──
        # 处理会话开始时取一次权威 mode_params，整个会话（含批量每文件、
        # _on_process_finished 的 corr_enabled 判定）使用同一快照，
        # 避免运行中改设置导致的"部分生效/部分不生效"混乱
        self._session_mp: dict = {}

        # ── UI 访问器（通过 configure() 注入） ──
        self._get_video_path: Callable[[], str | None] = lambda: None
        self._get_is_image: Callable[[], bool] = lambda: False
        self._get_regions: Callable[[], list] = lambda: []
        self._get_batch_files: Callable[[], list] = lambda: []
        self._set_regions: Callable[[list], None] = lambda r: None
        self._get_time_range: Callable[[], tuple] = lambda: (0, 0)
        self._get_audio_cache_path: Callable[[], str | None] = lambda: None
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
        self._sort_by_time: Callable[[], None] = lambda: None
        self._get_polished_results: Callable[[float, int, bool], list] = lambda sim, ml, dedup=True: []
        self._get_table_row_count: Callable[[], int] = lambda: 0

        # ── 子域流程协作者（OCR/ASR/纠错/批量）──
        self._init_flows()

    def configure(self, **kwargs):
        """一次性注入所有依赖和 UI 访问器。"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            elif hasattr(self, f"_{key}"):
                setattr(self, f"_{key}", value)

    def clear_all_caches(self):
        """清除所有缓存（LLM 响应缓存 + ASR 结果缓存 + 调试日志）。"""
        project_root = Path(__file__).resolve().parent.parent
        count = 0

        # LLM 缓存 (output/llm_log/*.json) + 日志 (output/log/*.json)
        for cache_dir in [project_root / "output" / "llm_log", project_root / "output" / "log"]:
            if cache_dir.exists():
                for f in cache_dir.glob("*.json"):
                    try:
                        f.unlink()
                        count += 1
                    except OSError:
                        pass

        # ASR 缓存（显式文件，可能已被 glob 覆盖，双保险）
        if self._asr_cache_file.exists():
            try:
                self._asr_cache_file.unlink()
                count += 1
            except OSError:
                pass

        logger.info("已清除 %d 个缓存文件", count)
        self.status_msg.emit(f"✅ 已清除 {count} 个缓存文件")

    def _set_buttons(self, **states):
        """发射按钮状态信号。"""
        self.buttons_enabled.emit(states)

    def _show_error(self, title: str, message: str):
        self.error_dialog.emit(title, message)

    def _show_info(self, title: str, message: str):
        self.info_dialog.emit(title, message)

    def _reload_all_config(self):
        """每次操作前从 JSON 文件重新读取所有配置。"""
        if self._corrector and hasattr(self._corrector, "reload_config"):
            self._corrector.reload_config()
        if self._asr_mgr and hasattr(self._asr_mgr, "reload_config"):
            self._asr_mgr.reload_config()
        if self._engine_mgr and hasattr(self._engine_mgr, "reload_config"):
            self._engine_mgr.reload_config()
        if self._config_mgr and hasattr(self._config_mgr, "reload"):
            self._config_mgr.reload()
        # 同步 RPM 限制
        from core.llm_utils.llm_client import set_global_rpm

        mp = self._get_mode_params()
        set_global_rpm(mp.get("corr_rpm", 30))

    def is_asr_running(self) -> bool:
        """ASR 语音识别 worker 是否正在运行（用于设置变更时的运行中保护）。"""
        w = getattr(self, "_audio_worker", None)
        return bool(w and w.isRunning())

    def start_processing(self):
        """单文件处理入口（对应 MainWindow._on_start_processing）。"""
        self._correction_stop_requested = False
        # 新处理会话开始时清除旧环境上下文，防止内容切换后上下文过期
        if self._corrector and hasattr(self._corrector, "clear_env_context"):
            self._corrector.clear_env_context()
        self._reload_all_config()
        vp = self._get_video_path()
        if not vp:
            self._show_error("提示", "请加载视频或图片文件。")
            return
        logger.info("开始处理: %s", Path(vp).name)

        mode_params = self._get_mode_params()
        # 会话配置快照：本会话（含 ASR→OCR 串行、自动纠错判定）全部使用该值
        self._session_mp = dict(mode_params)
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

    def stop_processing(self):
        """停止所有正在进行的处理（OCR / ASR / 纠错 / 批量）。"""
        logger.info("停止处理")

        # 阻止纠错批处理链继续
        self._correction_stop_requested = True
        self._correction_in_progress = False

        # 阻止润色批处理链继续
        self._polish_stop_requested = True
        self._polish_in_progress = False

        # 停止环境提取 worker
        if hasattr(self, "_env_worker") and self._env_worker is not None and self._env_worker.isRunning():
            self._env_worker.quit()
            if not self._env_worker.wait(3000):
                self._env_worker.terminate()
            self._env_worker = None
            self._env_extraction_running = False

        # 视频帧处理器（sentinel / OCR 循环）
        if self._frame_processor:
            self._frame_processor.stop()

        # 终止 OCR 引擎子进程（识别中的子进程无法被 stop_flag 中断，
        # 必须显式终止才能立即停止；下次识别时 _ensure_subproc 自动重启）
        if self._engine_mgr:
            try:
                for ename in self._engine_mgr.get_engine_names():
                    eng = self._engine_mgr.get_engine(ename, warm_up=False)
                    if eng and hasattr(eng, "_stop_server"):
                        eng._stop_server()
            except Exception as e:
                logger.warning("OCR 引擎子进程终止失败: %s", e)

        # 各独立 worker
        workers = [
            ("视频", self._video_worker),
            ("图片", self._image_worker),
            ("音频", self._audio_worker),
            ("批量", self._batch_worker),
        ]
        for name, w in workers:
            if w and w.isRunning():
                if hasattr(w, "stop"):
                    w.stop()
                else:
                    w.quit()
                # 等待线程退出，超时强制终止（阻塞调用无法被 quit/stop 中断，
                # 残留线程在对象被覆盖/GC 时触发 "QThread: Destroyed while running"）
                if not w.wait(2000):
                    w.terminate()
                    w.wait(1000)
                logger.info("已停止: %s", name)

        # 清空待处理批次队列
        self._batch_pending_batches.clear()

        # 停止润色 workers（P1-1/P1-2：代际作废 + 三段式——stop 后立即 clear 会触发
        # "QThread: Destroyed while thread is still running" GC 崩溃）
        self._polish_session_generation += 1
        with self._polish_workers_lock:
            alive_polish = [w for w in (*self._polish_workers, *self._polish_draining) if w.isRunning()]
            for w in alive_polish:
                w.stop()
        for w in alive_polish:  # 锁外 wait，避免阻塞 append
            if not w.wait(2000):
                w.terminate()
                w.wait(1000)
        with self._polish_workers_lock:
            self._polish_workers.clear()
            self._polish_draining.clear()
        self._polish_pending_batches.clear()

        # 批量纠错（并行 worker 列表；P1-1：代际作废 + 三段式）
        self._correction_session_generation += 1
        with self._batch_correction_workers_lock:
            alive_corr = [w for w in self._batch_correction_workers if w.isRunning()]
            for w in alive_corr:
                if hasattr(w, "stop"):
                    w.stop()
                else:
                    w.quit()
            logger.info("已停止: %d 个批量纠错 worker", len(alive_corr))
        for w in alive_corr:  # 锁外 wait
            if not w.wait(2000):
                w.terminate()
                w.wait(1000)
        with self._batch_correction_workers_lock:
            self._batch_correction_workers.clear()

        # 🔥 重要：释放 ASR 引擎（停止时若 _stream_proc 仍在运行，
        # 不释放会导致 GPU 显存泄漏，影响后续 OCR/ASR 使用）
        if self._asr_mgr:
            self._asr_mgr.release_all_engines()

        self.status_msg.emit(_("已停止"))
        self._set_buttons(**IDLE_BUTTON_STATES)
        self.progress_val.emit(0)
        self.progress_val.emit(0)

    def pause_processing(self):
        """暂停当前处理（视频 OCR / 音频 ASR / 批量）。"""
        logger.info("暂停处理")
        paused = False
        if self._frame_processor and hasattr(self._frame_processor, "_pause_flag"):
            self._frame_processor.pause()
            paused = True
        if self._video_worker and self._video_worker.isRunning():
            if hasattr(self._video_worker, "pause"):
                self._video_worker.pause()
                paused = True
        if self._audio_worker and self._audio_worker.isRunning():
            if hasattr(self._audio_worker, "pause"):
                self._audio_worker.pause()
                paused = True
        if self._batch_worker and self._batch_worker.isRunning():
            if hasattr(self._batch_worker, "pause"):
                self._batch_worker.pause()
                paused = True
        if paused:
            self.status_msg.emit(_("已暂停"))
        else:
            self.status_msg.emit("当前无正在运行的任务")

    def resume_processing(self):
        """继续当前处理。"""
        logger.info("继续处理")
        resumed = False
        if self._frame_processor and hasattr(self._frame_processor, "_pause_flag"):
            self._frame_processor.resume()
            resumed = True
        if self._video_worker and self._video_worker.isRunning():
            if hasattr(self._video_worker, "resume"):
                self._video_worker.resume()
                resumed = True
        if self._audio_worker and self._audio_worker.isRunning():
            if hasattr(self._audio_worker, "resume"):
                self._audio_worker.resume()
                resumed = True
        if self._batch_worker and self._batch_worker.isRunning():
            if hasattr(self._batch_worker, "resume"):
                self._batch_worker.resume()
                resumed = True
        if resumed:
            self.status_msg.emit(_("继续处理"))
        else:
            self.status_msg.emit("当前无暂停的任务")

    def cleanup(self, total_wait_budget_ms: int = DEFAULT_CLEANUP_WAIT_BUDGET_MS):
        """清理所有线程 —— 有界同步清理，供关窗时调用。

        与旧实现的区别（关窗崩溃/挂死隐患）：
        - 等待阶段使用**全局**预算而非"每线程 2s"。旧写法在 N 个 worker 串行等待时
          最坏为 N×2s，作者因此把 ``cleanup()`` 丢进裸 ``threading.Thread`` 后台执行；
          但那样 Qt 对象会在主线程拆除 QApplication 的同时被外部线程访问，且进程退出
          会中途杀掉清理线程，反而更危险。
        - 不再派生内部"后台等待"线程：它会在 ``QApplication`` 拆除后继续对可能已销毁的
          QThread 调用 ``wait()``。
        - terminate 后仍未退出的线程进入 ``_ZOMBIE_WORKERS`` 保留引用，避免 Python GC
          在 QThread 仍在运行时销毁它（"QThread: Destroyed while thread is still running"）。
        """
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
        with self._batch_correction_workers_lock:
            workers.extend([w for w in self._batch_correction_workers if w.isRunning()])
            self._batch_correction_workers.clear()
        with self._polish_workers_lock:  # P1-2：cleanup 此前遗漏润色 worker（关窗崩溃隐患）
            workers.extend([w for w in self._polish_workers if w.isRunning()])
            workers.extend([w for w in self._polish_draining if w.isRunning()])
            self._polish_workers.clear()
            self._polish_draining.clear()
        if self._video_worker and self._video_worker.isRunning():
            workers.append(self._video_worker)
        if self._audio_worker and self._audio_worker.isRunning():
            workers.append(self._audio_worker)
        if self._batch_worker and self._batch_worker.isRunning():
            workers.append(self._batch_worker)
        if self._image_worker and self._image_worker.isRunning():
            workers.append(self._image_worker)
        if hasattr(self, "_env_worker") and self._env_worker is not None and self._env_worker.isRunning():
            workers.append(self._env_worker)

        # ── 第一步：对所有线程发 stop/quit 信号 ──
        for w in workers:
            try:
                if hasattr(w, "stop"):
                    w.stop()
                if hasattr(w, "quit"):
                    w.quit()
            except Exception as e:
                logger.warning("工作线程清理异常: %s", e)

        # ── 第二步：全局预算内等待线程自行退出 ──
        deadline = time.monotonic() + max(0, total_wait_budget_ms) / 1000.0
        for w in workers:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            try:
                w.wait(remaining_ms)
            except Exception as e:
                logger.warning("工作线程清理异常: %s", e)

        # ── 第三步：仅对仍未退出的线程使用 terminate（最后手段） ──
        for w in workers:
            try:
                if not w.isRunning():
                    continue
                logger.warning("线程未响应 quit，强制终止: %s", w.__class__.__name__)
                w.terminate()
                if not w.wait(TERMINATE_REAP_MS):
                    # terminate 是异步的；仍存活则保留引用防 GC 销毁运行中的 QThread
                    _ZOMBIE_WORKERS.append(w)
            except Exception as e:
                logger.warning("工作线程清理异常: %s", e)

        # ── ASR 子进程：kill 而非优雅 shutdown ──
        if self._asr_mgr:
            try:
                engine = self._asr_mgr.get_engine()
                if engine and hasattr(engine, "_stop_server"):
                    engine._stop_server()
            except Exception as e:
                logger.warning("ASR 引擎关闭异常: %s", e)

        logger.info("清理完成（workers=%d）", len(workers))

    # ═══════════════════════════════════════════════════════════════
    # 子域委托
    # ═══════════════════════════════════════════════════════════════

    def _init_flows(self):
        """创建子域流程协作者（在 __init__ 末尾调用）。"""
        self._ocr_flow = OCRFlow(self)
        self._asr_flow = ASRFlow(self)
        self._correction_flow = CorrectionFlow(self)
        self._batch_flow = BatchFlow(self)
        self._flows = (self._ocr_flow, self._asr_flow, self._correction_flow, self._batch_flow)

    def __getattr__(self, name):
        """子域方法委托：manager 未定义时在 4 个 flow 中查找。"""
        flows = self.__dict__.get("_flows", ())
        for flow in flows:
            if name in type(flow).__dict__:
                return getattr(flow, name)
        raise AttributeError(name)
