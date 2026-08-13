"""重建路由器 —— 参数变更 → 防抖 → 域持久化 + 引擎重建决策（方案 A）。

替换 main_window 中散落的 _schedule_asr_restart / _restart_ocr_engine /
_sync_asr_config / _sync_correction_config 与手写 diff（C5/C6）。

职责（单一出口）：
1. 域对象 changed 信号 → notify() → 防抖 400ms
2. _flush 到期：变更域 commit 写盘（业务参数持久化唯一入口）+ 按路由表重建引擎
3. ASR 运行中保护：处理进行时不杀子进程，process_finished 后补建
"""

import logging
import threading

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

_DEBOUNCE_MS = 400  # 与旧 _schedule_asr_restart 保持一致（P1-4 修复语义）


class RebuildRouter(QObject):
    """声明式路由表：域键 → 重建目标。

    - ``rebuild_requested`` 信号由 MainWindow 连接，用于状态栏/toast 反馈（批次 4）
    - 路由判定为纯函数（``route_for``），便于单测锁定
    - owner 需提供：_registry（域对象）、_engine_mgr/_asr_mgr/_corrector（引擎）、
      _workflow（is_asr_running）
    """

    rebuild_requested = Signal(str)  # "ocr" | "asr" | "corrector" | "sync" | "live" | "none"

    # 哨兵 OCR 版本变更 → 重建 OCR 引擎（UI 白名单键，经 notify_ui_key 路由）
    _OCR_REBUILD_KEYS = frozenset({"s_ocr_version"})
    # ASR 需重启子进程/重建引擎的键（模型/设备/批处理类）
    _ASR_RESTART_KEYS = frozenset(
        {
            "model_size",
            "model_dir",
            "device",
            "compute_type",
            "batch_size",
            "vad_enabled",
            "vad_min_silence_ms",
            "vad_threshold",
            "word_timestamps",
            "asr_region_name",
        }
    )
    # ASR 免重启键（sync_params_from_config 即时生效；language 可热同步——
    # 旧版任意 asr_* 变更都重启，v3 起语言切换走 sync 免杀子进程）
    _ASR_SYNC_KEYS = frozenset(
        {
            "language",
            "beam_size",
            "initial_prompt",
            "condition_on_previous_text",
            "no_speech_threshold",
            "compression_ratio_threshold",
            "temperature",
            "hotwords",
        }
    )
    # 纠错 API 配置变更 → AICorrector 重建
    _CORR_REBUILD_KEYS = frozenset({"api_key", "base_url", "model", "timeout", "engine"})
    # 纠错实例 setter 即时同步（免重建，由 _on_mode_changed 的 live 段处理）。
    # 注意：extract_environment 刻意不在其中（corr_extract_env 由 workflow 直接消费，
    # 不得同步到 AICorrector._extract_env —— R12 例外语义保留）。
    _CORR_LIVE_KEYS = frozenset({"enabled", "translate_mode", "stream_mode", "json_mode", "enable_polish"})

    def __init__(self, owner=None):
        # parent 不传 owner：owner 可能是 mock（测试）或非 QObject，仅作协作引用
        super().__init__()
        self._owner = owner
        self._pending: set[str] = set()  # 待执行的重建目标
        self._dirty_domains: set[str] = set()  # 待写盘的域（asr/correction/ocr）
        self._asr_pending: bool = False  # ASR 运行中保护：处理完成后补建
        self._timer = None  # QTimer（延迟创建，_schedule 内 import）

    # ── 路由判定（纯函数，可单测）──
    def route_for(self, domain: str, key: str) -> str:
        """变更的 (域, 键) → 重建目标。"""
        if domain == "asr":
            if key in self._ASR_RESTART_KEYS:
                return "asr"
            if key in self._ASR_SYNC_KEYS:
                return "sync"
            return "none"
        if domain == "correction":
            if key in self._CORR_REBUILD_KEYS:
                return "corrector"
            if key in self._CORR_LIVE_KEYS:
                return "live"
            return "none"
        if domain == "ocr":
            return "ocr"
        return "none"

    # ── 通知入口 ──
    def notify(self, domain: str, key: str) -> None:
        """域对象 changed 信号入口：记录待办并重置防抖。"""
        target = self.route_for(domain, key)
        if target != "none":
            self._pending.add(target)
        self._dirty_domains.add(domain)
        self._schedule()

    def notify_ui_key(self, key: str) -> None:
        """UI 白名单键（mode_params）变更入口（如 s_ocr_version）。"""
        if key in self._OCR_REBUILD_KEYS:
            self._pending.add("ocr")
            self._schedule()

    def on_process_finished(self) -> None:
        """处理会话完成：ASR 运行中保护的补建。"""
        if self._asr_pending:
            self._asr_pending = False
            self._rebuild_asr()

    def on_dialog_saved(self) -> None:
        """设置对话框 OK 后：立即执行防抖队列（域已 commit，引擎按需重建）。"""
        self._flush()

    # ── 防抖 ──
    def _schedule(self) -> None:
        if self._timer is None:
            # 延迟导入（与旧 _schedule_asr_restart 一致，便于测试 patch QTimer）
            from PySide6.QtCore import QTimer

            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self._flush)
        self._timer.start(_DEBOUNCE_MS)

    def _flush(self) -> None:
        """防抖到期：持久化变更域 + 执行重建决策。"""
        targets, self._pending = self._pending, set()
        dirty, self._dirty_domains = self._dirty_domains, set()
        owner = self._owner
        if owner is None:
            return
        # 1. 域对象持久化（业务参数写盘唯一入口；settings_dialog 的
        #    commit_to_domains 已写盘，此处幂等）
        reg = owner._registry
        if "asr" in dirty:
            reg.asr.commit()
        if "correction" in dirty:
            reg.correction.commit()
        if "ocr" in dirty:
            reg.ocr_engines.commit()
        # 2. 引擎重建（目标集合取并集；ASR 运行中保护优先）
        if "asr" in targets:
            if owner._workflow.is_asr_running():
                self._asr_pending = True  # 运行中 → 处理完成后补建
            else:
                self._rebuild_asr()
        if "sync" in targets:
            self._sync_asr_params()
        if "ocr" in targets:
            self._rebuild_ocr()
        if "corrector" in targets:
            self._rebuild_corrector()
        if targets:
            self.rebuild_requested.emit("|".join(sorted(targets)))

    # ── 执行（引擎重放均放后台线程，避免子进程启停阻塞 UI）──
    def _rebuild_asr(self) -> None:
        def _restart():
            if self._owner._asr_mgr:
                self._owner._asr_mgr.reload_config()
                self._owner._asr_mgr.get_engine()
                logger.info("ASR 引擎已重建")

        threading.Thread(target=_restart, daemon=True).start()

    def _sync_asr_params(self) -> None:
        """解码类参数免重启同步（sync_params_from_config）。"""

        def _sync():
            eng = self._owner._asr_mgr.get_engine()
            if eng and hasattr(eng, "sync_params_from_config"):
                eng.sync_params_from_config(self._owner._registry.asr.get_all())
                logger.info("ASR 解码参数已同步（免重启）")

        threading.Thread(target=_sync, daemon=True).start()

    def _rebuild_ocr(self) -> None:
        def _restart():
            self._owner._engine_mgr.reload_config()
            logger.info("OCR 配置已重载，引擎将在下次处理时重建")

        threading.Thread(target=_restart, daemon=True).start()

    def _rebuild_corrector(self) -> None:
        """纠错器重载（读盘；保留运行时模式标志，现状同 _sync_correction_config）。"""
        try:
            self._owner._corrector.reload_config()
            logger.info("纠错器配置已重载")
        except Exception as e:
            logger.warning("纠错器重载失败: %s", e)
