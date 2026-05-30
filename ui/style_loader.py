"""主题 / 样式加载器 —— 基于 qt-material 官方库 + 项目自定义覆盖。

qt-material 提供 Material Design 基础样式；
本模块在此基础上叠加项目特有控件的样式（CollapsibleGroup、底部栏等）。
"""

import os
import re
from pathlib import Path

from PyQt5.QtWidgets import QApplication

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STYLES_DIR = BASE_DIR / "styles"

# ─── qt-material 主题映射 ───
# 用户友好的主题名 → qt-material XML 文件名
THEME_MAP = {
    # 深色系
    "dark_teal":    "dark_teal.xml",
    "dark_blue":    "dark_blue.xml",
    "dark_cyan":    "dark_cyan.xml",
    "dark_purple":  "dark_purple.xml",
    "dark_pink":    "dark_pink.xml",
    "dark_red":     "dark_red.xml",
    "dark_amber":   "dark_amber.xml",
    "dark_yellow":  "dark_yellow.xml",
    "dark_lightgreen": "dark_lightgreen.xml",
    # 浅色系
    "light_teal":   "light_teal.xml",
    "light_blue":   "light_blue.xml",
    "light_cyan":   "light_cyan.xml",
    "light_purple": "light_purple.xml",
    "light_pink":   "light_pink.xml",
    "light_red":    "light_red.xml",
    "light_amber":  "light_amber.xml",
    "light_yellow": "light_yellow.xml",
    "light_lightgreen": "light_lightgreen.xml",
    "light_cyan_500": "light_cyan_500.xml",
}

# 默认主题
DEFAULT_DARK = "dark_teal"
DEFAULT_LIGHT = "light_teal"
DEFAULT_THEME = "default"

# 主题显示名（中文）
THEME_DISPLAY_NAMES = {
    "default":      "经典",
    "dark_teal":    "深色 · 青绿",
    "dark_blue":    "深色 · 蓝",
    "dark_cyan":    "深色 · 天蓝",
    "dark_purple":  "深色 · 紫",
    "dark_pink":    "深色 · 粉",
    "dark_red":     "深色 · 红",
    "dark_amber":   "深色 · 琥珀",
    "dark_yellow":  "深色 · 黄",
    "dark_lightgreen": "深色 · 浅绿",
    "light_teal":   "浅色 · 青绿",
    "light_blue":   "浅色 · 蓝",
    "light_cyan":   "浅色 · 天蓝",
    "light_purple": "浅色 · 紫",
    "light_pink":   "浅色 · 粉",
    "light_red":    "浅色 · 红",
    "light_amber":  "浅色 · 琥珀",
    "light_yellow": "浅色 · 黄",
    "light_lightgreen": "浅色 · 浅绿",
    "light_cyan_500": "浅色 · 天蓝500",
}

# ─── 主题预览色（用于卡片选择器）───
# 每个主题 3 个代表色：[主色, 次色, 背景色]
THEME_COLORS = {
    "default":         ["#555555", "#888888", "#f0f0f0"],
    "dark_teal":       ["#26a69a", "#00796b", "#1e1e1e"],
    "dark_blue":       ["#42a5f5", "#1565c0", "#1e1e1e"],
    "dark_cyan":       ["#26c6da", "#00838f", "#1e1e1e"],
    "dark_purple":     ["#ab47bc", "#7b1fa2", "#1e1e1e"],
    "dark_pink":       ["#ec407a", "#c2185b", "#1e1e1e"],
    "dark_red":        ["#ef5350", "#c62828", "#1e1e1e"],
    "dark_amber":      ["#ffca28", "#ff8f00", "#1e1e1e"],
    "dark_yellow":     ["#ffee58", "#f9a825", "#1e1e1e"],
    "dark_lightgreen": ["#66bb6a", "#2e7d32", "#1e1e1e"],
    "light_teal":      ["#00897b", "#004d40", "#f3f3f3"],
    "light_blue":      ["#1e88e5", "#0d47a1", "#f3f3f3"],
    "light_cyan":      ["#00acc1", "#006064", "#f3f3f3"],
    "light_purple":    ["#8e24aa", "#4a148c", "#f3f3f3"],
    "light_pink":      ["#d81b60", "#880e4f", "#f3f3f3"],
    "light_red":       ["#e53935", "#b71c1c", "#f3f3f3"],
    "light_amber":     ["#ffa000", "#e65100", "#f3f3f3"],
    "light_yellow":    ["#fdd835", "#f57f17", "#f3f3f3"],
    "light_lightgreen":["#43a047", "#1b5e20", "#f3f3f3"],
    "light_cyan_500":  ["#00bcd4", "#006064", "#f3f3f3"],
}

