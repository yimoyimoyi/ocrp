"""结果表格组件 —— QTableWidget + 持久化 cell widget，编辑前后视觉完全一致。"""


from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QKeyEvent, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import _
from core.logger import get_logger

logger = get_logger(__name__)

_CELL_FONT = None
_CELL_STYLE = (
    "QLineEdit, QTextEdit {"
    "  background: transparent; border: none; padding: 3px 6px;"
    "  font-size: 15px;"
    "}"
    "QLineEdit[readOnly=\"true\"], QTextEdit[readOnly=\"true\"] {"
    "  background: transparent;"
    "}"
    "QLineEdit:focus, QTextEdit:focus {"
    "  outline: 1px solid #58a6ff; outline-offset: 0;"
    "}"
)


def _get_cell_font():
    global _CELL_FONT
    if _CELL_FONT is None:
        _CELL_FONT = QFont("Microsoft YaHei", 12)
        _CELL_FONT.setStyleHint(QFont.SansSerif)
    return _CELL_FONT


class _CellEditor(QWidget):
    """持久化单元格编辑器 —— 只读时与表格融为一体，双击进入编辑，Enter/Escape 提交/取消。"""

    def __init__(self, table: QTableWidget, text: str, is_text_col: bool):
        super().__init__(table)
        self._table = table
        self._sync_cb = None
        self._saved = text
        self._editing = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAutoFillBackground(True)

        # 阻止选中时文字变白：覆盖 HighlightedText 为普通文字色
        pal = self.palette()
        pal.setColor(QPalette.HighlightedText, pal.color(QPalette.Text))
        self.setPalette(pal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if is_text_col and len(text) > 60:
            self._editor = QTextEdit(self)
            self._editor.setPlainText(text)
            self._editor.setAcceptRichText(False)
            self._editor.setFrameShape(QFrame.NoFrame)
            self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            self._editor = QLineEdit(self)
            self._editor.setText(text)
            self._editor.setFrame(False)

        self._editor.setReadOnly(True)
        self._editor.setAutoFillBackground(True)
        self._editor.setFont(_get_cell_font())
        self._editor.setStyleSheet(_CELL_STYLE)
        self._editor.setPalette(pal)  # 继承消除白字的调色板
        self._editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._editor.installEventFilter(self)
        if isinstance(self._editor, QTextEdit):
            self._editor.viewport().installEventFilter(self)
        layout.addWidget(self._editor)

    def set_sync_callback(self, cb):
        self._sync_cb = cb

    def set_text(self, text: str):
        self._saved = text
        if isinstance(self._editor, QTextEdit):
            self._editor.setPlainText(text)
        else:
            self._editor.setText(text)

    def text(self) -> str:
        if isinstance(self._editor, QTextEdit):
            return self._editor.toPlainText()
        return self._editor.text()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonDblClick and not self._editing:
            self._saved = self.text()
            self._editing = True
            self._editor.setReadOnly(False)
            self._editor.setFocus()
            if isinstance(self._editor, QLineEdit):
                self._editor.selectAll()
            return True
        if event.type() == QEvent.KeyPress and self._editing:
            key = event.key() if isinstance(event, QKeyEvent) else 0
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if isinstance(self._editor, QTextEdit) and event.modifiers() & Qt.ShiftModifier:
                    return False
                self._commit()
                return True
            if key == Qt.Key_Escape:
                self._cancel()
                return True
        return super().eventFilter(obj, event)

    def _commit(self):
        self._editing = False
        self._editor.setReadOnly(True)
        self._editor.clearFocus()
        if self._sync_cb:
            self._sync_cb(self)

    def _cancel(self):
        self._editing = False
        if isinstance(self._editor, QTextEdit):
            self._editor.setPlainText(self._saved)
        else:
            self._editor.setText(self._saved)
        self._editor.setReadOnly(True)
        self._editor.clearFocus()


class ResultTableWidget(QWidget):
    """识别结果表格。每列使用持久化 _CellEditor，编辑前后外观完全一致。

    Columns: 时间戳 | 区域 | 引擎 | 原始结果 | 纠错结果 | 置信度 | (+)
    """

    export_requested = pyqtSignal(str, str)
    filter_requested = pyqtSignal(str)
    delete_filtered_requested = pyqtSignal()
    cell_edit_activated = pyqtSignal(int)

    COLUMNS = ["✓", "时间戳", "区域", "引擎", "原始结果", "纠错结果", "润色结果", "置信度", ""]
    COL_WIDTHS = [32, 65, 65, 65, 200, 200, 200, 55, 40]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: list[dict] = []
        self._is_templated = False
        self._search_matches: list[int] = []
        self._search_current_idx = -1
        self._init_ui()
        self._table.installEventFilter(self)

    # ── 事件 ──

    def eventFilter(self, obj, event):
        if obj is self._table and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._adjust_column_widths)
        return super().eventFilter(obj, event)

    def _adjust_column_widths(self):
        n = len(self.COLUMNS)
        if self._table.columnCount() < n:
            return
        total = self._table.viewport().width()
        btn_w = self.COL_WIDTHS[-1]
        cb_w = self.COL_WIDTHS[0]
        available = total - btn_w - cb_w - 4
        if available <= 0:
            return
        sum_ratios = sum(self.COL_WIDTHS[1:-1])
        self._table.blockSignals(True)
        self._table.setColumnWidth(0, cb_w)
        # 前三个固定列（时间戳/区域/引擎）给最小宽度
        min_widths = {1: 55, 2: 55, 3: 55, 4: 100, 5: 100, 6: 100, 7: 45}
        for i in range(1, n - 1):
            w = max(int(available * self.COL_WIDTHS[i] / sum_ratios), min_widths.get(i, 50))
            self._table.setColumnWidth(i, w)
        self._table.setColumnWidth(n - 1, btn_w)
        self._table.blockSignals(False)

    # ── UI 构建 ──

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 标题栏
        header = QHBoxLayout()
        header.setSpacing(4)
        title = QLabel(_("📋 识别结果"))
        title.setObjectName("resultTitle")
        header.addWidget(title)
        self._count_label = QLabel(_("(0 条)"))
        self._count_label.setObjectName("countLabel")
        header.addWidget(self._count_label)
        header.addStretch()

        for fmt, short_label, tip in [
            ("txt", "TXT", "导出为 TXT"),
            ("json", "JSON", "导出为 JSON"),
            ("csv", "CSV", "导出为 CSV"),
            ("srt", "SRT", "导出为 SRT 字幕"),
        ]:
            btn = QToolButton()
            btn.setText(short_label)
            btn.setToolTip(tip)
            btn.setAutoRaise(True)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(36)
            btn.clicked.connect(lambda checked, f=fmt: self._on_export(f))
            header.addWidget(btn)

        header.addSpacing(6)
        for label, slot in [("清空", self.clear_results),
                            ("🗑 删过滤", self._on_delete_filtered)]:
            btn = QToolButton()
            btn.setText(label)
            btn.setAutoRaise(True)
            btn.setFixedHeight(26)
            btn.clicked.connect(slot)
            header.addWidget(btn)

        header.addSpacing(8)
        self._batch_sep = QFrame()
        self._batch_sep.setFrameShape(QFrame.VLine)
        self._batch_sep.setFixedHeight(18)
        header.addWidget(self._batch_sep)
        self._batch_label = QLabel(_("📋 队列:"))
        header.addWidget(self._batch_label)
        self._batch_count_label = QLabel(_("(空)"))
        header.addWidget(self._batch_count_label)
        self._batch_sep.setVisible(False)
        self._batch_label.setVisible(False)
        self._batch_count_label.setVisible(False)

        # 搜索切换按钮
        self._btn_toggle_search = QToolButton(self)
        self._btn_toggle_search.setText("🔍")
        self._btn_toggle_search.setToolTip(_("打开/关闭搜索替换"))
        self._btn_toggle_search.setCheckable(True)
        self._btn_toggle_search.toggled.connect(self._on_toggle_search)
        header.addWidget(self._btn_toggle_search)

        layout.addLayout(header)

        # 全选复选框
        self._select_all_cb = QCheckBox(_("全选"))
        self._select_all_cb.setStyleSheet("font-size: 12px;")
        self._select_all_cb.toggled.connect(self._on_select_all_toggled)
        header.addWidget(self._select_all_cb)

        # 分隔线
        _sep1 = QFrame()
        _sep1.setFrameShape(QFrame.HLine)
        _sep1.setFrameShadow(QFrame.Sunken)
        layout.addWidget(_sep1)

        # 搜索条
        self._search_bar = QFrame()
        self._search_bar.setObjectName("searchBar")
        self._search_bar.setFrameShape(QFrame.StyledPanel)
        self._search_bar.setVisible(False)
        sbl = QHBoxLayout(self._search_bar)
        sbl.setContentsMargins(4, 2, 4, 2); sbl.setSpacing(4)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(_("搜索..."))
        self._search_edit.setMinimumWidth(120)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        sbl.addWidget(self._search_edit)
        for arrow, tip, slot in [("▲", "上一个匹配", self._on_search_prev),
                                  ("▼", "下一个匹配", self._on_search_next)]:
            btn = QToolButton(); btn.setText(arrow); btn.setToolTip(tip)
            btn.clicked.connect(slot); sbl.addWidget(btn)
        self._search_count_label = QLabel("")
        self._search_count_label.setMinimumWidth(60)
        sbl.addWidget(self._search_count_label)
        sbl.addSpacing(8)
        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText(_("替换为..."))
        self._replace_edit.setMinimumWidth(100)
        sbl.addWidget(self._replace_edit)
        for label, slot in [("替换", self._on_replace_current),
                            ("全部替换", self._on_replace_all)]:
            btn = QToolButton(); btn.setText(label); btn.clicked.connect(slot)
            sbl.addWidget(btn)
        sbl.addStretch()
        layout.addWidget(self._search_bar)

        # 分隔线
        _sep2 = QFrame()
        _sep2.setFrameShape(QFrame.HLine)
        _sep2.setFrameShadow(QFrame.Sunken)
        layout.addWidget(_sep2)

        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(False)
        for i in range(len(self.COLUMNS)):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
        # 第0列（复选框）固定宽度
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 28)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.verticalHeader().setMinimumSectionSize(30)
        # 强制调色板：选中高亮使用极浅蓝色，不遮挡文字
        pal = self._table.palette()
        pal.setColor(QPalette.Highlight, QColor(31, 111, 235, 30))
        pal.setColor(QPalette.HighlightedText, pal.color(QPalette.Text))
        self._table.setPalette(pal)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table)

        # ── 选中行计数栏 ──
        selection_bar = QFrame()
        selection_bar.setObjectName("selectionBar")
        sbl = QHBoxLayout(selection_bar)
        sbl.setContentsMargins(4, 2, 4, 2)
        sbl.setSpacing(8)
        self._selection_label = QLabel(_("未选中任何行"))
        self._selection_label.setStyleSheet("color: #78909c; font-size: 12px;")
        sbl.addWidget(self._selection_label, 1)
        layout.addWidget(selection_bar)

        # 复选框集合
        self._checkboxes: dict[int, QCheckBox] = {}

        # 连接选择变化信号
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

    def _on_selection_changed(self):
        """选中行变化时更新计数栏。"""
        count = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        if count == 0:
            self._selection_label.setText(_("未选中任何行"))
            self._selection_label.setStyleSheet("color: #78909c; font-size: 12px;")
        else:
            self._selection_label.setText(f"已选中 {count} 行")
            self._selection_label.setStyleSheet("color: #42a5f5; font-size: 12px; font-weight: bold;")

    def get_selected_rows(self) -> set[int]:
        """返回当前选中的行号集合。"""
        return {row for row, cb in self._checkboxes.items() if cb.isChecked()}

    def select_all(self, checked: bool = True):
        """全选/全不选。"""
        for cb in self._checkboxes.values():
            cb.setChecked(checked)

    def _on_checkbox_toggled(self, row: int):
        """单个复选框状态变化。"""
        self._on_selection_changed()
        # 更新全选框状态（避免循环信号）
        self._select_all_cb.blockSignals(True)
        self._select_all_cb.setChecked(
            all(cb.isChecked() for cb in self._checkboxes.values()) if self._checkboxes else False
        )
        self._select_all_cb.blockSignals(False)

    def _on_select_all_toggled(self, checked: bool):
        """全选/全不选。"""
        for cb in self._checkboxes.values():
            cb.setChecked(checked)

    # ── 单元格同步回调 ──

    def _on_cell_committed(self, editor: _CellEditor):
        for r in range(self._table.rowCount()):
            for c in range(1, self._table.columnCount() - 1):  # skip col 0 (checkbox)
                if self._table.cellWidget(r, c) is editor:
                    text = editor.text()
                    if 0 <= r < len(self._results):
                        if c == 4:    # raw
                            self._results[r]["raw"] = text
                        elif c == 5:  # 纠错结果 (segmented)
                            self._results[r]["segmented"] = text
                        elif c == 6:  # 润色结果 (corrected)
                            self._results[r]["corrected"] = text
                    self.cell_edit_activated.emit(r)
                    return

    # ── 公共方法 ──

    def _make_filter_btn(self, row: int) -> QWidget:
        """创建居中对齐的过滤按钮容器。"""
        btn = QPushButton("+")
        btn.setToolTip(_("将此条内容加入过滤器"))
        btn.setFixedSize(28, 22)
        btn.setStyleSheet("QPushButton { font-size: 11px; padding: 0; margin: 0; }")
        btn.clicked.connect(lambda checked, r=row: self._on_filter_row(r))
        wrapper = QWidget()
        wrapper.setAutoFillBackground(True)
        lo = QHBoxLayout(wrapper)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setAlignment(Qt.AlignCenter)
        lo.addWidget(btn)
        return wrapper

    def add_result(self, time_str: str, region: str, engine: str,
                   raw_text: str, confidence: float = 0.0,
                   time_sec: float = 0.0, end_sec: float = 0.0,
                   sorted_insert: bool = False) -> int:
        result_item = {
            "time_sec": time_sec, "end_sec": end_sec,
            "time": time_str, "region": region, "engine": engine,
            "raw": raw_text, "segmented": "", "corrected": "", "confidence": confidence,
        }

        if sorted_insert:
            # 按时间顺序插入：找到正确的位置
            insert_pos = len(self._results)
            for i, existing in enumerate(self._results):
                existing_time = existing.get("time_sec", 0.0) or 0.0
                if time_sec < existing_time:
                    insert_pos = i
                    break
            self._results.insert(insert_pos, result_item)
            row = insert_pos
            self._table.insertRow(row)
        else:
            # 追加到末尾
            self._results.append(result_item)
            row = self._table.rowCount()
            self._table.insertRow(row)

        # 第0列：复选框
        cb = QCheckBox()
        cb.setStyleSheet("margin-left: 6px;")
        cb.toggled.connect(lambda _, r=row: self._on_checkbox_toggled(r))
        self._table.setCellWidget(row, 0, cb)
        self._checkboxes[row] = cb

        # 数据列（从第1列开始）
        cells = [
            (time_str, False),
            (region, False),
            (engine, False),
            (raw_text, True),
            ("", True),
            ("", True),
            (f"{confidence:.0%}" if confidence else "-", False),
        ]
        for col, (text, is_text) in enumerate(cells):
            editor = _CellEditor(self._table, text, is_text)
            editor.set_sync_callback(self._on_cell_committed)
            self._table.setCellWidget(row, col + 1, editor)  # col+1: 偏移复选框列

        self._table.setCellWidget(row, len(self.COLUMNS) - 1,
                                  self._make_filter_btn(row))

        self._update_count()
        self._table.scrollToBottom()
        return row

    def update_correction(self, row: int, corrected_text: str):
        """更新润色结果列（col 6）。"""
        if 0 <= row < len(self._results):
            self._results[row]["corrected"] = corrected_text
            editor = self._table.cellWidget(row, 6)
            if isinstance(editor, _CellEditor):
                editor.set_text(corrected_text)

    def update_correction_result(self, row: int, corrected_text: str):
        """更新纠错结果列（col 5）。"""
        if 0 <= row < len(self._results):
            self._results[row]["segmented"] = corrected_text
            editor = self._table.cellWidget(row, 5)
            if isinstance(editor, _CellEditor):
                editor.set_text(corrected_text)

    def update_confidence(self, row: int, confidence: float):
        if 0 <= row < len(self._results):
            self._results[row]["confidence"] = confidence
            editor = self._table.cellWidget(row, 7)  # col+1 for checkbox offset
            if isinstance(editor, _CellEditor):
                editor.set_text(f"{confidence:.0%}")

    def clear_results(self):
        self._results.clear()
        self._checkboxes.clear()
        self._table.setRowCount(0)
        self._is_templated = False
        self._update_count()

    def clear_by_type(self, region_name: str = "", engine_name: str = ""):
        if not region_name and not engine_name:
            self.clear_results()
            return
        for i in range(len(self._results) - 1, -1, -1):
            r = self._results[i]
            if ((not region_name or r.get("region", "") == region_name) and
                    (not engine_name or r.get("engine", "") == engine_name)):
                self._table.removeRow(i)
                del self._results[i]
        # 重建 _checkboxes 映射（行索引已变化）
        self._checkboxes.clear()
        for row in range(self._table.rowCount()):
            widget = self._table.cellWidget(row, 0)
            if widget:
                from PyQt5.QtWidgets import QCheckBox
                cb = widget.findChild(QCheckBox)
                if cb:
                    self._checkboxes[row] = cb
        self._update_count()

    def _rebuild_table_rows(self, new_results: list):
        self._table.setRowCount(0)
        self._results = []
        self._checkboxes.clear()
        for item in new_results:
            self._results.append(item)
            ts = item.get("time_sec", 0.0) or 0.0
            m, s = divmod(int(ts), 60)
            time_str = item.get("time", f"{m:02d}:{s:02d}")
            conf_val = item.get("confidence", 0.0) or 0.0
            conf_text = f"{conf_val:.0%}" if conf_val else "-"
            raw = item.get("raw", "")
            segmented = item.get("segmented", "")
            corrected = item.get("corrected", "")
            row = self._table.rowCount()
            self._table.insertRow(row)

            # 第0列：复选框
            cb = QCheckBox()
            cb.setStyleSheet("margin-left: 6px;")
            cb.toggled.connect(lambda _, r=row: self._on_checkbox_toggled(r))
            self._table.setCellWidget(row, 0, cb)
            self._checkboxes[row] = cb

            # 数据列（从第1列开始）
            cells = [
                (time_str, False),
                (item.get("region", ""), False),
                (item.get("engine", ""), False),
                (raw, True),
                (segmented, True),
                (corrected, True),
                (conf_text, False),
            ]
            for col, (text, is_text) in enumerate(cells):
                editor = _CellEditor(self._table, text, is_text)
                editor.set_sync_callback(self._on_cell_committed)
                self._table.setCellWidget(row, col + 1, editor)

            self._table.setCellWidget(row, self._table.columnCount() - 1,
                                      self._make_filter_btn(row))
        self._update_count()

    def sort_by_time(self):
        n = self._table.rowCount()
        if n <= 1:
            return
        indices = list(range(n))
        indices.sort(key=lambda i: (
            self._results[i].get("time_sec", 0.0) or 0.0,
            self._results[i].get("region", ""),
        ))
        old = list(self._results)
        self._rebuild_table_rows([old[i] for i in indices])

    def sort_by_order(self, region_order: str = ""):
        n = self._table.rowCount()
        if n <= 1:
            return
        self._is_templated = bool(region_order)
        all_region_names = set(r.get("region", "") for r in self._results)
        from collections import OrderedDict
        time_groups = OrderedDict()
        for r in self._results:
            ts_key = round(r.get("time_sec", 0.0) or 0.0, 1)
            if ts_key not in time_groups:
                time_groups[ts_key] = {}
            time_groups[ts_key][r.get("region", "")] = r

        template_lines = [line.strip() for line in region_order.splitlines() if line.strip()]
        if not template_lines:
            self.sort_by_time()
            return

        new_results = []
        for ts in sorted(time_groups.keys()):
            group = time_groups[ts]
            for template in template_lines:
                output_line = template
                matched = False
                for rname in all_region_names:
                    if rname in output_line:
                        content = group.get(rname, {}).get("raw", "").strip()
                        if not content:
                            output_line = ""; matched = False; break
                        output_line = output_line.replace(rname, content)
                        matched = True
                if matched and output_line.strip():
                    time_str = "--:--"; src = None
                    for rn in all_region_names:
                        if rn in template and rn in group:
                            time_str = group[rn].get("time", "--:--"); src = group[rn]; break
                    seg = src.get("segmented", "") if src else ""
                    corr = src.get("corrected", "") if src else ""
                    new_results.append({
                        "time_sec": ts, "end_sec": src.get("end_sec", ts + 3.0) if src else ts + 3.0,
                        "time": time_str, "region": output_line,
                        "engine": src.get("engine", "") if src else "",
                        "raw": output_line, "segmented": seg, "corrected": corr,
                        "confidence": src.get("confidence", 0.0) if src else 0.0,
                    })
        self._rebuild_table_rows(new_results)

    def get_results(self) -> list:
        return list(self._results)

    def get_polished_results(self, post_sim_dedup: bool = True,
                              post_sim_threshold: float = 0.9,
                              post_min_text_len: int = 2) -> list:
        if getattr(self, '_is_templated', False):
            return list(self._results)
        from core.result_processor import polish_results
        raw_list = [(r["time_sec"], r["time"], r["region"], r["engine"], r["raw"])
                    for r in self._results]
        polished = polish_results(raw_list, post_sim_dedup=post_sim_dedup,
                                  post_sim_threshold=post_sim_threshold,
                                  post_min_text_len=post_min_text_len)
        # 携带 corrected / segmented / end_sec 字段：用 (time_sec, region, raw) 匹配回原始结果
        correction_map = {}
        segmented_map = {}
        endsec_map = {}
        for r in self._results:
            corr = r.get("corrected", "").strip()
            seg = r.get("segmented", "").strip()
            end_sec = r.get("end_sec", 0.0)
            key = (round(r.get("time_sec", 0.0) or 0.0, 1),
                   r.get("region", ""), r.get("raw", ""))
            if corr:
                correction_map[key] = corr
            if seg:
                segmented_map[key] = seg
            if end_sec:
                endsec_map[key] = end_sec
        for p in polished:
            key = (round(p.get("time_sec", 0.0) or 0.0, 1),
                   p.get("region", ""), p.get("raw", ""))
            if key in correction_map:
                p["corrected"] = correction_map[key]
            if key in segmented_map:
                p["segmented"] = segmented_map[key]
            if key in endsec_map:
                p["end_sec"] = endsec_map[key]
        return polished

    # ── 事件处理 ──

    def _on_filter_row(self, row: int):
        if 0 <= row < len(self._results):
            self.filter_requested.emit(self._results[row]["raw"])

    def _on_cell_clicked(self, row: int, col: int):
        self.cell_edit_activated.emit(row)

    def _on_export(self, fmt: str):
        if not self._results:
            QMessageBox.information(self, _("提示"), _("暂无识别结果可导出。"))
            return
        file_path, _filter = QFileDialog.getSaveFileName(
            self, f"导出为 {fmt.upper()}", "",
            f"{fmt.upper()} Files (*.{fmt});;All Files (*.*)")
        if file_path:
            self.export_requested.emit(fmt, file_path)

    def delete_by_filter(self, matcher) -> int:
        """删除匹配的结果行。matcher(raw, corrected) -> bool。返回删除数。"""
        deleted = 0
        for i in range(len(self._results) - 1, -1, -1):
            raw = self._results[i].get("raw", "")
            corrected = self._results[i].get("corrected", "")
            if matcher(raw, corrected):
                self._table.removeRow(i)
                del self._results[i]
                deleted += 1
        if deleted:
            self._checkboxes.clear()
            self._update_count()
        return deleted

    def _on_delete_filtered(self):
        self.delete_filtered_requested.emit()

    def _update_count(self):
        self._count_label.setText(f"({self._table.rowCount()} 条)")

    def set_batch_count(self, count: int, total_size: int = 0):
        visible = count > 0
        self._batch_sep.setVisible(visible)
        self._batch_label.setVisible(visible)
        self._batch_count_label.setVisible(visible)
        if visible:
            if total_size > 0:
                self._batch_count_label.setText(f"{count}/{total_size} 个文件")
            else:
                self._batch_count_label.setText(f"{count} 个文件")

    # ── 搜索/替换 ──

    def _on_toggle_search(self, visible: bool):
        self._search_bar.setVisible(visible)
        if visible:
            self._search_edit.setFocus()
            self._search_edit.selectAll()

    def _find_all_matches(self, keyword: str) -> list[int]:
        if not keyword.strip():
            return []
        kw = keyword.lower()
        return [i for i, r in enumerate(self._results)
                if kw in r.get("raw", "").lower() or kw in r.get("segmented", "").lower() or kw in r.get("corrected", "").lower()]

    def _highlight_rows(self, matches: list[int], current: int = -1):
        from PyQt5.QtGui import QPalette
        match_set = set(matches)
        _MATCH = QColor(45, 160, 60, 60)
        _CUR = QColor(50, 200, 70, 100)
        for row in range(self._table.rowCount()):
            for col in range(self._table.columnCount() - 1):
                w = self._table.cellWidget(row, col)
                if isinstance(w, _CellEditor):
                    pal = w.palette()
                    if row == current:
                        pal.setColor(QPalette.Window, _CUR)
                    elif row in match_set:
                        pal.setColor(QPalette.Window, _MATCH)
                    else:
                        pal.setColor(QPalette.Window, QColor(0, 0, 0, 0))
                    w.setPalette(pal)
                    w.setAutoFillBackground(row == current or row in match_set)
        if 0 <= current < self._table.rowCount():
            self._table.selectRow(current)

    def _on_search_text_changed(self, text: str):
        if not text.strip():
            self._highlight_rows([])
            self._search_count_label.setText("")
            self._search_matches = []; self._search_current_idx = -1; return
        self._search_matches = self._find_all_matches(text)
        self._search_current_idx = 0 if self._search_matches else -1
        if self._search_matches:
            self._highlight_rows(self._search_matches, self._search_matches[0])
            self._search_count_label.setText(f"1/{len(self._search_matches)}")
        else:
            self._highlight_rows([])
            self._search_count_label.setText("无匹配")

    def _on_search_next(self):
        if not self._search_matches: return
        idx = (self._search_current_idx + 1) % len(self._search_matches)
        self._search_current_idx = idx
        self._highlight_rows(self._search_matches, self._search_matches[idx])
        self._search_count_label.setText(f"{idx + 1}/{len(self._search_matches)}")

    def _on_search_prev(self):
        if not self._search_matches: return
        idx = (self._search_current_idx - 1) % len(self._search_matches)
        self._search_current_idx = idx
        self._highlight_rows(self._search_matches, self._search_matches[idx])
        self._search_count_label.setText(f"{idx + 1}/{len(self._search_matches)}")

    def _on_replace_current(self):
        if not self._search_matches or self._search_current_idx < 0: return
        row = self._search_matches[self._search_current_idx]
        self._do_replace(row, self._search_edit.text(), self._replace_edit.text())

    def _on_replace_all(self):
        if not self._search_matches: return
        for row in list(self._search_matches):
            self._do_replace(row, self._search_edit.text(), self._replace_edit.text())

    def _do_replace(self, row: int, search: str, replace: str):
        if row < 0 or row >= len(self._results): return
        for col, key in [(3, "raw"), (4, "segmented"), (5, "corrected")]:  # col4=纠错, col5=润色
            val = self._results[row].get(key, "")
            if search in val:
                new_val = val.replace(search, replace)
                self._results[row][key] = new_val
                editor = self._table.cellWidget(row, col)
                if isinstance(editor, _CellEditor):
                    editor.set_text(new_val)
