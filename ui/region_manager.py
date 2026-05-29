"""区域管理面板 —— 区域列表 + 滑动属性编辑器。"""


from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class RegionManagerWidget(QWidget):
    """区域管理面板（紧凑版）。

    Signals:
        region_selected(int): 选中区域索引
        region_updated(int, dict): 区域属性更新
        region_add_requested(): 请求添加新区域
        region_removed(int): 区域被删除
    """

    region_selected = pyqtSignal(int)
    region_updated = pyqtSignal(int, dict)
    region_add_requested = pyqtSignal()
    region_removed = pyqtSignal(int)
    regions_cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._regions: list[dict] = []
        self._current_index: int = -1
        self._engine_names: list[str] = ["paddleocr"]
        self._template_names: list[str] = ["通用OCR"]
        self._init_ui()

    @property
    def regions(self) -> list:
        return list(self._regions)

    @regions.setter
    def regions(self, val: list):
        self._regions = list(val)
        self._refresh_list()

    def set_engine_names(self, names: list[str]):
        self._engine_names = list(names)
        self._engine_combo.clear()
        self._engine_combo.addItems(self._engine_names)

    def set_template_names(self, names: list[str]):
        self._template_names = list(names)
        self._template_combo.clear()
        self._template_combo.addItems(self._template_names)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ── 标题栏 ──
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("📐 区域")
        title.setObjectName("regionTitle")
        header.addWidget(title)
        header.addStretch()

        btn_add = QPushButton("+ 添加")
        btn_add.setToolTip("在预览图上拖拽绘制矩形区域")
        btn_add.clicked.connect(self.region_add_requested.emit)
        btn_add.setFixedHeight(28)
        header.addWidget(btn_add)

        btn_clear = QPushButton("清空")
        btn_clear.setFixedHeight(28)
        btn_clear.clicked.connect(self._on_clear_all)
        header.addWidget(btn_clear)
        layout.addLayout(header)

        # ── 区域列表 ──
        self._list_widget = QListWidget()
        self._list_widget.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list_widget)

        # ── 属性编辑 ──
        prop_root = QWidget()
        vl = QVBoxLayout(prop_root)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(6)
        self._build_prop_editor(vl)
        layout.addWidget(prop_root, 1)

        self._set_editor_enabled(False)

    def _build_prop_editor(self, vl):
        """构建滑动窗口内的属性编辑区域。"""
        # 第1行：名称 + 启用
        row1 = QHBoxLayout(); row1.setSpacing(6)
        row1.addWidget(QLabel("名称:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("区域名称")
        self._name_edit.textChanged.connect(self._on_prop_changed)
        row1.addWidget(self._name_edit, 1)
        self._enabled_check = QCheckBox("启用")
        self._enabled_check.setChecked(True)
        self._enabled_check.toggled.connect(self._on_prop_changed)
        row1.addWidget(self._enabled_check)
        vl.addLayout(row1)

        # 第2行：引擎 + 模板
        row2 = QHBoxLayout(); row2.setSpacing(6)
        row2.addWidget(QLabel("引擎:"))
        self._engine_combo = QComboBox()
        self._engine_combo.addItems(self._engine_names)
        self._engine_combo.currentTextChanged.connect(self._on_prop_changed)
        row2.addWidget(self._engine_combo, 1)
        row2.addWidget(QLabel("模板:"))
        self._template_combo = QComboBox()
        self._template_combo.addItems(self._template_names)
        self._template_combo.currentTextChanged.connect(self._on_prop_changed)
        row2.addWidget(self._template_combo, 1)
        vl.addLayout(row2)

        # 第3-4行：X/Y/W/H 网格
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.addWidget(QLabel("X:"), 0, 0)
        self._x_spin = QSpinBox(); self._x_spin.setRange(0, 9999)
        self._x_spin.valueChanged.connect(self._on_prop_changed)
        grid.addWidget(self._x_spin, 0, 1)
        grid.addWidget(QLabel("Y:"), 0, 2)
        self._y_spin = QSpinBox(); self._y_spin.setRange(0, 9999)
        self._y_spin.valueChanged.connect(self._on_prop_changed)
        grid.addWidget(self._y_spin, 0, 3)
        grid.addWidget(QLabel("W:"), 1, 0)
        self._w_spin = QSpinBox(); self._w_spin.setRange(0, 9999)
        self._w_spin.valueChanged.connect(self._on_prop_changed)
        grid.addWidget(self._w_spin, 1, 1)
        grid.addWidget(QLabel("H:"), 1, 2)
        self._h_spin = QSpinBox(); self._h_spin.setRange(0, 9999)
        self._h_spin.valueChanged.connect(self._on_prop_changed)
        grid.addWidget(self._h_spin, 1, 3)
        vl.addLayout(grid)

        # 第5行：膨胀比例
        row5 = QHBoxLayout(); row5.setSpacing(6)
        row5.addWidget(QLabel("膨胀:"))
        self._expand_ratio_spin = QSpinBox()
        self._expand_ratio_spin.setRange(0, 200); self._expand_ratio_spin.setSuffix("%")
        self._expand_ratio_spin.setToolTip("ROI 区域向外膨胀的比例")
        self._expand_ratio_spin.valueChanged.connect(self._on_prop_changed)
        row5.addWidget(self._expand_ratio_spin)
        row5.addStretch()
        vl.addLayout(row5)

        # 第6行：裁剪四边
        row6 = QHBoxLayout(); row6.setSpacing(6)
        row6.addWidget(QLabel("裁剪 L:"))
        self._crop_left_spin = QSpinBox(); self._crop_left_spin.setRange(0, 9999)
        self._crop_left_spin.valueChanged.connect(self._on_prop_changed)
        row6.addWidget(self._crop_left_spin)
        row6.addWidget(QLabel("R:"))
        self._crop_right_spin = QSpinBox(); self._crop_right_spin.setRange(0, 9999)
        self._crop_right_spin.valueChanged.connect(self._on_prop_changed)
        row6.addWidget(self._crop_right_spin)
        row6.addWidget(QLabel("T:"))
        self._crop_top_spin = QSpinBox(); self._crop_top_spin.setRange(0, 9999)
        self._crop_top_spin.valueChanged.connect(self._on_prop_changed)
        row6.addWidget(self._crop_top_spin)
        row6.addWidget(QLabel("B:"))
        self._crop_bottom_spin = QSpinBox(); self._crop_bottom_spin.setRange(0, 9999)
        self._crop_bottom_spin.valueChanged.connect(self._on_prop_changed)
        row6.addWidget(self._crop_bottom_spin)
        vl.addLayout(row6)

        # 第7行：OCR 提示词
        vl.addWidget(QLabel("OCR 提示词:"))
        self._prompt_edit = QTextEdit()
        self._prompt_edit.setPlaceholderText("自定义 OCR 提示词（覆盖模板）")
        self._prompt_edit.setMaximumHeight(60)
        self._prompt_edit.textChanged.connect(self._on_prop_changed)
        vl.addWidget(self._prompt_edit)

        # 第8行：纠错提示词
        vl.addWidget(QLabel("纠错提示词:"))
        self._corr_prompt_edit = QTextEdit()
        self._corr_prompt_edit.setPlaceholderText("此区域专用的纠错提示词（留空用全局）")
        self._corr_prompt_edit.setMaximumHeight(60)
        self._corr_prompt_edit.textChanged.connect(self._on_prop_changed)
        vl.addWidget(self._corr_prompt_edit)

        # 删除按钮
        self._btn_remove = QPushButton("- 删除此区域")
        self._btn_remove.setObjectName("btnRemoveRegion")
        self._btn_remove.setFixedHeight(28)
        self._btn_remove.clicked.connect(self._on_remove_current)
        vl.addWidget(self._btn_remove)

        # 底部弹性空间
        vl.addStretch()

    def _refresh_list(self):
        prev_row = self._list_widget.currentRow()
        self._list_widget.blockSignals(True)
        self._list_widget.clear()
        for r in self._regions:
            color = r.get("color", QColor(0, 200, 100))
            text = f"{'✅ ' if r.get('enabled', True) else '⏸ '}{r['name']}"
            item = QListWidgetItem(text)
            item.setForeground(color)
            self._list_widget.addItem(item)
        if 0 <= prev_row < self._list_widget.count():
            self._list_widget.setCurrentRow(prev_row)
        elif self._list_widget.count() > 0:
            self._list_widget.setCurrentRow(0)
        self._list_widget.blockSignals(False)
        # 手动触发选中事件（blockSignals 期间丢失的信号不会重放）
        current = self._list_widget.currentRow()
        if current >= 0:
            self._on_selection_changed(current)

    def _on_selection_changed(self, row: int):
        self._current_index = row
        if 0 <= row < len(self._regions):
            self._set_editor_enabled(True)
            self._populate_editor(self._regions[row])
            self.region_selected.emit(row)
        else:
            self._set_editor_enabled(False)

    def _populate_editor(self, r: dict):
        self._block_signals(True)
        self._name_edit.setText(r.get("name", ""))
        engine = r.get("engine", "paddleocr")
        if engine in [self._engine_combo.itemText(i) for i in range(self._engine_combo.count())]:
            self._engine_combo.setCurrentText(engine)
        template = r.get("prompt_template", "通用OCR")
        if template in [self._template_combo.itemText(i) for i in range(self._template_combo.count())]:
            self._template_combo.setCurrentText(template)
        self._prompt_edit.setPlainText(r.get("prompt", ""))
        self._corr_prompt_edit.setPlainText(r.get("correction_prompt", ""))
        self._x_spin.setValue(r.get("x", 0))
        self._y_spin.setValue(r.get("y", 0))
        self._w_spin.setValue(r.get("w", 0))
        self._h_spin.setValue(r.get("h", 0))
        self._expand_ratio_spin.setValue(r.get("expand_ratio", 0))
        self._crop_left_spin.setValue(r.get("crop_left", 0))
        self._crop_right_spin.setValue(r.get("crop_right", 0))
        self._crop_top_spin.setValue(r.get("crop_top", 0))
        self._crop_bottom_spin.setValue(r.get("crop_bottom", 0))
        self._enabled_check.setChecked(r.get("enabled", True))
        self._block_signals(False)

    def _on_prop_changed(self, *_):
        """区域属性变更回调。

        仅当 name / enabled 变化时才刷新列表（避免编辑提示词时重建列表导致卡顿）。
        """
        if self._current_index < 0 or self._current_index >= len(self._regions):
            return
        r = self._regions[self._current_index]
        old_name = r.get("name", "")
        old_enabled = r.get("enabled", True)

        r["name"] = self._name_edit.text()
        r["engine"] = self._engine_combo.currentText()
        r["prompt_template"] = self._template_combo.currentText()
        r["prompt"] = self._prompt_edit.toPlainText()
        r["correction_prompt"] = self._corr_prompt_edit.toPlainText()
        r["x"] = self._x_spin.value()
        r["y"] = self._y_spin.value()
        r["w"] = self._w_spin.value()
        r["h"] = self._h_spin.value()
        r["expand_ratio"] = self._expand_ratio_spin.value()
        r["crop_left"] = self._crop_left_spin.value()
        r["crop_right"] = self._crop_right_spin.value()
        r["crop_top"] = self._crop_top_spin.value()
        r["crop_bottom"] = self._crop_bottom_spin.value()
        r["enabled"] = self._enabled_check.isChecked()

        self.region_updated.emit(self._current_index, dict(r))

        # 仅当列表显示内容变化时才刷新列表
        if r["name"] != old_name or r["enabled"] != old_enabled:
            self._refresh_list()

    def _on_remove_current(self):
        if self._current_index >= 0:
            self.region_removed.emit(self._current_index)

    def _on_clear_all(self):
        reply = QMessageBox.question(
            self, "确认清空", "确定要清空所有区域吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.regions_cleared.emit()

    def _set_editor_enabled(self, enabled: bool):
        for w in [self._name_edit, self._engine_combo, self._template_combo,
                  self._prompt_edit, self._corr_prompt_edit,
                  self._x_spin, self._y_spin,
                  self._w_spin, self._h_spin,
                  self._expand_ratio_spin,
                  self._crop_left_spin, self._crop_right_spin,
                  self._crop_top_spin, self._crop_bottom_spin,
                  self._enabled_check, self._btn_remove]:
            w.setEnabled(enabled)

    def _block_signals(self, block: bool):
        for w in [self._name_edit, self._engine_combo, self._template_combo,
                  self._x_spin, self._y_spin, self._w_spin, self._h_spin,
                  self._expand_ratio_spin,
                  self._crop_left_spin, self._crop_right_spin,
                  self._crop_top_spin, self._crop_bottom_spin,
                  self._enabled_check]:
            w.blockSignals(block)
        self._prompt_edit.blockSignals(block)
        self._corr_prompt_edit.blockSignals(block)

    def select_region(self, index: int):
        if 0 <= index < self._list_widget.count():
            self._list_widget.setCurrentRow(index)
