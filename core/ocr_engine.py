# -*- coding: utf-8 -*-
"""多 OCR 引擎管理器 —— 支持 PaddleOCR 本地引擎及各类 Vision API。
硬件加速由全局设置 hw_accel 统一控制。

DLL 隔离策略：
  - PaddleOCR CPU 模式：仅注册 torch/lib，不加载 CUDA DLL，避免与 CPU torch 冲突
  - PaddleOCR GPU 模式：在 _ensure_ocr 中按需调用 _register_gpu_dll_dirs() 加载 CUDA/cuDNN
"""

import os, sys, ctypes, base64, threading, requests, time, numpy as np

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List, Dict

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"
_CORE_DLL_DIR = os.path.dirname(os.path.abspath(__file__))

from config_manager import _load_json_with_comments


def load_engines_config() -> dict:
    path = CONFIG_DIR / "ocr_engines.json"
    if path.exists():
        try:
            return _load_json_with_comments(path)
        except Exception:
            pass
    return {"engines": {}, "default_engine": "paddleocr"}


# ── DLL 预加载 ──
_dll_loaded = False
_dll_load_lock = threading.Lock()
_gpu_dll_loaded = False


def _find_nvidia_site_packages_dirs() -> List[str]:
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

        print("[PaddleOCR] DLL dirs: torch/lib")