# ─── 项目自定义 CSS（叠加在 qt-material 之上）───
# 这些样式用于 qt-material 不覆盖的项目特有控件

_CUSTOM_CSS_DARK = """
/* ═══ 项目自定义样式 (深色) ═══ */

/* ── 可折叠分组 ── */
QWidget#collapsibleGroup {
    background: transparent;
    border: 1px solid #363636;
    border-radius: 10px;
    margin: 3px 0;
}
QWidget#collapsibleHeader {
    background-color: #282828;
    border: none;
    border-radius: 10px 10px 0 0;
    min-height: 34px;
}
QWidget#collapsibleHeader:hover {
    background-color: #303030;
}
QWidget#collapsibleContent {
    background-color: transparent;
    border: none;
}
QLabel#collapsibleTitle {
    color: #e0e0e0;
    font-weight: 600;
    font-size: 13px;
}
QToolButton#collapsibleToggle {
    background: transparent;
    border: none;
}

/* ── 右侧面板 ── */
QFrame#rightPanel {
    background-color: #1c1c1c;
    border-left: 1px solid #333333;
}

/* ── 模板栏 ── */
QFrame#tplBar {
    background-color: #282828;
    border-radius: 8px;
    padding: 4px;
}

/* ── 底部栏 ── */
QFrame#bottomBar {
    background-color: #222222;
    border-top: 1px solid #363636;
}

/* ── 底部栏分隔线 ── */
QFrame#barSeparator {
    background-color: #404040;
    width: 1px;
    margin: 4px 6px;
}

/* ── 主操作按钮 (开始处理) ── */
QPushButton#btnStart {
    background-color: #0078d4;
    color: #ffffff;
    font-weight: 600;
    border: none;
    border-radius: 8px;
    padding: 8px 28px;
}
QPushButton#btnStart:hover { background-color: #1a8ae8; }
QPushButton#btnStart:pressed { background-color: #006cbd; }
QPushButton#btnStart:disabled { background-color: #1b3a4d; color: #4a6a7a; }

/* ── 暂停/停止按钮 ── */
QPushButton#btnPause, QPushButton#btnStop {
    background-color: #383838;
    color: #e0e0e0;
    border: 1px solid #505050;
    border-radius: 8px;
    padding: 8px 18px;
}
QPushButton#btnPause:hover, QPushButton#btnStop:hover {
    background-color: #454545;
    border: 1px solid #666666;
}
QPushButton#btnPause:pressed, QPushButton#btnStop:pressed {
    background-color: #2a2a2a;
}
QPushButton#btnPause:disabled, QPushButton#btnStop:disabled {
    background-color: #252525;
    color: #555555;
    border: 1px solid #333333;
}

/* ── 纠错/润色按钮 ── */
QPushButton#btnCorrection, QPushButton#btnCorrectionAll,
QPushButton#btnPolish, QPushButton#btnPolishAll {
    border-radius: 8px;
    padding: 8px 16px;
}

/* ── 删除区域按钮 ── */
QPushButton#btnRemoveRegion {
    color: #cf6679;
}

/* ── 结果标题 ── */
QLabel#resultTitle {
    color: #e0e0e0;
    font-weight: 600;
    font-size: 14px;
}
QLabel#countLabel {
    color: #80cbc4;
    font-size: 12px;
    font-weight: 500;
}
QLabel#hintLabel {
    color: #808080;
    font-size: 12px;
}

/* ── 区域标题 ── */
QLabel#regionTitle {
    color: #e0e0e0;
    font-weight: 600;
    font-size: 13px;
}

/* ── 搜索栏 ── */
QFrame#searchBar {
    background-color: #282828;
    border: none;
    border-radius: 8px;
    padding: 6px;
}

/* ── 选中行栏 ── */
QFrame#selectionBar {
    background-color: #1c1c1c;
    border: none;
    border-radius: 6px;
}

/* ── 设置对话框 ── */
QDialog#settingsDialog {
    background-color: #1e1e1e;
}

/* ── 表格增强 ── */
QTableWidget {
    border-radius: 8px;
    font-size: 14px;
}
QTableWidget::item {
    padding: 3px 6px;
    border-bottom: 1px solid #2a2a2a;
}
QHeaderView::section {
    font-size: 12px;
    font-weight: 600;
    padding: 8px 10px;
    border-bottom: 2px solid #26a69a;
    border-right: 1px solid #333333;
}

/* ── 列表增强 ── */
QListWidget::item {
    padding: 6px 10px;
    border-radius: 6px;
    margin: 1px 2px;
}
QListWidget::item:hover {
    background-color: rgba(255, 255, 255, 0.04);
}

/* ── 滚动条 ── */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    border: none;
    margin: 4px 0;
}
QScrollBar::handle:vertical {
    background: #505050;
    min-height: 40px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #666666; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0; border: none;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    border: none;
    margin: 0 4px;
}
QScrollBar::handle:horizontal {
    background: #505050;
    min-width: 40px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover { background: #666666; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0; border: none;
}

/* ── 分割器 ── */
QSplitter::handle {
    background-color: #333333;
    border-radius: 2px;
}
QSplitter::handle:hover {
    background-color: #26a69a;
}

/* ── 状态栏 ── */
QStatusBar {
    background-color: #1a1a1a;
    color: #808080;
    border-top: 1px solid #333333;
    padding: 2px 10px;
    font-size: 12px;
    min-height: 26px;
}

/* ── 进度条 ── */
QProgressBar#progressAnimated {
    border: none;
    border-radius: 4px;
}

/* ── 工具栏增强 ── */
QToolBar {
    spacing: 6px;
    padding: 6px 10px;
}
QToolBar::separator {
    width: 1px;
    background: #404040;
    margin: 4px 6px;
}

/* ── Tab 指示条 ── */
QTabBar::tab {
    padding: 8px 16px;
    background: transparent;
    color: #808080;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 13px;
}
QTabBar::tab:selected {
    color: #e0e0e0;
    border-bottom: 2px solid #26a69a;
}
QTabBar::tab:hover {
    color: #b0b0b0;
    background: rgba(255, 255, 255, 0.03);
}

/* ── 输入控件聚焦态 ── */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QTimeEdit:focus {
    border: 1px solid #26a69a;
    border-radius: 4px;
}
QTextEdit:focus {
    border: 1px solid #26a69a;
    border-radius: 4px;
}

/* ── 表格行悬浮 ── */
QTableWidget::item:hover {
    background-color: rgba(38, 166, 154, 0.06);
}
QTableWidget::item:selected {
    background-color: rgba(38, 166, 154, 0.15);
}

/* ── 工具提示 ── */
QToolTip {
    background-color: #333333;
    color: #e0e0e0;
    border: 1px solid #505050;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}

/* ── 纠错/润色按钮悬浮 ── */
QPushButton#btnCorrection:hover, QPushButton#btnCorrectionAll:hover {
    border: 1px solid #26a69a;
}
QPushButton#btnPolish:hover, QPushButton#btnPolishAll:hover {
    border: 1px solid #7c4dff;
}

"""

