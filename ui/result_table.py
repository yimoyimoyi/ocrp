"""结果表格组件 —— QTableView + QAbstractTableModel + delegate（4c.1 模型化重构）。

替换原 QTableWidget + 每格 cellWidget 方案：
- 万行级渲染性能（仅渲染可见行）
- 复选框列使用 CheckStateRole（消灭 _checkboxes 字典）
- 内联编辑用 QStyledItemDelegate（QLineEdit/QTextEdit，透明无边框保持视觉一致）
- 搜索高亮通过 data(BackgroundRole) 按行返回

公开 API 与旧版完全兼容（main_window/workflow 调用方零改动）。
"""

import bisect

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
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
    QStyledItemDelegate,
    QTableView,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import _
from core.logger import get_logger

logger = get_logger(__name__)

_CELL_FONT = None
# 注意：编辑器背景不透明（autoFillBackground + palette Base），
# 否则 view 绘制的单元格文本与编辑器文本重叠显示（QTableView 编辑场景）
_CELL_STYLE = (
    "QLineEdit, QTextEdit {"
    "  border: none; padding: 3px 6px;"
    "  font-size: 15px;"
    "}"
    "QLineEdit:focus, QTextEdit:focus {"
    "  outline: 1px solid #58a6ff; outline-offset: 0;"
    "}"
)

# 搜索高亮颜色
_MATCH_BG = QColor(45, 160, 60, 60)
_CUR_BG = QColor(50, 200, 70, 100)


def _get_cell_font():
    global _CELL_FONT
    if _CELL_FONT is None:
        _CELL_FONT = QFont("Microsoft YaHei", 12)
        _CELL_FONT.setStyleHint(QFont.SansSerif)
    return _CELL_FONT


# ── 列定义 ──
COL_COUNT = 9
COL_CHECKBOX = 0
COL_TIME = 1
COL_REGION = 2
COL_ENGINE = 3
COL_RAW = 4
COL_SEGMENTED = 5
COL_CORRECTED = 6
COL_CONFIDENCE = 7
COL_FILTER = 8
EDITABLE_COLS = (COL_RAW, COL_SEGMENTED, COL_CORRECTED)
COL_WIDTHS = [32, 65, 65, 65, 200, 200, 200, 55, 40]


