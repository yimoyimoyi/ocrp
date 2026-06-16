"""FFmpeg 帧读取器 —— 替代 cv2.VideoCapture，支持硬件加速解码。"""

import atexit
import json
import os
import subprocess
import time

import numpy as np

from core.logger import get_logger
from core.utils import find_ffmpeg

logger = get_logger(__name__)

_FFMPEG = find_ffmpeg("ffmpeg")
_FFPROBE = find_ffmpeg("ffprobe")


def _get_video_info(path: str) -> dict:
    """用 ffprobe 获取视频元数据。"""
    try:
        cmd = [_FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            return {}
        stdout = result.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        data = json.loads(stdout)
        info = {"duration": 0.0, "fps": 30.0, "width": 0, "height": 0}
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                fps_str = stream.get("r_frame_rate", "30/1")
                if "/" in fps_str:
                    a, b = fps_str.split("/")
                    info["fps"] = float(a) / float(b) if float(b) > 0 else 30
                else:
                    info["fps"] = float(fps_str) or 30
                info["width"] = stream.get("width", 0)
                info["height"] = stream.get("height", 0)
                break
        fmt = data.get("format", {})
        info["duration"] = float(fmt.get("duration", 0))
        return info
    except Exception as e:
        logger.warning("获取视频信息失败: %s", e)
        return {"duration": 0.0, "fps": 30.0, "width": 0, "height": 0}


class FFmpegReader:
    """用 FFmpeg 解码视频帧，返回 numpy RGB 数组。

    优势：
    - 自动硬件加速（h264_cuvid / hevc_cuvid）
    - 精确帧定位（seek + select frame）
    - 无 OpenCV 依赖的视频解码
    """

    def __init__(self, path: str, hw_accel: bool = False):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"视频文件不存在: {path}")
        self._path = path
        self._hw_accel = hw_accel
        self._proc: subprocess.Popen | None = None
        self._closed = False
        atexit.register(self.close)
        self._width = 0
        self._height = 0
        self._fps = 30.0
        self._duration = 0.0
        self._info = _get_video_info(path)
        self._width = self._info.get("width", 0)
        self._height = self._info.get("height", 0)
        self._fps = self._info.get("fps", 30.0)
        self._duration = self._info.get("duration", 0.0)
        self._frame_idx: int = 0
        self._total_frames: int = 0
        # 预分配帧缓冲区（避免每帧 bytearray() + bytes() 拷贝）
        self._frame_size: int = self._width * self._height * 3
        self._frame_buf: bytearray = bytearray(self._frame_size) if self._frame_size > 0 else bytearray()

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def duration(self) -> float:
        return self._duration

    def open(self) -> bool:
        """启动 FFmpeg 解码管道。

        注意：stderr 必须用 DEVNULL（不能用 PIPE），否则 FFmpeg 的
        stderr 管道缓冲区填满后会阻塞 stdout 输出（经典死锁）。
        """
        if self._width == 0 or self._height == 0:
            return False

        # hw_accel 仅用于解码加速，不能使用 -hwaccel_output_format cuda
        # 否则帧保留在 GPU 内存中，pipe stdout 无法读取 rawvideo
        vcodec = []
        if self._hw_accel:
            vcodec = ["-hwaccel", "cuda"]

        cmd = [
            _FFMPEG,
            "-v",
            "error",
            *vcodec,
            "-i",
            self._path,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-vsync",
            "0",
            "pipe:1",
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # 必须 DEVNULL，避免管道死锁
                stdin=subprocess.DEVNULL,
            )
            # 短暂等待确认进程存活
            time.sleep(0.1)
            if self._proc.poll() is not None:
                self._proc = None
                return False
            self._closed = False
            return True
        except Exception as e:
            logger.warning("FFmpeg 打开视频失败: %s", e)
            return False

    def read(self) -> np.ndarray | None:
        """读取下一帧，返回 BGR numpy 数组（使用预分配 buffer，减少内存分配）。"""
        if not self._proc:
            return None
        try:
            view = memoryview(self._frame_buf)
            pos = 0
            while pos < self._frame_size:
                n = self._proc.stdout.readinto(view[pos:])
                if not n:
                    return None
                pos += n
            # copy 确保 frame 拥有独立内存（buf 会被下一帧覆盖）
            frame = np.frombuffer(self._frame_buf, dtype=np.uint8).reshape((self._height, self._width, 3)).copy()
            self._frame_idx += 1
            return frame
        except Exception as e:
            logger.warning("读取视频帧失败: %s", e)
            return None

    def _skip_frame(self):
        """跳过一帧（读取但不返回，用于小范围 seek）。"""
        if not self._proc:
            return
        try:
            view = memoryview(self._frame_buf)
            pos = 0
            while pos < self._frame_size:
                n = self._proc.stdout.readinto(view[pos:])
                if not n:
                    return
                pos += n
            self._frame_idx += 1
        except Exception:
            pass

    def seek(self, frame_idx: int) -> np.ndarray | None:
        """跳转到指定帧号并读取。

        小范围跳转（≤30 帧）：逐帧跳过，避免进程重启开销。
        大范围跳转：关闭重启 FFmpeg 进程（-ss 快速 seek）。
        """
        frames_to_skip = frame_idx - self._frame_idx
        if 0 < frames_to_skip <= 30 and self._proc and self._proc.poll() is None:
            # 小跳转：逐帧跳过（读取但不返回）
            for _ in range(frames_to_skip - 1):
                self._skip_frame()
            self._frame_idx = frame_idx
            return self.read()

        # 大跳转：关闭重启
        self.close()
        target_sec = frame_idx / self._fps if self._fps > 0 else 0

        cmd = [
            _FFMPEG,
            "-v",
            "error",
            "-ss",
            f"{target_sec:.3f}",
            "-i",
            self._path,
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-vsync",
            "0",
            "pipe:1",
        ]
        try:
            raw = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30).stdout
            expected = self._frame_size
            if not raw or len(raw) < expected:
                logger.warning(
                    "FFmpeg seek(%d): 数据不足 (got %d, expected %d)",
                    frame_idx,
                    len(raw) if raw else 0,
                    expected,
                )
                return None
            frame = np.frombuffer(raw[:expected], dtype=np.uint8).reshape((self._height, self._width, 3))
            self._frame_idx = frame_idx + 1
            if not self.open():
                logger.warning("FFmpeg seek(%d): 重新打开失败", frame_idx)
                return None
            return frame
        except Exception as e:
            logger.warning("视频 seek 失败: %s", e)
            return None

    def seek_sec(self, seconds: float) -> np.ndarray | None:
        """跳转到指定秒数并读取帧。"""
        return self.seek(int(seconds * self._fps))

    def is_opened(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._proc:
            try:
                self._proc.stdout.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception as e:
                logger.warning("FFmpeg 进程终止失败: %s", e)
                try:
                    self._proc.kill()
                except Exception as e2:
                    logger.debug("FFmpeg 进程强杀失败: %s", e2)
            self._proc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
