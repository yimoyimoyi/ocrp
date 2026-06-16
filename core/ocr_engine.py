"""多 OCR 引擎管理器 —— 支持 PaddleOCR 本地引擎及各类 Vision API。
硬件加速由全局设置 hw_accel 统一控制。

DLL 隔离策略：
  - PaddleOCR CPU 模式：仅注册 torch/lib，不加载 CUDA DLL，避免与 CPU torch 冲突
  - PaddleOCR GPU 模式：在 _ensure_ocr 中按需调用 _register_gpu_dll_dirs() 加载 CUDA/cuDNN
"""

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from openai import OpenAI

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"
_CORE_DLL_DIR = os.path.dirname(os.path.abspath(__file__))

from core.config_manager import _load_json_with_comments
from core.llm_utils import ask_llm
from core.logger import get_logger

logger = get_logger(__name__)


def load_engines_config() -> dict:
    path = CONFIG_DIR / "ocr_engines.json"
    if path.exists():
        try:
            cfg = _load_json_with_comments(path)
            from core.config_schema import validate_config
            from core.config_schemas import OCR_ENGINES_SCHEMA

            validate_config(cfg, OCR_ENGINES_SCHEMA, "ocr_engines.json")
            return cfg
        except Exception as e:
            logger.warning("加载 OCR 引擎配置失败: %s", e)
    return {"engines": {}, "default_engine": "paddleocr"}


def _check_v1_availability(base_url: str, api_key: str = "", timeout: int = 10) -> bool:
    """检测 OpenAI 兼容 /v1 端点是否可达。"""
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    try:
        client = OpenAI(api_key=api_key or "not-needed", base_url=url)
        client.models.list(timeout=timeout)
        return True
    except Exception:
        return False


def _get_v1_model_list(base_url: str, api_key: str = "", timeout: int = 15) -> list[str]:
    """从 OpenAI 兼容 /v1/models 端点获取模型列表。"""
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    try:
        client = OpenAI(api_key=api_key or "not-needed", base_url=url)
        models = client.models.list(timeout=timeout)
        return [m.id for m in models.data] if models.data else []
    except Exception:
        return []


# ── DLL 预加载 ──
_dll_loaded = False
_dll_load_lock = threading.Lock()
_gpu_dll_loaded = False


def _find_nvidia_site_packages_dirs() -> list[str]:
    """扫描 site-packages/nvidia/*/bin/ 目录，返回所有 DLL 路径列表。"""
    bin_dirs = []
    for sp in sys.path:
        nvidia_dir = os.path.join(sp, "nvidia")
        if not os.path.isdir(nvidia_dir):
            continue
        try:
            for pkg_name in os.listdir(nvidia_dir):
                bin_dir = os.path.join(nvidia_dir, pkg_name, "bin")
                if os.path.isdir(bin_dir):
                    bin_dirs.append(bin_dir)
        except OSError:
            continue
    return bin_dirs


def _register_dll_dirs():
    """模块级别执行：注册 torch/lib DLL 搜索路径（CPU/GPU 均需要）。"""
    global _dll_loaded
    if _dll_loaded:
        return
    with _dll_load_lock:
        if _dll_loaded:
            return
        _dll_loaded = True
        if sys.platform != "win32":
            return

        # torch/lib/ —— 从 sys.path 定位（避免 importlib.util 触发导入）
        for sp in sys.path:
            if not sp:
                continue
            _torch_init = os.path.join(sp, "torch", "__init__.py")
            if os.path.isfile(_torch_init):
                _tl = os.path.join(sp, "torch", "lib")
                if os.path.isdir(_tl):
                    os.add_dll_directory(_tl)
                break

        # paddle/libs/ — PaddlePaddle 自带 DLL（common.dll, phi.dll 等）
        for _sp in sys.path:
            if not _sp:
                continue
            _paddle_init = os.path.join(_sp, "paddle", "__init__.py")
            if os.path.isfile(_paddle_init):
                _pl = os.path.join(_sp, "paddle", "libs")
                if os.path.isdir(_pl):
                    os.add_dll_directory(_pl)
                break

        logger.debug("DLL 搜索路径已注册: torch/lib, paddle/libs")


