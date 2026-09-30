"""主题令牌 —— 语义色与字体族的单一来源。

QSS（``styles/*.qss`` 与 material 主题）负责整体外观，但少量控件必须**内联**着色：
状态栏按 emoji 前缀染色、选中提示文字、图标色。此前这些位置直接写死深色主题的
十六进制值，在 ``ui/style_loader.py`` 提供的 18 套浅色主题下会出现"白字白底 /
浅灰字看不清"的对比度问题（例如状态栏 ``▸`` 用 ``#ffffff``、默认色 ``#b0bec5``）。

这里的颜色按**当前主题明暗**给出变体。判定不依赖主题名，而是读取控件调色板的
窗口色亮度 —— material/QSS 主题都会写入调色板，因此以后新增主题自动适配。
"""

from PySide6.QtGui import QPalette

# 字体族单一来源（与 style_loader.apply_theme 的默认参数保持一致）
FONT_FAMILY = "Microsoft YaHei UI"

#: 窗口色亮度高于该值判为浅色主题（深色主题窗口色多为 #1e1e1e ≈ 30，浅色多为 #f3f3f3 ≈ 243）
LIGHT_THEME_LIGHTNESS_THRESHOLD = 128

# 状态栏/图标前缀 → 颜色（深色主题 / 浅色主题）
STATUS_COLORS_DARK: dict[str, str] = {
    "✅": "#4caf50",  # 绿
    "❌": "#f44336",  # 红
    "⚠": "#ff9800",  # 橙
    "⏳": "#2196f3",  # 蓝
    "🔲": "#78909c",  # 灰
    "🗑": "#78909c",  # 灰
    "▸": "#ffffff",  # 白
    "默认": "#b0bec5",  # 淡灰
}

STATUS_COLORS_LIGHT: dict[str, str] = {
    "✅": "#2e7d32",  # 深绿（浅底可读）
    "❌": "#c62828",  # 深红
    "⚠": "#e65100",  # 深橙
    "⏳": "#1565c0",  # 深蓝
    "🔲": "#546e7a",  # 蓝灰
    "🗑": "#546e7a",  # 蓝灰
    "▸": "#000000",  # 黑
    "默认": "#546e7a",  # 蓝灰
}

DEFAULT_STATUS_COLOR_DARK = "#b0bec5"
DEFAULT_STATUS_COLOR_LIGHT = "#546e7a"

# 次要文字（选中计数、搜索计数等）
MUTED_TEXT_DARK = "#78909c"
MUTED_TEXT_LIGHT = "#546e7a"

# 强调文字（当前选中计数）
ACCENT_TEXT_DARK = "#42a5f5"
ACCENT_TEXT_LIGHT = "#1565c0"

# 表格聚焦描边
FOCUS_OUTLINE_DARK = "#58a6ff"
FOCUS_OUTLINE_LIGHT = "#1565c0"

# ── 当前主题登记 ──
# qt-material 主题只注入 QSS。控件一旦在深色主题下创建，Qt 会把 QSS 中 palette(...)
# 的解析结果物化到该控件自己的 QPalette 上；之后切到浅色主题时这个已物化的调色板
# 不会自动刷新 —— 因此**不能**依赖 widget.palette() 判定明暗。主题名是唯一可靠来源，
# 由 ui/style_loader.apply_theme()（所有主题应用的唯一入口）登记。
_current_theme: str = ""


def set_current_theme(theme_name: str) -> None:
    """登记最近一次应用的主题名（由 ``ui.style_loader.apply_theme`` 调用）。"""
    global _current_theme
    _current_theme = str(theme_name or "")


def current_theme() -> str:
    """最近一次应用的主题名；空串表示尚未应用过任何主题。"""
    return _current_theme


def _theme_registry_is_light() -> bool | None:
    """按已登记主题名判定明暗；未登记时返回 None（交由调用方回退）。"""
    if not _current_theme:
        return None
    from ui.style_loader import is_dark_theme  # 惰性导入：避免模块级循环依赖

    return not is_dark_theme(_current_theme)


def is_light_theme(widget=None) -> bool:
    """当前是否浅色主题。

    判定顺序：
    1. 已登记的主题名（真实运行路径；QSS 主题下唯一可靠）
    2. 控件调色板亮度（未登记主题时，如独立控件 / 单元测试）

    注意比较方向：窗口色**亮度高**才是浅色主题。
    """
    decided = _theme_registry_is_light()
    if decided is not None:
        return decided
    if widget is None:
        return False
    try:
        window = widget.palette().color(QPalette.Window)
    except (AttributeError, RuntimeError):
        return False
    return window.lightness() > LIGHT_THEME_LIGHTNESS_THRESHOLD


def muted_text_color(widget=None) -> str:
    """次要文字颜色（随主题）。"""
    return MUTED_TEXT_LIGHT if is_light_theme(widget) else MUTED_TEXT_DARK


def accent_text_color(widget=None) -> str:
    """强调文字颜色（随主题）。"""
    return ACCENT_TEXT_LIGHT if is_light_theme(widget) else ACCENT_TEXT_DARK


def focus_outline_color(widget=None) -> str:
    """表格聚焦描边颜色（随主题）。"""
    return FOCUS_OUTLINE_LIGHT if is_light_theme(widget) else FOCUS_OUTLINE_DARK


def muted_text_style(widget=None, *, font_size: int = 12, bold: bool = False) -> str:
    """次要文字/强调文字的完整 QSS（避免调用方各写一份十六进制值）。"""
    color = accent_text_color(widget) if bold else muted_text_color(widget)
    weight = " font-weight: bold;" if bold else ""
    return f"color: {color}; font-size: {font_size}px;{weight}"


def status_color(text: str, widget=None) -> str:
    """按消息前缀返回状态色（深/浅主题各一套）。"""
    table = STATUS_COLORS_LIGHT if is_light_theme(widget) else STATUS_COLORS_DARK
    for prefix, color in table.items():
        if text.startswith(prefix):
            return color
    return DEFAULT_STATUS_COLOR_LIGHT if is_light_theme(widget) else DEFAULT_STATUS_COLOR_DARK
