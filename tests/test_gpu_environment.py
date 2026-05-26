"""GPU 环境 / 导入链完整性测试 —— 防止 DLL 回归。

注意: import torch 测试需要在 ocr_gui.py 的 DLL 搜索路径设置之后才能成功。
      pytest 直接运行不会执行 ocr_gui.py 的 DLL 初始化，因此需要手动设置。
"""

import ast
import os
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent


def _setup_dll_search_paths():
    """复制 ocr_gui.py 的 DLL 搜索路径设置（pytest 环境下必需）。"""
    if sys.platform != "win32":
        return
    import importlib.util

    # torch/lib
    try:
        ts = importlib.util.find_spec("torch")
        if ts and ts.origin:
            tl = os.path.join(os.path.dirname(ts.origin), "lib")
            if os.path.isdir(tl):
                os.add_dll_directory(tl)
    except Exception:
        pass

    # nvidia/*/bin
    sp = os.path.join(str(BASE_DIR), ".venv", "Lib", "site-packages", "nvidia")
    if not os.path.isdir(sp):
        try:
            ns = importlib.util.find_spec("nvidia.cuda_runtime")
            if ns and ns.origin:
                sp = os.path.dirname(os.path.dirname(ns.origin))
        except Exception:
            sp = None
    if sp and os.path.isdir(sp):
        for entry in os.listdir(sp):
            bin_dir = os.path.join(sp, entry, "bin")
            if os.path.isdir(bin_dir):
                try:
                    os.add_dll_directory(bin_dir)
                except OSError:
                    pass


class TestTorchBeforePyQt:
    """验证 ocr_gui.py 中 import torch 在 PyQt5 之前。"""

    def test_torch_import_before_pyqt(self):
        content = (BASE_DIR / "ocr_gui.py").read_text(encoding="utf-8")
        tree = ast.parse(content)

        torch_line = pyqt_line = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "torch":
                        torch_line = node.lineno
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("PyQt5"):
                    if pyqt_line is None:
                        pyqt_line = node.lineno

        assert torch_line is not None, (
            "ocr_gui.py 缺少 'import torch' 预加载 —— "
            "PyQt5 导入前必须预加载 torch 防止 DLL 冲突"
        )
        assert pyqt_line is not None, "ocr_gui.py 缺少 PyQt5 import"
        assert torch_line < pyqt_line, (
            f"import torch (L{torch_line}) 必须在 PyQt5 import (L{pyqt_line}) 之前，"
            f"否则 Qt DLL 会破坏 torch DLL 搜索环境 → c10.dll 初始化失败"
        )


class TestTorchImport:
    """验证 torch 导入链（子进程隔离，避免 PyQt5 DLL 干扰）。

    注意: 不能直接 import torch —— pytest 已加载 PyQt5.QtCore，
          torch 必须在 PyQt5 之前导入，否则 c10.dll 失败 (WinError 1114)。
          因此所有 torch 导入测试必须在独立子进程中运行。
    """

    @staticmethod
    def _run_subprocess(script: str) -> tuple[int, str, str]:
        import subprocess
        r = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=30,
            cwd=str(BASE_DIR),
        )
        return r.returncode, r.stdout, r.stderr

    @pytest.mark.skipif(sys.platform != "win32", reason="torch DLL 隔离仅 Windows 需要")
    def test_torch_can_import_in_isolation(self):
        """torch 应能在独立进程中正常导入（无 PyQt5 干扰）。"""
        code = f"""
import os, sys
sys.path.insert(0, r'{BASE_DIR!s}')
if sys.platform == "win32":
    import importlib.util
    ts = importlib.util.find_spec("torch")
    if ts and ts.origin:
        d = os.path.join(os.path.dirname(ts.origin), "lib")
        if os.path.isdir(d):
            os.add_dll_directory(d)
import torch
print("torch", torch.__version__)
print("CUDA", torch.cuda.is_available())
"""
        rc, out, err = self._run_subprocess(code)
        assert rc == 0, f"torch 导入失败 (exit {rc}):\n{err}"
        assert "torch" in out, f"torch 导入未输出版本:\n{out}"

    @pytest.mark.skipif(sys.platform != "win32", reason="CUDA DLL 检测仅 Windows 需要")
    def test_cuda_availability_consistent(self):
        """如果 torch/lib 包含 CUDA DLL，子进程中 cuda.is_available() 应为 True。"""
        code = f"""
import os, sys
sys.path.insert(0, r'{BASE_DIR!s}')
if sys.platform == "win32":
    import importlib.util
    ts = importlib.util.find_spec("torch")
    if ts and ts.origin:
        d = os.path.join(os.path.dirname(ts.origin), "lib")
        if os.path.isdir(d):
            os.add_dll_directory(d)
import torch
lib = os.path.join(os.path.dirname(torch.__file__), "lib")
has_cuda = any(f.startswith("cudart") for f in os.listdir(lib)) if os.path.isdir(lib) else False
cuda_ok = torch.cuda.is_available()
if has_cuda and not cuda_ok:
    raise SystemExit("torch/lib has CUDA DLLs but cuda.is_available()=False")
print("OK")
"""
        rc, out, err = self._run_subprocess(code)
        assert rc == 0, f"CUDA 一致性检查失败 (exit {rc}):\n{err}\n{out}"


