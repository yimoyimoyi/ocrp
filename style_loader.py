# -*- coding: utf-8 -*-
"""主题 / QSS 样式加载器 —— 支持 dark / light 主题，优先读取 styles/*.qss 文件。"""

import os
import re
from pathlib import Path
from typing import Optional

path = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = Path(path) if not path.endswith("style_loader.py") else Path(os.getcwd())
STYLES_DIR = BASE_DIR / "styles"


def _load_qss(name: str) -> str:
    """从 .qss 文件读取样式表。"""
    try:
        p = STYLES_DIR / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


# ── 精简版内置 fallback（仅在 styles/*.qss 不存在时使用） ──
_DEFAULT_DARK_FALLBACK = """
QMainWindow, QDialog { background-color: #0f1117; color: #c9d1d9; }
QLabel { color: #c9d1d9; }
QWidget { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 12px; }
QPushButton { background-color: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 6px 16px; border-radius: 6px; min-height: 28px; }
QPushButton:hover { background-color: #30363d; }
QPushButton:pressed { background-color: #0d1117; }
QPushButton:disabled { color: #484f58; background-color: #0d1117; }
QPushButton#btnStart { background-color: #238636; color: #ffffff; font-weight: bold; min-height: 32px; border-radius: 6px; }
QPushButton#btnStop { background-color: #da3633; color: #ffffff; font-weight: bold; min-height: 32px; border-radius: 6px; }
QPushButton#btnPause { background-color: #9e6a03; color: #ffffff; font-weight: bold; min-height: 32px; border-radius: 6px; }
QLineEdit, QTextEdit, QPlainTextEdit { background-color: #0d1117; color: #c9d1d9; border: 1px solid #30363d; padding: 6px 8px; border-radius: 6px; }
QLineEdit:focus, QTextEdit:focus { border-color: #1f6feb; }
QTableWidget { background-color: #0d1117; color: #c9d1d9; gridline-color: #21262d; border: 1px solid #21262d; selection-background-color: #1f6feb33; alternate-background-color: #10141c; border-radius: 6px; }
QTableWidget::item { padding: 5px 8px; }
QHeaderView::section { background-color: #161b22; color: #8b949e; border: none; border-bottom: 2px solid #21262d; padding: 7px 10px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #21262d; background-color: #0f1117; border-radius: 0 0 8px 8px; }
QTabBar::tab { background-color: #161b22; color: #8b949e; padding: 8px 20px; border: 1px solid #21262d; border-bottom: none; border-radius: 6px 6px 0 0; margin-right: 2px; }
QTabBar::tab:selected { background-color: #0f1117; color: #e6edf3; border-bottom: 2px solid #1f6feb; }
QTabBar::tab:hover { color: #e6edf3; background-color: #1f6feb22; }
QStatusBar { background-color: #161b22; color: #8b949e; border-top: 1px solid #21262d; }
QProgressBar { background-color: #0d1117; border: 1px solid #21262d; border-radius: 6px; text-align: center; color: #ffffff; font-size: 11px; }
QProgressBar::chunk { background-color: #1f6feb; border-radius: 5px; }
QGroupBox { color: #c9d1d9; border: 1px solid #21262d; border-radius: 8px; margin-top: 16px; padding-top: 14px; font-weight: 600; }
QGroupBox::title { color: #58a6ff; }
QToolTip { background-color: #1c2128; color: #e6edf3; border: 1px solid #30363d; padding: 6px 10px; border-radius: 6px; }
"""

_DEFAULT_LIGHT_FALLBACK = """
QMainWindow, QDialog { background-color: #f6f8fa; color: #1f2328; }
QLabel { color: #1f2328; }
QWidget { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 12px; }
QPushButton { background-color: #f6f8fa; color: #1f2328; border: 1px solid #d1d9e0; padding: 6px 16px; border-radius: 6px; min-height: 28px; }
QPushButton:hover { background-color: #eaeef2; }
QPushButton:disabled { color: #8c959f; }
QPushButton#btnStart { background-color: #1f883d; color: #ffffff; font-weight: bold; min-height: 32px; border-radius: 6px; }
QPushButton#btnStop { background-color: #d1242f; color: #ffffff; font-weight: bold; min-height: 32px; border-radius: 6px; }
QPushButton#btnPause { background-color: #bf8700; color: #ffffff; font-weight: bold; min-height: 32px; border-radius: 6px; }
QLineEdit, QTextEdit, QPlainTextEdit { background-color: #ffffff; color: #1f2328; border: 1px solid #d1d9e0; padding: 6px 8px; border-radius: 6px; }
QLineEdit:focus, QTextEdit:focus { border-color: #0969da; }
QTableWidget { background-color: #ffffff; color: #1f2328; gridline-color: #e8ecf0; border: 1px solid #d1d9e0; selection-background-color: #0969da22; alternate-background-color: #f6f8fa; border-radius: 6px; }
QHeaderView::section { background-color: #f6f8fa; color: #656d76; border: none; border-bottom: 2px solid #d1d9e0; padding: 7px 10px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #d1d9e0; background-color: #f6f8fa; border-radius: 0 0 8px 8px; }
QTabBar::tab { background-color: #f6f8fa; color: #656d76; padding: 8px 20px; border: 1px solid #d1d9e0; border-bottom: none; border-radius: 6px 6px 0 0; margin-right: 2px; }
QTabBar::tab:selected { background-color: #ffffff; color: #1f2328; border-bottom: 2px solid #0969da; }
QTabBar::tab:hover { color: #1f2328; background-color: #0969da11; }
QStatusBar { background-color: #ffffff; color: #656d76; border-top: 1px solid #d1d9e0; }
QGroupBox { color: #1f2328; border: 1px solid #d1d9e0; border-radius: 8px; margin-top: 16px; padding-top: 14px; font-weight: 600; }
QGroupBox::title { color: #0969da; }
QToolTip { background-color: #1f2328; color: #ffffff; border: 1px solid #30363d; padding: 6px 10px; border-radius: 6px; }
"""


def load_qss_theme(theme: str = "dark") -> str:
    """加载指定主题的 QSS 样式表（优先 .qss 文件，回退到内置默认）。"""
    if theme == "dark":
        qss = _load_qss("dark_style.qss")
        return qss if qss else _DEFAULT_DARK_FALLBACK
    else:
        qss = _load_qss("light_style.qss")
        return qss if qss else _DEFAULT_LIGHT_FALLBACK


def scale_stylesheet(sheet: str, scale: float) -> str:
    """按缩放比例调整样式表中所有 px 值。"""
    if scale == 1.0:
        return sheet

    def _scale_px(m: re.Match) -> str:
        val = int(m.group(1))
        return f"{int(val * scale)}px"

    return re.sub(r"(\d+)px", _scale_px, sheet)
