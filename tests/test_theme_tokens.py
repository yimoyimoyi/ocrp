"""主题令牌与主题自适应控件测试。

回归背景：状态栏按 emoji 前缀染色、次要文字灰、表格聚焦描边等位置写死了**深色**
主题的十六进制值。``ui/style_loader.py`` 提供 18 套主题（其中 10 套浅色），于是浅色
主题下出现"白字白底 / 浅灰看不清"的对比度缺陷（状态栏 ``▸`` 用 ``#ffffff``、
默认色 ``#b0bec5``）。
"""

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QWidget

from ui.theme_tokens import (
    DEFAULT_STATUS_COLOR_DARK,
    DEFAULT_STATUS_COLOR_LIGHT,
    FONT_FAMILY,
    STATUS_COLORS_DARK,
    STATUS_COLORS_LIGHT,
    accent_text_color,
    is_light_theme,
    muted_text_color,
    status_color,
)

DARK_WINDOW = QColor(30, 30, 30)
LIGHT_WINDOW = QColor(243, 243, 243)


@pytest.fixture(autouse=True)
def _clear_theme_registry():
    """主题名是**全局**登记状态：清空后本模块测试走"调色板回退"路径。

    否则同一次会话中先跑过的 GUI 测试（会 apply_theme）会让这里的判定改走主题名，
    使这些调色板用例变成顺序相关的假失败。
    """
    from ui import theme_tokens

    saved = theme_tokens.current_theme()
    theme_tokens.set_current_theme("")
    yield
    theme_tokens.set_current_theme(saved)


def _widget_with_window_color(color: QColor) -> QWidget:
    w = QWidget()
    pal = w.palette()
    pal.setColor(QPalette.Window, color)
    w.setPalette(pal)
    return w


class TestThemeDetection:
    def test_dark_window_is_dark(self, qapp):
        assert is_light_theme(_widget_with_window_color(DARK_WINDOW)) is False

    def test_light_window_is_light(self, qapp):
        assert is_light_theme(_widget_with_window_color(LIGHT_WINDOW)) is True

    def test_no_widget_defaults_to_dark(self):
        """未知主题按深色处理（原行为即深色值），避免抛异常。"""
        assert is_light_theme(None) is False


class TestThemeRegistryTakesPrecedence:
    """qt-material 只注入 QSS：控件在深色主题下创建后，其 QPalette 会被物化，
    切到浅色主题时不会刷新。因此**主题名必须优先于调色板**。"""

    def test_light_theme_name_wins_over_dark_palette(self, qapp):
        from ui import theme_tokens

        theme_tokens.set_current_theme("light_blue")
        widget = _widget_with_window_color(DARK_WINDOW)
        assert is_light_theme(widget) is True

    def test_dark_theme_name_wins_over_light_palette(self, qapp):
        from ui import theme_tokens

        theme_tokens.set_current_theme("dark_teal")
        widget = _widget_with_window_color(LIGHT_WINDOW)
        assert is_light_theme(widget) is False

    def test_legacy_theme_names_resolve(self):
        from ui import theme_tokens

        theme_tokens.set_current_theme("default")  # 经典浅色
        assert is_light_theme() is True
        theme_tokens.set_current_theme("default_dark")
        assert is_light_theme() is False

    def test_style_loader_registers_theme_on_apply(self, qapp):
        """apply_theme 是全部主题应用的唯一入口，必须登记主题名。"""
        from ui import style_loader, theme_tokens

        style_loader.apply_theme(qapp, "light_cyan")
        try:
            assert theme_tokens.current_theme() == "light_cyan"
            assert theme_tokens.is_light_theme() is True
        finally:
            theme_tokens.set_current_theme("")


class TestStatusColorTable:
    def test_variants_cover_same_prefixes(self):
        assert set(STATUS_COLORS_DARK) == set(STATUS_COLORS_LIGHT)

    @pytest.mark.parametrize("prefix", ["✅", "❌", "⚠", "⏳", "🔲", "🗑", "▸", "默认"])
    def test_light_variant_differs_from_dark(self, prefix):
        assert STATUS_COLORS_DARK[prefix] != STATUS_COLORS_LIGHT[prefix]

    @pytest.mark.parametrize("prefix", ["✅", "❌", "⚠", "⏳", "🔲", "🗑", "▸", "默认"])
    def test_light_variant_is_dark_paint(self, prefix):
        """浅色主题下的文字色必须足够暗，否则在白底上看不清。"""
        assert QColor(STATUS_COLORS_LIGHT[prefix]).lightness() < 160


class TestStatusColorSelection:
    def test_dark_widget_gets_dark_variant(self, qapp):
        w = _widget_with_window_color(DARK_WINDOW)
        assert status_color("✅ 完成", w) == STATUS_COLORS_DARK["✅"]

    def test_light_widget_gets_light_variant(self, qapp):
        w = _widget_with_window_color(LIGHT_WINDOW)
        assert status_color("✅ 完成", w) == STATUS_COLORS_LIGHT["✅"]

    def test_unknown_prefix_falls_back(self, qapp):
        assert status_color("普通消息", _widget_with_window_color(DARK_WINDOW)) == DEFAULT_STATUS_COLOR_DARK
        assert status_color("普通消息", _widget_with_window_color(LIGHT_WINDOW)) == DEFAULT_STATUS_COLOR_LIGHT

    def test_no_white_text_on_light_theme(self, qapp):
        """回归：``▸`` 曾在浅色主题下用 #ffffff（白底白字完全不可见）。"""
        w = _widget_with_window_color(LIGHT_WINDOW)
        for text in ("▸ 处理中", "默认状态"):
            assert QColor(status_color(text, w)).lightness() < 200