def _register_gpu_dll_dirs():
    """GPU 模式专用：注册 CUDA/cuDNN DLL 搜索路径（纯 pip 包 DLL 方案）。

    不使用系统 CUDA Toolkit（会与 pip 包 DLL 版本冲突）。
    DLL 来源：
      - torch/lib/（ocr_gui.py 已添加）→ torch 自带全部 CUDA DLL
      - site-packages/nvidia/*/lib/ → paddlepaddle-gpu 依赖的 nvidia-*-cu12 包
    """
    global _gpu_dll_loaded
    if _gpu_dll_loaded:
        return
    with _dll_load_lock:
        if _gpu_dll_loaded:
            return
        _gpu_dll_loaded = True
        if sys.platform != "win32":
            return

        # nvidia pip 包 DLL 目录（paddlepaddle-gpu 依赖）
        # 优先使用 sys.path 扫描，避免硬编码路径
        _nvidia_dirs = 0
        for _bin_dir in _find_nvidia_site_packages_dirs():
            try:
                os.add_dll_directory(_bin_dir)
                _nvidia_dirs += 1
            except OSError as _e:
                logger.debug("nvidia DLL 目录注册失败 (%s): %s", _bin_dir, _e)

        # 兼容旧目录
        for legacy in ("cuda12", "cudnn8"):
            _legacy_dir = os.path.join(_CORE_DLL_DIR, legacy)
            if os.path.isdir(_legacy_dir):
                try:
                    os.add_dll_directory(_legacy_dir)
                except OSError:
                    pass

        logger.debug("GPU DLL 搜索路径已注册（nvidia pip 包: %d 个目录）", _nvidia_dirs)


# ── IPC 辅助（warm_up 中使用 subprocess.Popen 时需要）──
def _send_json(fp, obj: dict):
    """原子写入 JSON 行到子进程 stdin。"""
    line = json.dumps(obj, ensure_ascii=False)
    fp.write(line + "\n")
    fp.flush()


# ═══════════════ 抽象基类 ═══════════════
class BaseOCREngine(ABC):
    def __init__(self, config: dict):
        self.config = config
        self.engine_name = "base"
        self._last_confidence: float = 0.0

    @property
    def last_confidence(self) -> float:
        return self._last_confidence

    @abstractmethod
    def recognize(self, image: np.ndarray, prompt: str | None = None) -> str:
        pass

    def is_available(self) -> bool:
        return True

    def check_availability(self) -> bool:
        return self.is_available()

    def get_model_list(self) -> list[str]:
        return []

    def warm_up(self):
        pass