_CUSTOM_CSS_LIGHT = """
/* ═══ 项目自定义样式 (浅色) ═══ */

QWidget#collapsibleGroup {
    background: transparent;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    margin: 3px 0;
}
QWidget#collapsibleHeader {
    background-color: #f5f5f5;
    border: none;
    border-radius: 10px 10px 0 0;
    min-height: 34px;
}
QWidget#collapsibleHeader:hover {
    background-color: #eeeeee;
}
QWidget#collapsibleContent {
    background-color: transparent;
    border: none;
}
QLabel#collapsibleTitle {
    color: #1a1a1a;
    font-weight: 600;
    font-size: 13px;
}
QToolButton#collapsibleToggle {
    background: transparent;
    border: none;
}

QFrame#rightPanel {
    background-color: #fafafa;
    border-left: 1px solid #e0e0e0;
}
QFrame#tplBar {
    background-color: #f0f0f0;
    border-radius: 8px;
    padding: 4px;
}
QFrame#bottomBar {
    background-color: #ffffff;
    border-top: 1px solid #e0e0e0;
}
QFrame#barSeparator {
    background-color: #e0e0e0;
    width: 1px;
    margin: 4px 6px;
}

QPushButton#btnStart {
    background-color: #0078d4;
    color: #ffffff;
    font-weight: 600;
    border: none;
    border-radius: 8px;
    padding: 8px 28px;
}
QPushButton#btnStart:hover { background-color: #1a8ae8; }
QPushButton#btnStart:pressed { background-color: #006cbd; }
QPushButton#btnStart:disabled { background-color: #cce4f7; color: #6a9ec2; }

QPushButton#btnPause, QPushButton#btnStop {
    background-color: #e8e8e8;
    color: #333333;
    border: 1px solid #cccccc;
    border-radius: 8px;
    padding: 8px 18px;
}
QPushButton#btnPause:hover, QPushButton#btnStop:hover {
    background-color: #d8d8d8;
    border: 1px solid #aaaaaa;
}
QPushButton#btnPause:pressed, QPushButton#btnStop:pressed {
    background-color: #c0c0c0;
}
QPushButton#btnPause:disabled, QPushButton#btnStop:disabled {
    background-color: #f0f0f0;
    color: #aaaaaa;
    border: 1px solid #e0e0e0;
}
QPushButton#btnCorrection, QPushButton#btnCorrectionAll,
QPushButton#btnPolish, QPushButton#btnPolishAll {
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton#btnRemoveRegion {
    color: #c62828;
}

QLabel#resultTitle {
    color: #1a1a1a;
    font-weight: 600;
    font-size: 14px;
}
QLabel#countLabel {
    color: #0078d4;
    font-size: 12px;
    font-weight: 500;
}
QLabel#hintLabel {
    color: #808080;
    font-size: 12px;
}
QLabel#regionTitle {
    color: #1a1a1a;
    font-weight: 600;
    font-size: 13px;
}

QFrame#searchBar {
    background-color: #f5f5f5;
    border: none;
    border-radius: 8px;
    padding: 6px;
}
QFrame#selectionBar {
    background-color: #fafafa;
    border: none;
    border-radius: 6px;
}
QDialog#settingsDialog {
    background-color: #f3f3f3;
}

QTableWidget {
    border-radius: 8px;
    font-size: 14px;
}
QTableWidget::item {
    padding: 3px 6px;
    border-bottom: 1px solid #f0f0f0;
}
QHeaderView::section {
    font-size: 12px;
    font-weight: 600;
    padding: 8px 10px;
    border-bottom: 2px solid #00897b;
    border-right: 1px solid #e0e0e0;
}
QListWidget::item {
    padding: 6px 10px;
    border-radius: 6px;
    margin: 1px 2px;
}
QListWidget::item:hover {
    background-color: rgba(0, 0, 0, 0.04);
}

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    border: none;
    margin: 4px 0;
}
QScrollBar::handle:vertical {
    background: #c0c0c0;
    min-height: 40px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #a0a0a0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0; border: none;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    border: none;
    margin: 0 4px;
}
QScrollBar::handle:horizontal {
    background: #c0c0c0;
    min-width: 40px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover { background: #a0a0a0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0; border: none;
}

QSplitter::handle {
    background-color: #e0e0e0;
}
QSplitter::handle:hover {
    background-color: #00897b;
}

QStatusBar {
    background-color: #ffffff;
    color: #808080;
    border-top: 1px solid #e0e0e0;
    padding: 2px 10px;
    font-size: 12px;
    min-height: 26px;
}

QProgressBar#progressAnimated {
    border: none;
    border-radius: 4px;
}

QToolBar {
    spacing: 6px;
    padding: 6px 10px;
}
QToolBar::separator {
    width: 1px;
    background: #e0e0e0;
    margin: 4px 6px;
}

/* ── Tab 指示条 ── */
QTabBar::tab {
    padding: 8px 16px;
    background: transparent;
    color: #808080;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 13px;
}
QTabBar::tab:selected {
    color: #1a1a1a;
    border-bottom: 2px solid #00897b;
}
QTabBar::tab:hover {
    color: #505050;
    background: rgba(0, 0, 0, 0.03);
}

/* ── 输入控件聚焦态 ── */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QTimeEdit:focus {
    border: 1px solid #00897b;
    border-radius: 4px;
}
QTextEdit:focus {
    border: 1px solid #00897b;
    border-radius: 4px;
}

/* ── 表格行悬浮 ── */
QTableWidget::item:hover {
    background-color: rgba(0, 137, 123, 0.06);
}
QTableWidget::item:selected {
    background-color: rgba(0, 137, 123, 0.15);
}

/* ── 工具提示 ── */
QToolTip {
    background-color: #333333;
    color: #e0e0e0;
    border: 1px solid #505050;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}

/* ── 纠错/润色按钮悬浮 ── */
QPushButton#btnCorrection:hover, QPushButton#btnCorrectionAll:hover {
    border: 1px solid #00897b;
}
QPushButton#btnPolish:hover, QPushButton#btnPolishAll:hover {
    border: 1px solid #7c4dff;
}

"""

