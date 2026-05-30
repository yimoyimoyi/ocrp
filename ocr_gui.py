"""ORCP —— 带 GUI 的通用 OCR 工具入口。

用法:
    python ocr_gui.py
    orcp              # 通过 pyproject.toml 注册的入口点
"""

import os
import sys
import warnings
from pathlib import Path

# ── 过滤无害的依赖版本警告（必须在其他导入之前） ──
warnings.filterwarnings(
    "ignore",
    message="urllib3.*doesn't match a supported version",
    category=UserWarning,
    module="requests",
)
# qt-material 的 QFontDatabase 警告（PyQt5 兼容性问题，无害）
warnings.filterwarnings(
    "ignore",
    message=".*QFontDatabase.*",
    category=UserWarning,
)

# qt-material 的 logging 警告（PyQt5 兼容性问题，无害）
import logging


class _QtMaterialFilter(logging.Filter):
    """过滤 qt-material 的无害警告。"""
    _SUPPRESSED = {"qt_material must be imported after", "QFontDatabase", "Could not parse"}

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(s in msg for s in self._SUPPRESSED)


logging.getLogger().addFilter(_QtMaterialFilter())

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

# ── 强制 Python 默认文件编码为 UTF-8（解决 qt-material 在中文 Windows 下 GBK 解码问题）──
os.environ.setdefault("PYTHONUTF8", "1")

# 确保项目根目录在 sys.path 中
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ── DLL 搜索路径注册 + 显式预加载 ──
#   1) torch/lib/   — torch 核心 DLL（c10.dll 等）
#   2) core/cuda12/ — CUDA 12 运行时
#   3) core/cudnn8/ — cuDNN 8（ctypes.CDLL 预加载，ctranslate2 GPU 必需）
if sys.platform == "win32":
    import ctypes
    import importlib.util

    # ── 辅助：启动阶段日志（logger 尚未初始化，使用 print）──
    def _startup_log(msg: str, kind: str = "info"):
        prefix = {"info": "[ORCP]", "warn": "[ORCP] ⚠", "error": "[ORCP] ❌"}.get(kind, "[ORCP]")
        print(f"{prefix} {msg}", flush=True)

    try:
        _ts = importlib.util.find_spec("torch")
        if _ts and _ts.origin:
            _tl = os.path.join(os.path.dirname(_ts.origin), "lib")
            if os.path.isdir(_tl):
                os.add_dll_directory(_tl)
    except Exception as _e:
        _startup_log(f"torch/lib DLL 目录注册失败: {_e}", "warn")

    # ── 注：PaddleOCR 已移至独立子进程（core/ocr_server.py），
    # 主进程不再需要 nvidia DLL 搜索路径或 cuDNN 同步。
    # OCR 子进程的 DLL 隔离由 ocr_server.py 自行处理。

    _cuda12 = os.path.join(str(BASE_DIR), "core", "cuda12")
    if os.path.isdir(_cuda12):
        try:
            os.add_dll_directory(_cuda12)
        except OSError as _e:
            _startup_log(f"core/cuda12 目录注册失败: {_e}", "warn")

    _cudnn8 = os.path.join(str(BASE_DIR), "core", "cudnn8")
    if os.path.isdir(_cudnn8):
        try:
            os.add_dll_directory(_cudnn8)
        except OSError as _e:
            _startup_log(f"core/cudnn8 目录注册失败: {_e}", "warn")
        for _name in ("cudnn_ops_infer64_8.dll", "cudnn_cnn_infer64_8.dll",
                       "cudnn_adv_infer64_8.dll", "cudnn64_8.dll"):
            _fp = os.path.join(_cudnn8, _name)
            if os.path.exists(_fp):
                try:
                    ctypes.CDLL(_fp)
                except Exception as _e:
                    _startup_log(f"cuDNN 8 DLL 预加载失败 ({_name}): {_e}", "warn")

# 屏蔽 PaddleOCR 的联网检查
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'