# ═══════════════ PaddleOCR 本地引擎 ═══════════════
class PaddleOCREngine(BaseOCREngine):
    """PaddleOCR 引擎 —— 默认进程内直接调用（最快），可选子进程隔离。

    进程内模式：参考 run_ocr.py，直接 `PaddleOCR.predict()`，零 IPC 开销。
    子进程模式：通过 ocr_server.py 隔离 DLL，避免 CUDA 冲突（后备方案）。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.engine_name = "paddleocr"
        cfg = config.get("config", {})
        self._ocr = None
        self._init_lock = threading.Lock()
        self._lang = cfg.get("lang", "ch")
        self._device = cfg.get("device") or ("gpu" if cfg.get("use_gpu") else "cpu")
        self._ocr_version = cfg.get("ocr_version") or None
        self._use_angle_cls = cfg.get("use_angle_cls", True)
        self._use_doc_orientation_classify = cfg.get("use_doc_orientation_classify", False)
        self._use_doc_unwarping = cfg.get("use_doc_unwarping", False)
        self._use_textline_orientation = cfg.get("use_textline_orientation", False)
        self._paddle_available = True

        # 子进程模式（后备，config 中可设置 use_subprocess: true）
        self._use_subprocess = bool(cfg.get("use_subprocess", False))
        self._subproc = None  # QtSubprocessManager（懒初始化）
        self._shm_mgr = None  # SharedMemoryManager（懒初始化）
        self._stop_event = threading.Event()  # 用于中断 warm_up
        if self._use_subprocess:
            atexit.register(self._stop_server)

    def is_available(self) -> bool:
        return self._paddle_available

    def set_ocr_version(self, version: str):
        """动态切换 OCR 模型版本（哨兵模式专用）。"""
        if not version or version == "跟随全局":
            return
        ver_map = {
            "PP-OCRv4 (最快)": "PP-OCRv4",
            "PP-OCRv5_mobile (平衡)": "PP-OCRv5_mobile",
            "PP-OCRv5_server (高精度)": None,
        }
        mapped = ver_map.get(version)
        if mapped != self._ocr_version:
            self._ocr_version = mapped
            self._ocr = None  # 下次 recognize 时重新初始化

    def set_hw_accel(self, enabled: bool):
        new_device = "gpu" if enabled else "cpu"
        if self._device != new_device:
            self._device = new_device
            self._ocr = None
            if self._use_subprocess and self._subproc and self._subproc.is_running():
                try:
                    self._subproc.send_json({"cmd": "set_device", "device": new_device})
                    self._subproc.read_json_response(timeout=30)
                except Exception as e:
                    logger.warning("OCR 子进程 set_device 失败: %s", e)
                    self._stop_server()

    # ── 子进程管理（基于 QProcess）──
    def _ensure_subproc(self):
        """懒初始化 QtSubprocessManager 和 SharedMemoryManager。"""
        if self._subproc is None:
            from core.subprocess_utils import QtSubprocessManager

            self._subproc = QtSubprocessManager()
        if self._shm_mgr is None:
            from core.subprocess_utils import SharedMemoryManager

            self._shm_mgr = SharedMemoryManager("orcp_ocr_img", capacity=10 * 1024 * 1024)

    def _start_server(self) -> bool:
        """启动 OCR 子进程服务器。"""
        self._ensure_subproc()
        if self._subproc.is_running() and self._subproc._ready:
            return True

        if self._subproc.is_running():
            self._stop_server()

        _OCR_SERVER = str(BASE_DIR / "core" / "ocr_server.py")
        _CONFIG_PATH = str(CONFIG_DIR / "ocr_engines.json")
        _PYTHON = sys.executable
        _args = [_OCR_SERVER, "--config", _CONFIG_PATH]
        _env = {"PYTHONIOENCODING": "utf-8"}

        ok = self._subproc.start(_PYTHON, _args, env=_env, ready_keyword="ready", timeout=120.0)
        if not ok:
            self._paddle_available = False
            return False

        # 子进程默认读取 config 中的 device (CPU)，主进程可能已设为 GPU
        if self._device.startswith("gpu"):
            try:
                self._subproc.send_json({"cmd": "set_device", "device": self._device})
                self._subproc.read_json_response(timeout=30)
                logger.info("OCR 子进程已切换到 GPU")
            except Exception as _e:
                logger.warning("OCR 子进程 GPU 切换失败: %s", _e)
        return True

    def _stop_server(self):
        """停止 OCR 子进程并清理共享内存。"""
        self._stop_event.set()
        if self._subproc:
            self._subproc.shutdown(timeout=5)
        if self._shm_mgr:
            self._shm_mgr.close()
            self._shm_mgr = None

    def _check_process_alive(self):
        """检查子进程是否存活。"""
        if self._subproc and not self._subproc.is_running():
            err = "\n".join(self._subproc.stderr_lines[-20:])
            logger.warning("OCR 子进程意外退出: %s", err[:200])
            self._subproc = None

    # ── 进程内模式：零 IPC 开销，参考 run_ocr.py ──
    def _ensure_ocr(self):
        """懒加载 PaddleOCR（主进程内，零 IPC）。"""
        if self._ocr is not None:
            return
        with self._init_lock:
            if self._ocr is not None:
                return
            # 抑制 PaddleOCR / PaddlePaddle 内部 stderr 日志
            import logging as _logging

            for _name in ("paddleocr", "paddle", "ppocr"):
                _logging.getLogger(_name).setLevel(_logging.WARNING)
            device = self._device
            if device.startswith("gpu"):
                _register_gpu_dll_dirs()
                try:
                    import paddle

                    if not paddle.device.is_compiled_with_cuda():
                        logger.warning("PaddlePaddle 未编译 CUDA 支持，回退 CPU。")
                        device = "cpu"
                except Exception as e:
                    logger.warning("GPU 检测失败，回退 CPU: %s", e)
                    device = "cpu"
                self._device = device  # 同步实际使用的设备
            try:
                from paddleocr import PaddleOCR
            except Exception as e:
                logger.warning("PaddleOCR 导入失败: %s", e)
                self._paddle_available = False
                return
            kwargs = {
                "lang": self._lang,
                "device": device,
                "use_angle_cls": self._use_angle_cls,
                "enable_mkldnn": False,
                "use_doc_orientation_classify": self._use_doc_orientation_classify,
                "use_doc_unwarping": self._use_doc_unwarping,
            }
            if self._ocr_version:
                kwargs["ocr_version"] = self._ocr_version
            logger.info("PaddleOCR 进程内初始化: device=%s, version=%s", device, self._ocr_version or "latest")
            # 诊断：检查 paddle 是否识别 CUDA
            try:
                import paddle as _pdl

                logger.info(
                    "paddle CUDA compiled: %s, version: %s",
                    getattr(_pdl.device, "is_compiled_with_cuda", lambda: "?")(),
                    _pdl.__version__,
                )
            except Exception as _diag_e:
                logger.warning("paddle 诊断失败: %s", _diag_e)
            try:
                self._ocr = PaddleOCR(**kwargs)
            except Exception as e:
                if device.startswith("gpu"):
                    logger.warning("GPU 初始化失败: %s，回退 CPU", e)
                    self._device = "cpu"
                    kwargs["device"] = "cpu"
                    self._ocr = PaddleOCR(**kwargs)
                else:
                    raise

    def recognize(self, image: np.ndarray, prompt: str | None = None) -> str:
        if not self._paddle_available:
            return ""
        if self._use_subprocess:
            return self._recognize_subprocess(image)
        return self._recognize_in_process(image)

    def _recognize_in_process(self, image: np.ndarray) -> str:
        """进程内直接调用 PaddleOCR，零 IPC（默认，最快）。"""
        try:
            self._ensure_ocr()
            if self._ocr is None:
                return ""
            result = self._ocr.predict(image)
            if result and len(result) > 0:
                j = result[0].json
                res_data = j.get("res", j)
                texts = res_data.get("rec_texts", [])
                scores = res_data.get("rec_scores", [])
                if texts:
                    text = "".join(texts).replace(" ", "")
                    self._last_confidence = sum(scores) / len(scores) if scores else 0.0
                    return text
            self._last_confidence = 0.0
            return ""
        except Exception as e:
            err = str(e)
            if self._device.startswith("gpu") and any(
                kw in err.lower() for kw in ("cuda", "cublas", "gpu", "out of memory", "onednn", "pir", "dll")
            ):
                logger.warning("GPU 运行时失败: %s，回退 CPU", e)
                self._device = "cpu"
                self._ocr = None
                return self._recognize_in_process(image)
            logger.error("PaddleOCR 识别失败: %s", e)
            self._last_confidence = 0.0
            return ""

    # ── 子进程模式（后备，use_subprocess=true 时启用）──
    def _recognize_subprocess(self, image: np.ndarray) -> str:
        """通过 QProcess 子进程进行 OCR 识别（共享内存传输图像）。"""
        if not self._paddle_available:
            return ""
        try:
            self._check_process_alive()
            if not self._subproc or not self._subproc.is_running():
                if not self._start_server():
                    return ""
            h, w = image.shape[:2]
            c = image.shape[2] if image.ndim == 3 else 1
            # 写入共享内存（零 base64 编码开销）
            self._shm_mgr.write_array(image)
            self._subproc.send_json(
                {
                    "cmd": "recognize",
                    "shm_name": self._shm_mgr.name,
                    "width": w,
                    "height": h,
                    "channels": c,
                    "lang": self._lang,
                    "device": self._device,
                }
            )
            resp = self._subproc.read_json_response(timeout=60)
            if resp and resp.get("status") == "result":
                self._last_confidence = resp.get("confidence", 0.0)
                return resp.get("text", "")
            if resp and resp.get("status") == "error":
                logger.error("OCR 子进程识别失败: %s", resp.get("message", ""))
            if resp is None:
                logger.warning("OCR 子进程无响应，重建连接")
                self._stop_server()
            return ""
        except Exception as e:
            logger.error("OCR 子进程通信异常: %s", e)
            self._stop_server()
            return ""

    def warm_up(self):
        """后台预热：子进程模式启动 server，进程内模式加载 PaddleOCR 模型。"""
        self._stop_event.clear()

        if self._use_subprocess:

            def _do_warmup():
                t0 = time.time()
                proc = None
                try:
                    _OCR_SERVER = str(BASE_DIR / "core" / "ocr_server.py")
                    _CONFIG_PATH = str(CONFIG_DIR / "ocr_engines.json")
                    _PYTHON = sys.executable
                    proc = subprocess.Popen(
                        [_PYTHON, _OCR_SERVER, "--config", _CONFIG_PATH],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    )
                    deadline = time.time() + 120
                    while time.time() < deadline:
                        if self._stop_event.is_set():
                            logger.debug("OCR warm_up 被中断")
                            proc.kill()
                            return
                        if proc.poll() is not None:
                            logger.warning("OCR warm_up 子进程提前退出 (code=%d)", proc.poll())
                            return
                        line = proc.stderr.readline()
                        if not line:
                            time.sleep(0.1)
                            continue
                        if "ready" in line:
                            elapsed = time.time() - t0
                            logger.info("OCR warm_up 完成 (%.1fs)", elapsed)
                            try:
                                _send_json(proc.stdin, {"cmd": "shutdown"})
                                proc.wait(timeout=5)
                            except Exception:
                                proc.kill()
                            return
                    logger.warning("OCR warm_up 超时 (120s)")
                    proc.kill()
                except Exception as e:
                    logger.debug("OCR warm_up 异常: %s", e)
                finally:
                    if proc and proc.poll() is None:
                        try:
                            proc.kill()
                        except Exception:
                            pass

            threading.Thread(target=_do_warmup, daemon=True).start()
        else:

            def _do_warmup_inprocess():
                t0 = time.time()
                self._ensure_ocr()
                if self._stop_event.is_set():
                    logger.debug("OCR warm_up (进程内) 被中断")
                    return
                elapsed = time.time() - t0
                logger.info("OCR warm_up 完成 (进程内模式, %.1fs)", elapsed)

            threading.Thread(target=_do_warmup_inprocess, daemon=True).start()

    def __del__(self):
        if self._use_subprocess:
            try:
                self._stop_server()
            except Exception:
                pass


# ═══════════════ OpenAI Vision API ═══════════════
class OpenAIVisionEngine(BaseOCREngine):
    def __init__(self, config: dict):
        super().__init__(config)
        self.engine_name = "openai_vision"
        cfg = config.get("config", {})
        self._api_key = cfg.get("api_key", "").strip()
        self._base_url = cfg.get("base_url", "https://api.openai.com/v1")
        self._model = cfg.get("model", "gpt-4o")
        self._prompt_template = cfg.get("prompt_template", "请识别图片中的文字，只返回文字内容")
        self._timeout = cfg.get("timeout", 30)
        self._retry = cfg.get("retry", 2)

    def check_availability(self) -> bool:
        return _check_v1_availability(self._base_url, self._api_key)

    def get_model_list(self) -> list[str]:
        models = _get_v1_model_list(self._base_url, self._api_key)
        if models:
            return models
        # 回退：根据 URL 特征返回已知模型
        host = self._base_url.lower()
        if "deepseek" in host:
            return ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"]
        if "openai" in host:
            return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o3-mini"]
        return []

    def recognize(self, image: np.ndarray, prompt: str | None = None) -> str:
        prompt_text = prompt or self._prompt_template
        logger.info("OpenAI Vision 请求 | model=%s | image=%dx%d", self._model, image.shape[1], image.shape[0])
        logger.debug("Prompt: %s", prompt_text[:120])
        content = ask_llm(
            prompt=prompt_text,
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            timeout=self._timeout,
            image=image,
            temperature=0.0,
            max_tokens=512,
            log_title="openai_vision",
        )
        result = content if isinstance(content, str) else ""
        logger.info("OpenAI Vision 响应: %d chars", len(result))
        return result


# ═══════════════ Ollama Vision ═══════════════
class OllamaVisionEngine(BaseOCREngine):
    """Ollama Vision API —— 使用 OpenAI 兼容 /v1 端点（Ollama >= 0.5.0）。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.engine_name = "ollama_vision"
        cfg = config.get("config", {})
        self._base_url = cfg.get("base_url", "http://localhost:11434/v1")
        self._model = cfg.get("model", "llama3.2-vision:11b")
        self._prompt_template = cfg.get("prompt_template", "请识别图片中的文字，只返回文字内容")
        self._timeout = cfg.get("timeout", 60)
        self._retry = cfg.get("retry", 2)

    def check_availability(self) -> bool:
        return _check_v1_availability(self._base_url)

    def get_model_list(self) -> list[str]:
        return _get_v1_model_list(self._base_url)

    def recognize(self, image: np.ndarray, prompt: str | None = None) -> str:
        prompt_text = prompt or self._prompt_template
        logger.info("Ollama Vision 请求 | model=%s | image=%dx%d", self._model, image.shape[1], image.shape[0])
        logger.debug("Prompt: %s", prompt_text[:120])
        content = ask_llm(
            prompt=prompt_text,
            api_key="ollama",
            base_url=self._base_url,
            model=self._model,
            timeout=self._timeout,
            image=image,
            temperature=0.0,
            max_tokens=512,
            log_title="ollama_vision",
        )
        result = content if isinstance(content, str) else ""
        logger.info("Ollama Vision 响应: %d chars", len(result))
        return result