_CUSTOM_CSS_DEFAULT = """
/* ═══ 项目自定义样式 (经典) ═══ */

/* ── 可折叠分组 ── */
QWidget#collapsibleGroup {
    background: transparent;
    border: 1px solid #d0d0d0;
    border-radius: 10px;
    margin: 3px 0;
}
QWidget#collapsibleHeader {
    background-color: #f5f5f5;
    border: none;
    border-radius: 10px 10px 0 0;
    min-height: 34px;
}
QWidget#collapsibleHeader:hover {
    background-color: #eaeaea;
}
QWidget#collapsibleContent {
    background-color: transparent;
    border: none;
}
QLabel#collapsibleTitle {
    color: #333333;
    font-weight: 600;
    font-size: 13px;
}
QToolButton#collapsibleToggle {
    background: transparent;
    border: none;
}

/* ── 右侧面板 ── */
QFrame#rightPanel {
    background-color: #f8f8f8;
    border-left: 1px solid #d0d0d0;
}

/* ── 模板栏 ── */
QFrame#tplBar {
    background-color: #f0f0f0;
    border-radius: 8px;
    padding: 4px;
}

/* ── 底部栏 ── */
QFrame#bottomBar {
    background-color: #ffffff;
    border-top: 1px solid #d0d0d0;
}

/* ── 底部栏分隔线 ── */
QFrame#barSeparator {
    background-color: #d0d0d0;
    width: 1px;
    margin: 4px 6px;
}

/* ── 主操作按钮 (开始处理) ── */
QPushButton#btnStart {
    background-color: #0078d4;
    color: #ffffff;
    font-weight: 600;
    border: none;
    border-radius: 8px;
    padding: 8px 28px;
}
QPushButton#btnStart:hover { background-color: #1a8ae8; }
QPushButton#btnStart:pressed { background-color: #006cbd; }
QPushButton#btnStart:disabled { background-color: #cce4f7; color: #6a9ec2; }

/* ── 暂停/停止按钮 ── */
QPushButton#btnPause, QPushButton#btnStop {
    background-color: #e8e8e8;
    color: #333333;
    border: 1px solid #cccccc;
    border-radius: 8px;
    padding: 8px 18px;
}
QPushButton#btnPause:hover, QPushButton#btnStop:hover {
    background-color: #d8d8d8;
    border: 1px solid #aaaaaa;
}
QPushButton#btnPause:pressed, QPushButton#btnStop:pressed {
    background-color: #c0c0c0;
}
QPushButton#btnPause:disabled, QPushButton#btnStop:disabled {
    background-color: #f0f0f0;
    color: #aaaaaa;
    border: 1px solid #e0e0e0;
}

/* ── 纠错/润色按钮 ── */
QPushButton#btnCorrection, QPushButton#btnCorrectionAll,
QPushButton#btnPolish, QPushButton#btnPolishAll {
    border-radius: 8px;
    padding: 8px 16px;
}

/* ── 删除区域按钮 ── */
QPushButton#btnRemoveRegion {
    color: #c62828;
}

/* ── 结果标题 ── */
QLabel#resultTitle {
    color: #333333;
    font-weight: 600;
    font-size: 14px;
}
QLabel#countLabel {
    color: #0078d4;
    font-size: 12px;
    font-weight: 500;
}
QLabel#hintLabel {
    color: #808080;
    font-size: 12px;
}
QLabel#regionTitle {
    color: #333333;
    font-weight: 600;
    font-size: 13px;
}

/* ── 搜索栏 ── */
QFrame#searchBar {
    background-color: #f5f5f5;
    border: none;
    border-radius: 8px;
    padding: 6px;
}

/* ── 选中行栏 ── */
QFrame#selectionBar {
    background-color: #f8f8f8;
    border: none;
    border-radius: 6px;
}

/* ── 设置对话框 ── */
QDialog#settingsDialog {
    background-color: #f3f3f3;
}

/* ── 表格增强 ── */
QTableWidget {
    border-radius: 8px;
    font-size: 14px;
}
QTableWidget::item {
    padding: 3px 6px;
    border-bottom: 1px solid #eeeeee;
}
QHeaderView::section {
    font-size: 12px;
    font-weight: 600;
    padding: 8px 10px;
    border-bottom: 2px solid #0078d4;
    border-right: 1px solid #d0d0d0;
}
QListWidget::item {
    padding: 6px 10px;
    border-radius: 6px;
    margin: 1px 2px;
}
QListWidget::item:hover {
    background-color: rgba(0, 0, 0, 0.04);
}

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    border: none;
    margin: 4px 0;
}
QScrollBar::handle:vertical {
    background: #c0c0c0;
    min-height: 40px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #a0a0a0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0; border: none;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    border: none;
    margin: 0 4px;
}
QScrollBar::handle:horizontal {
    background: #c0c0c0;
    min-width: 40px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover { background: #a0a0a0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0; border: none;
}

QSplitter::handle {
    background-color: #d0d0d0;
}
QSplitter::handle:hover {
    background-color: #0078d4;
}

QStatusBar {
    background-color: #ffffff;
    color: #808080;
    border-top: 1px solid #d0d0d0;
    padding: 2px 10px;
    font-size: 12px;
    min-height: 26px;
}

QProgressBar#progressAnimated {
    border: none;
    border-radius: 4px;
}

QToolBar {
    spacing: 6px;
    padding: 6px 10px;
}
QToolBar::separator {
    width: 1px;
    background: #d0d0d0;
    margin: 4px 6px;
}

/* ── Tab 指示条 ── */
QTabBar::tab {
    padding: 8px 16px;
    background: transparent;
    color: #808080;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 13px;
}
QTabBar::tab:selected {
    color: #333333;
    border-bottom: 2px solid #0078d4;
}
QTabBar::tab:hover {
    color: #505050;
    background: rgba(0, 0, 0, 0.03);
}

/* ── 输入控件聚焦态 ── */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QTimeEdit:focus {
    border: 1px solid #0078d4;
    border-radius: 4px;
}
QTextEdit:focus {
    border: 1px solid #0078d4;
    border-radius: 4px;
}

/* ── 表格行悬浮 ── */
QTableWidget::item:hover {
    background-color: rgba(0, 120, 212, 0.06);
}
QTableWidget::item:selected {
    background-color: rgba(0, 120, 212, 0.15);
}

/* ── 工具提示 ── */
QToolTip {
    background-color: #333333;
    color: #e0e0e0;
    border: 1px solid #505050;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}

/* ── 纠错/润色按钮悬浮 ── */
QPushButton#btnCorrection:hover, QPushButton#btnCorrectionAll:hover {
    border: 1px solid #0078d4;
}
QPushButton#btnPolish:hover, QPushButton#btnPolishAll:hover {
    border: 1px solid #7c4dff;
}

"""


