"""ASR —— 子进程隔离方案。

通过 core/asr_server.py 子进程运行 faster-whisper，
彻底隔离 CUDA DLL 环境（避免与 PaddleOCR 的 cuda12/ 冲突）。

通信协议：stdin/stdout JSON 行。
"""

import atexit
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from core.logger import get_logger
from core.utils import DEFAULT_ASR_MODEL_DIR, find_ffmpeg

logger = get_logger(__name__)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"

_FFMPEG = find_ffmpeg("ffmpeg")
_DEFAULT_MODEL_DIR = DEFAULT_ASR_MODEL_DIR


def scan_local_asr_models(model_dir: str = "") -> list[str]:
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
                for _root, _dirs, files in os.walk(sub_path):
                    if "model.bin" in files:
                        has_bin = True
                        break
                if has_bin:
                    models.append(sub_path)
        # 也检查顶层目录本身
        if os.path.isfile(os.path.join(target, "model.bin")):
            models.insert(0, target)
    except Exception as e:
        logger.warning("扫描 ASR 模型目录失败: %s", e)
    return models

from core.config_manager import _load_json_with_comments
from core.logger import get_logger

# ── 不在模块级别加载任何 torch/cuda DLL ──
# 子进程 server 有自己隔离的 DLL 环境

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
            from core.config_schema import validate_config
            from core.config_schemas import ASR_ENGINES_SCHEMA
            validate_config(cfg, ASR_ENGINES_SCHEMA, "asr_engines.json")
            for k, v in _DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception as e:
            logger.warning("加载 ASR 配置失败: %s", e)
    return dict(_DEFAULT_CONFIG)


