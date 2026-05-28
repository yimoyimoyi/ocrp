"""OCR 子进程服务器 —— 独立进程空间，隔离 PaddleOCR DLL 环境，避免与 torch CUDA 冲突。

协议（stdin/stdout JSON 行）：
  输入:  {"cmd":"recognize","id":1,"image_b64":"...","lang":"ch","device":"gpu"}
  输出:  {"status":"result","id":1,"text":"...","confidence":0.95}
  输出:  {"status":"error","id":1,"message":"..."}
  输入:  {"cmd":"set_device","device":"cpu"}
  输出:  {"status":"ok"}
  输入:  {"cmd":"shutdown"}
  输出:  {"status":"bye"}

用法:
  python core/ocr_server.py --config config/ocr_engines.json
"""

import base64
import json
import os
import sys

# ── 路径设置 ──
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# 屏蔽 PaddleOCR 联网检查
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

# ── DLL 路径注册（独立于主进程）──
if sys.platform == "win32":
    import importlib.util

    # torch/lib/ — torch 自带 CUDA 运行时
    _tl = None
    try:
        _ts = importlib.util.find_spec("torch")
        if _ts and _ts.origin:
            _tl = os.path.join(os.path.dirname(_ts.origin), "lib")
            if os.path.isdir(_tl):
                os.add_dll_directory(_tl)
                print(f"[OCR_SERVER] add_dll_directory: {_tl}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[OCR_SERVER] DLL 目录注册失败: {e}", file=sys.stderr, flush=True)

    # site-packages/nvidia/*/bin/ — paddlepaddle-gpu 依赖的 nvidia-* 包
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
        print("[OCR_SERVER] Registered nvidia site-packages DLL dirs", file=sys.stderr, flush=True)

    # cuDNN DLL 同步：torch cu126 与 nvidia-cudnn-cu12 各自携带不同构建的 cuDNN 9
    # 统一使用 torch/lib 的版本（已在 DLL 搜索路径中优先）
    _cudnn_bin = None
    for _sp in sys.path:
        _cand = os.path.join(_sp, "nvidia", "cudnn", "bin")
        if os.path.isdir(_cand):
            _cudnn_bin = _cand
            break
    if _cudnn_bin and _tl:
        import shutil
        _synced = 0
        for _fn in os.listdir(_tl):
            if (_fn.startswith("cudnn") or _fn == "zlibwapi.dll") and _fn.endswith(".dll"):
                _src = os.path.join(_tl, _fn)
                _dst = os.path.join(_cudnn_bin, _fn)
                try:
                    if not os.path.exists(_dst) or os.path.getsize(_src) != os.path.getsize(_dst):
                        shutil.copy2(_src, _dst)
                        _synced += 1
                except OSError:
                    pass
        if _synced > 0:
            print(f"[OCR_SERVER] Synced {_synced} cuDNN DLL(s) to nvidia-cudnn", file=sys.stderr, flush=True)


# ── PaddleOCR 懒加载 ──
_ocr_instance = None
_ocr_device = "cpu"
_ocr_lang = "ch"
_ocr_kwargs: dict = {}


