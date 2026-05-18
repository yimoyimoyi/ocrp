# -*- coding: utf-8 -*-
"""ORCP —— 带 GUI 的通用 OCR 工具入口。

用法:
    python ocr_gui.py
"""

import os
import sys
import warnings
from pathlib import Path

# ── 过滤无害的依赖版本警告 ──
warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version",
                        category=UserWarning, module="requests")

# ── Windows 控制台 UTF-8 编码 ──
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 确保项目根目录在 sys.path 中
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ── DLL 搜索路径注册 + 显式预加载 ──
#   1) torch/lib/   — torch 核心 DLL（c10.dll 等）
#   2) core/cuda12/ — CUDA 12 运行时
#   3) core/cudnn8/ — cuDNN 8（ctypes.CDLL 预加载，ctranslate2 GPU 必需）
if sys.platform == "win32":
    import importlib.util, ctypes

    try:
        _ts = importlib.util.find_spec("torch")
        if _ts and _ts.origin:
            _tl = os.path.join(os.path.dirname(_ts.origin), "lib")
            if os.path.isdir(_tl):
                os.add_dll_directory(_tl)
    except Exception:
        pass

    _cuda12 = os.path.join(str(BASE_DIR), "core", "cuda12")
    if os.path.isdir(_cuda12):
        os.add_dll_directory(_cuda12)

    _cudnn8 = os.path.join(str(BASE_DIR), "core", "cudnn8")
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

# 屏蔽 PaddleOCR 的联网检查
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

# ── 预加载 torch —— 必须在 PyQt5 之前，否则 Qt DLL 会破坏 torch 的 DLL 搜索环境 ──
try:
    import torch
except Exception:
    pass

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from ui.main_window import MainWindow


def main():
    """应用入口。"""
    app = QApplication(sys.argv)
    app.setApplicationName("ORCP")
    app.setOrganizationName("ORCP")

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