def _parse_temperature(val: str) -> list:
    """将逗号分隔的温度字符串解析为 float 列表。"""
    try:
        parts = [v.strip() for v in val.split(",") if v.strip()]
        return [float(v) for v in parts] if parts else [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    except Exception as e:
        logger.warning("解析温度参数失败: %s", e)
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

    @abstractmethod
    def warm_up(self):
        ...


class WhisperXEngine(BaseASREngine):
    """子进程隔离版 ASR 引擎。

    启动 core/asr_server.py 子进程，通过 stdin/stdout JSON 行通信。
    子进程有独立 DLL 空间，不加载 core/cuda12/，
    因此不会与 PaddleOCR 的 CUDA DLL 冲突。
    """

    def __init__(self, c):
        super().__init__(c)
        self.engine_name = "whisperx"
        self._proc: subprocess.Popen | None = None
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
        self._closed = False
        self._start_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._stderr_lines: list = []
        self._stderr_drain_thread: threading.Thread | None = None
        atexit.register(self._stop_server)

    @staticmethod
    def get_available_models(model_dir: str = "") -> list[str]:
        """返回本地可用的 ASR 模型列表（路径），供 UI 填充下拉框。"""
        return scan_local_asr_models(model_dir)

    def set_model(self, model_path_or_size: str):
        """动态切换模型（需重启子进程）。"""
        if model_path_or_size != self._model_size:
            self._model_size = model_path_or_size
            self._stop_server()

    def sync_params_from_config(self, config: dict):
        """从配置同步运行时参数（无需重启子进程）。"""
        self._language = config.get("language", self._language)
        self._beam_size = config.get("beam_size", self._beam_size)
        self._initial_prompt = config.get("initial_prompt", "") or None
        self._condition_on_prev = config.get("condition_on_previous_text", self._condition_on_prev)
        self._no_speech_thresh = config.get("no_speech_threshold", self._no_speech_thresh)
        self._comp_ratio_thresh = config.get("compression_ratio_threshold", self._comp_ratio_thresh)
        self._temperature_str = config.get("temperature", self._temperature_str)
        self._hotwords = config.get("hotwords", "") or None
        self._vad_enabled = config.get("vad_enabled", self._vad_enabled)
        self._vad_min_silence = config.get("vad_min_silence_ms", self._vad_min_silence)
        self._vad_threshold = config.get("vad_threshold", self._vad_threshold)
        self._word_timestamps = config.get("word_timestamps", self._word_timestamps)

    def set_hw_accel(self, e: bool):
        self._hw_accel = e
        self._device = "cuda" if e else "cpu"
        self._compute_type = "float16" if e else "int8"
        # 需要重启子进程才能生效
        self._stop_server()

    def _start_server(self) -> bool:
        """启动 ASR 子进程服务器（线程安全，首次调用阻塞直到模型加载完成）。"""
        # 快速路径：已就绪
        if self._ready and self._proc and self._proc.poll() is None:
            return True

        # 另一个线程正在加载 → 等待其完成
        if self._proc is not None and self._proc.poll() is None and not self._ready:
            logger.info("ASR 子进程正在加载中，等待完成...")
            self._ready_event.wait(timeout=120)
            return self._ready

        with self._start_lock:
            # 双重检查
            if self._ready and self._proc and self._proc.poll() is None:
                return True

            # 清理死进程
            if self._proc is not None:
                self._stop_server()

            self._ready_event.clear()

            _ASR_SERVER = str(BASE_DIR / "core" / "asr_server.py")
            _CONFIG_PATH = str(CONFIG_DIR / "asr_engines.json")
            _PYTHON = sys.executable

            # 将当前 device/compute_type 通过命令行传给子进程，确保 hw_accel 状态生效
            _cmd = [_PYTHON, _ASR_SERVER, "--config", _CONFIG_PATH,
                    "--device", self._device,
                    "--compute-type", self._compute_type]

            try:
                self._proc = subprocess.Popen(
                    _cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    bufsize=1,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
            except Exception as e:
                logger.error("ASR 子进程启动异常: %s", e)
                self._proc = None
                return False

            # 启动 stderr 排空线程（确保管道不会因缓冲区满而死锁）
            self._stderr_lines.clear()
            self._stderr_drain_thread = threading.Thread(
                target=self._drain_stderr, daemon=True)
            self._stderr_drain_thread.start()

            # 等待 "ready" 信号（从 stderr_lines 中检测）
            deadline = time.time() + 120
            while time.time() < deadline:
                if self._proc is None or self._proc.poll() is not None:
                    err = "\n".join(self._stderr_lines[-30:])
                    code = self._proc.poll() if self._proc is not None else -1
                    logger.error("ASR 子进程提前退出 (code=%d): %s",
                                 code, err[:300])
                    self._stop_server()
                    return False

                # 检查 stderr_lines 中的关键消息
                for line in list(self._stderr_lines):
                    if "ready" in line:
                        logger.info("ASR 子进程就绪")
                        self._ready = True
                        self._closed = False
                        self._ready_event.set()
                        return True
                    if "error" in line.lower() and "Model load failed" in line:
                        err = "\n".join(self._stderr_lines[-20:])
                        logger.error("ASR 子进程启动失败: %s", err[:300])
                        self._stop_server()
                        return False

                time.sleep(0.1)

            logger.error("ASR 子进程启动超时 (120s)")
            self._stop_server()
            return False

    def _drain_stderr(self):
        """后台线程：持续排空子进程 stderr，防止管道缓冲区满导致死锁。"""
        with contextlib.suppress(Exception):
            for line in self._proc.stderr:
                self._stderr_lines.append(line.rstrip())

    def _stop_server(self):
        if self._closed:
            return
        if self._proc is None:
            self._ready_event.set()
            return
        self._ready = False
        self._ready_event.set()  # 唤醒所有等待 _ready_event 的线程
        try:
            _send_json(self._proc.stdin, {"cmd": "shutdown"})
            self._proc.wait(timeout=10)
        except BrokenPipeError:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception as e:
                logger.warning("ASR 子进程 kill 失败: %s", e)
        except Exception as e:
            logger.debug("停止 ASR 子进程异常: %s", e)
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception as e2:
                logger.debug("ASR 子进程强制 kill 失败: %s", e2)
        finally:
            # 关闭 stdin 管道，避免垃圾回收时 TextIOWrapper 报 OSError
            if self._proc and self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except OSError:
                    pass
            self._proc = None
            self._stderr_lines.clear()
            self._closed = True

    # ── 子进程通信辅助 ──

    def _check_process_alive(self) -> str | None:
        """检查子进程是否存活，返回错误信息或 None。"""
        if self._proc and self._proc.poll() is not None:
            code = self._proc.poll()
            err = "\n".join(self._stderr_lines[-20:]) if self._stderr_lines else "(no stderr)"
            if self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except OSError:
                    pass
            self._proc = None
            self._ready = False
            self._ready_event.clear()
            return f"ASR server exited (code={code}): {err[:200]}"
        return None

    def _start_stderr_reader(self):
        """返回当前 stderr 排空状态（线程已在 _start_server 中启动）。"""
        return self._stderr_drain_thread, self._stderr_lines

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

        # 捕获本地引用，防止 _stop_server() 在另一个线程中置 _proc=None 导致 NPE
        proc = self._proc
        if proc is None:
            return {"status": "error", "message": "ASR server not available"}

        try:
            from threading import Thread

            _result = [None]
            _exception = [None]

            def _read_response():
                try:
                    line = proc.stdout.readline()
                    _result[0] = json.loads(line) if line else None
                except Exception as e:
                    _exception[0] = e

            # 1. 启动 stderr 收集线程 + 2. 发送请求
            stderr_reader, stderr_lines = self._start_stderr_reader()
            _send_json(proc.stdin, req)

            # 3. 启动 stdout 读取线程
            reader = Thread(target=_read_response, daemon=True)
            reader.start()

            # 轮询等待，每 0.5s 检查进程存活和超时
            deadline = time.time() + timeout
            while reader.is_alive() and time.time() < deadline:
                reader.join(timeout=0.5)
                if proc.poll() is not None:
                    break  # 进程退出，跳出循环统一处理

            # ── 进程已退出（崩溃）──
            if proc.poll() is not None:
                exit_code = proc.poll()
                if stderr_reader.is_alive():
                    stderr_reader.join(timeout=2)
                stderr_text = "\n".join(stderr_lines[-30:]) if stderr_lines else "(no stderr)"
                logger.error("ASR 服务器进程退出 (code=%d): %s", exit_code, stderr_text[:600])
                self._proc = None
                self._ready = False
                return {"status": "error",
                        "message": f"ASR server exited (code={exit_code}): {stderr_text[:300]}"}

            # ── 超时 ──
            if reader.is_alive():
                logger.warning("ASR 请求超时 (%ds)，强制终止", timeout)

                if stderr_reader.is_alive():
                    stderr_reader.join(timeout=1)
                stderr_text = "\n".join(stderr_lines[-20:]) if stderr_lines else "(no stderr)"
                logger.debug("超时时 stderr: %s", stderr_text[:500])

                try:
                    proc.kill()
                    proc.wait(2)
                except Exception as e:
                    logger.debug("ASR 超时 kill 失败: %s", e)
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
                stderr_text = "\n".join(stderr_lines[-20:]) if stderr_lines else "(no stderr)"
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
                          on_segment: Callable | None = None,
                          error_holder: list | None = None) -> None:
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
            err = self._check_process_alive()
            if err:
                if error_holder is not None:
                    error_holder[0] = err
                return

            # 捕获本地引用，防止 _stop_server() 在另一个线程中置 _proc=None 导致 NPE
            proc = self._proc
            if proc is None:
                if error_holder is not None:
                    error_holder[0] = "ASR server not available"
                return

            import time

            stderr_reader, stderr_lines = self._start_stderr_reader()

            _send_json(proc.stdin, req)
            logger.info("ASR 流式请求已发送: audio=%s", Path(audio_path).name)

            # 逐行读取 segment / done / error
            deadline = time.time() + 300  # 5 分钟总超时
            while time.time() < deadline and proc is not None:
                # 检查进程存活
                if proc.poll() is not None:
                    exit_code = proc.poll()
                    if stderr_reader.is_alive():
                        stderr_reader.join(timeout=1)
                    stderr_text = "\n".join(stderr_lines[-20:]) if stderr_lines else "(no stderr)"
                    logger.error("ASR 服务器退出 (code=%d): %s", exit_code, stderr_text[:500])
                    self._proc = None
                    self._ready = False
                    if error_holder is not None:
                        error_holder[0] = f"Server crashed: {stderr_text[:200]}"
                    return

                line = proc.stdout.readline()
                if not line:
                    # EOF — 服务器静默退出
                    if proc.poll() is not None:
                        # 进程已死，上面已经处理过
                        continue
                    # 进程还活着但 stdout 关闭了？异常情况
                    stderr_text = "\n".join(stderr_lines[-10:]) if stderr_lines else "(no stderr)"
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
                proc.kill()
                proc.wait(2)
            except Exception as e:
                logger.debug("ASR 超时 kill 失败: %s", e)
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
        """后台线程预加载模型（不阻塞调用线程）。"""
        import threading
        threading.Thread(target=self._start_server, daemon=True).start()

    def __del__(self):
        with contextlib.suppress(Exception):
            self._stop_server()


def _ffmpeg_to_wav(input_path: str, output_dir: str = None,
                   sample_rate: int = 16000, extra_args: list = None) -> str | None:
    """通用 FFmpeg WAV 转换：输出路径构建 + subprocess 执行 + 验证。"""
    if not os.path.isfile(input_path):
        logger.warning("音频文件不存在: %s", input_path)
        return None
    if output_dir:
        d = Path(output_dir)
        d.mkdir(parents=True, exist_ok=True)
        wp = d / f"asr_{Path(input_path).stem}.wav"
    else:
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".wav", prefix="orcp_asr_")
        os.close(fd)
        wp = Path(p)
    cmd = [_FFMPEG, "-v", "error", "-i", str(input_path),
           "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1", "-y"]
    if extra_args:
        cmd += extra_args
    cmd.append(str(wp))
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        if wp.exists() and wp.stat().st_size > 1024:
            return str(wp)
    except Exception as e:
        logger.error("FFmpeg WAV 转换失败: %s", e)
    return None


