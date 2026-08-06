"""views —— MainWindow 拆分的视图构建器组件。

每个构建器持有 MainWindow 引用，通过 base._ViewBase 双向委托共享状态，
方法体从 main_window.py 原样搬移。
"""

from ui.views.base import _ViewBase
from ui.views.bottom_bar import BottomBarView
from ui.views.menu_bar import MenuBarView
from ui.views.quick_toolbar import QuickToolbar
from ui.views.right_panel import RightPanelView
from ui.views.status_bar import StatusBarView

__all__ = ["BottomBarView", "MenuBarView", "QuickToolbar", "RightPanelView", "StatusBarView", "_ViewBase"]