class TestOcrGuiStartupCheck:
    """验证 ocr_gui.py 中的 _verify_startup_environment 函数存在且语法正确。"""

    def test_startup_check_function_exists(self):
        content = (BASE_DIR / "ocr_gui.py").read_text(encoding="utf-8")
        tree = ast.parse(content)

        func_names = {
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        assert "_verify_startup_environment" in func_names, (
            "ocr_gui.py 缺少 _verify_startup_environment 启动自检函数"
        )


class TestNoQTimerInThread:
    """验证 ui/*.py 中没有在 threading.Thread target 内使用 QTimer.singleShot。"""

    @pytest.mark.parametrize("pyfile", sorted(
        (BASE_DIR / "ui").glob("*.py")
    ))
    def test_no_qtimer_in_thread(self, pyfile):
        content = pyfile.read_text(encoding="utf-8")
        tree = ast.parse(content)

        # 收集 threading.Thread(target=fn) 的 target 函数名
        thread_targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute) and func.attr == "Thread" and
                        isinstance(func.value, ast.Name) and func.value.id == "threading"):
                    for kw in node.keywords:
                        if kw.arg == "target" and isinstance(kw.value, ast.Name):
                            thread_targets.add(kw.value.id)

        # 检查 target 函数内是否有 QTimer.singleShot
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in thread_targets:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        f = sub.func
                        if (isinstance(f, ast.Attribute) and f.attr == "singleShot" and
                                isinstance(f.value, ast.Name) and f.value.id == "QTimer"):
                            violations.append(
                                f"  {pyfile.name}:{sub.lineno} '{node.name}()'"
                            )

        assert not violations, (
            "以下函数在 threading.Thread 中使用 QTimer.singleShot，"
            "应改用 pyqtSignal 跨线程桥:\n" + "\n".join(violations)
        )


class TestBatchFilesAscii:
    """验证所有 .bat 文件为纯 ASCII。"""

    @pytest.mark.parametrize("batfile", sorted(BASE_DIR.glob("*.bat")))
    def test_bat_file_is_ascii(self, batfile):
        data = batfile.read_bytes()
        for i, byte in enumerate(data):
            if byte > 127 and byte not in (0x0D, 0x0A):
                line = data[:i].count(b"\n") + 1
                pytest.fail(
                    f"{batfile.name}:L{line} 包含非 ASCII 字节 0x{byte:02X} "
                    f"—— .bat 文件必须为纯 ASCII"
                )


class TestImportChain:
    """验证 ORCP 核心导入链不触发 DLL 冲突（pytest 环境）。"""

    @pytest.fixture(autouse=True)
    def _setup_dlls(self):
        _setup_dll_search_paths()

    def test_core_imports_work(self):
        """所有 core 模块应能正常导入。"""
        from core.utils import MODE_OCR_ONLY, find_ffmpeg
        assert find_ffmpeg and MODE_OCR_ONLY

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL 检查")
    def test_ocr_engine_imports_on_windows(self):
        """PaddleOCR 引擎模块应能导入（DLL 搜索路径已设置）。"""
        from core.config_manager import ensure_config_files
        from core.ocr_engine import OCREngineManager
        ensure_config_files()
        mgr = OCREngineManager()
        assert "paddleocr" in mgr.get_engine_names()
