# -*- coding: utf-8 -*-
"""多 OCR 引擎管理器 —— 支持 PaddleOCR 本地引擎及各类 Vision API。
硬件加速由全局设置 hw_accel 统一控制。

DLL 隔离策略：
  - PaddleOCR CPU 模式：不加载 CUDA DLL，torch/WhisperX 正常运行
  - PaddleOCR GPU 模式：从 site-packages/nvidia/*/bin/ 加载 CUDA/cuDNN（pip 安装的 nvidia-* 包）
  - albumentations → torch 导入由 _BlockTorchImport 阻断，PaddleOCR 初始化完即恢复
"""

import os, sys, ctypes, re, json, base64, threading, requests, time, numpy as np

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


def _preload_core_dlls():
    """GPU 模式时从 site-packages/nvidia/*/bin/ 加载 CUDA/cuDNN DLL。

    这些 DLL 由 pip 包（nvidia-cuda-runtime-cu12, nvidia-cublas-cu12,
    nvidia-cudnn-cu12 等）安装，不再需要捆绑到仓库中。
    使用 os.add_dll_directory() 注册搜索路径，让 Windows 在需要时自动解析。
    """
    global _dll_loaded
    if _dll_loaded:
        return
    with _dll_load_lock:
        if _dll_loaded:
            return
        _dll_loaded = True
        if sys.platform != "win32":
            return

        # 优先从 site-packages/nvidia/*/bin/ 加载
        nvidia_dirs = _find_nvidia_site_packages_dirs()
        if nvidia_dirs:
            for d in nvidia_dirs:
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
            print(f"[PaddleOCR] ✅ 已注册 {len(nvidia_dirs)} 个 nvidia DLL 搜索路径")
            return

        # 备选：从旧 core/cuda12/ core/cudnn8/ 目录加载（向后兼容迁移期）
        for legacy in ("cuda12", "cudnn8"):
            _legacy_dir = os.path.join(_CORE_DLL_DIR, legacy)
            if os.path.isdir(_legacy_dir):
                try:
                    os.add_dll_directory(_legacy_dir)
                except OSError:
                    pass

        if not nvidia_dirs:
            print("[PaddleOCR] ⚠ 未找到 nvidia-* pip 包的 DLL，"
                  "GPU 加速可能不可用，请运行: uv sync")


# ── 临时阻断 torch 导入 ──
class _BlockTorchImport:
    """sys.meta_path 导入钩子 —— 阻截 torch 及其子模块的导入。"""
    def find_spec(self, fullname, path, target=None):
        if fullname == "torch" or fullname.startswith("torch."):
            raise ImportError("torch blocked during PaddleOCR init: " + fullname)
        return None


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
        self._use_gpu = cfg.get("use_gpu", False)
        self._show_log = cfg.get("show_log", False)
        self._fast_mode = cfg.get("fast_mode", True)
        self._rec_batch = cfg.get("rec_batch_num", 6)

    def set_hw_accel(self, enabled: bool):
        if self._use_gpu != enabled:
            self._use_gpu = enabled
            self._ocr = None

    def _ensure_ocr(self):
        if self._ocr is None:
            with self._init_lock:
                if self._ocr is None:
                    _blocker = _BlockTorchImport()
                    sys.meta_path.insert(0, _blocker)
                    try:
                        if self._use_gpu:
                            _preload_core_dlls()
                        from paddleocr import PaddleOCR
                    finally:
                        if _blocker in sys.meta_path:
                            sys.meta_path.remove(_blocker)

                    try:
                        self._ocr = PaddleOCR(
                            use_angle_cls=self._use_angle_cls,
                            lang=self._lang,
                            use_gpu=self._use_gpu,
                            show_log=self._show_log,
                            det_db_score_mode="fast" if self._fast_mode else "slow",
                            rec_batch_num=self._rec_batch,
                        )
                    except Exception as e:
                        if self._use_gpu:
                            print(f"[PaddleOCR] GPU 初始化失败: {e}")
                            print("[PaddleOCR] 自动退回 CPU 模式...")
                            self._use_gpu = False
                            self._ocr = PaddleOCR(
                                use_angle_cls=self._use_angle_cls,
                                lang=self._lang,
                                use_gpu=False,
                                show_log=self._show_log,
                                det_db_score_mode="fast" if self._fast_mode else "slow",
                                rec_batch_num=self._rec_batch,
                            )
                        else:
                            raise

    def _upscale_if_small(self, image: np.ndarray, min_side: int = 300) -> np.ndarray:
        """PaddleOCR 的文字检测模型（DB）对极小图片检测不到文本框。
        若图片最短边小于 min_side，等比放大到短边为 min_side。"""
        h, w = image.shape[:2]
        if h >= min_side and w >= min_side:
            return image
        scale = min_side / min(h, w)
        import cv2
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    def recognize(self, image: np.ndarray, prompt: Optional[str] = None) -> str:
        try:
            self._ensure_ocr()
            upscaled = self._upscale_if_small(image)
            results = self._ocr.ocr(upscaled, cls=self._use_angle_cls)
            if results and results[0]:
                confs = [line[1][1] for line in results[0]]
                text = "".join([line[1][0] for line in results[0]]).replace(" ", "")
                self._last_confidence = sum(confs) / len(confs) if confs else 0.0
                return text
            self._last_confidence = 0.0
            return ""
        except Exception as e:
            err_msg = str(e)
            if self._use_gpu and any(kw in err_msg.lower() for kw in
                ("cudnn", "preconditionnotmet", "dynamic library")):
                print(f"[PaddleOCR] GPU 运行时失败: {e}")
                print("[PaddleOCR] 自动退回 CPU 模式，重建引擎...")
                self._use_gpu = False
                self._ocr = None
                try:
                    self._ensure_ocr()
                except Exception:
                    self._last_confidence = 0.0
                    return ""
                try:
                    results = self._ocr.ocr(image, cls=self._use_angle_cls)
                    if results and results[0]:
                        confs = [line[1][1] for line in results[0]]
                        self._last_confidence = sum(confs) / len(confs) if confs else 0.0
                        return "".join([line[1][0] for line in results[0]]).replace(" ", "")
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

    def reload_config(self):
        self._config = load_engines_config()
        self._engines.clear()

    def get_engine_names(self) -> List[str]:
        engines_cfg = self._config.get("engines", {})
        return [name for name, cfg in engines_cfg.items() if cfg.get("enabled", True)]

    def get_engine_config(self, name: str) -> dict:
        return self._config.get("engines", {}).get(name, {})

    def set_hw_accel(self, enabled: bool):
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
