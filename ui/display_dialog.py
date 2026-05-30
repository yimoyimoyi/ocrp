"""显示设置对话框 —— 主题（qt-material）/ 字体大小 / 密度缩放"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.i18n import _
from ui.style_loader import THEME_COLORS, THEME_DISPLAY_NAMES


class _ThemeCard(QFrame):
    """单个主题卡片：显示主题名 + 3 个色块预览。"""

    clicked = pyqtSignal(str)  # theme_key

    def __init__(self, theme_key: str, display_name: str, colors: list[str],
                 selected: bool = False, parent=None):
        super().__init__(parent)
        self._key = theme_key
        self._selected = selected
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(120, 64)
        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 色块预览行
        color_row = QHBoxLayout()
        color_row.setSpacing(3)
        for color in colors[:3]:
            swatch = QLabel()
            swatch.setFixedSize(28, 20)
            swatch.setStyleSheet(
                f"background-color: {color}; border-radius: 3px; border: 1px solid rgba(255,255,255,0.1);"
            )
            color_row.addWidget(swatch)
        color_row.addStretch()
        layout.addLayout(color_row)

        # 主题名
        name_label = QLabel(display_name)
        name_label.setStyleSheet("font-size: 11px; font-weight: 500;")
        name_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(name_label)

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(
                "_ThemeCard { background: rgba(38,166,154,0.15); border: 2px solid #26a69a; border-radius: 8px; }"
            )
        else:
            self.setStyleSheet(
                "_ThemeCard { background: rgba(128,128,128,0.08); border: 2px solid transparent; border-radius: 8px; }"
                "_ThemeCard:hover { background: rgba(128,128,128,0.15); border: 2px solid rgba(128,128,128,0.3); }"
            )

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)


class DisplayDialog(QDialog):
    """显示设置对话框。"""

    theme_applied = pyqtSignal(str, int, float)  # (theme_name, font_size, scale)

    def __init__(self, theme: str, font_size: int, ui_scale: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("显示设置"))
        self.setMinimumWidth(520)
        self.resize(560, 480)
        self._theme = theme
        self._font_size = font_size
        self._ui_scale = ui_scale
        self._cards: dict[str, _ThemeCard] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── 主题卡片选择 ──
        theme_label = QLabel(_("主题配色"))
        theme_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(theme_label)

        # 经典主题（独立一行）
        default_colors = THEME_COLORS.get("default", ["#555555", "#888888", "#f0f0f0"])
        default_card = _ThemeCard("default", THEME_DISPLAY_NAMES.get("default", "经典"),
                                  default_colors, selected=(theme == "default"))
        default_card.clicked.connect(self._on_card_clicked)
        self._cards["default"] = default_card
        default_row = QHBoxLayout()
        default_row.addWidget(default_card)
        default_row.addStretch()
        layout.addLayout(default_row)

        # 深色/浅色分组
        for section_name, prefix in [("深色", "dark_"), ("浅色", "light_")]:
            section_label = QLabel(section_name)
            section_label.setStyleSheet("color: #808080; font-size: 11px; margin-top: 4px;")
            layout.addWidget(section_label)

            grid_widget = QWidget()
            grid = QGridLayout(grid_widget)
            grid.setSpacing(6)
            grid.setContentsMargins(0, 0, 0, 0)

            col = 0
            row = 0
            for key, display in THEME_DISPLAY_NAMES.items():
                if not key.startswith(prefix):
                    continue
                colors = THEME_COLORS.get(key, ["#808080", "#606060", "#1e1e1e"])
                card = _ThemeCard(key, display, colors, selected=(key == theme))
                card.clicked.connect(self._on_card_clicked)
                self._cards[key] = card
                grid.addWidget(card, row, col)
                col += 1
                if col >= 4:
                    col = 0
                    row += 1

            layout.addWidget(grid_widget)

        # ── 字体和缩放 ──
        form = QFormLayout()
        form.setSpacing(8)

        self._font_spin = QSpinBox()
        self._font_spin.setRange(10, 24)
        self._font_spin.setValue(font_size)
        self._font_spin.setSuffix(" px")
        form.addRow(_("字体大小:"), self._font_spin)

        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setRange(0.8, 1.5)
        self._scale_spin.setSingleStep(0.1)
        self._scale_spin.setDecimals(1)
        self._scale_spin.setValue(ui_scale)
        self._scale_spin.setSuffix("x")
        self._scale_spin.setToolTip(_("整体 UI 缩放比例（影响密度和字体）"))
        form.addRow(_("UI 缩放:"), self._scale_spin)

        layout.addLayout(form)

        hint = QLabel(_("点击主题卡片即时预览，关闭窗口自动保存。"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 按钮 ──
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_card_clicked(self, key: str):
        """点击主题卡片：更新选中状态并即时预览。"""
        self._theme = key
        for k, card in self._cards.items():
            card.set_selected(k == key)
        self._on_apply()

    def _on_apply(self):
        """立即应用但不关闭。"""
        self.theme_applied.emit(
            self._theme,
            self._font_spin.value(),
            self._scale_spin.value()
        )

    def _on_accept(self):
        self._on_apply()
        self.accept()

    def get_config(self) -> dict:
        return {
            "theme": self._theme,
            "font_size": self._font_spin.value(),
            "ui_scale": self._scale_spin.value(),
        }
