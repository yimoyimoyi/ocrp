# -*- coding: utf-8 -*-
"""QThread 工作线程 —— OCR 处理、AI 纠错、视频处理、批量处理。"""

import traceback
from pathlib import Path
import cv2
import numpy as np

from PyQt5.QtCore import QThread, pyqtSignal, QObject
from typing import Optional


class WorkerSignals(QObject):
    """工作线程信号集合。"""
    started = pyqtSignal()
    finished = pyqtSignal()
    error = pyqtSignal(str)
    result = pyqtSignal(object)
    progress = pyqtSignal(int, int)
    log = pyqtSignal(str)


class OCRWorker(QThread):
    """单帧 OCR 识别线程。"""

    result_ready = pyqtSignal(float, str, str, str, str)

    def __init__(self, engine, frame: np.ndarray, region: dict,
                 timestamp: float, engine_name: str):
        super().__init__()
        self._engine = engine
        self._frame = frame
        self._region = region
        self._timestamp = timestamp
        self._engine_name = engine_name

    def run(self):
        try:
            from core.frame_processor import extract_roi
            roi = extract_roi(self._frame, self._region)
            if roi is None or roi.size == 0:
                return

            prompt = self._region.get("prompt", "")
            if self._engine.engine_name == "paddleocr":
                text = self._engine.recognize(roi)
            else:
                text = self._engine.recognize(roi, prompt=prompt)

            if text and text.strip():
                from core.frame_processor import format_time
                t_str = format_time(self._timestamp)
                rname = self._region.get("name", "unknown")
                self.result_ready.emit(self._timestamp, t_str, rname, self._engine_name, text)
        except Exception:
            traceback.print_exc()


class AICorrectionWorker(QThread):
    """AI 纠错线程 —— 支持 API 文本纠错和本地引擎图像重识别。"""

    correction_ready = pyqtSignal(int, str, str)
    correction_failed = pyqtSignal(int, str)
    correction_stream = pyqtSignal(int, str)  # row, partial_text (流式增量更新)

    def __init__(self, corrector, result_index: int, raw_text: str,
                 context_texts: Optional[list] = None,
                 image: Optional[np.ndarray] = None,
                 region_correction_prompt: str = ""):
        super().__init__()
        self._corrector = corrector
        self._result_index = result_index
        self._raw_text = raw_text
        self._context_texts = context_texts or []
        self._image = image
        self._region_correction_prompt = region_correction_prompt

    def run(self):
        try:
            # 区域级纠错提示词临时覆盖
            saved = ""
            if self._region_correction_prompt and hasattr(self._corrector, '_prompt_template'):
                saved = self._corrector._prompt_template
                self._corrector._prompt_template = self._region_correction_prompt

            # 构建流式回调（逐字发射信号更新表格）
            stream_mode = getattr(self._corrector, 'stream_mode', False)
            stream_cb = None
            accumulated = ""
            if stream_mode:
                def on_stream(chunk: str):
                    nonlocal accumulated
                    accumulated += chunk
                    self.correction_stream.emit(self._result_index, accumulated)
                stream_cb = on_stream

            corrected = self._corrector.correct(self._raw_text, self._context_texts,
                                                image=self._image,
                                                stream_callback=stream_cb)

            if saved:
                self._corrector._prompt_template = saved

            if corrected and corrected != self._raw_text:
                self.correction_ready.emit(self._result_index, self._raw_text, corrected)
            else:
                self.correction_failed.emit(self._result_index, "无变化或纠错失败")
        except Exception as e:
            self.correction_failed.emit(self._result_index, str(e))