def _load_config():
    """从 JSON 文件加载 paddleocr 引擎配置。"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="")
    args, _ = parser.parse_known_args()

    cfg_path = args.config
    if not cfg_path:
        cfg_path = os.path.join(BASE_DIR, "config", "ocr_engines.json")

    if not os.path.exists(cfg_path):
        print(f"[OCR_SERVER] config file not found: {cfg_path}", file=sys.stderr, flush=True)
        return {}

    from core.config_manager import _load_json_with_comments
    config = _load_json_with_comments(cfg_path)
    engines = config.get("engines", {})
    return engines.get("paddleocr", {}).get("config", {})


def _get_ocr():
    """获取或创建 PaddleOCR 实例。"""
    global _ocr_instance, _ocr_device, _ocr_lang, _ocr_kwargs

    if _ocr_instance is not None:
        return _ocr_instance

    try:
        from paddleocr import PaddleOCR
    except Exception as e:
        raise RuntimeError(f"PaddleOCR 导入失败: {e}") from e

    # GPU 检测
    device = _ocr_device
    if device.startswith("gpu"):
        try:
            import torch
            if not torch.cuda.is_available():
                device = "cpu"
                print("[OCR_SERVER] GPU not available, falling back to CPU", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[OCR_SERVER] GPU 检测失败: {e}", file=sys.stderr, flush=True)
            device = "cpu"

    kwargs = {
        "lang": _ocr_lang,
        "device": device,
        "enable_mkldnn": False,
    }
    # 只传递 PaddleOCR 实际接受的参数
    for _k in ("use_textline_orientation", "use_doc_orientation_classify",
               "use_doc_unwarping", "ocr_version"):
        if _k in _ocr_kwargs:
            kwargs[_k] = _ocr_kwargs[_k]

    print(f"[OCR_SERVER] loading PaddleOCR (device={device}, lang={_ocr_lang})...", file=sys.stderr, flush=True)

    try:
        _ocr_instance = PaddleOCR(**kwargs)
        print(f"[OCR_SERVER] PaddleOCR loaded OK (device={device})", file=sys.stderr, flush=True)
        return _ocr_instance
    except Exception as e:
        if device.startswith("gpu"):
            print(f"[OCR_SERVER] GPU init failed: {e}, retrying CPU...", file=sys.stderr, flush=True)
            device = "cpu"
            kwargs["device"] = "cpu"
            _ocr_device = "cpu"
            _ocr_instance = PaddleOCR(**kwargs)
            print("[OCR_SERVER] PaddleOCR loaded OK (CPU fallback)", file=sys.stderr, flush=True)
            return _ocr_instance
        raise


def _reload_ocr(device: str, **kwargs):
    """切换设备或参数后重建 PaddleOCR 实例。"""
    global _ocr_instance, _ocr_device, _ocr_lang, _ocr_kwargs
    _ocr_instance = None
    _ocr_device = device
    _ocr_lang = kwargs.pop("lang", _ocr_lang)
    _ocr_kwargs = kwargs
    return _get_ocr()


def _decode_image(image_b64: str, width: int = 0, height: int = 0, channels: int = 3):
    """从 base64 + 尺寸 JSON 解码为 numpy 数组（BGR）。"""
    import numpy as np
    try:
        raw = base64.b64decode(image_b64)
        if width > 0 and height > 0:
            # 原始像素 raw bytes 模式：零编码开销
            img = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, channels))
            return img
        # fallback: 编码图像模式（向后兼容）
        import cv2
        buf = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("cv2.imdecode returned None")
        return img
    except Exception as e:
        raise ValueError(f"图片解码失败: {e}") from e


def _log_stderr(msg: str):
    """原子写入 stderr（绕过 Python 缓冲，确保崩溃时信息不丢失）。"""
    try:
        os.write(2, (msg + "\n").encode("utf-8", errors="replace"))
    except Exception:
        pass


def main():
    global _ocr_device, _ocr_lang, _ocr_kwargs

    # 加载配置
    cfg = _load_config()
    _ocr_lang = cfg.get("lang", "ch")
    _ocr_device = cfg.get("device") or ("gpu" if cfg.get("use_gpu") else "cpu")
    _ocr_kwargs = {}
    # use_angle_cls (旧) 和 use_textline_orientation (新) 互斥
    if cfg.get("use_textline_orientation"):
        _ocr_kwargs["use_textline_orientation"] = True
    elif cfg.get("use_angle_cls", True):
        _ocr_kwargs["use_textline_orientation"] = True  # use_angle_cls → 新 API
    for _k in ("use_doc_orientation_classify", "use_doc_unwarping", "ocr_version"):
        _v = cfg.get(_k)
        if _v is not None:
            _ocr_kwargs[_k] = _v

    print(f"[OCR_SERVER] config: device={_ocr_device}, lang={_ocr_lang}", file=sys.stderr, flush=True)

    # 预热加载
    try:
        _get_ocr()
    except Exception as e:
        _log_stderr(f"[OCR_SERVER] warm-up failed: {e}")
        _send({"status": "error", "message": f"Warm-up failed: {e}"})
        # 继续运行，后续 recognize 会重试

    print("[OCR_SERVER] ready", file=sys.stderr, flush=True)

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
        req_id = req.get("id", 0)

        if cmd == "shutdown":
            _send({"status": "bye"})
            break

        elif cmd == "recognize":
            image_b64 = req.get("image_b64", "")
            if not image_b64:
                _send({"status": "error", "id": req_id, "message": "Missing image_b64"})
                continue

            try:
                img = _decode_image(image_b64, req.get("width", 0),
                                    req.get("height", 0), req.get("channels", 3))
            except Exception as e:
                _send({"status": "error", "id": req_id, "message": str(e)})
                continue

            try:
                ocr = _get_ocr()
                import time as _t
                t0 = _t.time()
                result = ocr.predict(img)
                elapsed = _t.time() - t0

                text = ""
                confidence = 0.0
                if result and len(result) > 0:
                    json_result = result[0].json
                    res_data = json_result.get("res", json_result)
                    texts = res_data.get("rec_texts", [])
                    scores = res_data.get("rec_scores", [])
                    if texts:
                        text = "".join(texts).replace(" ", "")
                        confidence = sum(scores) / len(scores) if scores else 0.0

                _log_stderr(f"[OCR_SERVER] recognize done ({elapsed:.1f}s): '{text[:50]}'")
                _send({"status": "result", "id": req_id, "text": text, "confidence": round(confidence, 4)})

            except Exception as e:
                err_msg = str(e)
                _log_stderr(f"[OCR_SERVER] recognize FAILED: {err_msg[:200]}")

                # GPU 错误 → 回退 CPU 重试一次（含 Paddle 内部 oneDNN bug 等非 GPU 显式报错）
                if _ocr_device.startswith("gpu"):
                    _log_stderr("[OCR_SERVER] GPU error, retrying with CPU...")
                    try:
                        _reload_ocr("cpu")
                        ocr = _get_ocr()
                        result = ocr.predict(img)
                        if result and len(result) > 0:
                            json_result = result[0].json
                            res_data = json_result.get("res", json_result)
                            texts = res_data.get("rec_texts", [])
                            scores = res_data.get("rec_scores", [])
                            text = "".join(texts).replace(" ", "") if texts else ""
                            confidence = sum(scores) / len(scores) if scores else 0.0
                        _send({"status": "result", "id": req_id, "text": text, "confidence": round(confidence, 4)})
                        continue
                    except Exception as e2:
                        err_msg = str(e2)

                _send({"status": "error", "id": req_id, "message": err_msg})

        elif cmd == "set_device":
            device = req.get("device", "cpu")
            try:
                _reload_ocr(device)
                _send({"status": "ok"})
                _log_stderr(f"[OCR_SERVER] device switched to {device}")
            except Exception as e:
                _send({"status": "error", "message": str(e)})

        elif cmd == "ping":
            _send({"status": "pong"})

        else:
            _send({"status": "error", "id": req_id, "message": f"Unknown cmd: {cmd}"})

    print("[OCR_SERVER] shutdown", file=sys.stderr, flush=True)


def _send(obj: dict):
    """发送 JSON 行到 stdout，确保原子写入。"""
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
