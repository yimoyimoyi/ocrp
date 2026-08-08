"""R13 批次字体颜色显示修复回归测试。

覆盖：
- region_manager 区域列表选中态：选中项文字切换为主题强调色（item foreground
  优先于 QSS，此前区域色为白色时选中后仍是白色，选中态不可辨识）
- video_preview 区域名标签：文字颜色按背景亮度自适应（白底黑字/暗底白字），
  消除白色区域白底白字不可见问题
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui.region_manager import RegionManagerWidget
from ui.video_preview import label_text_color


def _app():
    return QApplication.instance() or QApplication([])


# ══════════════════════════════════════════════════════════════════
# region_manager 列表选中态
# ══════════════════════════════════════════════════════════════════


class TestRegionListSelectionColor:
    """R13：非选中为区域色对比度自适应，选中为区域色深色版（delegate）。"""

    @pytest.fixture(autouse=True)
    def _ensure_app(self):
        _app()

    @pytest.fixture
    def _dark_palette(self):
        """模拟暗色主题：QPalette.Base 为深色。"""
        app = QApplication.instance()
        original = app.palette()
        pal = QPalette(original)
        pal.setColor(QPalette.Window, QColor(30, 30, 30))
        pal.setColor(QPalette.Base, QColor(35, 35, 35))
        app.setPalette(pal)
        yield
        app.setPalette(original)

    @staticmethod
    def _make_widget(regions):
        w = RegionManagerWidget()
        w.regions = regions
        return w

    def test_delegate_installed(self):
        """选中态由 _RegionItemDelegate 绘制（Qt 选中文字不走 item foreground）。"""
        from ui.region_manager import _RegionItemDelegate

        w = self._make_widget([{"name": "区域1", "color": QColor(0, 200, 100)}])
        assert isinstance(w._list_widget.itemDelegate(), _RegionItemDelegate)

    def test_foreground_always_region_adaptive(self, _dark_palette):
        """item.foreground 恒为区域色对比度自适应版（选中/未选中一致）。"""
        w = self._make_widget(
            [{"name": "区域1", "color": QColor(255, 255, 255)}, {"name": "区域2", "color": QColor(0, 200, 100)}]
        )
        item0 = w._list_widget.item(0)
        item1 = w._list_widget.item(1)
        # 选中第 0 行
        w._list_widget.setCurrentRow(0)
        w._on_selection_changed(0)
        # 暗色背景下白色区域 → 加亮可见
        assert item0.foreground().color().lightness() > 150
        assert item1.foreground().color().lightness() > 100

    def test_white_region_visible_on_light_bg(self):
        """关键回归：亮色主题下白色区域被加深，不再与背景一致。"""
        w = self._make_widget([{"name": "区域1", "color": QColor(255, 255, 255)}])
        item = w._list_widget.item(0)
        # 亮色 palette（默认）：白色区域色 → 加深为深灰，可见
        assert item.foreground().color().lightness() < 200
        assert item.foreground().color().name() != "#ffffff"

    def test_dark_region_lightened_on_dark_bg(self, _dark_palette):
        """暗色主题下深色区域色被加亮（此前深蓝区域在暗背景不可见）。"""
        w = self._make_widget([{"name": "区域1", "color": QColor(20, 20, 80)}])
        item = w._list_widget.item(0)
        assert item.foreground().color().lightness() > 120


# ══════════════════════════════════════════════════════════════════
# video_preview 区域名标签亮度自适应
# ══════════════════════════════════════════════════════════════════


class TestLabelTextColor:
    """R13：标签文字按背景亮度自适应。"""

    def test_bright_background_uses_dark_text(self):
        # 白色区域背景 → 深色文字（此前白底白字不可见）
        assert label_text_color(QColor(255, 255, 255)).name() == "#141414"

    def test_dark_background_uses_white_text(self):
        assert label_text_color(QColor(0, 200, 100)).name() == "#ffffff"
        assert label_text_color(QColor(20, 20, 80)).name() == "#ffffff"

    def test_boundary(self):
        # 亮度 140 上下边界
        assert label_text_color(QColor(141, 141, 141)).name() == "#141414"
        assert label_text_color(QColor(139, 139, 139)).name() == "#ffffff"