class BatchCorrectionWorker(QThread):
    """批量 AI 纠错线程 —— 使用 correct_batch() 一次提交多条，保证顺序与完整性。"""

    correction_ready = pyqtSignal(int, str, str)   # row, raw, corrected
    batch_finished = pyqtSignal()                   # 全部完成
    batch_error = pyqtSignal(str)                   # 错误信息

    def __init__(self, corrector, texts: list, context_window: int = 3,
                 max_retries: int = 3):
        """
        Args:
            corrector: AICorrector 实例
            texts: list of (row_index, raw_text)
            context_window: 上下文窗口
            max_retries: 最大重试次数
        """
        super().__init__()
        self._corrector = corrector
        self._texts = list(texts)
        self._context_window = context_window
        self._max_retries = max_retries
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            if self._stop_flag:
                return

            n = len(self._texts)
            if n == 0:
                self.batch_finished.emit()
                return

            # 构建流式回调
            stream_mode = getattr(self._corrector, 'stream_mode', False)
            stream_cb = None
            if stream_mode:
                def on_batch_stream(chunk: str):
                    # 批量模式下流式输出完整文本（无法区分单条）
                    pass  # 批量流式暂不输出到表格，只走后台日志
                stream_cb = on_batch_stream

            # 调用批量纠错
            corrected_map = self._corrector.correct_batch(
                self._texts,
                context_window=self._context_window,
                max_retries=self._max_retries,
                stream_callback=stream_cb,
            )

            if self._stop_flag:
                return

            # 发射每条结果（兼容 (row, raw) 和 (row, raw, ts, te) 两种格式）
            for item in self._texts:
                row_idx = item[0]
                raw_text = item[1]
                if row_idx in corrected_map:
                    self.correction_ready.emit(row_idx, raw_text,
                                                corrected_map[row_idx])

            self.batch_finished.emit()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.batch_error.emit(str(e))


class VideoProcessWorker(QThread):
    """视频处理线程。"""

    progress = pyqtSignal(int, int, int, str)
    log = pyqtSignal(str)
    result_item = pyqtSignal(float, str, str, str, str, float)
    finished_all = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, frame_processor, video_path: str, engine_name: str,
                 time_start: float = 0.0, time_end: float = 0.0):
        super().__init__()
        self._fp = frame_processor
        self._video_path = video_path
        self._engine_name = engine_name
        self._time_start = time_start
        self._time_end = time_end

    def run(self):
        try:
            self._fp._on_result = self._on_result
            self._fp._on_progress = self._on_progress
            self._fp._on_log = self._on_log
            self._fp._stop_flag.clear()

            results = self._fp.process_video(
                self._video_path, self._engine_name,
                time_start=self._time_start, time_end=self._time_end)
            self.finished_all.emit(results)
        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))

    def stop(self):
        """停止视频处理。"""
        self._fp.stop()

    def _on_result(self, ts, t_str, rname, engine_name, raw_text, conf: float = 0.0):
        self.result_item.emit(ts, t_str, rname, engine_name, raw_text, conf)

    def _on_progress(self, cur_sec, total_sec, queue_size, sentinel):
        self.progress.emit(cur_sec, total_sec, queue_size, sentinel)

    def _on_log(self, msg):
        self.log.emit(msg)


class ImageProcessWorker(QThread):
    """单张图片 OCR 处理线程（用于图片直接 OCR 场景）。"""

    result_item = pyqtSignal(float, str, str, str, str, float)
    finished_all = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, engine_manager, frame: np.ndarray, regions: list, timestamp: float = 0.0):
        super().__init__()
        self._engine_mgr = engine_manager
        self._frame = frame
        self._regions = regions
        self._timestamp = timestamp

    def run(self):
        try:
            from core.frame_processor import extract_roi
            results = []
            for region in self._regions:
                engine_name = region.get("engine", "")
                engine = self._engine_mgr.get_engine(engine_name) if engine_name else self._engine_mgr.get_engine()
                if engine is None:
                    continue

                roi = extract_roi(self._frame, region)
                if roi is None or roi.size == 0:
                    continue

                prompt = region.get("prompt", "")
                if engine.engine_name == "paddleocr":
                    text = engine.recognize(roi)
                else:
                    text = engine.recognize(roi, prompt=prompt)

                # 从引擎读取置信度（与 FrameProcessor._process_frame 相同逻辑）
                is_paddle = engine.engine_name == "paddleocr"
                if is_paddle:
                    conf = engine.last_confidence if hasattr(engine, 'last_confidence') else 0.0
                else:
                    conf = 1.0  # API 引擎默认可信

                if text and text.strip():
                    # 图片无时间戳用空串
                    t_str = "" if self._timestamp <= 0 else f"{int(self._timestamp // 60):02d}:{int(self._timestamp % 60):02d}"
                    rname = region.get("name", "unknown")
                    self.result_item.emit(self._timestamp, t_str, rname, engine.engine_name, text, conf)
                    results.append({
                        "timestamp": self._timestamp,
                        "time_str": t_str,
                        "region": rname,
                        "engine": engine.engine_name,
                        "raw": text,
                        "confidence": conf,
                    })
            self.finished_all.emit(results)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))

    def stop(self):
        """停止图片处理。"""
        self.quit()


