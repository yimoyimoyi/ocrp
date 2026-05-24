"""可收纳分组控件 —— 点击标题栏展开/折叠内容区域。

用法:
    group = CollapsibleGroup("基本属性")
    group.addWidget(some_widget)
    layout.addWidget(group)

    # 设置默认折叠
    group.set_collapsed(True)
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CollapsibleGroup(QWidget):
    """可折叠的分组卡片。

    Signals:
        toggled(bool): 展开/折叠状态变化
    """

    toggled = pyqtSignal(bool)

    def __init__(self, title: str = "", parent=None, collapsed: bool = False):
        super().__init__(parent)
        self._collapsed = collapsed
        self._content_layout: QVBoxLayout = None  # type: ignore
        self._toggle_btn: QToolButton = None  # type: ignore
        self._title_label: QLabel = None  # type: ignore
        self._build(title)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, coll: bool):
        """程序化设置折叠状态。"""
        if self._collapsed == coll:
            return
        self._collapse(coll)

    def toggle(self):
        """切换折叠状态。"""
        self._collapse(not self._collapsed)

    def content_layout(self) -> QVBoxLayout:
        """返回内容区域的 layout，用于向其添加控件。"""
        return self._content_layout

    def addWidget(self, widget: QWidget):
        """便捷方法：向内容区域添加 widget。"""
        self._content_layout.addWidget(widget)

    def addLayout(self, layout):
        """便捷方法：向内容区域添加 layout。"""
        self._content_layout.addLayout(layout)

    # ── 内部 ──

    def _build(self, title: str):
        self.setObjectName("collapsibleGroup")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 标题栏 ──
        header = QWidget()
        header.setObjectName("collapsibleHeader")
        header.setCursor(Qt.PointingHandCursor)
        header.mousePressEvent = lambda e: self._collapse(not self._collapsed)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(8, 5, 8, 5)
        hl.setSpacing(4)

        self._toggle_btn = QToolButton()
        self._toggle_btn.setObjectName("collapsibleToggle")
        self._toggle_btn.setArrowType(Qt.DownArrow if not self._collapsed else Qt.RightArrow)
        self._toggle_btn.setAutoRaise(True)
        self._toggle_btn.setFixedSize(16, 16)
        self._toggle_btn.clicked.connect(lambda: self._collapse(not self._collapsed))
        hl.addWidget(self._toggle_btn)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("collapsibleTitle")
        font = QFont()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self._title_label.setFont(font)
        hl.addWidget(self._title_label, 1)
        outer.addWidget(header)

        # ── 内容区域 ──
        content = QWidget()
        content.setObjectName("collapsibleContent")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(8, 4, 8, 8)
        self._content_layout.setSpacing(4)
        outer.addWidget(content)

        if self._collapsed:
            content.setVisible(False)
            self._toggle_btn.setArrowType(Qt.RightArrow)

    def _collapse(self, coll: bool):
        if self._collapsed == coll:
            return
        self._collapsed = coll
        # 内容区域
        content = self.findChild(QWidget, "collapsibleContent")
        if content:
            content.setVisible(not coll)
            # 更新父级布局
            self.updateGeometry()
        # 箭头切换
        self._toggle_btn.setArrowType(Qt.RightArrow if coll else Qt.DownArrow)
        self.toggled.emit(coll)
