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

# ── 不在模块级别加载任何 torch/cuda DLL ──
# 子进程 server 有自己隔离的 DLL 环境


_DEFAULT_CONFIG = {
    "enabled": False,
    "engine": "whisperx",
    "model_size": "large-v3",
    "model_dir": _DEFAULT_MODEL_DIR,
    "language": "zh",
    "device": "cuda",
    "compute_type": "float16",
    "batch_size": 16,
    "vad_enabled": False,
    "vad_min_silence_ms": 500,
    "vad_threshold": 0.5,
    "word_timestamps": True,
    "beam_size": 5,
    "initial_prompt": "",
    "condition_on_previous_text": True,
    "no_speech_threshold": 0.6,
    "compression_ratio_threshold": 2.4,
    "temperature": "0.0,0.2,0.4,0.6,0.8,1.0",
    "hotwords": "",
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
    def warm_up(self): ...


class WhisperXEngine(BaseASREngine):
    """子进程隔离版 ASR 引擎。

    启动 core/asr_server.py 子进程，通过 stdin/stdout JSON 行通信。
    子进程有独立 DLL 空间，不加载 core/cuda12/，
    因此不会与 PaddleOCR 的 CUDA DLL 冲突。

    使用 QtSubprocessManager（基于 QProcess）管理子进程生命周期，
    消除手动 threading.Thread 和轮询循环。
    """

    def __init__(self, c):
        super().__init__(c)
        self.engine_name = "whisperx"
        self._subproc = None  # QtSubprocessManager（懒初始化）
        self._stream_proc = None  # subprocess.Popen 缓存（用于流式通信）
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
        self._warmup_thread = None
        self._stop_event = threading.Event()  # 用于中断 warm_up
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
        # 同步写回配置文件，确保子进程重启时读到正确值
        self._save_device_to_config()
        # 需要重启子进程才能生效
        self._stop_server()

    def _save_device_to_config(self):
        """将当前 device/compute_type 写回 asr_engines.json。"""
        cfg_path = CONFIG_DIR / "asr_engines.json"
        try:
            cfg = _load_json_with_comments(cfg_path) if cfg_path.exists() else {}
            cfg["device"] = self._device
            cfg["compute_type"] = self._compute_type
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存 ASR device 配置失败: %s", e)

    def _ensure_subproc(self):
        """懒初始化 QtSubprocessManager。"""
        if self._subproc is None:
            from core.subprocess_utils import QtSubprocessManager

            self._subproc = QtSubprocessManager()

    def _start_server(self) -> bool:
        """启动 ASR 子进程服务器。使用 QProcess 管理，无手动线程。"""
        self._ensure_subproc()
        if self._subproc.is_running() and self._subproc._ready:
            return True

        # 清理旧进程
        if self._subproc.is_running():
            self._stop_server()

        _ASR_SERVER = str(BASE_DIR / "core" / "asr_server.py")
        _CONFIG_PATH = str(CONFIG_DIR / "asr_engines.json")
        _PYTHON = sys.executable
        _args = [_ASR_SERVER, "--config", _CONFIG_PATH, "--device", self._device, "--compute-type", self._compute_type]
        _env = {"PYTHONIOENCODING": "utf-8"}

        return self._subproc.start(_PYTHON, _args, env=_env, ready_keyword="ready", timeout=120.0)

    def _stop_server(self):
        """停止 ASR 子进程。"""
        self._stop_event.set()  # 通知 warm_up 线程退出
        if self._subproc:
            self._subproc.shutdown(timeout=10)
        # 清理流式子进程
        if self._stream_proc is not None:
            try:
                self._stream_proc.kill()
                self._stream_proc.wait(timeout=5)
            except Exception:
                pass
            self._stream_proc = None

    def _send_request(self, req: dict, timeout: float = 300.0) -> dict:
        """发送请求并等待响应（基于 QProcess，无手动线程）。"""
        if not self._start_server():
            return {"status": "error", "message": "ASR server not available"}

        self._subproc.send_json(req)
        resp = self._subproc.read_json_response(timeout=timeout)

        if resp is None:
            if not self._subproc.is_running():
                err = "\n".join(self._subproc.stderr_lines[-20:])
                return {"status": "error", "message": f"ASR server exited: {err[:300]}"}
            return {"status": "error", "message": f"ASR request timeout after {timeout}s"}

        if resp.get("status") == "ok":
            lang = resp.get("detected_lang", "?")
            prob = resp.get("lang_prob", 0)
            logger.info("ASR 响应: lang=%s prob=%.2f%%, %d 条结果", lang, prob * 100, len(resp.get("results", [])))
        else:
            err_msg = resp.get("message", "unknown")
            logger.error("ASR 响应错误: %s", err_msg)
        return resp

    def transcribe(self, audio_path: str) -> tuple:
        """兼容旧接口：收集全部结果后一次性返回。"""
        results = []
        error = [None]

        def _collect(seg):
            results.append(seg)

        self.transcribe_stream(audio_path, on_segment=_collect, error_holder=error)
        return results, error[0]

    def _ensure_running_proc(self) -> subprocess.Popen | None:
        """确保 ASR 子进程正在运行，返回 Popen 实例（用于直接管道 I/O）。

        QProcess 依赖事件循环，在 QThread.run() 中无法接收信号。
        此方法使用 subprocess.Popen 直接管理子进程，用于流式通信。
        会缓存 Popen 实例，避免重复启动模型。
        """
        # 复用已有的缓存进程
        if self._stream_proc is not None and self._stream_proc.poll() is None:
            return self._stream_proc

        # 清理旧进程
        self._stream_proc = None

        _ASR_SERVER = str(BASE_DIR / "core" / "asr_server.py")
        _CONFIG_PATH = str(CONFIG_DIR / "asr_engines.json")
        _PYTHON = sys.executable
        _cmd = [_PYTHON, _ASR_SERVER, "--config", _CONFIG_PATH,
                "--device", self._device, "--compute-type", self._compute_type]
        _env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

        proc = subprocess.Popen(
            _cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, env=_env)

        # 等待 ready
        deadline = time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                logger.error("ASR 子进程提前退出 (code=%d)", proc.poll())
                return None
            line = proc.stderr.readline()
            if not line:
                time.sleep(0.1)
                continue
            if "ready" in line:
                self._stream_proc = proc
                return proc
            if "error" in line.lower() and "failed" in line.lower():
                logger.error("ASR 子进程启动失败: %s", line.rstrip()[:200])
                proc.kill()
                return None
        logger.error("ASR 子进程启动超时")
        proc.kill()
        return None

    def transcribe_stream(
        self, audio_path: str, on_segment: Callable | None = None, error_holder: list | None = None
    ) -> None:
        """流式语音识别。每识别出一段就调用 on_segment({"start","end","text"})。

        使用 subprocess.Popen 直接管道 I/O（不依赖 Qt 事件循环）。
        on_segment 在 QThread 中调用，请确保线程安全。
        error_holder 用于存放错误消息（如有）。
        """
        proc = self._ensure_running_proc()
        if proc is None:
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
            _send_json(proc.stdin, req)
            logger.info("ASR 流式请求已发送: audio=%s", Path(audio_path).name)

            deadline = time.time() + 300
            while time.time() < deadline:
                if proc.poll() is not None:
                    err = f"ASR 子进程意外退出 (code={proc.poll()})"
                    logger.error(err)
                    if error_holder is not None:
                        error_holder[0] = err
                    return

                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        continue
                    break

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
                    logger.info("ASR 流式完成")
                    return
                elif status == "error":
                    err_msg = resp.get("message", "unknown")
                    logger.error("ASR 流式错误: %s", err_msg)
                    if error_holder is not None:
                        error_holder[0] = err_msg
                    return

            # 超时
            logger.warning("ASR 流式超时 (300s)")
            if error_holder is not None:
                error_holder[0] = "ASR request timeout after 300s"

        except Exception as e:
            import traceback
            logger.error("ASR transcribe_stream 异常: %s", e)
            traceback.print_exc()
            if error_holder is not None:
                error_holder[0] = str(e)
        finally:
            # 不复用子进程时仅关闭旧进程引用（非缓存进程）
            # 缓存的 _stream_proc 由 _stop_server / atexit 统一清理
            if proc is not self._stream_proc:
                try:
                    _send_json(proc.stdin, {"cmd": "shutdown"})
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def warm_up(self):
        """后台线程预加载模型（不阻塞调用线程）。

        注意：warm_up 在普通 threading.Thread 中调用，无 Qt 事件循环。
        使用 subprocess.Popen 直接启动（绕过 QProcess 的 processEvents 依赖）。
        通过 _stop_event 支持中断（引擎释放时立即退出）。
        """
        self._stop_event.clear()

        def _do_warmup():
            t0 = time.time()
            proc = None
            try:
                _ASR_SERVER = str(BASE_DIR / "core" / "asr_server.py")
                _CONFIG_PATH = str(CONFIG_DIR / "asr_engines.json")
                _PYTHON = sys.executable
                _cmd = [
                    _PYTHON,
                    _ASR_SERVER,
                    "--config",
                    _CONFIG_PATH,
                    "--device",
                    self._device,
                    "--compute-type",
                    self._compute_type,
                ]
                _env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
                proc = subprocess.Popen(
                    _cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=_env,
                )
                deadline = time.time() + 120
                stderr_lines = []
                while time.time() < deadline:
                    if self._stop_event.is_set():
                        logger.debug("ASR warm_up 被中断")
                        proc.kill()
                        return
                    if proc.poll() is not None:
                        logger.warning("ASR warm_up 子进程提前退出 (code=%d)", proc.poll())
                        return
                    line = proc.stderr.readline()
                    if not line:
                        time.sleep(0.1)
                        continue
                    stderr_lines.append(line.rstrip())
                    if "ready" in line:
                        elapsed = time.time() - t0
                        logger.info("ASR warm_up 完成 (%.1fs)", elapsed)
                        try:
                            _send_json(proc.stdin, {"cmd": "shutdown"})
                            proc.wait(timeout=10)
                        except Exception:
                            proc.kill()
                        return
                logger.warning("ASR warm_up 超时 (120s)")
                proc.kill()
            except Exception as e:
                logger.debug("ASR warm_up 异常: %s", e)
            finally:
                if proc and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        self._warmup_thread = threading.Thread(target=_do_warmup, daemon=True)
        self._warmup_thread.start()

    def __del__(self):
        with contextlib.suppress(Exception):
            self._stop_server()


def _ffmpeg_to_wav(
    input_path: str, output_dir: str = None, sample_rate: int = 16000, extra_args: list = None
) -> str | None:
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
    cmd = [
        _FFMPEG,
        "-v",
        "error",
        "-i",
        str(input_path),
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-y",
    ]
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


def extract_audio_from_video(
    video_path: str,
    output_dir: str = None,
    time_start: float = 0.0,
    time_end: float = 0.0,
    sample_rate: int = 16000,
    cache_path: str = None,
) -> str | None:
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


def convert_to_wav(audio_path: str, output_dir: str = None, sample_rate: int = 16000) -> str | None:
    """将任意音频文件转换为标准 WAV 格式（16kHz/mono/16bit）。"""
    ext = Path(audio_path).suffix.lower()
    if ext == ".wav":
        return audio_path
    return _ffmpeg_to_wav(audio_path, output_dir, sample_rate)


SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".aiff"}


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
            if hasattr(eng, "set_hw_accel"):
                eng.set_hw_accel(e)

    def get_engine(self, name: str | None = None, warm_up: bool = True) -> BaseASREngine | None:
        en = name or self._default_name
        if en in self._engines:
            return self._engines[en]
        if en != "whisperx":
            return None
        eng = WhisperXEngine(self._config)
        self._engines[en] = eng
        if warm_up and hasattr(eng, "warm_up"):
            eng.warm_up()
        return eng

    def release_engine(self, name: str | None = None):
        """释放指定的 ASR 引擎（停止子进程并清理资源）。"""
        en = name or self._default_name
        eng = self._engines.pop(en, None)
        if eng and hasattr(eng, "_stop_server"):
            try:
                eng._stop_server()
                logger.info("ASR 引擎已释放: %s", en)
            except Exception as e:
                logger.debug("释放 ASR 引擎失败: %s", e)

    def release_all_engines(self):
        """释放所有 ASR 引擎。"""
        for eng in list(self._engines.values()):
            # 先设置 stop_event 中断 warm_up，再停止服务器
            if hasattr(eng, "_stop_event"):
                eng._stop_event.set()
            if hasattr(eng, "_stop_server"):
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