class AudioProcessWorker(QThread):
    """WhisperX 语音识别线程 —— 从音频/视频中提取语音并转录。"""

    progress = pyqtSignal(str)           # 阶段描述
    result_item = pyqtSignal(float, str, str, str, str, float)  # ts, t_str, rname, ename, raw, end_sec
    log = pyqtSignal(str)
    finished_all = pyqtSignal(list)      # 返回结果列表
    error = pyqtSignal(str)

    def __init__(self, asr_engine, audio_source: str, is_video: bool = True,
                 time_start: float = 0.0, time_end: float = 0.0,
                 asr_region_name: str = "语音"):
        super().__init__()
        self._asr_engine = asr_engine
        self._audio_source = audio_source
        self._is_video = is_video
        self._time_start = time_start
        self._time_end = time_end
        self._region_name = asr_region_name
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        audio_path = None
        try:
            if self._stop_flag:
                return

            self.progress.emit("正在提取音频...")
            from core.asr_engine import extract_audio_from_video, convert_to_wav
            import os

            if self._is_video:
                audio_path = extract_audio_from_video(
                    self._audio_source,
                    time_start=self._time_start,
                    time_end=self._time_end,
                )
                if not audio_path:
                    self.error.emit("音频提取失败")
                    return
                self._was_converted = False
            else:
                # 音频文件：非 WAV 格式先转换为标准 16kHz/mono WAV
                converted = convert_to_wav(self._audio_source)
                if converted:
                    audio_path = converted
                    self._was_converted = (converted != self._audio_source)
                else:
                    self.error.emit("音频格式转换失败")
                    return

            if self._stop_flag:
                return

            self.progress.emit("正在语音识别...")

            from core.frame_processor import format_time
            results = []
            error_holder = [None]

            def _on_segment(seg):
                """每识别出一段立即发射信号，UI 实时更新。"""
                if self._stop_flag:
                    return
                ts = seg.get("start", 0.0)
                end_ts = seg.get("end", ts + 3.0)
                t_str = format_time(ts)
                text = seg.get("text", "").strip()
                if text:
                    self.result_item.emit(ts, t_str, self._region_name, "whisperx", text, end_ts)
                    results.append({
                        "timestamp": ts,
                        "end_sec": end_ts,
                        "time_str": t_str,
                        "region": self._region_name,
                        "engine": "whisperx",
                        "raw": text,
                    })

            # 🔥 流式调用：每识别出一段就实时发射 result_item
            # transcribe 仍可用作兼容（收集全部后一次性返回）
            if hasattr(self._asr_engine, 'transcribe_stream'):
                self._asr_engine.transcribe_stream(audio_path, on_segment=_on_segment, error_holder=error_holder)
            else:
                segments, err = self._asr_engine.transcribe(audio_path)
                if err:
                    error_holder[0] = err
                else:
                    for seg in (segments or []):
                        _on_segment(seg)

            if error_holder[0]:
                self.error.emit(f"语音识别失败: {error_holder[0]}")
                return

            if self._stop_flag:
                return

            # 无语音内容时给出提示
            if not results:
                self.log.emit("ASR 未检测到语音内容")

            self.finished_all.emit(results)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            # 确保临时文件被清理
            if audio_path and audio_path != self._audio_source:
                try:
                    import os
                    os.unlink(audio_path)
                except Exception:
                    pass


