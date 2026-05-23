# -*- coding: utf-8 -*-
"""显示设置对话框 —— 主题 / 字体大小 / UI 缩放"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFormLayout, QComboBox, QSpinBox, QDoubleSpinBox,
    QDialogButtonBox, QWidget, QGroupBox,
)
from PyQt5.QtCore import Qt, pyqtSignal


class DisplayDialog(QDialog):
    """显示设置对话框。"""

    theme_applied = pyqtSignal(str, int, float)

    def __init__(self, theme: str, font_size: int, ui_scale: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🖥 显示设置")
        self.setMinimumWidth(400)
        self._theme = theme
        self._font_size = font_size
        self._ui_scale = ui_scale

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # ── 主题选择 ──
        theme_group = QGroupBox("外观")
        theme_form = QFormLayout(theme_group)
        theme_form.setSpacing(8)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["dark", "light"])
        self._theme_combo.setCurrentText(theme)
        self._theme_combo.setToolTip("切换深色/浅色主题")
        theme_form.addRow("主题:", self._theme_combo)

        self._font_spin = QSpinBox()
        self._font_spin.setRange(10, 48)
        self._font_spin.setValue(font_size)
        self._font_spin.setSuffix(" px")
        theme_form.addRow("字体大小:", self._font_spin)

        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setRange(0.5, 3.0)
        self._scale_spin.setSingleStep(0.1)
        self._scale_spin.setDecimals(1)
        self._scale_spin.setValue(ui_scale)
        self._scale_spin.setSuffix("×")
        theme_form.addRow("UI 缩放:", self._scale_spin)

        layout.addWidget(theme_group)

        hint = QLabel("💡 修改后点击「应用」立即预览，确定后点击「OK」保存。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()
        apply_btn = QPushButton("🎨 应用预览")
        apply_btn.setToolTip("立即应用当前设置（不关闭窗口）")
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(apply_btn)
        btn_row.addStretch()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        btn_row.addWidget(btns)
        layout.addLayout(btn_row)

    def _on_apply(self):
        """立即应用但不关闭。"""
        self.theme_applied.emit(
            self._theme_combo.currentText(),
            self._font_spin.value(),
            self._scale_spin.value(),
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