class _ResultsModel(QAbstractTableModel):
    """结果数据模型 —— 包装 ResultTableWidget._results 列表。"""

    def __init__(self, results_ref: list, parent=None):
        super().__init__(parent)
        self._results = results_ref  # 引用外部列表，数据与 widget 共享
        self._checked_rows: set[int] = set()
        self._match_rows: set[int] = set()
        self._current_row: int = -1

    # ── 基础 ──
    def rowCount(self, parent=None) -> int:
        parent = parent or QModelIndex()
        return 0 if parent.isValid() else len(self._results)

    def columnCount(self, parent=None) -> int:
        parent = parent or QModelIndex()
        return 0 if parent.isValid() else COL_COUNT

    def removeRows(self, row: int, count: int, parent=None) -> bool:
        """防御性实现（P0-T6 修复）：外部调用 removeRow 不再静默空操作。

        业务删除应优先走 ResultTableWidget.remove_rows_by_predicate /
        delete_by_filter（整表 reset 更高效）；此处保证单行删除语义正确。
        """
        parent = parent or QModelIndex()
        if parent.isValid() or row < 0 or row + count > len(self._results):
            return False
        self.beginRemoveRows(parent, row, row + count - 1)
        del self._results[row : row + count]
        # 行索引整体前移后，选中/高亮集合一并前移（删除区之前的行保留原索引，
        # 之后的减 count；此前实现丢弃了删除区之前的选中行）
        self._checked_rows = {r for r in self._checked_rows if r < row or r >= row + count}
        self._checked_rows = {r if r < row else r - count for r in self._checked_rows}
        self._match_rows = {r for r in self._match_rows if r < row or r >= row + count}
        self._match_rows = {r if r < row else r - count for r in self._match_rows}
        if self._current_row >= row + count:
            self._current_row -= count
        elif self._current_row >= row:
            self._current_row = -1
        self.endRemoveRows()
        return True

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._results)):
            return None
        r = self._results[index.row()]
        col = index.column()

        if int(role) in (int(Qt.ItemDataRole.DisplayRole), int(Qt.ItemDataRole.EditRole)):
            if col == COL_TIME:
                return r.get("time", "")
            if col == COL_REGION:
                return r.get("region", "")
            if col == COL_ENGINE:
                return r.get("engine", "")
            if col == COL_RAW:
                return r.get("raw", "")
            if col == COL_SEGMENTED:
                return r.get("segmented", "")
            if col == COL_CORRECTED:
                return r.get("corrected", "")
            if col == COL_CONFIDENCE:
                conf = r.get("confidence", 0.0) or 0.0
                return f"{conf:.0%}" if conf else "-"
            return None

        if int(role) == int(Qt.ItemDataRole.CheckStateRole) and col == COL_CHECKBOX:
            return Qt.Checked if index.row() in self._checked_rows else Qt.Unchecked

        if int(role) == int(Qt.ItemDataRole.BackgroundRole) and col in (
            COL_TIME,
            COL_REGION,
            COL_ENGINE,
            COL_RAW,
            COL_SEGMENTED,
            COL_CORRECTED,
            COL_CONFIDENCE,
        ):
            if index.row() == self._current_row:
                return _CUR_BG
            if index.row() in self._match_rows:
                return _MATCH_BG
            return None

        if int(role) == int(Qt.ItemDataRole.TextAlignmentRole) and col in (
            COL_TIME,
            COL_REGION,
            COL_ENGINE,
            COL_CONFIDENCE,
        ):
            return int(Qt.AlignCenter)

        if int(role) == int(Qt.ItemDataRole.FontRole):
            return _get_cell_font()

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.NoItemFlags
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        col = index.column()
        if col == COL_CHECKBOX:
            f |= Qt.ItemIsUserCheckable
        if col in EDITABLE_COLS:
            f |= Qt.ItemIsEditable
        return f

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or not (0 <= index.row() < len(self._results)):
            return False
        row = index.row()
        col = index.column()

        if int(role) == int(Qt.ItemDataRole.CheckStateRole) and col == COL_CHECKBOX:
            if value == Qt.Checked:
                self._checked_rows.add(row)
            else:
                self._checked_rows.discard(row)
            self.dataChanged.emit(index, index)
            return True

        if int(role) == int(Qt.ItemDataRole.EditRole) and col in EDITABLE_COLS:
            r = self._results[row]
            if col == COL_RAW:
                r["raw"] = value
            elif col == COL_SEGMENTED:
                r["segmented"] = value
            elif col == COL_CORRECTED:
                r["corrected"] = value
            # P1-3：显式携带 EditRole，区分用户编辑提交（跳转）与程序化更新（不跳转）
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.EditRole])
            return True
        return False

    # ── 数据变更辅助 ──
    def notify_row(self, row: int, col: int):
        """通知单格数据变化。"""
        if 0 <= row < len(self._results):
            self.dataChanged.emit(self.index(row, col), self.index(row, col))

    def notify_all(self):
        """全表数据变化（排序/重建后）。"""
        self.dataChanged.emit(self.index(0, 0), self.index(max(0, len(self._results) - 1), COL_COUNT - 1))

    def set_highlight(self, match_rows: set[int], current_row: int = -1):
        """更新搜索高亮状态并重绘数据列。"""
        self._match_rows = set(match_rows)
        self._current_row = current_row
        if self._results:
            self.dataChanged.emit(self.index(0, COL_TIME), self.index(len(self._results) - 1, COL_CONFIDENCE))

    def set_all_checked(self, checked: bool):
        """全选/全不选。"""
        if checked:
            self._checked_rows = set(range(len(self._results)))
        else:
            self._checked_rows.clear()
        if self._results:
            self.dataChanged.emit(self.index(0, COL_CHECKBOX), self.index(len(self._results) - 1, COL_CHECKBOX))

    def clear_checked(self):
        self._checked_rows.clear()


