# -*- coding: utf-8 -*-
"""显示设置对话框 —— 主题 / 字体大小 / UI 缩放"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFormLayout, QComboBox, QSpinBox, QDoubleSpinBox,
    QDialogButtonBox, QWidget,
)
from PyQt5.QtCore import Qt


class DisplayDialog(QDialog):
    """显示设置对话框。"""

    def __init__(self, theme: str, font_size: int, ui_scale: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🖥 显示设置")
        self.setMinimumWidth(360)
        self._theme = theme
        self._font_size = font_size
        self._ui_scale = ui_scale

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(6)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["dark", "light"])
        self._theme_combo.setCurrentText(theme)
        form.addRow("主题:", self._theme_combo)

        self._font_spin = QSpinBox()
        self._font_spin.setRange(10, 48)
        self._font_spin.setValue(font_size)
        self._font_spin.setSuffix(" px")
        form.addRow("字体大小:", self._font_spin)

        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setRange(0.5, 3.0)
        self._scale_spin.setSingleStep(0.1)
        self._scale_spin.setDecimals(1)
        self._scale_spin.setValue(ui_scale)
        self._scale_spin.setSuffix("×")
        form.addRow("UI 缩放:", self._scale_spin)

        layout.addLayout(form)

        hint = QLabel("修改后立即生效，关闭窗口自动保存。")
        hint.setStyleSheet("color: #888;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 实时预览 ──
        apply_btn = QPushButton("应用")
        apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(apply_btn)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_apply(self):
        """立即应用但不关闭。"""
        if self.parent() and hasattr(self.parent(), '_apply_theme_from_dialog'):
            self.parent()._apply_theme_from_dialog(
                self._theme_combo.currentText(),
                self._font_spin.value(),
                self._scale_spin.value()
            )

    def _on_accept(self):
        self._on_apply()
        self.accept()

    def get_config(self) -> dict:
        return {
            "theme": self._theme_combo.currentText(),
            "font_size": self._font_spin.value(),
            "ui_scale": self._scale_spin.value(),
        }
