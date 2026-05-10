# -*- coding: utf-8 -*-
"""ASR —— 子进程隔离方案。

通过 core/asr_server.py 子进程运行 faster-whisper，
彻底隔离 CUDA DLL 环境（避免与 PaddleOCR 的 cuda12/ 冲突）。

通信协议：stdin/stdout JSON 行。
"""

import os, sys, json, subprocess, shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List, Dict

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"


# Windows 包管理器常见 FFmpeg 安装路径（不在系统 PATH 中的情况）
_WIN_FFMPEG_EXTRA_PATHS = [
    r"C:\Program Files\FFmpeg\bin",
    os.path.expanduser(r"~\scoop\apps\ffmpeg\current\bin"),
    r"C:\ProgramData\chocolatey\bin",
    r"C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin",
]


def _find_ffmpeg() -> str:
    """查找 ffmpeg：优先系统 PATH → 包管理器路径 → core/ 捆绑二进制。"""
    system = shutil.which("ffmpeg")
    if system:
        return system

    if sys.platform == "win32":
        for base in _WIN_FFMPEG_EXTRA_PATHS:
            candidate = os.path.join(base, "ffmpeg.exe")
            if os.path.isfile(candidate):
                return candidate
        return str(BASE_DIR / "core" / "ffmpeg.exe")
    else:
        return str(BASE_DIR / "core" / "ffmpeg")


_FFMPEG = _find_ffmpeg()
_DEFAULT_MODEL_DIR = str(BASE_DIR / "models" / "asr")


def scan_local_asr_models(model_dir: str = "") -> List[str]:
    """扫描本地模型目录，返回所有包含 model.bin 的子目录名（或路径）。

    返回的每一项可直接作为 WhisperModel 的 model_size_or_path 参数。
    """
    target = model_dir or _DEFAULT_MODEL_DIR
    if not os.path.isdir(target):
        return []
    models = []
    try:
        for entry in os.scandir(target):
            if entry.is_dir():
                sub_path = os.path.join(target, entry.name)
                has_bin = False
                for root, dirs, files in os.walk(sub_path):
                    if "model.bin" in files:
                        has_bin = True
                        break
                if has_bin:
                    models.append(sub_path)
        # 也检查顶层目录本身
        if os.path.isfile(os.path.join(target, "model.bin")):
            models.insert(0, target)
    except Exception:
        pass
    return models

# ── 不在模块级别加载任何 torch/cuda DLL ──
# 子进程 server 有自己隔离的 DLL 环境

from config_manager import _load_json_with_comments


_DEFAULT_CONFIG = {
    "enabled": False, "engine": "whisperx", "model_size": "large-v3",
    "model_dir": _DEFAULT_MODEL_DIR, "language": "zh", "device": "cuda",
    "compute_type": "float16", "batch_size": 16,
    "vad_enabled": False, "vad_min_silence_ms": 500, "vad_threshold": 0.5,
    "word_timestamps": True, "beam_size": 5,
    "initial_prompt": "", "condition_on_previous_text": True,
    "no_speech_threshold": 0.6, "compression_ratio_threshold": 2.4,
    "temperature": "0.0,0.2,0.4,0.6,0.8,1.0", "hotwords": "",
    "asr_region_name": "语音",
}


def load_asr_config() -> dict:
    p = CONFIG_DIR / "asr_engines.json"
    if p.exists():
        try:
            cfg = _load_json_with_comments(p)
            for k, v in _DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(_DEFAULT_CONFIG)