# ── 预加载 torch —— 必须在 PyQt5 之前，否则 Qt DLL 会破坏 torch 的 DLL 搜索环境 ──
try:
    import torch  # noqa: F401 预加载，防止 Qt DLL 干扰
    _torch_loaded = True
except Exception as _e:
    print(f"[ORCP] ⚠ torch 预加载失败: {_e}", flush=True)
    _torch_loaded = False


# ── 启动时环境自检 ──
def _verify_startup_environment():
    """验证关键环境约束，防止 DLL/导入回归问题。"""
    issues = []

    # 1) torch 必须在 PyQt5 之前预加载
    if not _torch_loaded:
        issues.append("torch 预加载失败：PyQt5 导入可能导致 c10.dll 初始化失败 (WinError 1114)")

    # 2) 检查 torch CUDA 状态
    try:
        import torch as _tc
        _cuda_ok = _tc.cuda.is_available()
        if _cuda_ok:
            _gpu_name = _tc.cuda.get_device_name(0)
            print(f"[ORCP] GPU 可用: {_gpu_name} (CUDA {_tc.version.cuda})")
        else:
            print("[ORCP] 主进程 torch 为 CPU 版本（OCR/ASR 子进程独立，仍可 GPU）")
    except Exception as _e:
        issues.append(f"CUDA 状态检查失败: {_e}")

    # 3) 输出汇总
    if issues:
        print("[ORCP] ⚠ 环境自检发现问题:")
        for _i in issues:
            print(f"  - {_i}")
    else:
        print("[ORCP] 环境自检通过")
    return len(issues) == 0


from PyQt5.QtWidgets import QApplication

from core.i18n import LanguageManager
from core.logger import get_logger
from ui.main_window import MainWindow

logger = get_logger(__name__)


def _install_crash_handler():
    """安装全局异常钩子，将未捕获异常写入崩溃日志。"""
    import traceback
    from datetime import datetime
    from pathlib import Path

    log_dir = Path(os.path.dirname(os.path.abspath(__file__))) / "logs"
    crash_log = log_dir / "crash.log"

    def handler(exc_type, exc_value, exc_tb):
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Unhandled exception:\n{''.join(tb_lines)}\n"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(msg)
        except OSError:
            pass
        # 同时输出到 stderr
        sys.stderr.write(msg)
        sys.stderr.flush()

    sys.excepthook = handler


_install_crash_handler()


def main():
    """应用入口。"""
    from core.config_manager import ConfigManager, ensure_config_files
    ensure_config_files()

    # ── 初始化国际化：优先从配置读取语言设置 ──
    _config_mgr_lang = ConfigManager()
    _saved_lang = _config_mgr_lang.get_language()
    LanguageManager.initialize(_saved_lang if _saved_lang else "")

    logger.info("ORCP 启动中...")
    logger.info("Python %s | 平台 %s", sys.version.split()[0], sys.platform)

    # Windows 上强制 QMediaPlayer 使用 WMF 后端，避免 DirectShow 解码器缺失
    if sys.platform == "win32":
        os.environ["QT_MULTIMEDIA_PREFERRED_PLUGINS"] = "wmf"
    app = QApplication(sys.argv)
    app.setApplicationName("ORCP")
    app.setOrganizationName("ORCP")
    app.setApplicationVersion("0.2.0")

    window = MainWindow()
    window.setup()
    window.setUpdatesEnabled(True)
    window.setMinimumSize(1024, 680)
    window._restore_window_geometry()

    # ── 淡入动画：先完全透明 → show → 150ms 渐变到不透明 ──
    window.setWindowOpacity(0.0)
    window.show()

    from PyQt5.QtCore import QEasingCurve, QPropertyAnimation
    _fade = QPropertyAnimation(window, b"windowOpacity")
    _fade.setDuration(150)
    _fade.setStartValue(0.0)
    _fade.setEndValue(1.0)
    _fade.setEasingCurve(QEasingCurve.OutCubic)
    _fade.start()

    _verify_startup_environment()

    logger.info("主窗口已显示，进入事件循环")
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
