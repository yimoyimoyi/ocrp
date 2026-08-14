"""结果表格模型化（4c.1）单元测试 —— 保护核心行为防回归。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QStyleOptionViewItem, QTextEdit

from ui.result_table import ResultTableWidget


@pytest.fixture(scope="module")
def app():
    _app = QApplication.instance() or QApplication([])
    yield _app


@pytest.fixture()
def table(app):
    return ResultTableWidget()


def _add(table, i: int, region="region1", engine="paddleocr", text=None):
    return table.add_result(
        f"{i // 60:02d}:{i % 60:02d}",
        region,
        engine,
        text or f"测试文本行 {i}",
        0.95,
        time_sec=float(i),
    )


class TestAddAndUpdate:
    def test_add_result_appends(self, table):
        _add(table, 0)
        _add(table, 1)
        assert table._table.model().rowCount() == 2
        assert table._results[0]["raw"] == "测试文本行 0"

    def test_add_result_sorted_insert(self, table):
        table.add_result("00:30", "r1", "eng", "晚", 0.9, time_sec=30.0, sorted_insert=True)
        table.add_result("00:10", "r1", "eng", "早", 0.9, time_sec=10.0, sorted_insert=True)
        assert table._results[0]["time_sec"] == 10.0
        assert table._results[1]["time_sec"] == 30.0

    def test_update_correction(self, table):
        _add(table, 0)
        table.update_correction(0, "润色X")
        table.update_correction_result(0, "纠错Y")
        table.update_confidence(0, 0.88)
        assert table._results[0]["corrected"] == "润色X"
        assert table._results[0]["segmented"] == "纠错Y"
        assert table._results[0]["confidence"] == 0.88
        # 模型数据同步
        assert table._table.model().index(0, 6).data(Qt.ItemDataRole.DisplayRole) == "润色X"


class TestCheckbox:
    def test_check_state_role(self, table):
        _add(table, 0)
        idx = table._table.model().index(0, 0)
        assert idx.data(Qt.ItemDataRole.CheckStateRole) == Qt.Unchecked
        table._table.model().setData(idx, Qt.Checked, Qt.ItemDataRole.CheckStateRole)
        assert 0 in table.get_selected_rows()
        assert idx.data(Qt.ItemDataRole.CheckStateRole) == Qt.Checked

    def test_select_all(self, table):
        for i in range(5):
            _add(table, i)
        table.select_all(True)
        assert len(table.get_selected_rows()) == 5
        table.select_all(False)
        assert table.get_selected_rows() == set()


class TestSearchReplace:
    def test_search_highlight(self, table):
        for i in range(10):
            _add(table, i, text=f"内容{i}")
        table._on_search_text_changed("内容5")
        assert 5 in table._search_matches
        assert table._table.model()._match_rows == {5}

    def test_replace_all(self, table):
        for i in range(5):
            _add(table, i, text=f"旧文本{i}")
        table._search_edit.setText("旧文本")
        table._on_search_text_changed("旧文本")
        table._replace_edit.setText("新文本")
        table._on_replace_all()
        assert all("新文本" in r["raw"] for r in table._results)

    def test_replace_all_multi_columns(self, table):
        """验证全部替换时 raw、segmented、corrected 三列同步更新。"""
        _add(table, 0, text="错别字原文")
        table.update_correction_result(0, "错别字纠错")
        table.update_correction(0, "错别字润色")
        table._search_edit.setText("错别字")
        table._on_search_text_changed("错别字")
        table._replace_edit.setText("正确字")
        table._on_replace_all()
        assert table._results[0]["raw"] == "正确字原文"
        assert table._results[0]["segmented"] == "正确字纠错"
        assert table._results[0]["corrected"] == "正确字润色"

    def test_filter_column_action_delegate_no_index_widgets(self, table):
        """验证第 8 列使用 Delegate 渲染，无真实 QWidget 挂载（内存与性能保护）。"""
        for i in range(5):
            _add(table, i)
        for i in range(5):
            idx = table._table.model().index(i, 8)
            assert table._table.indexWidget(idx) is None


class TestSortAndDelete:
    def test_sort_by_time(self, table):
        table.add_result("00:30", "r1", "eng", "晚", 0.9, time_sec=30.0)
        table.add_result("00:10", "r1", "eng", "早", 0.9, time_sec=10.0)
        table.sort_by_time()
        assert table._results[0]["time_sec"] == 10.0

    def test_delete_by_filter(self, table):
        for i in range(5):
            _add(table, i, text=f"关键词{i}" if i % 2 == 0 else f"普通{i}")
        deleted = table.delete_by_filter(lambda raw, corr: "关键词" in raw)
        assert deleted == 3
        assert table._table.model().rowCount() == 2

    def test_clear_results(self, table):
        for i in range(3):
            _add(table, i)
        table.clear_results()
        assert table._table.model().rowCount() == 0
        assert table._results == []


class TestInlineEdit:
    """delegate 编辑流程：纯文本语义、不泄漏 HTML、未编辑提交不清空。"""

    def test_edit_flow_short_text(self, table):
        _add(table, 0, text="短文本")
        idx = table._table.model().index(0, 4)
        delegate = table._table.itemDelegate()
        editor = delegate.createEditor(None, QStyleOptionViewItem(), idx)
        assert isinstance(editor, QLineEdit)
        delegate.setEditorData(editor, idx)
        assert editor.text() == "短文本"
        # 未编辑提交 → 内容保持
        delegate.setModelData(editor, table._table.model(), idx)
        assert table._results[0]["raw"] == "短文本"

    def test_edit_flow_long_text_no_html_leak(self, table):
        long_text = "很长的测试文本内容内容" * 6  # >60 字符触发 QTextEdit 分支
        _add(table, 0, text=long_text)
        idx = table._table.model().index(0, 4)
        delegate = table._table.itemDelegate()
        editor = delegate.createEditor(None, QStyleOptionViewItem(), idx)
        assert isinstance(editor, QTextEdit)
        delegate.setEditorData(editor, idx)
        assert editor.toPlainText() == long_text
        delegate.setModelData(editor, table._table.model(), idx)
        assert table._results[0]["raw"] == long_text
        assert "<!DOCTYPE" not in table._results[0]["raw"]
        # 编辑后回写
        editor.setPlainText("修改后内容")
        delegate.setModelData(editor, table._table.model(), idx)
        assert table._results[0]["raw"] == "修改后内容"

    def test_edit_role_data_matches_display(self, table):
        _add(table, 0, text="角色数据")
        idx = table._table.model().index(0, 4)
        assert idx.data(Qt.ItemDataRole.EditRole) == idx.data(Qt.ItemDataRole.DisplayRole) == "角色数据"

    def test_editor_background_opaque(self, table):
        """编辑时遮挡 view 文本，避免重叠渲染。"""
        _add(table, 0, text="短文本")
        idx = table._table.model().index(0, 4)
        editor = table._table.itemDelegate().createEditor(None, QStyleOptionViewItem(), idx)
        assert editor.autoFillBackground() is True
        base = editor.palette().color(editor.palette().ColorRole.Base)
        assert base.alpha() == 255


class TestSyncPlayPosition:
    """测试视频播放位置与字幕行实时高亮与跟随。"""

    def test_sync_highlights_matching_row(self, table):
        table.add_result("00:01", "r1", "eng", "第一句", 0.9, time_sec=1.0, end_sec=3.0)
        table.add_result("00:04", "r1", "eng", "第二句", 0.9, time_sec=4.0, end_sec=6.0)

        # 2.0s 在第一句区间内
        text = table.sync_play_position(2.0, auto_scroll=False)
        assert text == "第一句"
        assert table._model._playing_row == 0
        assert table._table.model().index(0, 4).data(Qt.ItemDataRole.BackgroundRole) is not None

        # 5.0s 在第二句区间内
        text2 = table.sync_play_position(5.0, auto_scroll=False)
        assert text2 == "第二句"
        assert table._model._playing_row == 1

        # 10.0s 超出区间
        text_out = table.sync_play_position(10.0, auto_scroll=False)
        assert text_out == ""
        assert table._model._playing_row == -1


class TestHeaderData:
    """测试表头标题与列显示名称。"""

    def test_horizontal_header_titles(self, table):
        model = table._table.model()
        assert model.headerData(1, Qt.Horizontal, Qt.ItemDataRole.DisplayRole) == "时间"
        assert model.headerData(2, Qt.Horizontal, Qt.ItemDataRole.DisplayRole) == "区域"
        assert model.headerData(3, Qt.Horizontal, Qt.ItemDataRole.DisplayRole) == "引擎"
        assert model.headerData(4, Qt.Horizontal, Qt.ItemDataRole.DisplayRole) == "原始识别"
        assert model.headerData(5, Qt.Horizontal, Qt.ItemDataRole.DisplayRole) == "纠错结果"
        assert model.headerData(6, Qt.Horizontal, Qt.ItemDataRole.DisplayRole) == "润色结果"
        assert model.headerData(7, Qt.Horizontal, Qt.ItemDataRole.DisplayRole) == "置信度"
        assert model.headerData(8, Qt.Horizontal, Qt.ItemDataRole.DisplayRole) == "操作"


class TestBatchOperations:
    """测试批量删除与反选操作。"""

    def test_batch_delete_checked(self, table):
        for i in range(5):
            _add(table, i, text=f"条目{i}")
        # 勾选第 0, 2, 4 行
        table._model._checked_rows = {0, 2, 4}
        table._on_batch_delete_checked()
        assert len(table._results) == 2
        assert table._results[0]["raw"] == "条目1"
        assert table._results[1]["raw"] == "条目3"

    def test_invert_checked(self, table):
        for i in range(4):
            _add(table, i)
        table._model._checked_rows = {1, 3}
        table._on_invert_checked()
        assert table._model._checked_rows == {0, 2}