def extract_audio_from_video(video_path: str, output_dir: str = None,
                              time_start: float = 0.0, time_end: float = 0.0,
                              sample_rate: int = 16000,
                              cache_path: str = None) -> str | None:
    """从视频提取音频为 WAV。若提供 cache_path（预览缓存）且无时间范围，直接复用。"""
    # 无时间范围且有缓存 → 直接复用，跳过 FFmpeg 提取
    if cache_path and os.path.isfile(cache_path) and time_start == 0.0 and time_end == 0.0:
        logger.info("复用预览缓存音频: %s", cache_path)
        return cache_path
    extra = []
    if time_start > 0:
        extra += ["-ss", str(time_start)]
    if time_end > 0:
        extra += ["-to", str(time_end)]
    return _ffmpeg_to_wav(video_path, output_dir, sample_rate, extra_args=["-vn", *extra])


def convert_to_wav(audio_path: str, output_dir: str = None,
                   sample_rate: int = 16000) -> str | None:
    """将任意音频文件转换为标准 WAV 格式（16kHz/mono/16bit）。"""
    ext = Path(audio_path).suffix.lower()
    if ext == ".wav":
        return audio_path
    return _ffmpeg_to_wav(audio_path, output_dir, sample_rate)


SUPPORTED_AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus', '.aiff'}


