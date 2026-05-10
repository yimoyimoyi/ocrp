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
QMainWindow, QDialog { background-color: #1e1e1e; color: #d4d4d4; }
QLabel { color: #d4d4d4; }
QPushButton { 
    background-color: #2d2d2d; color: #d4d4d4; border: 1px solid #3e3e3e;
    padding: 4px 12px; border-radius: 3px; min-height: 24px;
}
QPushButton:hover { background-color: #3c3c3c; }
QPushButton:pressed { background-color: #505050; }
QPushButton:disabled { color: #666666; }
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #252525; color: #d4d4d4; border: 1px solid #3e3e3e;
    padding: 4px; border-radius: 3px;
}
QTableWidget {
    background-color: #252525; color: #d4d4d4; gridline-color: #3e3e3e;
    border: 1px solid #3e3e3e; selection-background-color: #264f78;
}
QTableWidget::item { padding: 4px; }
QHeaderView::section {
    background-color: #2d2d2d; color: #d4d4d4; border: 1px solid #3e3e3e;
    padding: 4px; font-weight: bold;
}
QListWidget {
    background-color: #252525; color: #d4d4d4; border: 1px solid #3e3e3e;
}
QListWidget::item { padding: 4px; }
QListWidget::item:selected { background-color: #264f78; }
QComboBox {
    background-color: #2d2d2d; color: #d4d4d4; border: 1px solid #3e3e3e;
    padding: 4px 8px; border-radius: 3px;
}
QComboBox QAbstractItemView {
    background-color: #2d2d2d; color: #d4d4d4; selection-background-color: #264f78;
}
QTabWidget::pane { border: 1px solid #3e3e3e; background-color: #1e1e1e; }
QTabBar::tab {
    background-color: #2d2d2d; color: #888888; padding: 6px 16px;
    border: 1px solid #3e3e3e; border-bottom: none;
}
QTabBar::tab:selected { background-color: #1e1e1e; color: #d4d4d4; }
QTabBar::tab:hover { color: #ffffff; }
QSplitter::handle { background-color: #3e3e3e; }
QScrollBar:vertical {
    background: #1e1e1e; width: 10px; border: none;
}
QScrollBar::handle:vertical { background: #555555; min-height: 30px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal {
    background: #1e1e1e; height: 10px; border: none;
}
QScrollBar::handle:horizontal { background: #555555; min-width: 30px; border-radius: 5px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QGroupBox {
    color: #d4d4d4; border: 1px solid #3e3e3e; border-radius: 4px;
    margin-top: 12px; padding-top: 10px;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; }
QCheckBox { color: #d4d4d4; }
QSpinBox, QDoubleSpinBox {
    background-color: #2d2d2d; color: #d4d4d4; border: 1px solid #3e3e3e;
    padding: 4px; border-radius: 3px;
}
QStatusBar { background-color: #007acc; color: #ffffff; }
QProgressBar {
    background-color: #252525; border: 1px solid #3e3e3e; border-radius: 3px;
    text-align: center; color: #d4d4d4;
}
QProgressBar::chunk { background-color: #007acc; border-radius: 2px; }
"""

DEFAULT_LIGHT_QSS = """
QMainWindow, QDialog { background-color: #f5f5f5; color: #333333; }
QLabel { color: #333333; }
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
