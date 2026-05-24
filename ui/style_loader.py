# -*- coding: utf-8 -*-
"""主题 / QSS 样式加载器"""

import os
import re
from pathlib import Path
from typing import Optional

path = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = Path(path) if not path.endswith("style_loader.py") else Path(os.getcwd())
STYLES_DIR = BASE_DIR / "styles"

DEFAULT_DARK_QSS = """
QMainWindow, QDialog { background-color: #0f1117; color: #c9d1d9; }
QLabel { color: #c9d1d9; background: transparent; }
QWidget { font-family: "Microsoft YaHei", "Segoe UI", "Noto Sans SC", sans-serif; font-size: 13px; }
QFrame { background: transparent; color: #c9d1d9; border: none; }
QAbstractItemView { background-color: #0d1117; color: #c9d1d9; outline: none; }
QAbstractScrollArea { background-color: #0d1117; }
QMenu { background-color: #1a1d21; color: #c9d1d9; border: 1px solid #353a3f; }
QPushButton {
    background-color: #1c2128; color: #c9d1d9; border: 1px solid #353a3f;
    padding: 6px 16px; border-radius: 6px; min-height: 28px; font-weight: 500;
}
QPushButton:hover { background-color: #292e36; border-color: #484f58; }
QPushButton:pressed { background-color: #0d1117; border-color: #2dd4bf; }
QPushButton:disabled { color: #484f58; background-color: #1a1d21; border-color: #1c2128; }
QPushButton:focus { border-color: #2dd4bf; }
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #0d1117; color: #c9d1d9; border: 1px solid #353a3f;
    padding: 6px 8px; border-radius: 6px; selection-background-color: #2dd4bf55;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: #2dd4bf; }
QTableWidget {
    background-color: #0d1117; color: #c9d1d9; gridline-color: #353a3f;
    border: 1px solid #353a3f; alternate-background-color: #10141c;
    border-radius: 0; font-size: 15px;
    selection-background-color: #2dd4bf22; selection-color: #c9d1d9;
}
QHeaderView::section {
    background-color: #1a1d21; color: #c9d1d9; border: none;
    border-bottom: 2px solid #2dd4bf; border-right: 1px solid #353a3f;
    padding: 8px 10px; font-weight: 600; font-size: 13px;
}
QListWidget {
    background-color: #0d1117; color: #c9d1d9; border: 1px solid #353a3f;
    border-radius: 6px; outline: none;
    selection-background-color: #2dd4bf22; selection-color: #c9d1d9;
}
QListWidget::item:selected { background-color: #2dd4bf22; color: #c9d1d9; }
QComboBox {
    background-color: #353a3f; color: #c9d1d9; border: 1px solid #353a3f;
    padding: 5px 12px; border-radius: 6px; min-height: 26px;
}
QComboBox:focus { border-color: #2dd4bf; }
QComboBox QAbstractItemView {
    background-color: #1a1d21; color: #c9d1d9;
    selection-background-color: #2dd4bf22; selection-color: #c9d1d9;
    border: 1px solid #353a3f; border-radius: 6px; padding: 2px;
}
QTabWidget::pane { border: 1px solid #353a3f; background-color: #0f1117; }
QTabBar::tab {
    background-color: #1a1d21; color: #8b949e; padding: 8px 20px;
    border: 1px solid #353a3f; border-bottom: none;
    border-radius: 6px 6px 0 0; margin-right: 2px; font-weight: 500;
}
QTabBar::tab:selected { background-color: #0f1117; color: #e6edf3; border-bottom: 2px solid #2dd4bf; }
QTabBar::tab:hover { color: #e6edf3; background-color: #2dd4bf22; }
QSplitter::handle { background-color: #353a3f; }
QSplitter::handle:hover { background-color: #2dd4bf; }
QScrollBar:vertical { background: transparent; width: 10px; border: none; margin: 2px 0; }
QScrollBar::handle:vertical { background: #353a3f; min-height: 36px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #484f58; }
QScrollBar::handle:vertical:pressed { background: #2dd4bf; }
QScrollBar:horizontal { background: transparent; height: 10px; border: none; margin: 0 2px; }
QScrollBar::handle:horizontal { background: #353a3f; min-width: 36px; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: #484f58; }
QScrollBar::handle:horizontal:pressed { background: #2dd4bf; }
QGroupBox {
    color: #c9d1d9; border: 1px solid #353a3f; border-radius: 8px;
    margin-top: 16px; padding-top: 14px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 8px; color: #5eead4; }
QCheckBox { color: #c9d1d9; spacing: 8px; }
QCheckBox::indicator:checked { border: 1.5px solid #2dd4bf; background-color: #2dd4bf; }
QSpinBox, QDoubleSpinBox {
    background-color: #353a3f; color: #c9d1d9; border: 1px solid #353a3f;
    padding: 5px 8px; border-radius: 6px;
}
QSpinBox:focus, QDoubleSpinBox:focus { border-color: #2dd4bf; }
QStatusBar { background-color: #1a1d21; color: #8b949e; border-top: 1px solid #353a3f; padding: 2px 8px; font-size: 11px; }
QProgressBar {
    background-color: #0d1117; border: 1px solid #353a3f; border-radius: 6px;
    text-align: center; color: #ffffff; font-size: 11px; font-weight: 600;
}
QProgressBar::chunk { background-color: #2dd4bf; border-radius: 5px; }
"""