class TestSemanticTextColors:
    def test_muted_differs_by_theme(self, qapp):
        assert muted_text_color(_widget_with_window_color(DARK_WINDOW)) != muted_text_color(
            _widget_with_window_color(LIGHT_WINDOW)
        )

    def test_accent_is_visible_on_light(self, qapp):
        w = _widget_with_window_color(LIGHT_WINDOW)
        assert QColor(accent_text_color(w)).lightness() < 160

    def test_font_family_is_shared_constant(self):
        assert FONT_FAMILY == "Microsoft YaHei UI"


class TestColoredStatusLabel:
    def test_label_recolours_for_light_palette(self, qapp):
        from ui.views.status_bar import ColoredStatusLabel

        label = ColoredStatusLabel("")
        pal = label.palette()
        pal.setColor(QPalette.Window, LIGHT_WINDOW)
        label.setPalette(pal)
        label.resize(400, 22)
        label.setText("▸ 处理中")
        assert STATUS_COLORS_LIGHT["▸"] in label.text()
        assert "#ffffff" not in label.text()

    def test_label_uses_dark_variant_by_default(self, qapp):
        from ui.views.status_bar import ColoredStatusLabel

        label = ColoredStatusLabel("")
        pal = label.palette()
        pal.setColor(QPalette.Window, DARK_WINDOW)
        label.setPalette(pal)
        label.resize(400, 22)
        label.setText("✅ 完成")
        assert STATUS_COLORS_DARK["✅"] in label.text()

    def test_plain_text_is_html_escaped(self, qapp):
        from ui.views.status_bar import ColoredStatusLabel

        label = ColoredStatusLabel("")
        label.resize(400, 22)
        label.setText("✅ a<b>c&d")
        assert "&lt;b&gt;" in label.text()
        assert "&amp;" in label.text()

    def test_elides_long_text(self, qapp):
        from ui.views.status_bar import ColoredStatusLabel

        label = ColoredStatusLabel("")
        label.resize(60, 22)
        label.setText("✅ " + "很长的一段状态消息" * 20)
        assert "…" in label.text() or "..." in label.text()


class TestCollapsibleGroupDirectReferences:
    """回归：折叠/展开此前用 findChild(..., "objectName") 字符串反射查找子控件，
    每次折叠都做一次递归子树搜索，嵌套分组时还需依赖遍历顺序才不取错控件。"""

    def test_header_and_content_are_kept_as_attributes(self, qapp):
        from ui.collapsible_group import CollapsibleGroup

        group = CollapsibleGroup("标题")
        assert group._header is not None
        assert group._header.objectName() == "collapsibleHeader"
        assert group._content is not None
        assert group._content.objectName() == "collapsibleContent"

    def test_collapse_does_not_disturb_nested_group(self, qapp):
        from ui.collapsible_group import CollapsibleGroup

        outer = CollapsibleGroup("outer")
        inner = CollapsibleGroup("inner")
        outer.addWidget(inner)
        outer.set_collapsed(True)
        assert outer.collapsed is True
        assert inner.collapsed is False

    def test_content_visibility_follows_collapse(self, qapp):
        from ui.collapsible_group import CollapsibleGroup

        group = CollapsibleGroup("标题")
        group.set_collapsed(True)
        assert group._content.isVisible() is False
        group.set_collapsed(False)
        # 未 show() 的顶层控件 isHidden 语义与 isVisible 不同，这里断言未被显式隐藏
        assert group._content.isHidden() is False

    def test_no_findchild_reflection_remains(self):
        import ast
        import inspect

        from ui import collapsible_group

        tree = ast.parse(inspect.getsource(collapsible_group))
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "findChild"
        ]
        assert offenders == [], f"collapsible_group 仍有字符串反射查找: 行 {offenders}"


class TestDensityScaleMapping:
    """回归：density_scale 判定按升序书写，``scale > 1.1`` 抢先匹配所有 >1.2 的值，
    导致 ``"2"``（最大档）永不生效。"""

    def test_large_scale_reaches_top_tier(self):
        from ui.main_window import _density_for_scale

        assert _density_for_scale(1.5) == "2"
        assert _density_for_scale(1.25) == "2"
        assert _density_for_scale(1.21) == "2"

    def test_mid_scale_uses_tier_one(self):
        from ui.main_window import _density_for_scale

        assert _density_for_scale(1.15) == "1"
        assert _density_for_scale(1.11) == "1"

    def test_small_scale_uses_negative_tiers(self):
        from ui.main_window import _density_for_scale

        assert _density_for_scale(0.85) == "-2"
        assert _density_for_scale(0.95) == "-1"

    def test_normal_scale_is_default(self):
        from ui.main_window import _density_for_scale

        for scale in (1.0, 1.05, 1.1):
            assert _density_for_scale(scale) == "0"

    def test_every_step_is_reachable(self):
        """每一档都必须至少有一个 scale 能命中（防止再次出现死分支）。"""
        from ui.main_window import _density_for_scale

        reached = {_density_for_scale(s / 100) for s in range(50, 201)}
        assert reached == {"-2", "-1", "0", "1", "2"}
