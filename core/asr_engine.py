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
from core.logger import get_logger

logger = get_logger(__name__)


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
    def transcribe(self, audio_path: str) -> tuple:
        """返回 (results: List[dict], error: str|None)。"""
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
                    logger.error("ASR 子进程提前退出: %s", err[:300])
                    self._proc = None
                    return False
                line = self._proc.stderr.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                logger.debug("ASR_SERVER: %s", line.rstrip())
                if "ready" in line:
                    self._ready = True
                    return True
                if "error" in line.lower() and "Model load failed" in line:
                    err = self._proc.stderr.read()
                    logger.error("ASR 子进程启动失败: %s", err[:300])
                    self._stop_server()
                    return False

            logger.error("ASR 子进程启动超时 (120s)")
            self._stop_server()
            return False
        except Exception as e:
            logger.error("ASR 子进程启动异常: %s", e)
            self._stop_server()
            return False

    def _stop_server(self):
        if self._proc is None:
            return
        try:
            _send_json(self._proc.stdin, {"cmd": "shutdown"})
            self._proc.wait(timeout=10)
        except BrokenPipeError:
            # stdin 已关闭，直接 kill
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            # 其他异常，尝试 kill
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:
                pass
        finally:
            self._proc = None
            self._ready = False

    def _send_request(self, req: dict, timeout: float = 300.0) -> dict:
        """发送请求并等待响应（带超时保护）。

        关键设计：
        - stderr 读取线程在请求发送前启动，确保 GPU 崩溃信息不丢失
        - stdout 读取放入独立线程，Windows 上 readline 可被超时中断

        Args:
            req: 请求字典
            timeout: 读取响应的超时秒数（默认 5 分钟）
        """
        if not self._start_server():
            return {"status": "error", "message": "ASR server not available"}

        try:
            # 检查子进程是否意外退出
            if self._proc.poll() is not None:
                poll_code = self._proc.poll()
                err_output = ""
                try:
                    err_output = self._proc.stderr.read()
                except Exception:
                    pass
                self._proc = None
                self._ready = False
                return {"status": "error",
                        "message": f"ASR server exited before request (code={poll_code}): {err_output[:200]}"}

            from threading import Thread
            import time

            _result = [None]
            _exception = [None]
            _stderr_lines = []  # 收集 server 端 stderr 日志
            _stdout = self._proc.stdout
            _stderr = self._proc.stderr

            def _read_response():
                try:
                    line = _stdout.readline()
                    if line:
                        _result[0] = json.loads(line)
                    else:
                        _result[0] = None  # EOF
                except Exception as e:
                    _exception[0] = e

            def _read_stderr():
                """持续读取 stderr，收集所有日志（包括崩溃堆栈）。"""
                try:
                    for line in _stderr:
                        stripped = line.rstrip()
                        _stderr_lines.append(stripped)
                        # 实时记录到日志
                        logger.debug("ASR_SERVER: %s", stripped)
                except Exception:
                    pass

            # ⚠️ 1️⃣ 先启动 stderr 收集线程（确保崩溃信息不丢失）
            stderr_reader = Thread(target=_read_stderr, daemon=True)
            stderr_reader.start()

            # ⚠️ 2️⃣ 再发送请求
            _send_json(self._proc.stdin, req)
            logger.info("ASR 请求已发送: cmd=%s, audio=%s", req.get('cmd'), Path(req.get('audio_path','')).name)

            # ⚠️ 3️⃣ 启动 stdout 读取线程
            reader = Thread(target=_read_response, daemon=True)
            reader.start()

            # 轮询等待，每 0.5s 检查进程存活和超时
            deadline = time.time() + timeout
            while reader.is_alive() and time.time() < deadline:
                reader.join(timeout=0.5)
                if self._proc.poll() is not None:
                    break  # 进程退出，跳出循环统一处理

            # ── 进程已退出（崩溃）──
            if self._proc is not None and self._proc.poll() is not None:
                exit_code = self._proc.poll()
                # 等待 stderr 线程收集完
                if stderr_reader.is_alive():
                    stderr_reader.join(timeout=2)
                stderr_text = "\n".join(_stderr_lines[-30:]) if _stderr_lines else "(no stderr)"
                logger.error("ASR 服务器进程退出 (code=%d): %s", exit_code, stderr_text[:600])
                self._proc = None
                self._ready = False
                return {"status": "error",
                        "message": f"ASR server exited (code={exit_code}): {stderr_text[:300]}"}

            # ── 超时 ──
            if reader.is_alive():
                logger.warning("ASR 请求超时 (%ds)，强制终止", timeout)

                # 收集已有 stderr 用于诊断
                if stderr_reader.is_alive():
                    stderr_reader.join(timeout=1)
                stderr_text = "\n".join(_stderr_lines[-20:]) if _stderr_lines else "(no stderr)"
                logger.debug("超时时 stderr: %s", stderr_text[:500])

                try:
                    self._proc.kill()
                    self._proc.wait(2)
                except Exception:
                    pass
                self._proc = None
                self._ready = False
                return {"status": "error",
                        "message": f"ASR request timeout after {timeout}s. stderr: {stderr_text[:200]}"}

            # ── 读取出错 ──
            if _exception[0]:
                raise _exception[0]

            # ── 处理响应 ──
            resp = _result[0]
            if resp is None:
                # EOF — 服务器静默退出（stderr 线程可能已捕获）
                self._proc = None
                self._ready = False
                stderr_text = "\n".join(_stderr_lines[-20:]) if _stderr_lines else "(no stderr)"
                logger.error("ASR stdout EOF, stderr: %s", stderr_text[:500])
                return {"status": "error",
                        "message": f"ASR server crashed: {stderr_text[:300]}"}

            if resp.get("status") == "ok":
                lang = resp.get("detected_lang", "?")
                prob = resp.get("lang_prob", 0)
                logger.info("ASR 响应: lang=%s prob=%.2f%%, %d 条结果", lang, prob * 100, len(resp.get('results',[])))
            else:
                err_msg = resp.get("message", "unknown")
                logger.error("ASR 响应错误: %s", err_msg)
            return resp

        except Exception as e:
            import traceback
            logger.error("ASR 请求异常: %s", e)
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def transcribe(self, audio_path: str) -> tuple:
        """兼容旧接口：收集全部结果后一次性返回。"""
        results = []
        error = [None]

        def _collect(seg):
            results.append(seg)

        self.transcribe_stream(audio_path, on_segment=_collect, error_holder=error)
        return results, error[0]

    def transcribe_stream(self, audio_path: str,
                          on_segment: callable = None,
                          error_holder: list = None) -> None:
        """流式语音识别。每识别出一段就调用 on_segment({"start","end","text"})。

        on_segment 在子线程中调用，请确保线程安全。
        error_holder 用于存放错误消息（如有）。
        """
        if not self._start_server():
            if error_holder is not None:
                error_holder[0] = "ASR server not available"
            return

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

        try:
            # 检查子进程是否意外退出
            if self._proc.poll() is not None:
                poll_code = self._proc.poll()
                if error_holder is not None:
                    error_holder[0] = f"ASR server exited (code={poll_code})"
                return

            from threading import Thread
            import time

            _stderr_lines = []
            _stdout = self._proc.stdout
            _stderr = self._proc.stderr

            def _read_stderr():
                try:
                    for line in _stderr:
                        stripped = line.rstrip()
                        _stderr_lines.append(stripped)
                        logger.debug("ASR_SERVER: %s", stripped)
                except Exception:
                    pass

            stderr_reader = Thread(target=_read_stderr, daemon=True)
            stderr_reader.start()

            _send_json(self._proc.stdin, req)
            logger.info("ASR 流式请求已发送: audio=%s", Path(audio_path).name)

            # 逐行读取 segment / done / error
            deadline = time.time() + 300  # 5 分钟总超时
            while time.time() < deadline:
                # 检查进程存活
                if self._proc.poll() is not None:
                    exit_code = self._proc.poll()
                    if stderr_reader.is_alive():
                        stderr_reader.join(timeout=1)
                    stderr_text = "\n".join(_stderr_lines[-20:]) if _stderr_lines else "(no stderr)"
                    logger.error("ASR 服务器退出 (code=%d): %s", exit_code, stderr_text[:500])
                    self._proc = None
                    self._ready = False
                    if error_holder is not None:
                        error_holder[0] = f"Server crashed: {stderr_text[:200]}"
                    return

                line = _stdout.readline()
                if not line:
                    # EOF — 服务器静默退出
                    if self._proc.poll() is not None:
                        # 进程已死，上面已经处理过
                        continue
                    # 进程还活着但 stdout 关闭了？异常情况
                    stderr_text = "\n".join(_stderr_lines[-10:]) if _stderr_lines else "(no stderr)"
                    logger.error("ASR stdout EOF (服务器仍存活): %s", stderr_text[:300])
                    if error_holder is not None:
                        error_holder[0] = f"Server stopped responding: {stderr_text[:200]}"
                    return

                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue

                status = resp.get("status", "")
                if status == "segment":
                    seg = {
                        "start": resp.get("start", 0.0),
                        "end": resp.get("end", 0.0),
                        "text": resp.get("text", ""),
                    }
                    if on_segment:
                        on_segment(seg)
                elif status == "done":
                    lang = resp.get("detected_lang", "?")
                    prob = resp.get("lang_prob", 0)
                    n = resp.get("valid_segments", 0)
                    logger.info("ASR 流式完成: lang=%s prob=%.2f%%, segments=%d", lang, prob * 100, n)
                    return
                elif status == "error":
                    err_msg = resp.get("message", "unknown")
                    logger.error("ASR 流式错误: %s", err_msg)
                    if error_holder is not None:
                        error_holder[0] = err_msg
                    return

            # 超时
            logger.warning("ASR 流式超时 (300s)")
            try:
                self._proc.kill()
                self._proc.wait(2)
            except Exception:
                pass
            self._proc = None
            self._ready = False
            if error_holder is not None:
                error_holder[0] = "ASR request timeout after 300s"

        except Exception as e:
            import traceback
            print(f"[ASR] transcribe_stream 异常: {e}")
            traceback.print_exc()
            if error_holder is not None:
                error_holder[0] = str(e)

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
        """重新加载配置并重启引擎（如有运行中的子进程会先停止）。"""
        # 先停止所有运行中的子进程
        for eng in self._engines.values():
            if hasattr(eng, '_stop_server'):
                try:
                    eng._stop_server()
                except Exception:
                    pass
        self._engines.clear()
        self._config = load_asr_config()
        self._default_name = self._config.get("engine", "whisperx")

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
