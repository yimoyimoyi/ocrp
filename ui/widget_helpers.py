# -*- coding: utf-8 -*-
"""UI 控件工具函数 —— 安全读写各类 Qt 控件的值。"""

from PyQt5.QtWidgets import QCheckBox, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit


def safe_read_widget(widget, default=None):
    """安全读取控件值，根据控件类型分发。"""
    try:
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText() or (default or "")
        if isinstance(widget, QLineEdit):
            return widget.text().strip() or (default or "")
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        return default
    except RuntimeError:
        return default


def safe_set_widget(widget, value) -> bool:
    """安全设置控件值，根据控件类型分发。返回 True 表示成功。"""
    try:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
            return True
        if isinstance(widget, QComboBox):
            widget.setCurrentText(str(value) if value else "")
            return True
        if isinstance(widget, QLineEdit):
            widget.setText(str(value) if value is not None else "")
            return True
        if isinstance(widget, QSpinBox):
            widget.setValue(int(value) if value is not None else 0)
            return True
        if isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value) if value is not None else 0.0)
            return True
        if isinstance(widget, QTextEdit):
            widget.setPlainText(str(value) if value is not None else "")
            return True
        return False
    except RuntimeError:
        return False