class _CellDelegate(QStyledItemDelegate):
    """内联编辑委托 —— 透明无边框编辑器，编辑前后视觉一致。

    显式覆写 setEditorData/setModelData：PySide6 默认对 QTextEdit 使用 HTML
    语义（提交时 toHtml 会把 "<!DOCTYPE HTML PUBLIC..." 文档头写回单元格），
    这里统一用纯文本语义。
    """

    def createEditor(self, parent, option, index: QModelIndex):
        col = index.column()
        text = index.data(Qt.DisplayRole) or ""
        if col in EDITABLE_COLS and len(text) > 60:
            editor = QTextEdit(parent)
            editor.setAcceptRichText(False)
            editor.setFrameShape(QFrame.NoFrame)
            editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            editor = QLineEdit(parent)
            editor.setFrame(False)
        editor.setFont(_get_cell_font())
        editor.setStyleSheet(_CELL_STYLE)
        # 不透明背景：编辑时遮挡 view 绘制的单元格文本，避免重叠
        editor.setAutoFillBackground(True)
        pal = editor.palette()
        pal.setColor(pal.ColorRole.Base, option.palette.color(option.palette.ColorRole.Base))
        editor.setPalette(pal)
        return editor

    def setEditorData(self, editor, index: QModelIndex):
        """编辑器填充纯文本（避免 QTextEdit 的 HTML 包装）。"""
        text = index.data(Qt.ItemDataRole.EditRole) or ""
        if isinstance(editor, QTextEdit):
            editor.setPlainText(text)
        else:
            editor.setText(text)

    def setModelData(self, editor, model, index: QModelIndex):
        """提交纯文本（避免 toHtml 文档头写回）。"""
        if isinstance(editor, QTextEdit):
            text = editor.toPlainText()
        else:
            text = editor.text()
        model.setData(index, text, Qt.ItemDataRole.EditRole)


