"""ASR 子进程服务器 —— 独立进程空间，隔离 CUDA DLL 环境，避免与 PaddleOCR 冲突。

协议（stdin/stdout JSON 行）：
  输入:  {"cmd":"transcribe","audio_path":"...","language":"zh","beam_size":5,...}
  输出:  {"status":"ok","results":[{"start":0.0,"end":2.5,"text":"..."},...]}
  输出:  {"status":"error","message":"..."}
  输入:  {"cmd":"shutdown"}
  输出:  {"status":"bye"}

用法:
  python core/asr_server.py --config config/asr_engines.json
"""

import json
import os
import sys

# ── 路径设置 ──
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ── DLL 路径注册（torch/lib/ + site-packages/nvidia/*/bin/）──
if sys.platform == "win32":
    import ctypes
    import importlib.util

    # torch/lib/ — torch 自带 CUDA 运行时
    try:
        _ts = importlib.util.find_spec("torch")
        if _ts and _ts.origin:
            _tl = os.path.join(os.path.dirname(_ts.origin), "lib")
            if os.path.isdir(_tl):
                os.add_dll_directory(_tl)
                print(f"[ASR_SERVER] add_dll_directory: {_tl}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[ASR_SERVER] DLL 目录注册失败: {e}", file=sys.stderr, flush=True)

    # site-packages/nvidia/*/bin/ — cuDNN 8 等（pip 安装的 nvidia-* 包）
    _nvidia_found = False
    for _sp in sys.path:
        _nv_dir = os.path.join(_sp, "nvidia")
        if not os.path.isdir(_nv_dir):
            continue
        try:
            for _pkg in os.listdir(_nv_dir):
                _bin = os.path.join(_nv_dir, _pkg, "bin")
                if os.path.isdir(_bin):
                    os.add_dll_directory(_bin)
                    _nvidia_found = True
        except OSError:
            continue

    if _nvidia_found:
        print("[ASR_SERVER] Registered nvidia site-packages DLL dirs", file=sys.stderr, flush=True)

    # 备选：旧 core/cudnn8/ 目录（向后兼容）
    if not _nvidia_found:
        _cudnn8 = os.path.join(BASE_DIR, "core", "cudnn8")
        if os.path.isdir(_cudnn8):
            os.add_dll_directory(_cudnn8)
            print(f"[ASR_SERVER] add_dll_directory (legacy): {_cudnn8}", file=sys.stderr, flush=True)
            for _name in ("cudnn_ops_infer64_8.dll", "cudnn_cnn_infer64_8.dll",
                           "cudnn_adv_infer64_8.dll", "cudnn64_8.dll"):
                _fp = os.path.join(_cudnn8, _name)
                if os.path.exists(_fp):
                    try:
                        ctypes.CDLL(_fp)
                    except Exception as e:
                        print(f"[ASR_SERVER] CDLL 加载失败 ({_fp}): {e}", file=sys.stderr, flush=True)

# HuggingFace 源测速（选择最快的源）
def _test_hf_endpoint(url: str, timeout: float = 3.0) -> float:
    """测试 HuggingFace 端点响应时间，返回秒数（失败返回 inf）。"""
    import time
    import urllib.request
    try:
        start = time.monotonic()
        req = urllib.request.Request(url, method="HEAD")
        urllib.request.urlopen(req, timeout=timeout)
        return time.monotonic() - start
    except Exception:
        return float("inf")


def _select_fastest_hf_endpoint() -> str:
    """测速并选择最快的 HuggingFace 源。"""
    candidates = [
        "https://hf-mirror.com",
        "https://huggingface.co",
    ]
    results = []
    for url in candidates:
        t = _test_hf_endpoint(url)
        results.append((t, url))
        print(f"[ASR_SERVER] HF endpoint test: {url} -> {t:.2f}s", file=sys.stderr, flush=True)

    # 选择最快的源
    results.sort()
    fastest = results[0][1]
    print(f"[ASR_SERVER] Selected HF endpoint: {fastest}", file=sys.stderr, flush=True)
    return fastest

from pathlib import Path

from core.config_manager import _load_json_with_comments

_CONFIG_DIR = os.path.join(BASE_DIR, "config")

_DEFAULT_CONFIG = {
    "model_size": "large-v3",
    "model_dir": os.path.join(BASE_DIR, "models", "asr"),
    "language": "zh",
    "device": "cuda",
    "compute_type": "float16",
    "batch_size": 16,
    "beam_size": 5,
    "initial_prompt": "",
    "condition_on_previous_text": True,
    "no_speech_threshold": 0.6,
    "compression_ratio_threshold": 2.4,
    "temperature": "0.0,0.2,0.4,0.6,0.8,1.0",
    "hotwords": "",
    "vad_enabled": False,
    "vad_min_silence_ms": 500,
    "vad_threshold": 0.5,
    "word_timestamps": True,
}


