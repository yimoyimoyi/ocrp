"""ASR 流程编排 —— 语音识别处理、回调与结果缓存。

方法体从原 workflow_manager.py 原样搬移（AST 提取，零改动）。
未定义的属性/方法自动委托给 WorkflowManager（见 _FlowBase）。
"""

import hashlib
import json

from core.i18n import _
from core.logger import get_logger
from core.workers import AudioProcessWorker

logger = get_logger(__name__)

from core.workflow.base import _FlowBase


class ASRFlow(_FlowBase):
    """ASR 流程编排 —— 语音识别处理、回调与结果缓存。"""

    def _load_asr_cache(self, video_path: str) -> list | None:
        """从缓存加载 ASR 结果。"""

        if not self._asr_cache_file.exists():
            return None
        key = hashlib.md5(video_path.encode()).hexdigest()
        try:
            with open(self._asr_cache_file, encoding="utf-8") as f:
                cache = json.load(f)
            if key in cache:
                logger.info("ASR 缓存命中: %s", video_path)
                return cache[key]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("ASR 缓存读取失败: %s", e)
        return None

    def _save_asr_cache(self, video_path: str, results: list):
        """保存 ASR 结果到缓存。"""

        key = hashlib.md5(video_path.encode()).hexdigest()
        self._asr_cache_dir.mkdir(parents=True, exist_ok=True)
        cache = {}
        if self._asr_cache_file.exists():
            try:
                with open(self._asr_cache_file, encoding="utf-8") as f:
                    cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                cache = {}
        cache[key] = results
        # 控制缓存大小，保留最新 20 条
        if len(cache) > 20:
            keys = list(cache.keys())
            for old_key in keys[:-20]:
                del cache[old_key]
        with open(self._asr_cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info("ASR 结果已缓存: %s (%d 段)", video_path, len(results))

    def _start_asr_only(self):
        vp = self._get_video_path()
        if not vp:
            self._show_error("提示", "请先加载视频或音频文件。")
            return
        is_audio = self._get_is_audio_file()

        self._correction_pending.clear()

        self._pending_vp = vp
        self._pending_regions = []
        self._pending_ename = self._get_current_engine()

        # 释放 OCR 引擎（OCR 与 ASR 互斥）
        if self._engine_mgr:
            self._engine_mgr.release_all_engines()

        asr_engine = self._asr_mgr.get_engine() if self._asr_mgr else None
        if not asr_engine:
            self._show_error("提示", "未检测到 ASR 引擎，请先在「语音识别」标签页启用。")
            return

        mp = self._session_mp or self._get_mode_params()  # 会话快照优先（设置同步 W1）
        region_name = mp.get("asr_region_name", "语音")
        t_start, t_end = self._get_time_range()

        self._audio_worker = AudioProcessWorker(
            asr_engine,
            vp,
            is_video=not is_audio,
            time_start=t_start,
            time_end=t_end,
            asr_region_name=region_name,
            audio_cache_path=self._get_audio_cache_path(),
        )
        self._audio_worker.progress.connect(lambda m: self.status_msg.emit(m))
        self._audio_worker.progress_percent.connect(self.progress_val)
        self._audio_worker.result_item.connect(self._on_asr_result)
        self._audio_worker.finished_all.connect(self._on_asr_finished)
        self._audio_worker.error.connect(self._on_asr_error)
        self._audio_worker.start()

        self._set_buttons(start=False, correction=False, pause=True, stop=True)
        self.progress_val.emit(0)
        self.status_msg.emit(_("语音识别中..."))

    def _start_asr_worker(self, video_path: str):
        mp = self._session_mp or self._get_mode_params()  # 会话快照优先（设置同步 W1）

        # 检查 ASR 缓存
        cached = self._load_asr_cache(video_path)
        if cached:
            self.status_msg.emit(f"✅ ASR 缓存命中: {len(cached)} 段")
            for seg in cached:
                ts = seg.get("start", 0.0)
                end_ts = seg.get("end", ts + 3.0)
                from core.utils import format_time

                t_str = format_time(ts)
                text = seg.get("text", "").strip()
                if text:
                    self._on_asr_result(ts, t_str, mp.get("asr_region_name", "语音"), "whisperx", text, end_ts)
            from PySide6.QtCore import QTimer

            QTimer.singleShot(100, lambda: self._on_asr_finished(cached))
            return

        # 释放 OCR 引擎（OCR 与 ASR 互斥）
        if self._engine_mgr:
            self._engine_mgr.release_all_engines()

        asr_engine = self._asr_mgr.get_engine() if self._asr_mgr else None
        if not asr_engine:
            self.status_msg.emit(_("⚠ ASR 引擎未加载"))
            return

        region_name = mp.get("asr_region_name", "语音")
        t_start, t_end = self._get_time_range()
        self._audio_worker = AudioProcessWorker(
            asr_engine,
            video_path,
            is_video=True,
            time_start=t_start,
            time_end=t_end,
            asr_region_name=region_name,
            audio_cache_path=self._get_audio_cache_path(),
        )
        self._audio_worker.progress.connect(lambda m: self.status_msg.emit(m))
        self._audio_worker.progress_percent.connect(self.progress_val)
        self._audio_worker.result_item.connect(self._on_asr_result)
        self._audio_worker.finished_all.connect(self._on_asr_finished)
        self._audio_worker.error.connect(self._on_asr_error)
        self._audio_worker.start()
        self.status_msg.emit(_("语音识别中..."))

    def _on_asr_result(self, ts, t_str, rname, ename, raw, end_sec: float = 0.0):
        if self._filter_mgr and self._filter_mgr.matches(raw):
            self._filtered_count += 1
            return
        self.result_row.emit(ts, t_str, rname, ename, raw, 0.0, end_sec)

    def _on_asr_finished(self, results):
        # 保存 ASR 结果到缓存
        if results and self._pending_vp:
            self._save_asr_cache(self._pending_vp, results)
        n = len(results)

        # 释放 ASR 引擎（用完销毁，为 OCR 腾出资源）
        if self._asr_mgr:
            self._asr_mgr.release_all_engines()

        ocr_regions = [r for r in self._pending_regions if r.get("enabled", True)]
        if ocr_regions:
            self.status_msg.emit(f"✅ 语音识别完成: {n} 段，开始 OCR...")
            self._do_ocr_pass(self._pending_vp)
        else:
            self.status_msg.emit(f"✅ 语音识别完成: {n} 段")
            # 延迟触发完成
            from PySide6.QtCore import QTimer

            QTimer.singleShot(100, lambda: self._on_process_finished([]))

    def _on_asr_error(self, err):
        # 释放 ASR 引擎（用完销毁，为 OCR 腾出资源）
        if self._asr_mgr:
            self._asr_mgr.release_all_engines()

        ocr_regions = [r for r in self._pending_regions if r.get("enabled", True)]
        if ocr_regions:
            self.status_msg.emit(f"⚠ 语音识别失败: {err}，继续 OCR...")
            self._do_ocr_pass(self._pending_vp)
        else:
            self.status_msg.emit(f"⚠ 语音识别失败: {err}")
            self._set_buttons(start=True, stop=False)