class ASREngineManager:
    def __init__(self):
        self._engines: dict[str, BaseASREngine] = {}
        self._config = load_asr_config()
        self._default_name = self._config.get("engine", "whisperx")

    def reload_config(self):
        """重新加载配置并重启引擎（如有运行中的子进程会先停止）。"""
        self.release_all_engines()
        self._config = load_asr_config()
        self._default_name = self._config.get("engine", "whisperx")

    def set_hw_accel(self, e: bool):
        for eng in self._engines.values():
            if hasattr(eng, 'set_hw_accel'):
                eng.set_hw_accel(e)

    def get_engine(self, name: str | None = None) -> BaseASREngine | None:
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

    def release_engine(self, name: str | None = None):
        """释放指定的 ASR 引擎（停止子进程并清理资源）。"""
        en = name or self._default_name
        eng = self._engines.pop(en, None)
        if eng and hasattr(eng, '_stop_server'):
            try:
                eng._stop_server()
                logger.info("ASR 引擎已释放: %s", en)
            except Exception as e:
                logger.debug("释放 ASR 引擎失败: %s", e)

    def release_all_engines(self):
        """释放所有 ASR 引擎。"""
        for eng in list(self._engines.values()):
            if hasattr(eng, '_stop_server'):
                try:
                    eng._stop_server()
                except Exception as e:
                    logger.debug("停止 ASR 引擎失败: %s", e)
        self._engines.clear()
        logger.info("所有 ASR 引擎已释放")

    def get_current_engine(self):
        return self.get_engine()

    @property
    def engine_name(self):
        return self._default_name

    @property
    def has_engine(self) -> bool:
        """检查是否有已加载的引擎。"""
        return len(self._engines) > 0


def _send_json(fp, obj: dict):
    """原子写入 JSON 行。"""
    line = json.dumps(obj, ensure_ascii=False)
    fp.write(line + "\n")
    fp.flush()