def _register_gpu_dll_dirs():
    """GPU 模式专用：确保 CUDA/cuDNN DLL 搜索路径可用。

    torch (cu124) 自带了所有 CUDA DLL，只需确保 torch/lib 和
    系统 CUDA Toolkit 路径已注册。不加载 nvidia pip 包中的 DLL
    （它们与 torch 自带版本冲突）。
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

        # 系统 CUDA Toolkit 路径（PaddlePaddle GPU 可能需要）
        for _cuda_env in ("CUDA_PATH_V12_6", "CUDA_PATH_V12_4", "CUDA_PATH"):
            _cuda_root = os.environ.get(_cuda_env, "")
            if _cuda_root:
                _cuda_bin = os.path.join(_cuda_root, "bin")
                if os.path.isdir(_cuda_bin):
                    os.add_dll_directory(_cuda_bin)
                    break

        # 旧 core/cuda12/ core/cudnn8/ 目录（兼容）
        for legacy in ("cuda12", "cudnn8"):
            _legacy_dir = os.path.join(_CORE_DLL_DIR, legacy)
            if os.path.isdir(_legacy_dir):
                try:
                    os.add_dll_directory(_legacy_dir)
                except OSError:
                    pass

        print("[PaddleOCR] GPU DLL dirs registered")


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
    def recognize(self, image: np.ndarray, prompt: Optional[str] = None) -> str:
        pass

    def is_available(self) -> bool:
        return True

    def check_availability(self) -> bool:
        return self.is_available()

    def get_model_list(self) -> List[str]:
        return []

    def warm_up(self):
        pass


# ═══════════════ PaddleOCR 本地引擎 ═══════════════
class PaddleOCREngine(BaseOCREngine):
    def __init__(self, config: dict):
        super().__init__(config)
        self.engine_name = "paddleocr"
        cfg = config.get("config", {})
        self._ocr = None
        self._init_lock = threading.Lock()
        self._lang = cfg.get("lang", "ch")
        self._use_angle_cls = cfg.get("use_angle_cls", True)
        # 兼容旧配置的 use_gpu → 转为 device 参数
        self._device = cfg.get("device") or ("gpu" if cfg.get("use_gpu") else "cpu")
        self._ocr_version = cfg.get("ocr_version") or None
        self._use_doc_orientation_classify = cfg.get("use_doc_orientation_classify", False)
        self._use_doc_unwarping = cfg.get("use_doc_unwarping", False)
        self._use_textline_orientation = cfg.get("use_textline_orientation", False)
        # ── 3.x 不再支持的参数：show_log / det_db_score_mode / rec_batch_num ──

    def set_hw_accel(self, enabled: bool):
        new_device = "gpu" if enabled else "cpu"
        if self._device != new_device:
            with self._init_lock:
                self._device = new_device
                self._ocr = None

    def _ensure_ocr(self):
        if self._ocr is None:
            with self._init_lock:
                if self._ocr is None:
                    device = self._device
                    if device.startswith("gpu"):
                        _register_gpu_dll_dirs()
                        # 如果 torch 无 CUDA，PaddleOCR GPU 内部回退 CPU 时
                        # 会丢失 enable_mkldnn=False，导致 oneDNN bug。直接走 CPU。
                        try:
                            import torch
                            if not torch.cuda.is_available():
                                device = "cpu"
                        except Exception:
                            device = "cpu"
                    from paddleocr import PaddleOCR

                    try:
                        kwargs = {
                            "lang": self._lang,
                            "device": device,
                            "use_doc_orientation_classify": self._use_doc_orientation_classify,
                            "use_doc_unwarping": self._use_doc_unwarping,
                            "use_textline_orientation": self._use_angle_cls,
                            "enable_mkldnn": False,  # Paddle 3.3.1 oneDNN PIR DoubleAttribute bug
                        }
                        if self._ocr_version:
                            kwargs["ocr_version"] = self._ocr_version
                        print(f"[PaddleOCR] 初始化引擎: device={device}")
                        self._ocr = PaddleOCR(**kwargs)
                    except Exception as e:
                        if device.startswith("gpu"):
                            print(f"[PaddleOCR] GPU 初始化失败: {e}")
                            print("[PaddleOCR] 自动退回 CPU 模式...")
                            self._device = "cpu"
                            kwargs["device"] = "cpu"
                            self._ocr = PaddleOCR(**kwargs)
                        else:
                            raise

    def recognize(self, image: np.ndarray, prompt: Optional[str] = None) -> str:
        try:
            self._ensure_ocr()
            result = self._ocr.predict(image)
            if result and len(result) > 0:
                json_result = result[0].json
                # PaddleOCR 3.x: rec_texts 在 res 子对象内
                res_data = json_result.get("res", json_result)
                texts = res_data.get("rec_texts", [])
                scores = res_data.get("rec_scores", [])
                if texts:
                    text = "".join(texts).replace(" ", "")
                    self._last_confidence = sum(scores) / len(scores) if scores else 0.0
                    return text
            self._last_confidence = 0.0
            return ""
        except Exception as e:
            err_msg = str(e)
            if self._device.startswith("gpu") and any(kw in err_msg.lower() for kw in
                ("cudnn", "preconditionnotmet", "dynamic library")):
                print(f"[PaddleOCR] GPU 运行时失败: {e}")
                print("[PaddleOCR] 自动退回 CPU 模式，重建引擎...")
                self._device = "cpu"
                self._ocr = None
                try:
                    self._ensure_ocr()
                except Exception:
                    self._last_confidence = 0.0
                    return ""
                try:
                    result = self._ocr.predict(image)
                    if result and len(result) > 0:
                        json_result = result[0].json
                        res_data = json_result.get("res", json_result)
                        texts = res_data.get("rec_texts", [])
                        scores = res_data.get("rec_scores", [])
                        if texts:
                            self._last_confidence = sum(scores) / len(scores) if scores else 0.0
                            return "".join(texts).replace(" ", "")
                except Exception as e2:
                    print(f"[PaddleOCR] CPU 重试失败: {e2}")
                return ""
            self._last_confidence = 0.0
            print(f"[PaddleOCR] 识别失败: {e}")
            return ""

    def warm_up(self):
        threading.Thread(target=self._ensure_ocr, daemon=True).start()


# ═══════════════ OpenAI Vision API ═══════════════
class OpenAIVisionEngine(BaseOCREngine):
    def __init__(self, config: dict):
        super().__init__(config)
        self.engine_name = "openai_vision"
        cfg = config.get("config", {})
        self._api_key = cfg.get("api_key", "")
        self._base_url = cfg.get("base_url", "https://api.openai.com/v1")
        self._model = cfg.get("model", "gpt-4o")
        self._prompt_template = cfg.get("prompt_template", "请识别图片中的文字，只返回文字内容")
        self._timeout = cfg.get("timeout", 30)
        self._retry = cfg.get("retry", 2)

    def check_availability(self) -> bool:
        try:
            url = self._base_url.rstrip("/") + "/models"
            headers = {"Authorization": f"Bearer {self._api_key}"}
            resp = requests.get(url, headers=headers, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def get_model_list(self) -> List[str]:
        try:
            url = self._base_url.rstrip("/") + "/models"
            headers = {"Authorization": f"Bearer {self._api_key}"}
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return [m["id"] for m in resp.json().get("data", [])]
        except Exception:
            pass
        return []

    def recognize(self, image: np.ndarray, prompt: Optional[str] = None) -> str:
        max_retries = getattr(self, '_retry', 2)
        t_start = time.time()
        prompt_text = prompt or self._prompt_template
        print(f"[OpenAI Vision] ═══════════ API 请求 ═══════════")
        print(f"[OpenAI Vision]   🔗 URL: {self._base_url}/chat/completions")
        print(f"[OpenAI Vision]   🤖 Model: {self._model}")
        print(f"[OpenAI Vision]   📝 Prompt: {prompt_text[:80]}{'...' if len(prompt_text) > 80 else ''}")
        print(f"[OpenAI Vision]   🖼 Image: {image.shape[1]}x{image.shape[0]}")
        for attempt in range(max_retries + 1):
            try:
                import cv2
                _, buf = cv2.imencode(".jpg", image)
                b64 = base64.b64encode(buf).decode("utf-8")
                headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": self._model,
                    "messages": [{"role": "user", "content": [{"type": "text", "text": prompt_text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}],
                    "max_tokens": 512
                }
                url = self._base_url.rstrip("/") + "/chat/completions"
                resp = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
                elapsed = time.time() - t_start
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    preview = content[:80].replace("\n", " ")
                    print(f"[OpenAI Vision]   ⏱ {elapsed:.1f}s | ✅ 响应({len(content)} chars): {preview}{'...' if len(content) > 80 else ''}")
                    return content
                print(f"[OpenAI Vision]   ❌ HTTP {resp.status_code} (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[OpenAI Vision]   ↻ 第{attempt+2}次重试...")
            except requests.exceptions.Timeout:
                elapsed = time.time() - t_start
                print(f"[OpenAI Vision]   ❌ 请求超时 ({self._timeout}s) (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[OpenAI Vision]   ↻ 第{attempt+2}次重试...")
                    continue
            except Exception as e:
                elapsed = time.time() - t_start
                print(f"[OpenAI Vision]   ❌ 请求异常: {e} (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[OpenAI Vision]   ↻ 第{attempt+2}次重试...")
                    continue
                return ""
        print(f"[OpenAI Vision]   ❌ 最终失败 (已重试 {max_retries} 次)")
        return ""


# ═══════════════ Ollama Vision ═══════════════
class OllamaVisionEngine(BaseOCREngine):
    def __init__(self, config: dict):
        super().__init__(config)
        self.engine_name = "ollama_vision"
        cfg = config.get("config", {})
        self._base_url = cfg.get("base_url", "http://localhost:11434")
        self._model = cfg.get("model", "llama3.2-vision:11b")
        self._prompt_template = cfg.get("prompt_template", "请识别图片中的文字，只返回文字内容")
        self._timeout = cfg.get("timeout", 60)
        self._retry = cfg.get("retry", 2)

    def check_availability(self) -> bool:
        try:
            url = self._base_url.rstrip("/")
            resp = requests.get(url, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def get_model_list(self) -> List[str]:
        try:
            url = self._base_url.rstrip("/") + "/api/tags"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                return [m["name"] for m in resp.json().get("models", [])]
        except Exception:
            pass
        return []

    def recognize(self, image: np.ndarray, prompt: Optional[str] = None) -> str:
        max_retries = getattr(self, '_retry', 2)
        t_start = time.time()
        prompt_text = prompt or self._prompt_template
        print(f"[Ollama Vision] ═══════════ API 请求 ═══════════")
        print(f"[Ollama Vision]   🔗 URL: {self._base_url}/api/generate")
        print(f"[Ollama Vision]   🤖 Model: {self._model}")
        print(f"[Ollama Vision]   📝 Prompt: {prompt_text[:80]}{'...' if len(prompt_text) > 80 else ''}")
        print(f"[Ollama Vision]   🖼 Image: {image.shape[1]}x{image.shape[0]}")
        for attempt in range(max_retries + 1):
            try:
                import cv2
                _, buf = cv2.imencode(".jpg", image)
                b64 = base64.b64encode(buf).decode("utf-8")
                payload = {"model": self._model, "prompt": prompt_text, "images": [b64], "stream": False}
                url = self._base_url.rstrip("/") + "/api/generate"
                resp = requests.post(url, json=payload, timeout=self._timeout)
                elapsed = time.time() - t_start
                if resp.status_code == 200:
                    content = resp.json().get("response", "").strip()
                    preview = content[:80].replace("\n", " ")
                    print(f"[Ollama Vision]   ⏱ {elapsed:.1f}s | ✅ 响应({len(content)} chars): {preview}{'...' if len(content) > 80 else ''}")
                    return content
                print(f"[Ollama Vision]   ❌ HTTP {resp.status_code} (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[Ollama Vision]   ↻ 第{attempt+2}次重试...")
            except requests.exceptions.Timeout:
                elapsed = time.time() - t_start
                print(f"[Ollama Vision]   ❌ 请求超时 ({self._timeout}s) (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[Ollama Vision]   ↻ 第{attempt+2}次重试...")
                    continue
            except Exception as e:
                elapsed = time.time() - t_start
                print(f"[Ollama Vision]   ❌ 请求异常: {e} (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[Ollama Vision]   ↻ 第{attempt+2}次重试...")
                    continue
                return ""
        print(f"[Ollama Vision]   ❌ 最终失败 (已重试 {max_retries} 次)")
        return ""


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
        try:
            url = self._base_url.rstrip("/") + "/v1/models"
            resp = requests.get(url, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def get_model_list(self) -> List[str]:
        try:
            url = self._base_url.rstrip("/") + "/v1/models"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                return [m["id"] for m in resp.json().get("data", [])]
        except Exception:
            pass
        return []

    def recognize(self, image: np.ndarray, prompt: Optional[str] = None) -> str:
        max_retries = getattr(self, '_retry', 2)
        t_start = time.time()
        prompt_text = prompt or self._prompt_template
        print(f"[llama.cpp] ═══════════ API 请求 ═══════════")
        print(f"[llama.cpp]   🔗 URL: {self._base_url}/v1/chat/completions")
        print(f"[llama.cpp]   🤖 Model: {self._model or '(default)'}")
        print(f"[llama.cpp]   📝 Prompt: {prompt_text[:80]}{'...' if len(prompt_text) > 80 else ''}")
        print(f"[llama.cpp]   🖼 Image: {image.shape[1]}x{image.shape[0]}")
        for attempt in range(max_retries + 1):
            try:
                import cv2
                _, buf = cv2.imencode(".jpg", image)
                b64 = base64.b64encode(buf).decode("utf-8")
                headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
                payload = {"model": self._model, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt_text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}], "max_tokens": 512, "temperature": 0}
                url = self._base_url.rstrip("/") + "/v1/chat/completions"
                resp = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
                elapsed = time.time() - t_start
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    preview = content[:80].replace("\n", " ")
                    print(f"[llama.cpp]   ⏱ {elapsed:.1f}s | ✅ 响应({len(content)} chars): {preview}{'...' if len(content) > 80 else ''}")
                    return content
                print(f"[llama.cpp]   ❌ HTTP {resp.status_code} (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[llama.cpp]   ↻ 第{attempt+2}次重试...")
            except requests.exceptions.Timeout:
                elapsed = time.time() - t_start
                print(f"[llama.cpp]   ❌ 请求超时 ({self._timeout}s) (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[llama.cpp]   ↻ 第{attempt+2}次重试...")
                    continue
            except Exception as e:
                elapsed = time.time() - t_start
                print(f"[llama.cpp]   ❌ 请求异常: {e} (第{attempt+1}次) | 耗时 {elapsed:.1f}s")
                if attempt < max_retries:
                    print(f"[llama.cpp]   ↻ 第{attempt+2}次重试...")
                    continue
                return ""
        print(f"[llama.cpp]   ❌ 最终失败 (已重试 {max_retries} 次)")
        return ""


# ═══════════════ 引擎注册表 ═══════════════
# 模块导入时立即注册 DLL 路径并预加载 torch
# 必须在 PyQt5 等可能干扰 DLL 搜索路径的模块之前执行
_register_dll_dirs()
try:
    import torch
except Exception:
    pass

ENGINE_CLASS_MAP = {
    "paddleocr": PaddleOCREngine,
    "openai_vision": OpenAIVisionEngine,
    "ollama_vision": OllamaVisionEngine,
    "llamacpp": LlamaCppEngine,
}


class OCREngineManager:
    def __init__(self):
        self._engines: Dict[str, BaseOCREngine] = {}
        self._config = load_engines_config()
        self._default_name = self._config.get("default_engine", "paddleocr")
        self._current_name = self._default_name
        self._hw_accel_enabled: bool = False

    def reload_config(self):
        self._config = load_engines_config()
        self._engines.clear()

    def get_engine_names(self) -> List[str]:
        engines_cfg = self._config.get("engines", {})
        return [name for name, cfg in engines_cfg.items() if cfg.get("enabled", True)]

    def get_engine_config(self, name: str) -> dict:
        return self._config.get("engines", {}).get(name, {})

    def set_hw_accel(self, enabled: bool):
        self._hw_accel_enabled = enabled
        for name, eng in self._engines.items():
            if hasattr(eng, 'set_hw_accel'):
                eng.set_hw_accel(enabled)

    def get_engine(self, name: Optional[str] = None) -> Optional[BaseOCREngine]:
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
        if self._hw_accel_enabled and hasattr(engine, 'set_hw_accel'):
            engine.set_hw_accel(True)
        if hasattr(engine, 'warm_up'):
            engine.warm_up()
        return engine

    def set_current_engine(self, name: str):
        if name in self.get_engine_names():
            self._current_name = name

    def get_current_engine_name(self) -> str:
        return self._current_name

    def get_current_engine(self) -> Optional[BaseOCREngine]:
        return self.get_engine(self._current_name)

    def get_default_engine_name(self) -> str:
        return self._default_name