class BatchProcessWorker(QThread):
    """批量文件处理线程 —— 按相同区域依次处理多个文件，自动导出结果。"""

    progress_file = pyqtSignal(str, int, int)     # current_file, index, total
    progress_detail = pyqtSignal(int, int)         # cur_sec, total_sec
    result_item = pyqtSignal(float, str, str, str, str, float)
    log = pyqtSignal(str)
    finished_one = pyqtSignal(str, list)            # file_path, results
    finished_all = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, engine_manager, file_list: list, regions: list,
                 mode_params: dict, output_dir: str,
                 corrector=None):
        super().__init__()
        self._engine_mgr = engine_manager
        self._file_list = list(file_list)
        self._regions = list(regions)
        self._mode_params = dict(mode_params)
        self._output_dir = output_dir
        self._corrector = corrector
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        total = len(self._file_list)
        for idx, file_path in enumerate(self._file_list):
            if self._stop_flag:
                self.log.emit("批量处理已停止")
                break

            fname = Path(file_path).name
            self.progress_file.emit(fname, idx + 1, total)
            self.log.emit(f"正在处理 [{idx+1}/{total}]: {fname}")

            results = self._process_one_file(file_path)
            if results and not self._stop_flag:
                self.finished_one.emit(file_path, results)
                self._auto_export(file_path, results)

        self.finished_all.emit()

    def _process_one_file(self, file_path: str) -> list:
        """处理单个文件（视频或图片），返回结果列表。"""
        import cv2
        from core.frame_processor import FrameProcessor, format_time
        from pathlib import Path

        ext = Path(file_path).suffix.lower()
        results = []

        if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
            # 视频处理
            fp = FrameProcessor(engine_manager=self._engine_mgr, regions=self._regions)
            mp = self._mode_params
            if mp:
                fp._sentinel_enabled = mp.get("sentinel_enabled", True)
                fp._s_drop_ratio = mp.get("s_drop_ratio", 0.5)
                fp._s_min_text_len = mp.get("s_min_text_len", 2)
                fp._s_buffer_size = mp.get("s_buffer_size", 8)
                fp._s_sim_threshold = mp.get("s_sim_threshold", 0.85)
                fp._frame_interval = mp.get("frame_interval", 0.1)

            fp._stop_flag.clear()
            # 禁用哨兵日志打印
            fp._on_log = lambda m: None

            results = fp.process_video(file_path, self._regions[0].get("engine", "paddleocr") if self._regions else "paddleocr")
            for r in results:
                # results 是元组 (ts, t_str, rname, ename, text, conf)
                if len(r) >= 6:
                    self.result_item.emit(r[0], r[1], r[2], r[3], r[4], r[5])
                else:
                    self.result_item.emit(r[0], r[1], r[2], r[3], r[4], 0.0)
        else:
            # 图片处理
            try:
                # 使用 Unicode 安全方式读取图片
                buf = np.fromfile(file_path, dtype=np.uint8)
                frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                if frame is None:
                    self.log.emit(f"⚠ 无法读取图片: {file_path}")
                    return []
                for region in self._regions:
                    if self._stop_flag:
                        break
                    engine_name = region.get("engine", "") or ""
                    engine = self._engine_mgr.get_engine(engine_name) if engine_name else None
                    if engine is None:
                        continue

                    x, y, w, h = (region.get(k, 0) for k in ("x", "y", "w", "h"))
                    roi = frame[y:y+h, x:x+w] if (w > 0 and h > 0) else frame
                    if roi.size == 0:
                        continue

                    prompt = region.get("prompt", "")
                    if engine.engine_name == "paddleocr":
                        text = engine.recognize(roi)
                    else:
                        text = engine.recognize(roi, prompt=prompt)

                    # 读取置信度
                    is_paddle = engine.engine_name == "paddleocr"
                    conf = engine.last_confidence if is_paddle and hasattr(engine, 'last_confidence') else 1.0

                    if text and text.strip():
                        t_str = format_time(0)
                        rname = region.get("name", "unknown")
                        ts = 0.0
                        self.result_item.emit(ts, t_str, rname, engine_name, text, conf)
                        results.append({
                            "timestamp": ts,
                            "time_str": t_str,
                            "region": rname,
                            "engine": engine_name,
                            "raw": text,
                            "confidence": conf,
                        })
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.log.emit(f"⚠ 图片处理失败: {file_path}: {e}")

        return results

    def _auto_export(self, file_path: str, results: list):
        """自动将结果导出到 output 目录。"""
        from pathlib import Path
        from core.result_processor import polish_results, export_results

        output_dir = Path(self._output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(file_path).stem

        # polish_results 期望 tuple 列表 (ts, t_str, rname, engine, raw)
        # 统一处理 tuple 和 dict 两种格式
        if results and isinstance(results[0], dict):
            # dict 格式（图片路径）→ 转为 tuple
            raw_list = []
            for r in results:
                raw_list.append((
                    r.get("timestamp", 0.0),
                    r.get("time_str", ""),
                    r.get("region", "unknown"),
                    r.get("engine", ""),
                    r.get("raw", ""),
                ))
            polished = polish_results(raw_list)
        else:
            polished = polish_results(results)

        # 导出 TXT
        txt_path = output_dir / f"{stem}.txt"
        try:
            export_results(polished, str(txt_path), "txt", False, {})
            self.log.emit(f"✅ 已导出: {txt_path.name}")
        except Exception as e:
            self.log.emit(f"⚠ 导出失败: {txt_path.name}: {e}")