# ═══════════════ llama.cpp ═══════════════
class LlamaCppEngine(BaseOCREngine):
    def __init__(self, config: dict):
        super().__init__(config)
        self.engine_name = "llamacpp"
        cfg = config.get("config", {})
        self._base_url = cfg.get("base_url", "http://127.0.0.1:8080")
        self._api_key = cfg.get("api_key", "not-needed")
        self._model = cfg.get("model", "")
        self._prompt_template = cfg.get("prompt_template", "请识别图片中的文字，只返回文字内容")
        self._timeout = cfg.get("timeout", 60)
        self._retry = cfg.get("retry", 2)

    def check_availability(self) -> bool:
        return _check_v1_availability(self._base_url, self._api_key)

    def get_model_list(self) -> list[str]:
        return _get_v1_model_list(self._base_url, self._api_key)

    def recognize(self, image: np.ndarray, prompt: str | None = None) -> str:
        prompt_text = prompt or self._prompt_template
        logger.info(
            "[llama.cpp] API 请求: %s 模型=%s 图片=%dx%d prompt=%.80s",
            self._base_url,
            self._model or "(default)",
            image.shape[1],
            image.shape[0],
            prompt_text,
        )
        content = ask_llm(
            prompt=prompt_text,
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            timeout=self._timeout,
            image=image,
            temperature=0.0,
            max_tokens=512,
            log_title="llamacpp_vision",
        )
        result = content if isinstance(content, str) else ""
        logger.info("[llama.cpp] 响应 %d chars", len(result))
        return result


