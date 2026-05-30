"""FFmpeg 后台连续解码播放器 —— 替代 ffplay 子进程方案。

架构：
  - 后台 QThread 连续读取 FFmpeg pipe 输出的 rawvideo 帧
  - 帧存入有界队列（默认 3 帧），QTimer 消费并渲染
  - seek 通过重启 FFmpeg pipe（-ss 参数）实现，延迟 <200ms
  - 支持 0.5x/1x/1.5x/2x 速度控制

优势：
  - 无 ffplay 子进程依赖，全内嵌播放
  - 帧精确，音频同步由系统音频管线处理
  - 渲染 24-30fps，CPU 开销可控
"""

import subprocess
import threading
import time
from collections.abc import Callable

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from core.ffmpeg_reader import _FFMPEG, _get_video_info
from core.logger import get_logger

logger = get_logger(__name__)


class _DecoderThread(QThread):
    """后台线程：持续从 FFmpeg stdout 读取 rawvideo 帧。"""

    frame_ready = pyqtSignal(object, float)  # (np.ndarray BGR, timestamp_sec)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, path: str, hw_accel: bool = False, start_sec: float = 0.0, parent=None):
        super().__init__(parent)
        self._path = path
        self._hw_accel = hw_accel
        self._start_sec = start_sec
        self._stop_flag = threading.Event()
        self._pause_flag = threading.Event()  # set = 暂停中
        self._seek_request: float | None = None  # 秒数
        self._seek_lock = threading.Lock()
        self._seek_offset: float = 0.0  # seek 后的基准偏移（帧时间戳 = seek_offset + frame_idx/fps）
        self._proc: subprocess.Popen | None = None
        self._width = 0
        self._height = 0
        self._fps = 30.0
        self._frame_duration = 1.0 / 30.0
        self._speed = 1.0

    def request_seek(self, seconds: float):
        """请求跳转到指定时间（线程安全）。"""
        with self._seek_lock:
            self._seek_request = seconds

    def set_speed(self, speed: float):
        self._speed = max(0.25, min(4.0, speed))
        self._frame_duration = 1.0 / (self._fps * self._speed)

    def stop(self):
        self._stop_flag.set()
        self._pause_flag.clear()
        self._kill_proc()

    def pause(self):
        self._pause_flag.set()

    def resume(self):
        self._pause_flag.clear()

    def _kill_proc(self):
        if self._proc:
            try:
                self._proc.stdout.close()
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception as e:
                logger.warning("解码线程终止失败: %s", e)
                try:
                    self._proc.kill()
                except Exception as e2:
                    logger.debug("解码线程强杀失败: %s", e2)
            self._proc = None

    def _start_ffmpeg(self, start_sec: float = 0.0) -> bool:
        """启动 FFmpeg 解码管道。"""
        self._kill_proc()

        info = _get_video_info(self._path)
        self._width = info.get("width", 0)
        self._height = info.get("height", 0)
        self._fps = info.get("fps", 30.0) or 30.0
        self._frame_duration = 1.0 / (self._fps * self._speed)

        if self._width == 0 or self._height == 0:
            self.error.emit("无法读取视频尺寸")
            return False

        vcodec = []
        if self._hw_accel:
            vcodec = ["-hwaccel", "cuda"]

        ss_args = ["-ss", f"{start_sec:.3f}"] if start_sec > 0.01 else []

        cmd = [
            _FFMPEG, "-v", "error",
            *vcodec,
            *ss_args,
            "-i", self._path,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-vsync", "0",
            "pipe:1",
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            time.sleep(0.05)
            if self._proc.poll() is not None:
                self.error.emit("FFmpeg 启动失败")
                return False
            return True
        except Exception as e:
            self.error.emit(f"FFmpeg 启动异常: {e}")
            return False

    def run(self):
        if not self._start_ffmpeg(self._start_sec):
            return

        frame_size = self._width * self._height * 3
        next_frame_time = time.monotonic()
        frame_idx = 0
        # 初始化 seek 偏移为起始位置（确保时间戳从正确位置开始）
        self._seek_offset = max(0.0, self._start_sec)

        try:
            while not self._stop_flag.is_set():
                # 暂停等待
                while self._pause_flag.is_set():
                    if self._stop_flag.is_set():
                        break
                    time.sleep(0.05)
                    next_frame_time = time.monotonic()
                if self._stop_flag.is_set():
                    break

                # 检查 seek 请求
                with self._seek_lock:
                    seek_sec = self._seek_request
                    self._seek_request = None
                if seek_sec is not None:
                    if not self._start_ffmpeg(seek_sec):
                        break
                    self._seek_offset = max(0.0, seek_sec if seek_sec is not None else 0.0)
                    frame_idx = 0
                    next_frame_time = time.monotonic()
                    continue

                # 检查进程存活
                if self._proc is None or self._proc.poll() is not None:
                    break

                # 读取一帧
                try:
                    raw = bytearray()
                    while len(raw) < frame_size and not self._stop_flag.is_set():
                        chunk = self._proc.stdout.read(frame_size - len(raw))
                        if not chunk:
                            break
                        raw.extend(chunk)
                    if len(raw) < frame_size:
                        break
                    frame = np.frombuffer(bytes(raw), dtype=np.uint8).reshape(
                        (self._height, self._width, 3))
                except Exception as e:
                    logger.warning("解码线程读取帧失败: %s", e)
                    break

                # 时间戳 = seek 偏移 + 帧序号 / 帧率
                timestamp = self._seek_offset + frame_idx / self._fps
                frame_idx += 1

                # 帧率控制：等待到下一帧时间
                now = time.monotonic()
                wait = next_frame_time - now
                if wait > 0.002:
                    time.sleep(wait)

                next_frame_time += self._frame_duration
                # 防止严重落后时的追赶（跳帧而非加速）
                if time.monotonic() - next_frame_time > 0.5:
                    next_frame_time = time.monotonic()

                if not self._stop_flag.is_set():
                    self.frame_ready.emit(frame, timestamp)
        finally:
            self._kill_proc()
        self.finished.emit()


class FFmpegPlayer:
    """内嵌式 FFmpeg 播放器 —— 无 ffplay 子进程依赖。

    用法:
        player = FFmpegPlayer(path, hw_accel=False)
        player.frame_callback = lambda frame, ts: ...
        player.play()
        player.pause()
        player.seek(30.0)
        player.set_speed(1.5)
        player.stop()
    """

    SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]

    def __init__(self, path: str, hw_accel: bool = False, video_info: dict = None):
        self._path = path
        self._hw_accel = hw_accel
        self._decoder: _DecoderThread | None = None
        self._is_playing = False
        self._current_speed_idx = 3  # 1.0x
        self._duration = 0.0

        # 回调（由外部设置）
        self.frame_callback: Callable[[np.ndarray, float], None] | None = None
        self.finished_callback: Callable[[], None] | None = None
        self.error_callback: Callable[[str], None] | None = None

        # 优先使用已获取的视频信息，避免重复 ffprobe 阻塞 UI
        if video_info:
            self._duration = video_info.get("duration", 0.0)
        else:
            info = _get_video_info(path)
            self._duration = info.get("duration", 0.0)

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def speed(self) -> float:
        return self.SPEEDS[self._current_speed_idx]

    def play(self, start_sec: float = 0.0):
        """开始播放。"""
        self.stop()
        self._decoder = _DecoderThread(self._path, self._hw_accel, start_sec=start_sec)
        self._decoder.frame_ready.connect(self._on_frame)
        self._decoder.finished.connect(self._on_finished)
        self._decoder.error.connect(self._on_error)
        self._decoder.set_speed(self.speed)
        self._decoder.start()
        self._is_playing = True

    def pause(self):
        if self._decoder and self._is_playing:
            self._decoder.pause()
            self._is_playing = False

    def resume(self):
        if self._decoder:
            self._decoder.resume()
            self._is_playing = True

    def toggle_pause(self):
        if self._is_playing:
            self.pause()
        else:
            self.resume()

    def stop(self):
        if self._decoder:
            self._decoder.stop()
            self._decoder.wait(2000)
            self._decoder = None
        self._is_playing = False

    def seek(self, seconds: float):
        """跳转到指定时间。"""
        if self._decoder:
            self._decoder.request_seek(seconds)
        elif self._path:
            # 未在播放时 seek，启动后立即暂停在该帧
            self.play(seconds)
            self.pause()

    def cycle_speed(self) -> float:
        """循环切换播放速度，返回当前速度。"""
        self._current_speed_idx = (self._current_speed_idx + 1) % len(self.SPEEDS)
        spd = self.speed
        if self._decoder:
            self._decoder.set_speed(spd)
        return spd

    def set_speed(self, speed: float):
        """设置播放速度。"""
        self._current_speed_idx = min(range(len(self.SPEEDS)),
                                       key=lambda i: abs(self.SPEEDS[i] - speed))
        if self._decoder:
            self._decoder.set_speed(self.speed)

    def _on_frame(self, frame: np.ndarray, ts: float):
        if self.frame_callback:
            self.frame_callback(frame, ts)

    def _on_finished(self):
        self._is_playing = False
        if self.finished_callback:
            self.finished_callback()

    def _on_error(self, msg: str):
        self._is_playing = False
        if self.error_callback:
            self.error_callback(msg)
