# -*- coding: utf-8 -*-
"""FFmpeg 帧读取器 —— 替代 cv2.VideoCapture，支持硬件加速解码。"""

import os
import sys
import subprocess
import shutil
import numpy as np
from pathlib import Path
from typing import Optional, Tuple

from core.logger import get_logger

logger = get_logger(__name__)

_BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Windows 包管理器常见 FFmpeg 安装路径（不在系统 PATH 中的情况）
_WIN_FFMPEG_EXTRA_PATHS = [
    # winget (Gyan.FFmpeg)
    r"C:\Program Files\FFmpeg\bin",
    # scoop
    os.path.expanduser(r"~\scoop\apps\ffmpeg\current\bin"),
    # chocolatey
    r"C:\ProgramData\chocolatey\bin",
    r"C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin",
]


def _find_ffmpeg(name: str) -> str:
    """查找 ffmpeg 系列工具：优先系统 PATH → 包管理器路径 → core/ 捆绑二进制。"""
    system = shutil.which(name)
    if system:
        return system

    if sys.platform == "win32":
        # 检查 winget/scoop/choco 安装路径（可能不在 PATH）
        for base in _WIN_FFMPEG_EXTRA_PATHS:
            candidate = os.path.join(base, f"{name}.exe")
            if os.path.isfile(candidate):
                return candidate
        return str(_BASE_DIR / "core" / f"{name}.exe")
    else:
        return str(_BASE_DIR / "core" / name)


_FFMPEG = _find_ffmpeg("ffmpeg")
_FFPROBE = _find_ffmpeg("ffprobe")


def _get_video_info(path: str) -> dict:
    """用 ffprobe 获取视频元数据。"""
    try:
        cmd = [
            _FFPROBE, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", path
        ]
        import json
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            return {}
        stdout = result.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode('utf-8', 'replace')
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
    except Exception:
        return {"duration": 0.0, "fps": 30.0, "width": 0, "height": 0}


class FFmpegReader:
    """用 FFmpeg 解码视频帧，返回 numpy RGB 数组。

    优势：
    - 自动硬件加速（h264_cuvid / hevc_cuvid）
    - 精确帧定位（seek + select frame）
    - 无 OpenCV 依赖的视频解码
    """

    def __init__(self, path: str, hw_accel: bool = False):
        self._path = path
        self._hw_accel = hw_accel
        self._proc: Optional[subprocess.Popen] = None
        self._width = 0
        self._height = 0
        self._fps = 30.0
        self._duration = 0.0
        self._frame_idx: int = 0
        self._info = _get_video_info(path)
        self._width = self._info.get("width", 0)
        self._height = self._info.get("height", 0)
        self._fps = self._info.get("fps", 30.0)
        self._duration = self._info.get("duration", 0.0)
        self._frame_idx: int = 0
        self._total_frames: int = 0

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
            _FFMPEG, "-v", "error",
            *vcodec,
            "-i", self._path,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-vsync", "0",
            "pipe:1"
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # 必须 DEVNULL，避免管道死锁
                stdin=subprocess.DEVNULL,
            )
            # 短暂等待确认进程存活
            import time; time.sleep(0.1)
            if self._proc.poll() is not None:
                self._proc = None
                return False
            return True
        except Exception:
            return False

    def read(self) -> Optional[np.ndarray]:
        """读取下一帧，返回 BGR numpy 数组。"""
        if not self._proc:
            return None
        try:
            frame_size = self._width * self._height * 3
            raw = bytearray()
            while len(raw) < frame_size:
                chunk = self._proc.stdout.read(frame_size - len(raw))
                if not chunk:
                    return None
                raw.extend(chunk)
            frame = np.frombuffer(bytes(raw), dtype=np.uint8).reshape(
                (self._height, self._width, 3))
            self._frame_idx += 1
            return frame
        except Exception:
            return None

    def seek(self, frame_idx: int) -> Optional[np.ndarray]:
        """跳转到指定帧号并读取（使用 -ss 前置快速 seek）。"""
        self.close()
        target_sec = frame_idx / self._fps if self._fps > 0 else 0

        cmd = [
            _FFMPEG, "-v", "error",
            "-ss", f"{target_sec:.3f}",
            "-i", self._path,
            "-vframes", "1",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-vsync", "0",
            "pipe:1"
        ]
        try:
            raw = subprocess.run(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL,
                                 timeout=30).stdout
            expected = self._width * self._height * 3
            if not raw or len(raw) < expected:
                logger.warning("FFmpeg seek(%d): 数据不足 (got %d, expected %d)", frame_idx, len(raw) if raw else 0, expected)
                return None
            frame = np.frombuffer(raw[:expected], dtype=np.uint8).reshape(
                (self._height, self._width, 3))
            self._frame_idx = frame_idx + 1
            if not self.open():
                logger.warning("FFmpeg seek(%d): 重新打开失败", frame_idx)
            return frame
        except Exception:
            return None

    def seek_sec(self, seconds: float) -> Optional[np.ndarray]:
        """跳转到指定秒数并读取帧。"""
        return self.seek(int(seconds * self._fps))

    def is_opened(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def close(self):
        if self._proc:
            try:
                self._proc.stdout.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def __del__(self):
        self.close()
