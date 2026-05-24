"""串行测试：先 ASR (faster-whisper + GPU)，再 OCR"""
import os
import sys
import time

# ── DLL 搜索路径注册 + 显式预加载 ──
if sys.platform == "win32":
    import ctypes
    import importlib.util
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        _ts = importlib.util.find_spec("torch")
        if _ts and _ts.origin:
            _tl = os.path.join(os.path.dirname(_ts.origin), "lib")
            if os.path.isdir(_tl):
                os.add_dll_directory(_tl)
    except Exception:
        pass

    _cuda12 = os.path.join(BASE_DIR, "core", "cuda12")
    if os.path.isdir(_cuda12):
        os.add_dll_directory(_cuda12)

    _cudnn8 = os.path.join(BASE_DIR, "core", "cudnn8")
    if os.path.isdir(_cudnn8):
        os.add_dll_directory(_cudnn8)
        for _name in ("cudnn_ops_infer64_8.dll", "cudnn_cnn_infer64_8.dll",
                       "cudnn_adv_infer64_8.dll", "cudnn64_8.dll"):
            _fp = os.path.join(_cudnn8, _name)
            if os.path.exists(_fp):
                try:
                    ctypes.CDLL(_fp)
                except Exception:
                    pass

sys.path.insert(0, BASE_DIR)

# 修复 Windows 终端 GBK 编码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

VIDEO = os.path.join(BASE_DIR, "37749722223-1-192_fixed.mp4")

print("=" * 60)
print("[GPU 诊断]")
try:
    from ctranslate2 import get_supported_compute_types
    cu = get_supported_compute_types("cuda")
    cp = get_supported_compute_types("cpu")
    print(f"  ctranslate2 CUDA: {cu}")
    print(f"  ctranslate2 CPU:  {cp}")
    print("  => ctranslate2 supports CUDA" if cu else "  => ctranslate2 CPU-only")
except Exception as ex:
    print(f"  ctranslate2 failed: {ex}")

try:
    import torch
    print(f"  torch: {torch.__version__}, cuda={torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  torch GPU: {torch.cuda.get_device_name(0)}")
except Exception as ex:
    print(f"  torch failed: {ex}")

print()
print("=" * 60)
print("[ASR] faster-whisper (GPU) ...")
from core.asr_engine import ASREngineManager, extract_audio_from_video

audio_path = extract_audio_from_video(VIDEO, time_end=30.0)
if not audio_path:
    print("[ASR] audio extraction failed!")
    sys.exit(1)

mgr = ASREngineManager()
mgr.set_hw_accel(True)
eng = mgr.get_engine()
t0 = time.time()
segs, err = eng.transcribe(audio_path)
elapsed = time.time() - t0
if err:
    print(f"[ASR] FAILED: {err}")
else:
    print(f"[ASR] done ({elapsed:.1f}s), {len(segs)} segments:")
    for s in segs[:8]:
        print(f"  [{s['start']:.1f}s-{s['end']:.1f}s] {s['text']}")
    if len(segs) > 8:
        print(f"  ... {len(segs)} total segments")
try: os.unlink(audio_path)
except Exception: pass

print()
print("=" * 60)
print("[OCR] PaddleOCR ...")
from core.ffmpeg_reader import FFmpegReader
from core.ocr_engine import OCREngineManager as OCRMgr

ocr = OCRMgr()
e = ocr.get_engine("paddleocr")
e._use_gpu = False
ff = FFmpegReader(VIDEO, hw_accel=False)
ff.open()
for i in range(3):
    frame = ff.read()
    if frame is not None:
        t = e.recognize(frame)
        print(f"  frame {i+1}: '{t}'")
ff.close()
print("=" * 60)
print("All tests passed!")
