# -*- coding: utf-8 -*-
"""结果表格组件 —— QTableWidget 显示 OCR 识别结果。"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QSizePolicy, QLineEdit, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QBrush
from typing import List, Tuple, Optional


class ResultTableWidget(QWidget):
    """识别结果表格。

    Columns: 时间戳 | 区域 | 引擎 | 原始结果 | 纠错结果 | 置信度

    Signals:
        export_requested(str, str): (format, file_path)
    """

    export_requested = pyqtSignal(str, str)

    filter_requested = pyqtSignal(str)
    delete_filtered_requested = pyqtSignal()  # 一键删除带关键词的结果
    cell_edit_activated = pyqtSignal(int)  # row → time_sec jump
    COLUMNS = ["时间戳", "区域", "引擎", "原始结果", "纠错结果", "置信度", ""]
    COL_WIDTHS = [80, 100, 100, 260, 260, 60, 40]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: List[dict] = []  # [{time_sec, time, region, engine, raw, corrected, confidence}, ...]
        self._is_templated: bool = False
        self._search_matches: List[int] = []
        self._search_current_idx: int = -1
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 标题栏
        header = QHBoxLayout()
        header.setSpacing(2)
        title = QLabel("📋 识别结果")
        title.setObjectName("resultTitle")
        header.addWidget(title)

        self._count_label = QLabel("(0 条)")
        self._count_label.setObjectName("countLabel")
        header.addWidget(self._count_label)
        header.addStretch()

        # 导出按钮（QToolButton 紧凑型）
        for fmt, short_label, tooltip_text in [
            ("txt", "TXT", "导出为 TXT"),
            ("json", "JSON", "导出为 JSON"),
            ("csv", "CSV", "导出为 CSV"),
            ("srt", "SRT", "导出为 SRT 字幕"),
        ]:
            btn = QToolButton()
            btn.setText(short_label)
            btn.setToolTip(tooltip_text)
            btn.setAutoRaise(True)
            btn.setMinimumHeight(22)
            btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            btn.clicked.connect(lambda checked, f=fmt: self._on_export(f))
            header.addWidget(btn)
            setattr(self, f"_btn_export_{fmt}", btn)

        self._btn_clear = QToolButton()
        self._btn_clear.setText("清空")
        self._btn_clear.setToolTip("清空所有识别结果")
        self._btn_clear.setAutoRaise(True)
        self._btn_clear.setMinimumHeight(22)
        self._btn_clear.clicked.connect(self.clear_results)
        header.addWidget(self._btn_clear)

        self._btn_delete_filtered = QToolButton()
        self._btn_delete_filtered.setText("🗑 删过滤")
        self._btn_delete_filtered.setToolTip("删除所有包含过滤器关键词的结果行")
        self._btn_delete_filtered.setAutoRaise(True)
        self._btn_delete_filtered.setMinimumHeight(22)
        self._btn_delete_filtered.clicked.connect(self._on_delete_filtered)
        header.addWidget(self._btn_delete_filtered)

        layout.addLayout(header)

        # ── 搜索/替换条 ──
        self._search_bar = QFrame()
        self._search_bar.setObjectName("searchBar")
        self._search_bar.setFrameShape(QFrame.StyledPanel)
        self._search_bar.setVisible(False)
        sbl = QHBoxLayout(self._search_bar)
        sbl.setContentsMargins(4, 2, 4, 2); sbl.setSpacing(4)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索...")
        self._search_edit.setMaximumHeight(22)
        self._search_edit.setMinimumWidth(120)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        sbl.addWidget(self._search_edit)

        self._search_prev_btn = QToolButton()
        self._search_prev_btn.setText("▲")
        self._search_prev_btn.setToolTip("上一个匹配")
        self._search_prev_btn.clicked.connect(self._on_search_prev)
        sbl.addWidget(self._search_prev_btn)

        self._search_next_btn = QToolButton()
        self._search_next_btn.setText("▼")
        self._search_next_btn.setToolTip("下一个匹配")
        self._search_next_btn.clicked.connect(self._on_search_next)
        sbl.addWidget(self._search_next_btn)

        self._search_count_label = QLabel("")
        self._search_count_label.setMinimumWidth(60)
        sbl.addWidget(self._search_count_label)

        sbl.addSpacing(8)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("替换为...")
        self._replace_edit.setMaximumHeight(22)
        self._replace_edit.setMinimumWidth(100)
        sbl.addWidget(self._replace_edit)

        self._replace_btn = QToolButton()
        self._replace_btn.setText("替换")
        self._replace_btn.setToolTip("替换当前选中结果")
        self._replace_btn.clicked.connect(self._on_replace_current)
        sbl.addWidget(self._replace_btn)

        self._replace_all_btn = QToolButton()
        self._replace_all_btn.setText("全部替换")
        self._replace_all_btn.setToolTip("替换所有匹配结果")
        self._replace_all_btn.clicked.connect(self._on_replace_all)
        sbl.addWidget(self._replace_all_btn)

        sbl.addStretch()

        self._btn_toggle_search = QToolButton()
        self._btn_toggle_search.setText("🔍")
        self._btn_toggle_search.setToolTip("打开/关闭搜索替换")
        self._btn_toggle_search.setCheckable(True)
        self._btn_toggle_search.toggled.connect(self._on_toggle_search)
        header.addWidget(self._btn_toggle_search)

        layout.addWidget(self._search_bar)

        # ── 表格 ──
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        for i, w in enumerate(self.COL_WIDTHS):
            if i not in (3, 4):
                self._table.setColumnWidth(i, w)

        self._table.setColumnWidth(len(self.COLUMNS) - 1, 40)

        self._table.verticalHeader().setVisible(False)
        # 编辑单元格或点击行时触发跳转信号
        self._table.cellChanged.connect(self._on_cell_changed)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table)

    # ── 公共方法 ─────────────────────────────────────────
    def add_result(
        self,
        time_str: str,
        region: str,
        engine: str,
        raw_text: str,
        confidence: float = 0.0,
        time_sec: float = 0.0,
        end_sec: float = 0.0
    ) -> int:
        """添加一条识别结果，返回行索引。"""
        row_data = {
            "time_sec": time_sec,
            "end_sec": end_sec,
            "time": time_str,
            "region": region,
            "engine": engine,
            "raw": raw_text,
            "corrected": "",
            "confidence": confidence
        }
        self._results.append(row_data)
        row = self._table.rowCount()

        # 阻止 cellChanged 信号，避免程序化填充触发 seek_to
        self._table.blockSignals(True)
        self._table.insertRow(row)

        items = [
            (time_str, ""),
            (region, ""),
            (engine, ""),
            (raw_text, ""),
            ("", "#555555"),
            (f"{confidence:.0%}" if confidence else "-", ""),
        ]
        for col, (text, fg) in enumerate(items):
            item = QTableWidgetItem(text)
            if fg:
                item.setForeground(QBrush(QColor(fg)))
            self._table.setItem(row, col, item)

        # 行内「+过滤」按钮
        btn = QPushButton("+")
        btn.setToolTip("将此条内容加入过滤器")
        btn.setMaximumWidth(30)
        btn.setMaximumHeight(22)
        btn.clicked.connect(lambda checked, r=row: self._on_filter_row(r))
        self._table.setCellWidget(row, len(self.COLUMNS) - 1, btn)

        self._table.blockSignals(False)
        self._update_count()
        return row

    def _on_filter_row(self, row: int):
        if 0 <= row < len(self._results):
            self.filter_requested.emit(self._results[row]["raw"])

    def update_correction(self, row: int, corrected_text: str):
        """更新指定行的纠错结果（阻止信号避免触发 seek_to）。"""
        if 0 <= row < len(self._results):
            self._results[row]["corrected"] = corrected_text
            self._table.blockSignals(True)
            item = QTableWidgetItem(corrected_text)
            item.setForeground(QBrush(QColor("#66cc66")))
            self._table.setItem(row, 4, item)
            self._table.blockSignals(False)

    def update_confidence(self, row: int, confidence: float):
        """更新指定行的置信度（阻止信号避免触发 seek_to）。"""
        if 0 <= row < len(self._results):
            self._results[row]["confidence"] = confidence
            self._table.blockSignals(True)
            item = QTableWidgetItem(f"{confidence:.0%}")
            self._table.setItem(row, 5, item)
            self._table.blockSignals(False)

    def clear_results(self):
        """清空所有结果。"""
        self._results.clear()
        self._table.setRowCount(0)
        self._is_templated = False
        self._update_count()

    def sort_by_order(self, region_order: str = ""):
        """按排序模板对表格行排序 + 替换区域名 token 为内容。

        region_order: 每行一个模板，如:
            区域1+10086
            111：区域2
        区域名 token 会被替换为对应识别内容。
        同一时间戳的内容按模板行聚合。
        """
        n = self._table.rowCount()
        if n <= 1:
            return

        self._is_templated = bool(region_order)  # 标记已模板化

        # 收集所有区域名
        all_region_names = set(r.get("region", "") for r in self._results)

        # 按时间分组
        from collections import OrderedDict
        time_groups = OrderedDict()
        for r in self._results:
            ts = r.get("time_sec", 0.0) or 0.0
            # 四舍五入到 0.1 秒避免浮点误差
            ts_key = round(ts, 1)
            if ts_key not in time_groups:
                time_groups[ts_key] = {}
            time_groups[ts_key][r.get("region", "")] = r

        # 解析模板行
        template_lines = []
        if region_order:
            for line in region_order.splitlines():
                line = line.strip()
                if not line:
                    continue
                template_lines.append(line)

        # 按模板生成新的结果列表
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
                            # 内容为空 → 跳过不添加此行
                            output_line = ""
                            matched = False
                            break
                        output_line = output_line.replace(rname, content)
                        matched = True

                if matched and output_line.strip():
                    # 找第一个匹配的时间字符串
                    time_str = "--:--"
                    for rname in all_region_names:
                        if rname in template and rname in group:
                            time_str = group[rname].get("time", "--:--")
                            break

                    # 从第一个匹配的源结果继承 confidence + end_sec
                    src = None
                    for rname in all_region_names:
                        if rname in template and rname in group:
                            src = group[rname]
                            break
                    new_results.append({
                        "time_sec": ts,
                        "end_sec": src.get("end_sec", ts + 3.0) if src else ts + 3.0,
                        "time": time_str,
                        "region": output_line,
                        "engine": src.get("engine", "") if src else "",
                        "raw": output_line,
                        "corrected": "",
                        "confidence": src.get("confidence", 0.0) if src else 0.0,
                    })

        # 如果没有模板行，按时间和区域排序
        if not template_lines:
            indices = list(range(n))
            indices.sort(key=lambda idx: (
                self._results[idx].get("time_sec", 0.0) or 0.0,
                self._results[idx].get("region", ""),
            ))
            old_results = list(self._results)
            new_results = [old_results[i] for i in indices]

        # 重建表格（阻止信号避免触发 seek_to）
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._results = []

        for row_idx, item in enumerate(new_results):
            self._results.append(item)
            self._table.insertRow(row_idx)

            ts = item.get("time_sec", 0.0) or 0.0
            m, s = divmod(int(ts), 60)
            time_str = item.get("time", f"{m:02d}:{s:02d}")

            # 置信度显示
            conf_val = item.get("confidence", 0.0) or 0.0
            conf_text = f"{conf_val:.0%}" if conf_val else "-"

            items = [
                (time_str, ""),
                (item.get("region", ""), ""),
                (item.get("engine", ""), ""),
                (item.get("raw", ""), ""),
                (item.get("corrected", ""), "#555555"),
                (conf_text, ""),
            ]
            for col, (text, fg) in enumerate(items):
                widget = QTableWidgetItem(text)
                if fg:
                    widget.setForeground(QBrush(QColor(fg)))
                self._table.setItem(row_idx, col, widget)

            # 过滤按钮
            btn = QPushButton("+")
            btn.setToolTip("将此条内容加入过滤器")
            btn.setMaximumWidth(30)
            btn.setMaximumHeight(22)
            btn.clicked.connect(lambda checked, r=row_idx: self._on_filter_row(r))
            self._table.setCellWidget(row_idx, self._table.columnCount() - 1, btn)
        self._table.blockSignals(False)
        self._update_count()

    def get_results(self) -> list:
        """获取所有结果数据。"""
        return list(self._results)

    def get_polished_results(self, post_sim_threshold: float = 0.9,
                              post_min_text_len: int = 2) -> list:
        """获取经过去重过滤的精炼结果。模板化结果直接返回，不重复处理。"""
        if getattr(self, '_is_templated', False):
            return list(self._results)

        from core.result_processor import polish_results

        raw_list = []
        for r in self._results:
            raw_list.append((
                r["time_sec"], r["time"], r["region"],
                r["engine"], r["raw"]
            ))
        return polish_results(raw_list, post_sim_dedup=True,
                               post_sim_threshold=post_sim_threshold,
                               post_min_text_len=post_min_text_len)

    # ── 内部方法 ─────────────────────────────────────────
    def _on_cell_changed(self, row: int, col: int):
        """单元格内容被编辑时同步数据 + 触发跳转。"""
        if col in (3, 4):  # 原始结果 或 纠错结果 列
            if 0 <= row < len(self._results):
                text = self._table.item(row, col).text() if self._table.item(row, col) else ""
                if col == 3:
                    self._results[row]["raw"] = text
                elif col == 4:
                    self._results[row]["corrected"] = text
            self.cell_edit_activated.emit(row)

    def _on_cell_clicked(self, row: int, col: int):
        """点击结果行时触发跳转。"""
        self.cell_edit_activated.emit(row)

    # ── 搜索/替换 ─────────────────────────────────────────────

    def _on_toggle_search(self, visible: bool):
        """切换搜索/替换条显示。"""
        self._search_bar.setVisible(visible)
        if visible:
            self._search_edit.setFocus()
            self._search_edit.selectAll()

    def _find_all_matches(self, keyword: str) -> List[int]:
        """在原始结果和纠错结果中搜索关键词，返回匹配的行号列表。"""
        if not keyword.strip():
            return []
        kw = keyword.lower()
        matches = []
        for i, r in enumerate(self._results):
            raw = r.get("raw", "").lower()
            corrected = r.get("corrected", "").lower()
            if kw in raw or kw in corrected:
                matches.append(i)
        return matches

    def _highlight_rows(self, matches: List[int], current: int = -1):
        """高亮匹配行，current 为当前选中匹配项。"""
        # 清除所有高亮
        for row in range(self._table.rowCount()):
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item:
                    item.setBackground(QBrush(QColor("transparent")))

        # 高亮匹配行
        for row in matches:
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item:
                    item.setBackground(QBrush(QColor(60, 130, 60, 80)))

        # 高亮当前选中项
        if current >= 0 and current < self._table.rowCount():
            self._table.selectRow(current)
            for col in range(self._table.columnCount()):
                item = self._table.item(current, col)
                if item:
                    item.setBackground(QBrush(QColor(80, 180, 80, 140)))

    def _on_search_text_changed(self, text: str):
        """搜索文本变化时更新匹配列表。"""
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
            self._search_count_label.setText(
                f"{1}/{len(self._search_matches)}")
        else:
            self._highlight_rows([])
            self._search_count_label.setText("无匹配")

    def _on_search_next(self):
        """跳转到下一个匹配行。"""
        if not hasattr(self, '_search_matches') or not self._search_matches:
            return
        idx = (self._search_current_idx + 1) % len(self._search_matches)
        self._search_current_idx = idx
        row = self._search_matches[idx]
        self._highlight_rows(self._search_matches, row)
        self._search_count_label.setText(
            f"{idx + 1}/{len(self._search_matches)}")
        self._table.scrollToItem(self._table.item(row, 0))

    def _on_search_prev(self):
        """跳转到上一个匹配行。"""
        if not hasattr(self, '_search_matches') or not self._search_matches:
            return
        idx = (self._search_current_idx - 1) % len(self._search_matches)
        self._search_current_idx = idx
        row = self._search_matches[idx]
        self._highlight_rows(self._search_matches, row)
        self._search_count_label.setText(
            f"{idx + 1}/{len(self._search_matches)}")
        self._table.scrollToItem(self._table.item(row, 0))

    def _on_replace_current(self):
        """替换当前选中匹配行的文本。"""
        replace_text = self._replace_edit.text()
        search_text = self._search_edit.text()
        if not search_text.strip() or not hasattr(self, '_search_matches'):
            return
        if self._search_current_idx < 0 or self._search_current_idx >= len(self._search_matches):
            return
        row = self._search_matches[self._search_current_idx]
        self._do_replace_in_row(row, search_text, replace_text)

    def _on_replace_all(self):
        """替换所有匹配行的文本。"""
        replace_text = self._replace_edit.text()
        search_text = self._search_edit.text()
        if not search_text.strip() or not hasattr(self, '_search_matches'):
            return
        for row in list(self._search_matches):
            self._do_replace_in_row(row, search_text, replace_text)

    def _do_replace_in_row(self, row: int, search_text: str, replace_text: str):
        """在指定行执行替换（替换 row 的 raw 和 corrected 字段）。"""
        if row < 0 or row >= len(self._results):
            return
        # 替换原始结果列
        raw = self._results[row].get("raw", "")
        if search_text in raw:
            new_raw = raw.replace(search_text, replace_text)
            self._results[row]["raw"] = new_raw
            item = self._table.item(row, 3)
            if item:
                item.setText(new_raw)
        # 替换纠错结果列
        corrected = self._results[row].get("corrected", "")
        if search_text in corrected:
            new_corrected = corrected.replace(search_text, replace_text)
            self._results[row]["corrected"] = new_corrected
            item = self._table.item(row, 4)
            if item:
                item.setText(new_corrected)

    def _on_delete_filtered(self):
        """删除所有包含过滤器关键词的结果行。"""
        self.delete_filtered_requested.emit()

    def _update_count(self):
        self._count_label.setText(f"({self._table.rowCount()} 条)")

    def _on_export(self, fmt: str):
        """触发导出操作。"""
        if not self._results:
            QMessageBox.information(self, "提示", "暂无识别结果可导出。")
            return

        last_dir = ""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            f"导出为 {fmt.upper()}",
            last_dir,
            f"{fmt.upper()} Files (*.{fmt});;All Files (*.*)"
        )
        if file_path:
            self.export_requested.emit(fmt, file_path)
