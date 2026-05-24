#!/usr/bin/env python
"""pre-commit 检查：防止 DLL/GPU 导入相关的已知回归问题。

检查项:
  1. ocr_gui.py 中 import torch 必须在 from PyQt5 之前
  2. ui/*.py 中禁止在 threading.Thread(target=...) 内使用 QTimer.singleShot
  3. .bat 文件必须是纯 ASCII（无多字节 UTF-8 字符）
"""

import ast
import os
import sys
from pathlib import Path

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXIT = 0


def fail(msg: str):
    global EXIT
    print(f"  FAIL: {msg}")
    EXIT = 1


def ok(msg: str):
    print(f"  OK: {msg}")


# ── 1) ocr_gui.py: import torch before from PyQt5 ──
def check_torch_before_pyqt():
    ocr_gui = BASE_DIR / "ocr_gui.py"
    if not ocr_gui.exists():
        return
    tree = ast.parse(ocr_gui.read_text(encoding="utf-8"))
    torch_line = pyqt_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "torch":
                    torch_line = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("PyQt5"):
                pyqt_line = node.lineno
    if torch_line and pyqt_line and torch_line < pyqt_line:
        ok(f"ocr_gui.py: import torch (L{torch_line}) < PyQt5 import (L{pyqt_line})")
    elif not torch_line:
        fail("ocr_gui.py: 缺少 'import torch' 预加载")
    elif not pyqt_line:
        ok("ocr_gui.py: 未检测到 PyQt5 import（可能已被重构）")
    else:
        fail(f"ocr_gui.py: import torch (L{torch_line}) 必须在 PyQt5 import (L{pyqt_line}) 之前")


# ── 2) ui/*.py: no QTimer.singleShot inside threading.Thread ──
def check_no_qtimer_in_thread(filepath: Path):
    """检查单个文件。"""
    content = filepath.read_text(encoding="utf-8")
    if "QTimer.singleShot" not in content and "QTimer" not in content:
        return  # 文件不涉及 QTimer，跳过

    # 简单启发式：找到所有 threading.Thread(target=...) 的 target 函数
    # 然后检查这些函数内是否使用了 QTimer.singleShot
    tree = ast.parse(content)

    # 收集所有 threading.Thread(target=name) 中的 target 函数名
    thread_targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "Thread":
                if isinstance(func.value, ast.Name) and func.value.id == "threading":
                    for kw in node.keywords:
                        if kw.arg == "target" and isinstance(kw.value, ast.Name):
                            thread_targets.add(kw.value.id)

    if not thread_targets:
        return

    # 检查这些 target 函数内是否有 QTimer.singleShot
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in thread_targets:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if (isinstance(f, ast.Attribute) and f.attr == "singleShot" and
                            isinstance(f.value, ast.Name) and f.value.id == "QTimer"):
                        fail(f"{filepath.name}:{sub.lineno} '{node.name}' 函数在 threading.Thread 中使用 QTimer.singleShot（应改用 pyqtSignal）")
                        return


def check_all_ui_files():
    ui_dir = BASE_DIR / "ui"
    if not ui_dir.is_dir():
        return
    for pyfile in sorted(ui_dir.glob("*.py")):
        check_no_qtimer_in_thread(pyfile)
    if EXIT == 0:
        ok("ui/*.py: 未检测到 threading.Thread 内 QTimer.singleShot")


# ── 3) .bat 文件必须是纯 ASCII ──
def check_bat_ascii(filepath: Path):
    data = filepath.read_bytes()
    for i, byte in enumerate(data):
        if byte > 127 and byte not in (0x0D, 0x0A):  # skip CR, LF
            line = data[:i].count(b"\n") + 1
            fail(f"{filepath.name}:L{line} 包含非 ASCII 字节 0x{byte:02X}（批处理必须为纯 ASCII）")
            return
    ok(f"{filepath.name}: 纯 ASCII")


def check_all_bat_files():
    for bat in sorted(BASE_DIR.glob("*.bat")):
        check_bat_ascii(bat)


# ── main ──
if __name__ == "__main__":
    print("[ORCP] DLL 回归检查...")
    check_torch_before_pyqt()
    check_all_ui_files()
    check_all_bat_files()
    if EXIT == 0:
        print("[ORCP] 全部检查通过")
    sys.exit(EXIT)
