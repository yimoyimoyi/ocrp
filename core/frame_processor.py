"""视频帧处理器 —— FFmpeg 解码 + 哨兵检测。"""

import difflib
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

import numpy as np

from core.ocr_engine import OCREngineManager

if TYPE_CHECKING:
    from core.filter_manager import FilterManager


def get_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def format_time(seconds: float) -> str:
    """格式化秒数为 HH:MM:SS,mmm（SRT 标准格式）。"""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_roi(frame: np.ndarray, region: dict) -> np.ndarray | None:
    """从帧中提取区域 ROI，应用膨胀比例和四边裁剪后返回。

    规则：
    - 有自定义区域（w>0 and h>0）：膨胀比例生效，裁剪参数忽略
    - 全画幅模式（w<=0 or h<=0）：裁剪参数生效，膨胀比例忽略
    """
    x = region.get("x", 0)
    y = region.get("y", 0)
    w = region.get("w", 0)
    h = region.get("h", 0)

    if frame is None:
        return None

    # ── 有自定义区域 ──
    if w > 0 and h > 0:
        roi = frame[y:y+h, x:x+w]
        if roi.size == 0:
            return None
        # 膨胀比例
        expand_ratio = region.get("expand_ratio", 0)
        if expand_ratio > 0:
            expand_px = int(min(w, h) * expand_ratio / 100)
            if expand_px > 0:
                fx = max(0, x - expand_px)
                fy = max(0, y - expand_px)
                fw = min(frame.shape[1] - fx, w + 2 * expand_px)
                fh = min(frame.shape[0] - fy, h + 2 * expand_px)
                roi = frame[fy:fy+fh, fx:fx+fw]
                if roi.size == 0:
                    return None
        return roi

    # ── 全画幅模式（无自定义区域）—— 应用四边裁剪 ──
    crop_l = region.get("crop_left", 0)
    crop_r = region.get("crop_right", 0)
    crop_t = region.get("crop_top", 0)
    crop_b = region.get("crop_bottom", 0)
    if any([crop_l, crop_r, crop_t, crop_b]):
        rh, rw = frame.shape[:2]
        cl = min(crop_l, rw - 1)
        cr = min(crop_r, rw - 1)
        ct = min(crop_t, rh - 1)
        cb = min(crop_b, rh - 1)
        roi = frame[ct:rh-cb, cl:rw-cr]
        if roi.size == 0:
            return None
        return roi
    return frame