# ═══════════════ 引擎注册表 ═══════════════
# 模块导入时立即注册 DLL 路径并预加载 torch
# 必须在 PyQt5 等可能干扰 DLL 搜索路径的模块之前执行
_register_dll_dirs()

ENGINE_CLASS_MAP = {
    "paddleocr": PaddleOCREngine,
    "openai_vision": OpenAIVisionEngine,
    "ollama_vision": OllamaVisionEngine,
    "llamacpp": LlamaCppEngine,
}


class OCREngineManager:
    def __init__(self):
        self._engines: dict[str, BaseOCREngine] = {}
        self._config = load_engines_config()
        self._default_name = self._config.get("default_engine", "paddleocr")
        self._current_name = self._default_name
        self._hw_accel_enabled: bool = False

    def reload_config(self):
        self._config = load_engines_config()
        self._engines.clear()

    def get_engine_names(self) -> list[str]:
        engines_cfg = self._config.get("engines", {})
        return [name for name, cfg in engines_cfg.items() if cfg.get("enabled", True)]

    def get_engine_config(self, name: str) -> dict:
        return self._config.get("engines", {}).get(name, {})

    def set_hw_accel(self, enabled: bool):
        self._hw_accel_enabled = enabled
        for name, eng in self._engines.items():
            if hasattr(eng, "set_hw_accel"):
                eng.set_hw_accel(enabled)

    def get_engine(self, name: str | None = None, warm_up: bool = True) -> BaseOCREngine | None:
        engine_name = name or self._current_name
        if engine_name in self._engines:
            return self._engines[engine_name]

        engines_cfg = self._config.get("engines", {})
        cfg = engines_cfg.get(engine_name)
        if not cfg or not cfg.get("enabled", True):
            return None

        engine_cls = ENGINE_CLASS_MAP.get(engine_name)
        if not engine_cls:
            engine_type = cfg.get("type", "")
            if engine_type == "api":
                base_url = cfg.get("config", {}).get("base_url", "")
                if "openai" in base_url:
                    engine_cls = OpenAIVisionEngine
                elif "ollama" in base_url or "11434" in base_url:
                    engine_cls = OllamaVisionEngine
                else:
                    engine_cls = LlamaCppEngine
            else:
                engine_cls = PaddleOCREngine

        engine = engine_cls(cfg)
        self._engines[engine_name] = engine
        if self._hw_accel_enabled and hasattr(engine, "set_hw_accel"):
            engine.set_hw_accel(True)
        if warm_up and hasattr(engine, "warm_up"):
            engine.warm_up()
        return engine

    def set_current_engine(self, name: str):
        if name in self.get_engine_names():
            self._current_name = name

    def get_current_engine_name(self) -> str:
        return self._current_name

    def get_current_engine(self, warm_up: bool = True) -> BaseOCREngine | None:
        return self.get_engine(self._current_name, warm_up=warm_up)

    def get_default_engine_name(self) -> str:
        return self._default_name

    def release_engine(self, name: str | None = None):
        """释放指定的 OCR 引擎（清理资源）。"""
        en = name or self._current_name
        eng = self._engines.pop(en, None)
        if eng:
            if hasattr(eng, "close"):
                try:
                    eng.close()
                except Exception as e:
                    logger.debug("关闭 OCR 引擎失败: %s", e)
            logger.info("OCR 引擎已释放: %s", en)

    def release_all_engines(self):
        """释放所有 OCR 引擎。"""
        for eng in list(self._engines.values()):
            if hasattr(eng, "close"):
                try:
                    eng.close()
                except Exception as e:
                    logger.debug("关闭 OCR 引擎失败: %s", e)
        self._engines.clear()
        logger.info("所有 OCR 引擎已释放")

    @property
    def has_engine(self) -> bool:
        """检查是否有已加载的引擎。"""
        return len(self._engines) > 0
