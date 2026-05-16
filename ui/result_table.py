# -*- coding: utf-8 -*-
"""结果表格组件 —— QTableWidget 显示 OCR 识别结果。"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QSizePolicy, QLineEdit, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QEvent
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
    COL_WIDTHS = [70, 70, 70, 200, 200, 55, 36]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: List[dict] = []
        self._is_templated: bool = False
        self._search_matches: List[int] = []
        self._search_current_idx: int = -1
        self._init_ui()
        # 表格首次显示时做自适应列宽，之后监听 resize
        self._table.installEventFilter(self)

    def eventFilter(self, obj, event):
        """拦截表格 resize 事件做流式列宽分配。"""
        if obj is self._table and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._adjust_column_widths)
        return super().eventFilter(obj, event)

    def _adjust_column_widths(self):
        """按比例分配所有列的宽度（最后一列固定），完全自适应。"""
        n = len(self.COLUMNS)
        if self._table.columnCount() < n:
            return
        total = self._table.viewport().width()
        btn_w = self.COL_WIDTHS[-1]
        available = total - btn_w - 4  # 留 4px 余量
        if available <= 0:
            return
        sum_ratios = sum(self.COL_WIDTHS[:-1])
        self._table.blockSignals(True)
        for i in range(n - 1):
            w = max(int(available * self.COL_WIDTHS[i] / sum_ratios), 30)
            self._table.setColumnWidth(i, w)
        self._table.setColumnWidth(n - 1, btn_w)
        self._table.blockSignals(False)

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

        # 批量队列显示
        header.addSpacing(8)
        self._batch_sep = QFrame()
        self._batch_sep.setFrameShape(QFrame.VLine)
        self._batch_sep.setFixedHeight(18)
        header.addWidget(self._batch_sep)
        header.addWidget(QLabel("📋 队列:"))
        self._batch_count_label = QLabel("(空)")
        header.addWidget(self._batch_count_label)

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

        # 全部使用交互式 → 由 _adjust_column_widths 按比例动态分配
        for i in range(len(self.COLUMNS)):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)

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

    def clear_by_type(self, region_name: str = "", engine_name: str = ""):
        """选择性地清除结果行。

        region_name="语音": 仅清除 ASR 结果
        engine_name="whisperx": 仅清除 whisperx 引擎结果
        两者都不传则清除所有（等效 clear_results）。
        两者都传则必须同时匹配。
        """
        if not region_name and not engine_name:
            self.clear_results()
            return

        removed = 0
        n = len(self._results)
        for i in range(n - 1, -1, -1):
            r = self._results[i]
            match_region = not region_name or r.get("region", "") == region_name
            match_engine = not engine_name or r.get("engine", "") == engine_name
            if match_region and match_engine:
                self._table.removeRow(i)
                del self._results[i]
                removed += 1
        if removed:
            print(f"[ResultTable] clear_by_type(region={region_name!r}, engine={engine_name!r}): removed {removed} rows, {len(self._results)} remaining")
        self._update_count()

    def sort_by_time(self):
        """按时间戳升序排列所有结果行（不改变内容）。"""
        n = self._table.rowCount()
        if n <= 1:
            return

        # 按 time_sec 升序，相同时间按 region 排序
        indices = list(range(n))
        indices.sort(key=lambda i: (
            self._results[i].get("time_sec", 0.0) or 0.0,
            self._results[i].get("region", ""),
        ))

        # 原地重排 _results
        old = list(self._results)

        self._table.blockSignals(True)
        self._table.setRowCount(0)

        new_results = []
        for i, idx in enumerate(indices):
            r = old[idx]
            new_results.append(r)
            self._table.insertRow(i)
            for col, val in enumerate([
                r.get("time", ""), r.get("region", ""), r.get("engine", ""),
                r.get("raw", ""),
                r.get("corrected", ""),
                f"{r.get('confidence', 0.0) or 0.0:.0%}" if r.get('confidence') else "-",
            ]):
                item = QTableWidgetItem(val)
                if col == 4 and r.get("corrected"):
                    item.setForeground(QBrush(QColor("#66cc66")))
                elif col == 4 and not r.get("corrected"):
                    item.setForeground(QBrush(QColor("#555555")))
                self._table.setItem(i, col, item)

            # 恢复过滤按钮
            btn = QPushButton("+")
            btn.setToolTip("将此条内容加入过滤器")
            btn.setMaximumWidth(30)
            btn.setMaximumHeight(22)
            btn.clicked.connect(lambda checked, rb=i: self._on_filter_row(rb))
            self._table.setCellWidget(i, self._table.columnCount() - 1, btn)

        self._results = new_results
        self._table.blockSignals(False)

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

    def set_batch_count(self, count: int, total_size: int = 0):
        """更新批量队列显示。"""
        if count == 0:
            self._batch_count_label.setText("(空)")
        elif total_size > 0:
            self._batch_count_label.setText(f"{count}/{total_size} 个文件")
        else:
            self._batch_count_label.setText(f"{count} 个文件")

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