class ResultTableWidget(QWidget):
    """识别结果表格（模型化重构版）。

    Columns: ✓ | 时间戳 | 区域 | 引擎 | 原始结果 | 纠错结果 | 润色结果 | 置信度 | (+)
    """

    export_requested = Signal(str, str)
    filter_requested = Signal(str)
    delete_filtered_requested = Signal()
    cell_edit_activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: list[dict] = []
        self._is_templated = False
        self._search_matches: list[int] = []
        self._search_current_idx = -1
        self._model = _ResultsModel(self._results, self)
        self._init_ui()
        self._table.installEventFilter(self)

    # ── 事件 ──

    def eventFilter(self, obj, event):
        if obj is self._table and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._adjust_column_widths)
        return super().eventFilter(obj, event)

    def _adjust_column_widths(self):
        total = self._table.viewport().width()
        btn_w = COL_WIDTHS[-1]
        cb_w = COL_WIDTHS[0]
        available = total - btn_w - cb_w - 4
        if available <= 0:
            return
        sum_ratios = sum(COL_WIDTHS[1:-1])
        self._table.blockSignals(True)
        self._table.setColumnWidth(0, cb_w)
        # 前三个固定列（时间戳/区域/引擎）给最小宽度
        min_widths = {1: 55, 2: 55, 3: 55, 4: 100, 5: 100, 6: 100, 7: 45}
        for i in range(1, COL_COUNT - 1):
            w = max(int(available * COL_WIDTHS[i] / sum_ratios), min_widths.get(i, 50))
            self._table.setColumnWidth(i, w)
        self._table.setColumnWidth(COL_COUNT - 1, btn_w)
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
        for label, slot in [("清空", self.clear_results), ("🗑 删过滤", self._on_delete_filtered)]:
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

        # 全选复选框
        self._select_all_cb = QCheckBox(_("全选"))
        self._select_all_cb.setStyleSheet("font-size: 12px;")
        self._select_all_cb.toggled.connect(self._on_select_all_toggled)
        header.addWidget(self._select_all_cb)

        layout.addLayout(header)

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
        sbl.setContentsMargins(4, 2, 4, 2)
        sbl.setSpacing(4)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(_("搜索..."))
        self._search_edit.setMinimumWidth(120)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        sbl.addWidget(self._search_edit)
        for arrow, tip, slot in [("▲", "上一个匹配", self._on_search_prev), ("▼", "下一个匹配", self._on_search_next)]:
            btn = QToolButton()
            btn.setText(arrow)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            sbl.addWidget(btn)
        self._search_count_label = QLabel("")
        self._search_count_label.setMinimumWidth(60)
        sbl.addWidget(self._search_count_label)
        sbl.addSpacing(8)
        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText(_("替换为..."))
        self._replace_edit.setMinimumWidth(100)
        sbl.addWidget(self._replace_edit)
        for label, slot in [("替换", self._on_replace_current), ("全部替换", self._on_replace_all)]:
            btn = QToolButton()
            btn.setText(label)
            btn.clicked.connect(slot)
            sbl.addWidget(btn)
        sbl.addStretch()
        layout.addWidget(self._search_bar)

        # 分隔线
        _sep2 = QFrame()
        _sep2.setFrameShape(QFrame.HLine)
        _sep2.setFrameShadow(QFrame.Sunken)
        layout.addWidget(_sep2)

        # 表格（QTableView）
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setItemDelegate(_CellDelegate(self._table))
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(False)
        for i in range(COL_COUNT):
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
        self._table.clicked.connect(self._on_cell_clicked)
        self._table.doubleClicked.connect(self._on_cell_double_clicked)
        # 编辑提交后触发跳转信号
        self._model.dataChanged.connect(self._on_model_data_changed)
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

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def _on_model_data_changed(self, top_left, bottom_right, roles):
        """模型数据变化 —— 仅用户编辑提交（显式 EditRole）触发跳转信号。

        P1-3 修复：程序化更新（流式纠错、搜索替换、置信度刷新）此前 roles 为空
        命中旧分支 → 逐字触发视频 seek；现仅 delegate 提交（setData 带 EditRole）跳转。
        """
        if top_left.column() in EDITABLE_COLS and any(int(r) == int(Qt.ItemDataRole.EditRole) for r in roles):
            self.cell_edit_activated.emit(top_left.row())

    def _on_selection_changed(self):
        """选中行变化时更新计数栏。"""
        count = len(self._model._checked_rows)
        if count == 0:
            self._selection_label.setText(_("未选中任何行"))
            self._selection_label.setStyleSheet("color: #78909c; font-size: 12px;")
        else:
            self._selection_label.setText(f"已选中 {count} 行")
            self._selection_label.setStyleSheet("color: #42a5f5; font-size: 12px; font-weight: bold;")

    def get_selected_rows(self) -> set[int]:
        """返回当前选中的行号集合。"""
        return set(self._model._checked_rows)

    def select_all(self, checked: bool = True):
        """全选/全不选。"""
        self._model.set_all_checked(checked)
        self._on_selection_changed()

    def _on_select_all_toggled(self, checked: bool):
        self._model.set_all_checked(checked)
        self._on_selection_changed()

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

    def _set_filter_button(self, row: int):
        """为指定行设置过滤按钮（col 8）。"""
        self._table.setIndexWidget(self._model.index(row, COL_FILTER), self._make_filter_btn(row))

    def add_result(
        self,
        time_str: str,
        region: str,
        engine: str,
        raw_text: str,
        confidence: float = 0.0,
        time_sec: float = 0.0,
        end_sec: float = 0.0,
        sorted_insert: bool = False,
    ) -> int:
        result_item = {
            "time_sec": time_sec,
            "end_sec": end_sec,
            "time": time_str,
            "region": region,
            "engine": engine,
            "raw": raw_text,
            "segmented": "",
            "corrected": "",
            "confidence": confidence,
        }

        if sorted_insert:
            # 按时间顺序插入（P2-2 性能：bisect_right 替代线性扫描 O(n²)；
            # right 语义与原循环一致——等值时间戳插到同值之后）
            # 注意：本环境 bisect 的 key 只作用于序列元素，x 需手动应用
            def _ts_key(d):
                return d.get("time_sec", 0.0) or 0.0

            insert_pos = bisect.bisect_right(self._results, _ts_key(result_item), key=_ts_key)
            self._model.beginInsertRows(QModelIndex(), insert_pos, insert_pos)
            self._results.insert(insert_pos, result_item)
            self._model.endInsertRows()
            row = insert_pos
        else:
            # 追加到末尾
            row = len(self._results)
            self._model.beginInsertRows(QModelIndex(), row, row)
            self._results.append(result_item)
            self._model.endInsertRows()

        # 过滤按钮列（col 8）
        self._set_filter_button(row)

        self._update_count()
        # 惰性滚动到底：QTableView scrollToBottom 为 O(n)，流式逐条添加时合并为一次
        QTimer.singleShot(0, self._table.scrollToBottom)
        return row

    def update_correction(self, row: int, corrected_text: str):
        """更新润色结果列（col 6）。"""
        if 0 <= row < len(self._results):
            self._results[row]["corrected"] = corrected_text
            self._model.notify_row(row, COL_CORRECTED)

    def update_correction_result(self, row: int, corrected_text: str):
        """更新纠错结果列（col 5）。"""
        if 0 <= row < len(self._results):
            self._results[row]["segmented"] = corrected_text
            self._model.notify_row(row, COL_SEGMENTED)

    def update_confidence(self, row: int, confidence: float):
        if 0 <= row < len(self._results):
            self._results[row]["confidence"] = confidence
            self._model.notify_row(row, COL_CONFIDENCE)

    def clear_results(self):
        self._model.beginResetModel()
        self._results.clear()
        self._model.clear_checked()
        self._model.endResetModel()
        self._is_templated = False
        self._update_count()

    def clear_by_type(self, region_name: str = "", engine_name: str = ""):
        if not region_name and not engine_name:
            self.clear_results()
            return
        self._model.beginResetModel()
        self._results[:] = [
            r
            for r in self._results
            if (region_name and r.get("region", "") != region_name)
            or (engine_name and r.get("engine", "") != engine_name)
            or (not region_name and not engine_name)
        ]
        self._model.clear_checked()
        self._model.endResetModel()
        self._refresh_filter_buttons()
        self._update_count()

    def _refresh_filter_buttons(self):
        """重建全部过滤按钮（行索引变化后）。"""
        for row in range(len(self._results)):
            self._set_filter_button(row)

    def _rebuild_table_rows(self, new_results: list):
        self._model.beginResetModel()
        self._results[:] = new_results
        self._model.clear_checked()
        self._model.endResetModel()
        self._refresh_filter_buttons()
        self._update_count()

    def sort_by_time(self):
        n = len(self._results)
        if n <= 1:
            return
        indices = list(range(n))
        indices.sort(
            key=lambda i: (
                self._results[i].get("time_sec", 0.0) or 0.0,
                self._results[i].get("region", ""),
            )
        )
        self._rebuild_table_rows([self._results[i] for i in indices])

    def sort_by_order(self, region_order: str = ""):
        n = len(self._results)
        if n <= 1:
            return
        self._is_templated = bool(region_order)
        all_region_names = set(r.get("region", "") for r in self._results)
        from collections import OrderedDict

        time_groups: dict[float, dict] = OrderedDict()
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
                            output_line = ""
                            matched = False
                            break
                        output_line = output_line.replace(rname, content)
                        matched = True
                if matched and output_line.strip():
                    time_str = "--:--"
                    src = None
                    for rn in all_region_names:
                        if rn in template and rn in group:
                            time_str = group[rn].get("time", "--:--")
                            src = group[rn]
                            break
                    seg = src.get("segmented", "") if src else ""
                    corr = src.get("corrected", "") if src else ""
                    new_results.append(
                        {
                            "time_sec": ts,
                            "end_sec": src.get("end_sec", ts + 3.0) if src else ts + 3.0,
                            "time": time_str,
                            "region": output_line,
                            "engine": src.get("engine", "") if src else "",
                            "raw": output_line,
                            "segmented": seg,
                            "corrected": corr,
                            "confidence": src.get("confidence", 0.0) if src else 0.0,
                        }
                    )
        self._rebuild_table_rows(new_results)

    def get_results(self) -> list:
        return list(self._results)

    def get_polished_results(
        self, post_sim_dedup: bool = True, post_sim_threshold: float = 0.9, post_min_text_len: int = 2
    ) -> list:
        if getattr(self, "_is_templated", False):
            return list(self._results)
        from core.result_processor import polish_results

        raw_list = [(r["time_sec"], r["time"], r["region"], r["engine"], r["raw"]) for r in self._results]
        polished = polish_results(
            raw_list,
            post_sim_dedup=post_sim_dedup,
            post_sim_threshold=post_sim_threshold,
            post_min_text_len=post_min_text_len,
        )
        # 携带 corrected / segmented / end_sec 字段：用 (time_sec, region, raw) 匹配回原始结果
        correction_map = {}
        segmented_map = {}
        endsec_map = {}
        for r in self._results:
            corr = r.get("corrected", "").strip()
            seg = r.get("segmented", "").strip()
            end_sec = r.get("end_sec", 0.0)
            key = (round(r.get("time_sec", 0.0) or 0.0, 1), r.get("region", ""), r.get("raw", ""))
            if corr:
                correction_map[key] = corr
            if seg:
                segmented_map[key] = seg
            if end_sec:
                endsec_map[key] = end_sec
        for p in polished:
            key = (round(p.get("time_sec", 0.0) or 0.0, 1), p.get("region", ""), p.get("raw", ""))
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

    def _on_cell_clicked(self, index: QModelIndex):
        # P1-3：仅可编辑列点击触发跳转（复选框列/过滤按钮列不再误跳）
        if index.column() in EDITABLE_COLS:
            self.cell_edit_activated.emit(index.row())

    def _on_cell_double_clicked(self, index: QModelIndex):
        if index.column() in EDITABLE_COLS:
            self._table.edit(index)

    def _on_export(self, fmt: str):
        if not self._results:
            QMessageBox.information(self, _("提示"), _("暂无识别结果可导出。"))
            return
        file_path, _filter = QFileDialog.getSaveFileName(
            self, f"导出为 {fmt.upper()}", "", f"{fmt.upper()} Files (*.{fmt});;All Files (*.*)"
        )
        if file_path:
            self.export_requested.emit(fmt, file_path)

    def delete_by_filter(self, matcher) -> int:
        """删除匹配的结果行。matcher(raw, corrected) -> bool。返回删除数。"""
        kept = []
        deleted = 0
        for r in self._results:
            if matcher(r.get("raw", ""), r.get("corrected", "")):
                deleted += 1
            else:
                kept.append(r)
        if deleted:
            self._rebuild_table_rows(kept)
        return deleted

    def remove_rows_by_predicate(self, pred) -> int:
        """删除满足 pred(dict) 的行，返回删除数（P0-T6 修复）。

        统一走 _rebuild_table_rows（beginResetModel 包裹），避免外部直接
        del _results 导致模型 rowCount 与视图错位；同时自动清理选中状态。
        """
        kept = [r for r in self._results if not pred(r)]
        deleted = len(self._results) - len(kept)
        if deleted:
            self._rebuild_table_rows(kept)
        return deleted

    def _on_delete_filtered(self):
        self.delete_filtered_requested.emit()

    def _update_count(self):
        self._count_label.setText(f"({len(self._results)} 条)")

    def _retranslate_strings(self):
        """重新翻译所有用户可见字符串（语言切换时调用）。"""
        self._table.setHorizontalHeaderLabels(
            [_("✓"), _("时间戳"), _("区域"), _("引擎"), _("原始结果"), _("纠错结果"), _("润色结果"), _("置信度"), ""]
        )
        self._select_all_cb.setText(_("全选"))
        self._count_label.setText(_("(0 条)"))
        self._search_edit.setPlaceholderText(_("搜索..."))
        self._replace_edit.setPlaceholderText(_("替换为..."))
        self._selection_label.setText(_("未选中任何行"))
        self._btn_toggle_search.setToolTip(_("打开/关闭搜索替换"))
        self._batch_label.setText(_("📋 队列:"))

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
        return [
            i
            for i, r in enumerate(self._results)
            if kw in r.get("raw", "").lower()
            or kw in r.get("segmented", "").lower()
            or kw in r.get("corrected", "").lower()
        ]

    def _highlight_rows(self, matches: list[int], current: int = -1):
        self._model.set_highlight(set(matches), current)
        if 0 <= current < len(self._results):
            self._table.selectRow(current)

    def _on_search_text_changed(self, text: str):
        if not text.strip():
            self._highlight_rows([])
            self._search_count_label.setText("")
            self._search_matches = []
            self._search_current_idx = -1
            return
        self._search_matches = self._find_all_matches(text)
        self._search_current_idx = 0 if self._search_matches else -1
        if self._search_matches:
            self._highlight_rows(self._search_matches, self._search_matches[0])
            self._search_count_label.setText(f"{self._search_current_idx + 1}/{len(self._search_matches)}")
        else:
            self._highlight_rows([])
            self._search_count_label.setText("无匹配")

    def _on_search_prev(self):
        if not self._search_matches:
            return
        self._search_current_idx = (self._search_current_idx - 1) % len(self._search_matches)
        row = self._search_matches[self._search_current_idx]
        self._highlight_rows(self._search_matches, row)
        self._search_count_label.setText(f"{self._search_current_idx + 1}/{len(self._search_matches)}")

    def _on_search_next(self):
        if not self._search_matches:
            return
        self._search_current_idx = (self._search_current_idx + 1) % len(self._search_matches)
        row = self._search_matches[self._search_current_idx]
        self._highlight_rows(self._search_matches, row)
        self._search_count_label.setText(f"{self._search_current_idx + 1}/{len(self._search_matches)}")

    def _on_replace_current(self):
        if not self._search_matches:
            return
        row = self._search_matches[self._search_current_idx]
        text = self._replace_edit.text()
        r = self._results[row]
        r["raw"] = r.get("raw", "").replace(self._search_edit.text(), text, 1)
        self._model.notify_row(row, COL_RAW)
        # 重新搜索
        self._on_search_text_changed(self._search_edit.text())

    def _on_replace_all(self):
        if not self._search_matches:
            return
        old = self._search_edit.text()
        new = self._replace_edit.text()
        count = 0
        for row in self._search_matches:
            r = self._results[row]
            raw = r.get("raw", "")
            if old in raw:
                r["raw"] = raw.replace(old, new)
                count += 1
        if count:
            self._model.notify_all()
            self._on_search_text_changed(old)