class FrameProcessor:
    def __init__(
        self,
        engine_manager: OCREngineManager,
        regions: list[dict[str, Any]] | None = None,
        on_result: Callable | None = None,
        on_progress: Callable | None = None,
        on_log: Callable | None = None,
        filter_manager: "FilterManager | None" = None,
    ):
        self._engine_mgr = engine_manager
        self._regions = regions or []
        self._on_result = on_result
        self._on_progress = on_progress
        self._on_log = on_log
        self._filter_mgr = filter_manager
        self._stop_flag = threading.Event()
        self._pause_flag = threading.Event()  # set 表示暂停
        self._frame_interval: float = 0.1
        self._subtitle_mode: str = "流式字幕（去重）"
        # ── 流式参数 ──
        self._sentinel_enabled: bool = True
        self._s_drop_ratio: float = 0.5
        self._s_buffer_size: int = 8
        self._s_sim_threshold: float = 0.85
        self._s_min_text_len: int = 2
        self._s_ocr_version: str = ""
        # ── 常规参数 ──
        self._r_dedup: bool = True
        self._r_sim_threshold: float = 0.9
        self._r_buffer_size: int = 5
        self._r_min_text_len: int = 2
        self._r_interval: float = 2.0

    @property
    def regions(self):
        return self._regions

    @regions.setter
    def regions(self, val):
        self._regions = val or []

    def stop(self):
        self._stop_flag.set()
        self._pause_flag.clear()  # 解除暂停等待

    def pause(self):
        self._pause_flag.set()

    def resume(self):
        self._pause_flag.clear()

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)

    def _process_frame(self, ocr_engine, frame: np.ndarray, region: dict) -> tuple:
        roi = extract_roi(frame, region)
        if roi is None or roi.size == 0:
            return ("", 0.0)
        is_paddle = ocr_engine.engine_name == "paddleocr"
        text = ocr_engine.recognize(roi) if is_paddle else ocr_engine.recognize(roi, prompt=region.get("prompt", ""))
        if is_paddle:
            conf = ocr_engine.last_confidence if hasattr(ocr_engine, 'last_confidence') else 0.0
        else:
            conf = 1.0
        return (text, conf)

    def _process_regions_parallel(self, frame: np.ndarray, engine_name: str,
                                   regions: list, max_workers: int = 4) -> list:
        """并行处理同一帧内的多个区域 OCR，大幅减少多 ROI 场景耗时。"""
        if len(regions) <= 1:
            # 单区域：直接串行，避免线程池开销
            result = []
            for region in regions:
                rname = region.get("name", "unknown")
                re_name = region.get("engine", engine_name) or engine_name
                re_engine = self._engine_mgr.get_engine(re_name)
                if not re_engine:
                    continue
                try:
                    text, conf = self._process_frame(re_engine, frame, region)
                except Exception as e:
                    self._log(f"⚠ OCR [{rname}]: {e}")
                    continue
                result.append((rname, re_name, text, conf))
            return result

        results = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(regions))) as executor:
            futures = {}
            for region in regions:
                re_name = region.get("engine", engine_name) or engine_name
                re_engine = self._engine_mgr.get_engine(re_name)
                if not re_engine:
                    continue
                future = executor.submit(self._process_frame, re_engine, frame, region)
                futures[future] = (region.get("name", "unknown"), re_name)

            for future in as_completed(futures):
                rname, re_name = futures[future]
                try:
                    text, conf = future.result()
                except Exception as e:
                    self._log(f"⚠ OCR [{rname}]: {e}")
                    continue
                results.append((rname, re_name, text, conf))
        return results

    def _prefetch_reader(self, ff, fps: float):
        """后台预读线程：提前读取下一帧，让 I/O 与 OCR 重叠。"""
        try:
            if ff.is_opened() and not self._stop_flag.is_set():
                frame = ff.read()
                if frame is not None and frame.size > 0:
                    return frame
        except Exception:
            pass
        return None

    def process_video(self, video_path: str, engine_name: str | None = None,
                      time_start: float = 0.0, time_end: float = 0.0) -> list:
        from core.ffmpeg_reader import FFmpegReader
        ff = FFmpegReader(video_path, hw_accel=False)
        if not ff.open():
            self._log(f"❌ 无法打开: {video_path}")
            return []

        fps = ff.fps
        total_sec = ff.duration
        frame_step = max(1, int(fps * self._frame_interval))

        # 哨兵模式：应用专用 OCR 模型版本（更快）
        if self._sentinel_enabled and self._s_ocr_version and self._s_ocr_version != "跟随全局":
            for region in self._regions:
                re_name = region.get("engine", engine_name) or engine_name
                re_engine = self._engine_mgr.get_engine(re_name)
                if re_engine and hasattr(re_engine, 'set_ocr_version'):
                    re_engine.set_ocr_version(self._s_ocr_version)
            self._log(f"🔤 哨兵 OCR: {self._s_ocr_version}")

        # 哨兵状态：全区域共享
        region_last_raw = {}  # rname → 上一帧原始文本
        region_last_sent = {}  # rname → 上一帧已发送文本
        region_buffer = {}  # rname → 连续相同文本计数

        all_results = []
        frame_idx = 0

        is_regular = "常规" in self._subtitle_mode
        last_regular_sec = -999.0

        self._log(f"🎬 开始: {os.path.basename(video_path)} FPS={fps:.1f} 字幕模式={self._subtitle_mode}")
        if time_start > 0:
            frame_idx = int(time_start * fps)
            ff.seek(frame_idx)

        # ── 帧预读：后台线程提前读取下一帧，让 I/O 与 OCR 重叠 ──
        prefetch_executor = ThreadPoolExecutor(max_workers=1)
        prefetch_future = None

        # 读取首帧
        frame = ff.read()
        if frame is not None:
            frame_idx += 1

        try:
            while ff.is_opened() and frame is not None:
                # ── 暂停等待 ──
                while self._pause_flag.is_set():
                    if self._stop_flag.is_set():
                        break
                    self._pause_flag.wait(0.3)
                current_sec = frame_idx / max(fps, 1)
                if time_end > 0 and current_sec >= time_end:
                    break
                if self._stop_flag.is_set():
                    self._log("⏹ 已中止")
                    break

                if frame_idx % frame_step != 0:
                    # 跳过非采样帧：直接读下一帧
                    if prefetch_future is not None:
                        frame = prefetch_future.result()
                        prefetch_future = None
                    else:
                        frame = ff.read()
                    frame_idx += 1
                    continue

                # ── 启动预读下一帧（在 OCR 期间并行执行）──
                next_future = prefetch_executor.submit(self._prefetch_reader, ff, fps)

                # ── 并行 OCR 当前帧的全部区域 ──
                frame_results = self._process_regions_parallel(frame, engine_name, self._regions)

                if is_regular:
                    # ── 常规字幕模式：按固定间隔输出，可选基本去重 ──
                    if current_sec - last_regular_sec >= self._r_interval:
                        for rname, re_name, text, conf in frame_results:
                            if self._filter_mgr and self._filter_mgr.matches(text):
                                continue
                            if text.strip():
                                if self._r_dedup:
                                    last_sent = region_last_sent.get(rname, "")
                                    sim = get_similarity(text, last_sent) if last_sent else 0.0
                                    if sim >= self._r_sim_threshold:
                                        # 缓冲区累积
                                        buf = region_buffer.get(rname, 0) + 1
                                        region_buffer[rname] = buf
                                        if buf < self._r_buffer_size:
                                            continue
                                        region_buffer[rname] = 0
                                t_str = format_time(current_sec)
                                entry = (current_sec, t_str, rname, re_name, text, conf)
                                all_results.append(entry)
                                if self._on_result:
                                    self._on_result(current_sec, t_str, rname, re_name, text, conf)
                                region_last_sent[rname] = text
                        last_regular_sec = current_sec
                else:
                    # ── 流式字幕模式：哨兵去重 ──
                    for rname, re_name, text, conf in frame_results:
                        last_raw = region_last_raw.get(rname, "")
                        last_sent = region_last_sent.get(rname, "")

                        if self._filter_mgr and self._filter_mgr.matches(text):
                            region_last_raw[rname] = text
                            continue

                        buffer_count = region_buffer.get(rname, 0)

                        if self._sentinel_enabled:
                            force_sentinel = (
                                len(last_raw) > self._s_min_text_len
                                and len(text) < len(last_raw) * self._s_drop_ratio
                            )
                            if force_sentinel:
                                if last_sent and get_similarity(text, last_sent) < self._s_sim_threshold:
                                    t_str = format_time(current_sec)
                                    entry = (current_sec, t_str, rname, re_name, last_sent, 0.0)
                                    all_results.append(entry)
                                    if self._on_result:
                                        self._on_result(current_sec, t_str, rname, re_name, last_sent, 0.0)
                                    region_last_sent[rname] = last_sent
                                    region_buffer[rname] = 0

                            if len(text) >= self._s_min_text_len:
                                sim = get_similarity(text, last_sent)
                                if sim < self._s_sim_threshold:
                                    t_str = format_time(current_sec)
                                    entry = (current_sec, t_str, rname, re_name, text, conf)
                                    all_results.append(entry)
                                    if self._on_result:
                                        self._on_result(current_sec, t_str, rname, re_name, text, conf)
                                    region_last_sent[rname] = text
                                    region_buffer[rname] = 0
                                else:
                                    region_buffer[rname] = buffer_count + 1
                                    if region_buffer[rname] >= self._s_buffer_size:
                                        t_str = format_time(current_sec)
                                        entry = (current_sec, t_str, rname, re_name, text, conf)
                                        all_results.append(entry)
                                        if self._on_result:
                                            self._on_result(current_sec, t_str, rname, re_name, text, conf)
                                        region_last_sent[rname] = text
                                        region_buffer[rname] = 0
                        else:
                            if len(text) >= self._s_min_text_len:
                                if get_similarity(text, last_sent) < self._s_sim_threshold:
                                    t_str = format_time(current_sec)
                                    entry = (current_sec, t_str, rname, re_name, text, conf)
                                    all_results.append(entry)
                                    if self._on_result:
                                        self._on_result(current_sec, t_str, rname, re_name, text, conf)
                                    region_last_sent[rname] = text

                        region_last_raw[rname] = text

                if self._on_progress:
                    s = region_last_raw.get(self._regions[0].get("name", ""), "")[:12] if self._regions else ""
                    self._on_progress(int(current_sec), int(total_sec), 0, s)

                # ── 获取预读的下一帧 ──
                try:
                    frame = next_future.result()
                except Exception:
                    frame = ff.read()
                frame_idx += 1

        finally:
            prefetch_executor.shutdown(wait=False)
            ff.close()
        self._log(f"✅ 完成: {len(all_results)} 条")
        return all_results