def _parse_temperature(val: str) -> list:
    """将逗号分隔的温度字符串解析为 float 列表。"""
    try:
        parts = [v.strip() for v in val.split(",") if v.strip()]
        return [float(v) for v in parts] if parts else [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    except Exception:
        return [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


class BaseASREngine(ABC):
    def __init__(self, c):
        self.config = c
        self.engine_name = "base"

    @abstractmethod
    def transcribe(self, audio_path: str) -> List[dict]:
        pass

    def is_available(self):
        return True

    def warm_up(self):
        pass


class WhisperXEngine(BaseASREngine):
    """子进程隔离版 ASR 引擎。

    启动 core/asr_server.py 子进程，通过 stdin/stdout JSON 行通信。
    子进程有独立 DLL 空间，不加载 core/cuda12/，
    因此不会与 PaddleOCR 的 CUDA DLL 冲突。
    """

    def __init__(self, c):
        super().__init__(c)
        self.engine_name = "whisperx"
        self._proc: Optional[subprocess.Popen] = None
        self._model_size = c.get("model_size", "large-v3")
        self._model_dir = c.get("model_dir", _DEFAULT_MODEL_DIR) or ""
        self._language = c.get("language", "zh")
        self._device = c.get("device", "cuda")
        self._compute_type = c.get("compute_type", "float16")
        self._beam_size = c.get("beam_size", 5)
        self._initial_prompt = c.get("initial_prompt", "") or None
        self._condition_on_prev = c.get("condition_on_previous_text", True)
        self._no_speech_thresh = c.get("no_speech_threshold", 0.6)
        self._comp_ratio_thresh = c.get("compression_ratio_threshold", 2.4)
        self._temperature_str = c.get("temperature", "0.0,0.2,0.4,0.6,0.8,1.0")
        self._hotwords = c.get("hotwords", "") or None
        self._vad_enabled = c.get("vad_enabled", False)
        self._vad_min_silence = c.get("vad_min_silence_ms", 500)
        self._vad_threshold = c.get("vad_threshold", 0.5)
        self._word_timestamps = c.get("word_timestamps", True)
        self._hw_accel = True
        self._ready = False

    @staticmethod
    def get_available_models(model_dir: str = "") -> List[str]:
        """返回本地可用的 ASR 模型列表（路径），供 UI 填充下拉框。"""
        return scan_local_asr_models(model_dir)

    def set_model(self, model_path_or_size: str):
        """动态切换模型（需重启子进程）。"""
        if model_path_or_size != self._model_size:
            self._model_size = model_path_or_size
            self._stop_server()

    def set_hw_accel(self, e: bool):
        self._hw_accel = e
        self._device = "cuda" if e else "cpu"
        self._compute_type = "float16" if e else "int8"
        # 需要重启子进程才能生效
        self._stop_server()

    def _start_server(self) -> bool:
        """启动 ASR 子进程服务器。"""
        if self._proc is not None:
            return self._ready

        _ASR_SERVER = str(BASE_DIR / "core" / "asr_server.py")
        _CONFIG_PATH = str(CONFIG_DIR / "asr_engines.json")
        _PYTHON = sys.executable

        try:
            self._proc = subprocess.Popen(
                [_PYTHON, _ASR_SERVER, "--config", _CONFIG_PATH],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            # 等待 "ready" 信号（stderr 输出）
            import time
            deadline = time.time() + 120  # 模型加载最多等 2 分钟
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    err = self._proc.stderr.read()
                    print(f"[ASR] server exited early: {err}")
                    self._proc = None
                    return False
                line = self._proc.stderr.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                print(f"[ASR_SERVER] {line.rstrip()}")
                if "ready" in line:
                    self._ready = True
                    return True
                if "error" in line.lower() and "Model load failed" in line:
                    err = self._proc.stderr.read()
                    print(f"[ASR] server failed: {err}")
                    self._stop_server()
                    return False

            print("[ASR] server startup timeout")
            self._stop_server()
            return False
        except Exception as e:
            print(f"[ASR] server start failed: {e}")
            self._stop_server()
            return False

    def _stop_server(self):
        if self._proc is None:
            return
        try:
            _send_json(self._proc.stdin, {"cmd": "shutdown"})
            self._proc.wait(timeout=10)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        self._ready = False

    def _send_request(self, req: dict) -> dict:
        """发送请求并等待响应。"""
        if not self._start_server():
            return {"status": "error", "message": "ASR server not available"}

        try:
            _send_json(self._proc.stdin, req)
            line = self._proc.stdout.readline()
            if not line:
                return {"status": "error", "message": "No response from ASR server"}
            return json.loads(line)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def transcribe(self, audio_path: str) -> List[dict]:
        if not self._start_server():
            return []

        req = {
            "cmd": "transcribe",
            "audio_path": audio_path,
            "language": self._language,
            "beam_size": self._beam_size,
            "initial_prompt": self._initial_prompt,
            "condition_on_previous_text": self._condition_on_prev,
            "no_speech_threshold": self._no_speech_thresh,
            "compression_ratio_threshold": self._comp_ratio_thresh,
            "temperature": self._temperature_str,
            "hotwords": self._hotwords,
            "vad_enabled": self._vad_enabled,
            "vad_min_silence_ms": self._vad_min_silence,
            "vad_threshold": self._vad_threshold,
            "word_timestamps": self._word_timestamps,
        }

        resp = self._send_request(req)
        if resp.get("status") == "ok":
            lang = resp.get("detected_lang", "?")
            prob = resp.get("lang_prob", 0)
            print(f"[ASR] detected lang: {lang} prob: {prob:.2%}")
            return resp.get("results", [])
        else:
            print(f"[ASR] fail: {resp.get('message', 'unknown')}")
            return []

    def warm_up(self):
        """主线程同步预加载模型（通过启动子进程）。"""
        self._start_server()

    def __del__(self):
        self._stop_server()


def extract_audio_from_video(video_path: str, output_dir: str = None,
                              time_start: float = 0.0, time_end: float = 0.0,
                              sample_rate: int = 16000) -> Optional[str]:
    if output_dir:
        d = Path(output_dir)
        d.mkdir(parents=True, exist_ok=True)
        wp = d / f"asr_{Path(video_path).stem}.wav"
    else:
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".wav", prefix="orcp_asr_")
        os.close(fd)
        wp = Path(p)
    cmd = [_FFMPEG, "-v", "error", "-i", str(video_path),
           "-vn", "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1", "-y", str(wp)]
    if time_start > 0:
        cmd.insert(-1, "-ss")
        cmd.insert(-1, str(time_start))
    if time_end > 0:
        cmd.insert(-1, "-to")
        cmd.insert(-1, str(time_end))
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        if wp.exists() and wp.stat().st_size > 1024:
            return str(wp)
    except Exception as e:
        print(f"[ASR] extract fail: {e}")
    return None


def convert_to_wav(audio_path: str, output_dir: str = None,
                   sample_rate: int = 16000) -> Optional[str]:
    """将任意音频文件转换为标准 WAV 格式（16kHz/mono/16bit）。"""
    ext = Path(audio_path).suffix.lower()
    # 如果已经是 WAV 且参数匹配，直接返回
    if ext == ".wav":
        return audio_path
    if output_dir:
        d = Path(output_dir)
        d.mkdir(parents=True, exist_ok=True)
        wp = d / f"asr_{Path(audio_path).stem}.wav"
    else:
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".wav", prefix="orcp_asr_")
        os.close(fd)
        wp = Path(p)
    cmd = [_FFMPEG, "-v", "error", "-i", str(audio_path),
           "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1", "-y", str(wp)]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        if wp.exists() and wp.stat().st_size > 1024:
            return str(wp)
    except Exception as e:
        print(f"[ASR] convert fail: {e}")
    return None


SUPPORTED_AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus', '.aiff'}


class ASREngineManager:
    def __init__(self):
        self._engines: Dict[str, BaseASREngine] = {}
        self._config = load_asr_config()
        self._default_name = self._config.get("engine", "whisperx")

    def reload_config(self):
        self._config = load_asr_config()
        self._engines.clear()

    def set_hw_accel(self, e: bool):
        for eng in self._engines.values():
            if hasattr(eng, 'set_hw_accel'):
                eng.set_hw_accel(e)

    def get_engine(self, name: Optional[str] = None) -> Optional[BaseASREngine]:
        en = name or self._default_name
        if en in self._engines:
            return self._engines[en]
        if en != "whisperx":
            return None
        eng = WhisperXEngine(self._config)
        self._engines[en] = eng
        if hasattr(eng, 'warm_up'):
            eng.warm_up()
        return eng

    def get_current_engine(self):
        return self.get_engine()

    @property
    def engine_name(self):
        return self._default_name


def _send_json(fp, obj: dict):
    """原子写入 JSON 行。"""
    line = json.dumps(obj, ensure_ascii=False)
    fp.write(line + "\n")
    fp.flush()