def _load_config(config_path: str = None) -> dict:
    """加载 ASR 配置。"""
    if config_path and os.path.exists(config_path):
        try:
            cfg = _load_json_with_comments(Path(config_path))
            for k, v in _DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception as e:
            print(f"[ASR_SERVER] 加载配置失败: {e}", file=sys.stderr, flush=True)
    return dict(_DEFAULT_CONFIG)


def _parse_temperature(val: str) -> list:
    try:
        parts = [v.strip() for v in val.split(",") if v.strip()]
        return [float(v) for v in parts] if parts else [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    except Exception as e:
        print(f"[ASR_SERVER] 解析温度参数失败: {e}", file=sys.stderr, flush=True)
        return [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def _resolve_local_model_path(model_dir: str, model_size: str = "") -> str:
    """在 model_dir 中查找模型目录。

    优先级：
    1. model_size 指向的本地路径（如 "models--Systran--faster-whisper-large-v3"）
    2. 递归搜索包含 model.bin + config.json 的目录
    3. 空字符串表示找不到
    """
    if not model_dir or not os.path.isdir(model_dir):
        return ""

    # 1. 尝试 model_size 作为子目录
    if model_size:
        candidate = os.path.join(model_dir, model_size)
        if os.path.isdir(candidate):
            # 检查该目录是否直接包含 model.bin
            if os.path.isfile(os.path.join(candidate, "model.bin")):
                return candidate
            # 否则递归搜索该目录
            for root, dirs, files in os.walk(candidate):
                if "model.bin" in files and "config.json" in files:
                    return root

    # 2. 递归搜索整个 model_dir
    for root, dirs, files in os.walk(model_dir):
        if "model.bin" in files and "config.json" in files:
            print(f"[ASR_SERVER] found model at: {root}", file=sys.stderr, flush=True)
            return root

    return ""


def _check_cudnn8_gpu_ready() -> bool:
    """检测 cuDNN 8 必需的 3 个 DLL 是否全部可用。

    ctranslate2 GPU 语音识别需要：
      - cudnn_ops_infer64_8.dll   (基础 ops)
      - cudnn_cnn_infer64_8.dll   (卷积推理)
      - cudnn64_8.dll             (运行时)

    检查顺序：系统 PATH → models/asr/lib/ → ctranslate2 包目录

    返回 True 表示 GPU 可用，False 表示需回退 CPU。
    """
    import ctypes

    # ── 用户手动放置目录 ──
    lib_dir = os.path.join(BASE_DIR, "models", "asr", "lib")
    if os.path.isdir(lib_dir) and sys.platform == "win32":
        try:
            os.add_dll_directory(lib_dir)
        except (AttributeError, OSError):
            pass

    if sys.platform == "win32":
        REQUIRED_DLLS = ("cudnn_ops_infer64_8.dll", "cudnn_cnn_infer64_8.dll", "cudnn64_8.dll")
        missing = []
        for dll in REQUIRED_DLLS:
            found = False
            # 1. 系统 PATH
            try:
                ctypes.CDLL(dll)
                found = True
            except OSError:
                pass
            # 2. models/asr/lib/
            if not found:
                dll_path = os.path.join(lib_dir, dll)
                if os.path.isfile(dll_path):
                    try:
                        ctypes.CDLL(dll_path)
                        found = True
                    except OSError:
                        pass
            # 3. ctranslate2 包目录
            if not found:
                try:
                    import ctranslate2
                    pkg_dir = os.path.dirname(ctranslate2.__file__)
                    dll_path = os.path.join(pkg_dir, dll)
                    if os.path.isfile(dll_path):
                        os.add_dll_directory(pkg_dir)
                        ctypes.CDLL(dll)
                        found = True
                except Exception as e:
                    print(f"[ASR_SERVER] cuDNN DLL 加载失败: {e}", file=sys.stderr, flush=True)
            if not found:
                missing.append(dll)

        if not missing:
            print("[ASR_SERVER] cuDNN 8 found: all 3 DLLs OK", file=sys.stderr, flush=True)
            return True
        else:
            print(f"[ASR_SERVER] cuDNN 8 INCOMPLETE - missing: {', '.join(missing)}", file=sys.stderr, flush=True)
            return False
    else:
        REQUIRED_SO = ("libcudnn_ops_infer.so.8", "libcudnn_cnn_infer.so.8", "libcudnn.so.8")
        missing = []
        for soname in REQUIRED_SO:
            found = False
            try:
                ctypes.CDLL(soname)
                found = True
            except OSError:
                so_path = os.path.join(lib_dir, soname)
                if os.path.isfile(so_path):
                    try:
                        ctypes.CDLL(so_path)
                        found = True
                    except OSError:
                        pass
            if not found:
                missing.append(soname)

        if not missing:
            print("[ASR_SERVER] cuDNN 8 found: all 3 SOs OK", file=sys.stderr, flush=True)
            return True
        else:
            print(f"[ASR_SERVER] cuDNN 8 INCOMPLETE - missing: {', '.join(missing)}", file=sys.stderr, flush=True)
            return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="Path to ASR config JSON")
    args = parser.parse_args()

    cfg = _load_config(args.config)

    model_size = cfg.get("model_size", "large-v3")
    model_dir = cfg.get("model_dir", "")
    language = cfg.get("language", "zh")
    device = cfg.get("device", "cuda")
    compute_type = cfg.get("compute_type", "float16")
    beam_size = cfg.get("beam_size", 5)
    initial_prompt = cfg.get("initial_prompt", "") or None
    condition_on_prev = cfg.get("condition_on_previous_text", True)
    no_speech_thresh = cfg.get("no_speech_threshold", 0.6)
    comp_ratio_thresh = cfg.get("compression_ratio_threshold", 2.4)
    temperature_str = cfg.get("temperature", "0.0,0.2,0.4,0.6,0.8,1.0")
    hotwords = cfg.get("hotwords", "") or None
    vad_enabled = cfg.get("vad_enabled", False)
    vad_min_silence = cfg.get("vad_min_silence_ms", 500)
    _vad_threshold = cfg.get("vad_threshold", 0.5)
    word_timestamps = cfg.get("word_timestamps", True)
    hf_endpoint = cfg.get("hf_endpoint", "") or None

    # 先检查本地模型是否存在（避免不必要的测速和下载）
    _local_model = _resolve_local_model_path(model_dir, model_size)
    if _local_model:
        # 本地模型存在，强制离线模式，跳过测速
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ.pop("HF_ENDPOINT", None)
        print(f"[ASR_SERVER] local model found: {_local_model}, using offline mode", file=sys.stderr, flush=True)
    else:
        # 本地模型不存在，测速选择最快的源
        os.environ.pop("HF_HUB_OFFLINE", None)
        if hf_endpoint:
            os.environ["HF_ENDPOINT"] = hf_endpoint
            print(f"[ASR_SERVER] HF_ENDPOINT (configured): {hf_endpoint}", file=sys.stderr, flush=True)
        else:
            fastest = _select_fastest_hf_endpoint()
            os.environ["HF_ENDPOINT"] = fastest

    # ── cuDNN 8 预检（GPU 模式）──
    # ctranslate2 < 5 的 CUDA 推理依赖 cuDNN 8 DLL。
    # 如果缺失，直接回退到 CPU 模式加载模型，避免 model.transcribe() 硬崩溃。
    _using_gpu = (device == "cuda")
    if _using_gpu and not _check_cudnn8_gpu_ready():
        print("[ASR_SERVER] ⚠ cuDNN 8 不可用，自动回退 CPU 模式", file=sys.stderr, flush=True)
        device = "cpu"
        compute_type = "int8"
        _using_gpu = False

    # ── 加载模型 ──
    print("[ASR_SERVER] loading model...", file=sys.stderr, flush=True)
    model_arg = None
    dl_root = None
    try:
        if _local_model:
            model_arg = _local_model
            dl_root = None
            print(f"[ASR_SERVER] using local model: {_local_model}", file=sys.stderr, flush=True)
        else:
            model_arg = model_size
            dl_root = model_dir if model_dir and os.path.isdir(model_dir) else None
            print(f"[ASR_SERVER] model: {model_size} (downloading from {os.environ.get('HF_ENDPOINT', 'default')})", file=sys.stderr, flush=True)

        from faster_whisper import WhisperModel
        model = WhisperModel(
            model_arg,
            device=device,
            compute_type=compute_type,
            download_root=dl_root,
        )
        print(f"[ASR_SERVER] model loaded OK (device={device})", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[ASR_SERVER] GPU load failed: {e}", file=sys.stderr, flush=True)
        try:
            print("[ASR_SERVER] fallback to CPU...", file=sys.stderr, flush=True)
            from faster_whisper import WhisperModel
            model = WhisperModel(
                model_arg,
                device="cpu",
                compute_type="int8",
                download_root=dl_root,
            )
            print("[ASR_SERVER] CPU model loaded OK", file=sys.stderr, flush=True)
        except Exception as e2:
            _send({"status": "error", "message": f"Model load failed: {e2}"})
            sys.exit(1)

    print("[ASR_SERVER] ready", file=sys.stderr, flush=True)

    # ── 辅助函数：原子写入 stderr（绕过 Python 缓冲，确保进程崩溃时数据不丢失）──
    def _log_stderr(msg: str):
        """使用原始文件描述符写入 stderr，确保硬崩溃时数据可送达父进程。"""
        try:
            os.write(2, (msg + "\n").encode("utf-8", errors="replace"))
        except Exception as e:
            print(f"[ASR_SERVER] 写入 stderr 失败: {e}", file=sys.stderr, flush=True)

    # ── 主循环 ──
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _send({"status": "error", "message": "Invalid JSON"})
            continue

        cmd = req.get("cmd", "")
        if cmd == "shutdown":
            _send({"status": "bye"})
            break
        elif cmd == "transcribe":
            audio_path = req.get("audio_path", "")
            if not audio_path or not os.path.exists(audio_path):
                _send({"status": "error", "message": f"Audio not found: {audio_path}"})
                continue

            print(f"[ASR_SERVER] transcribe request: {os.path.basename(audio_path)}", file=sys.stderr, flush=True)

            # 使用请求中的参数覆盖，否则用默认配置
            lang = req.get("language", language)
            lang = None if lang == "auto" else lang
            bs = req.get("beam_size", beam_size)
            ip = req.get("initial_prompt") or initial_prompt
            cop = req.get("condition_on_previous_text", condition_on_prev)
            nst = req.get("no_speech_threshold", no_speech_thresh)
            crt = req.get("compression_ratio_threshold", comp_ratio_thresh)
            temp = _parse_temperature(req.get("temperature", temperature_str))
            hw = req.get("hotwords") or hotwords
            vad = req.get("vad_enabled", vad_enabled)
            vms = req.get("vad_min_silence_ms", vad_min_silence)
            wts = req.get("word_timestamps", word_timestamps)

            vad_params = None
            if vad:
                vad_params = {"min_silence_duration_ms": vms}

            def _do_transcribe(_model):
                import time as _t
                t0 = _t.time()
                _log_stderr(f"[ASR_SERVER] >>> model.transcribe() START: audio={os.path.basename(audio_path)} lang={lang}")
                segs, inf = _model.transcribe(
                    audio_path,
                    language=lang,
                    beam_size=bs,
                    initial_prompt=ip,
                    condition_on_previous_text=cop,
                    no_speech_threshold=nst,
                    compression_ratio_threshold=crt,
                    temperature=temp,
                    hotwords=hw,
                    word_timestamps=wts,
                    vad_filter=vad,
                    vad_parameters=vad_params,
                )
                res = []
                total = 0
                for s in segs:
                    total += 1
                    if s.text.strip():
                        seg_obj = {
                            "start": s.start,
                            "end": s.end,
                            "text": s.text.strip(),
                        }
                        res.append(seg_obj)
                        # 🔥 逐段发送，UI 实时更新
                        _send({"status": "segment",
                               "start": s.start,
                               "end": s.end,
                               "text": s.text.strip()})
                elapsed = _t.time() - t0
                print(f"[ASR_SERVER] transcribe done ({elapsed:.1f}s): {len(res)}/{total} segments, lang={inf.language}", file=sys.stderr, flush=True)
                if total > 0 and len(res) == 0:
                    print(f"[ASR_SERVER] WARNING: all {total} segments had empty text! Check no_speech_threshold (current={nst})", file=sys.stderr, flush=True)
                # 最终确认帧
                _send({"status": "done",
                       "detected_lang": inf.language,
                       "lang_prob": inf.language_probability,
                       "total_segments": total,
                       "valid_segments": len(res)})

            # ── 尝试 GPU / 已加载模型 ──
            try:
                _do_transcribe(model)
                continue
            except Exception as e:
                err_msg = str(e)
                print(f"[ASR_SERVER] transcribe FAILED: {err_msg[:200]}", file=sys.stderr, flush=True)

                # GPU OOM 等运行时错误 → 回退 CPU
                is_gpu_error = any(kw in err_msg.lower() for kw in
                    ("cuda", "cublas", "gpu", "device", "out of memory"))

                if not is_gpu_error or not _using_gpu:
                    _send({"status": "error", "message": err_msg})
                    continue

            # ── 回退到 CPU ──
            print("[ASR_SERVER] falling back to CPU...", file=sys.stderr, flush=True)
            try:
                from faster_whisper import WhisperModel as _WhisperModel
                cpu_model = _WhisperModel(
                    model_arg,
                    device="cpu",
                    compute_type="int8",
                    download_root=dl_root,
                )
                _do_transcribe(cpu_model)
            except Exception as e2:
                print(f"[ASR_SERVER] CPU transcribe also FAILED: {e2}", file=sys.stderr, flush=True)
                _send({"status": "error", "message": f"GPU+CPU both failed: {e2}"})
        else:
            _send({"status": "error", "message": f"Unknown cmd: {cmd}"})

    print("[ASR_SERVER] shutdown", file=sys.stderr, flush=True)


def _send(obj: dict):
    """发送 JSON 行到 stdout，确保原子写入。"""
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