def is_dark_theme(theme_name: str) -> bool:
    """判断主题名是否为深色主题。"""
    if theme_name == "default":
        return False
    return theme_name.startswith("dark")


def get_custom_css(theme_name: str) -> str:
    """根据主题名返回对应的自定义 CSS。"""
    if theme_name == "default":
        return _CUSTOM_CSS_DEFAULT
    if is_dark_theme(theme_name):
        return _CUSTOM_CSS_DARK
    return _CUSTOM_CSS_LIGHT


def _migrate_theme_name(theme_name: str) -> str:
    """兼容旧版 "dark" / "light" 主题名，映射到 qt-material 主题。"""
    if theme_name in THEME_MAP or theme_name == "default":
        return theme_name
    if theme_name == "dark":
        return DEFAULT_DARK
    if theme_name == "light":
        return DEFAULT_LIGHT
    return DEFAULT_DARK


def apply_theme(app: QApplication, theme_name: str = "dark_teal",
                font_family: str = "Microsoft YaHei UI",
                density_scale: str = "0"):
    """应用 qt-material 主题 + 项目自定义覆盖。

    Args:
        app: QApplication 实例
        theme_name: 主题名（如 "dark_teal", "light_blue", "default"）
        font_family: 字体族
        density_scale: 密度缩放 ("-2" 最紧凑, "0" 默认, "2" 最宽松)
    """
    theme_name = _migrate_theme_name(theme_name)

    # 设置 Fusion 风格
    app.setStyle("Fusion")

    # 经典主题：仅使用 Fusion + 自定义 CSS，不加载 qt-material
    if theme_name == "default":
        custom_css = get_custom_css(theme_name)
        app.setStyleSheet(custom_css)
        return

    xml_name = THEME_MAP.get(theme_name, "dark_teal.xml")

    extra = {
        'font_family': font_family,
        'density_scale': density_scale,
    }

    # 使用 build_stylesheet 构建基础样式，避免 qt-material 内部 open(css_file) 的 GBK 编码问题
    from qt_material import build_stylesheet
    stylesheet = build_stylesheet(xml_name, invert_secondary=False, extra=extra, parent="theme")
    if stylesheet is None:
        return

    # qt_material 只对 PySide6/PyQt6 注册 icon: 搜索路径；
    # PyQt5 下 GUI=False，需要手动注册，否则 Qt 无法解析样式表中的 icon:/ 引用
    from PyQt5.QtCore import QDir
    QDir.addSearchPath("icon", str(Path.home() / ".qt_material" / "theme"))

    # 手动追加自定义 CSS
    custom_css = get_custom_css(theme_name)
    if custom_css.strip():
        stylesheet += "\n" + custom_css

    app.setStyleSheet(stylesheet)


def load_qss_theme(theme: str = "dark_teal") -> str:
    """兼容旧接口：返回自定义 CSS（供不使用 qt-material 的场景回退）。"""
    if _load_qss_file():
        return _load_qss_file()
    return get_custom_css(theme)


def scale_stylesheet(sheet: str, scale: float) -> str:
    """按缩放比例调整样式表中所有 px 值。"""
    if scale == 1.0:
        return sheet

    def _scale_px(m: re.Match) -> str:
        val = int(m.group(1))
        return f"{int(val * scale)}px"

    return re.sub(r"(\d+)px", _scale_px, sheet)


def _load_qss_file() -> str:
    """从 styles/ 目录加载自定义 .qss 文件（如果存在）。"""
    try:
        for name in ("custom_style.qss", "dark_style.qss"):
            p = STYLES_DIR / name
            if p.exists():
                return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""
