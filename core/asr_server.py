# -*- coding: utf-8 -*-
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

import sys, os, json, traceback

# ── 路径设置 ──
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ── DLL 路径注册（torch/lib/ + site-packages/nvidia/*/bin/）──
if sys.platform == "win32":
    import importlib.util, ctypes

    # torch/lib/ — torch 自带 CUDA 运行时
    try:
        _ts = importlib.util.find_spec("torch")
        if _ts and _ts.origin:
            _tl = os.path.join(os.path.dirname(_ts.origin), "lib")
            if os.path.isdir(_tl):
                os.add_dll_directory(_tl)
                print(f"[ASR_SERVER] add_dll_directory: {_tl}", file=sys.stderr, flush=True)
    except Exception:
        pass

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
        print(f"[ASR_SERVER] Registered nvidia site-packages DLL dirs", file=sys.stderr, flush=True)

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
                    except Exception:
                        pass

# 屏蔽 PaddleOCR 联网检查（虽然此进程不跑 PaddleOCR，但 config_manager 可能会触发）
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from config_manager import _load_json_with_comments
from pathlib import Path

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
        except Exception:
            pass
    return dict(_DEFAULT_CONFIG)


def _parse_temperature(val: str) -> list:
    try:
        parts = [v.strip() for v in val.split(",") if v.strip()]
        return [float(v) for v in parts] if parts else [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    except Exception:
        return [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def _resolve_local_model_path(model_dir: str) -> str:
    if not model_dir or not os.path.isdir(model_dir):
        return ""
    for root, dirs, files in os.walk(model_dir):
        if "model.bin" in files and "config.json" in files:
            return root
    return ""


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
    vad_threshold = cfg.get("vad_threshold", 0.5)
    word_timestamps = cfg.get("word_timestamps", True)

    # ── 加载模型 ──
    print(f"[ASR_SERVER] loading model...", file=sys.stderr, flush=True)
    try:
        local = _resolve_local_model_path(model_dir)
        if local:
            model_arg = local
            dl_root = None
            print(f"[ASR_SERVER] local model: {local}", file=sys.stderr, flush=True)
        else:
            model_arg = model_size
            dl_root = model_dir if model_dir and os.path.isdir(model_dir) else None
            print(f"[ASR_SERVER] model: {model_size}", file=sys.stderr, flush=True)

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
            print(f"[ASR_SERVER] fallback to CPU...", file=sys.stderr, flush=True)
            from faster_whisper import WhisperModel
            model = WhisperModel(
                model_arg,
                device="cpu",
                compute_type="int8",
                download_root=dl_root if 'dl_root' in dir() else None,
            )
            print(f"[ASR_SERVER] CPU model loaded OK", file=sys.stderr, flush=True)
        except Exception as e2:
            _send({"status": "error", "message": f"Model load failed: {e2}"})
            sys.exit(1)

    print(f"[ASR_SERVER] ready", file=sys.stderr, flush=True)

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
            vt = req.get("vad_threshold", vad_threshold)
            wts = req.get("word_timestamps", word_timestamps)

            vad_params = None
            if vad:
                vad_params = {"min_silence_duration_ms": vms}
                # threshold 在新版 faster-whisper 中可能改名/移除，安全忽略

            try:
                segments, info = model.transcribe(
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
                results = []
                for seg in segments:
                    if seg.text.strip():
                        results.append({
                            "start": seg.start,
                            "end": seg.end,
                            "text": seg.text.strip(),
                        })
                _send({"status": "ok", "results": results,
                       "detected_lang": info.language,
                       "lang_prob": info.language_probability})
            except Exception as e:
                _send({"status": "error", "message": str(e)})
        else:
            _send({"status": "error", "message": f"Unknown cmd: {cmd}"})

    print(f"[ASR_SERVER] shutdown", file=sys.stderr, flush=True)


def _send(obj: dict):
    """发送 JSON 行到 stdout，确保原子写入。"""
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