DEFAULT_LIGHT_QSS = """
QMainWindow, QDialog { background-color: #f5f5f5; color: #333333; }
QLabel { color: #333333; }
QWidget { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 12px; }
QPushButton { 
    background-color: #ffffff; color: #333333; border: 1px solid #cccccc;
    padding: 4px 12px; border-radius: 3px; min-height: 24px;
}
QPushButton:hover { background-color: #e8e8e8; }
QPushButton:pressed { background-color: #d0d0d0; }
QPushButton:disabled { color: #999999; }
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #ffffff; color: #333333; border: 1px solid #cccccc;
    padding: 4px; border-radius: 3px;
}
QTableWidget {
    background-color: #ffffff; color: #333333; gridline-color: #cccccc;
    border: 1px solid #cccccc; selection-background-color: #007acc;
}
QHeaderView::section {
    background-color: #e8e8e8; color: #333333; border: 1px solid #cccccc;
    padding: 4px; font-weight: bold;
}
QListWidget {
    background-color: #ffffff; color: #333333; border: 1px solid #cccccc;
}
QListWidget::item:selected { background-color: #007acc; color: #ffffff; }
QComboBox {
    background-color: #ffffff; color: #333333; border: 1px solid #cccccc;
    padding: 4px 8px; border-radius: 3px;
}
QTabWidget::pane { border: 1px solid #cccccc; background-color: #f5f5f5; }
QTabBar::tab {
    background-color: #e8e8e8; color: #888888; padding: 6px 16px;
    border: 1px solid #cccccc; border-bottom: none;
}
QTabBar::tab:selected { background-color: #f5f5f5; color: #333333; }
QSplitter::handle { background-color: #cccccc; }
QStatusBar { background-color: #007acc; color: #ffffff; }
QGroupBox { color: #333333; border: 1px solid #cccccc; border-radius: 4px; }
QCheckBox { color: #333333; }
QSpinBox, QDoubleSpinBox {
    background-color: #ffffff; color: #333333; border: 1px solid #cccccc;
    padding: 4px; border-radius: 3px;
}
"""


def _load_qss(name: str) -> str:
    """从 .qss 文件读取样式表。"""
    try:
        p = STYLES_DIR / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def load_qss_theme(theme: str = "dark") -> str:
    """加载指定主题的 QSS 样式表（优先 .qss 文件，回退到内置默认）。"""
    if theme == "dark":
        qss = _load_qss("dark_style.qss")
        return qss if qss else DEFAULT_DARK_QSS
    else:
        qss = _load_qss("light_style.qss")
        return qss if qss else DEFAULT_LIGHT_QSS


def scale_stylesheet(sheet: str, scale: float) -> str:
    """按缩放比例调整样式表中所有 px 值。"""
    if scale == 1.0:
        return sheet

    def _scale_px(m: re.Match) -> str:
        val = int(m.group(1))
        return f"{int(val * scale)}px"

    return re.sub(r"(\d+)px", _scale_px, sheet)
