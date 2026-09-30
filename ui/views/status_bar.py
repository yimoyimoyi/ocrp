"""状态栏构建器（状态标签/进度条/引擎/时间）。

方法体从 main_window.py 原样搬移（AST 提取，零改动）。
通过 _ViewBase 与 MainWindow 双向委托共享状态。
"""

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt
from PySide6.QtWidgets import QLabel, QProgressBar, QSizePolicy, QStatusBar

from core.i18n import _
from ui.theme_tokens import status_color
from ui.views.base import _ViewBase


class ColoredStatusLabel(QLabel):
    """自动根据消息前缀着色的状态标签，超长文本自动省略。

    颜色在**每次渲染时**按当前主题求解（见 ``ui.theme_tokens.status_color``）：
    此前颜色写死在类常量里，切换浅色主题后 ``▸``/默认色仍是白色/淡灰，在浅底上
    几乎不可见。
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._plain = text
        self._applying = False
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(60)
        self._apply_text()

    def setText(self, text: str):  # type: ignore[override]
        self._plain = text
        self._apply_text()

    def changeEvent(self, event):
        super().changeEvent(event)
        # 主题切换会下发 PaletteChange/StyleChange，需要重新求解颜色
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange) and not self._applying:
            self._apply_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_text()

    def _apply_text(self):
        if self._applying:  # 防止 super().setText 触发的样式事件递归
            return
        self._applying = True
        try:
            color = status_color(self._plain, self)
            w = self.width()
            if w < 20:
                # 宽度未确定，先显示纯文本，等 resize 时再省略
                super().setText(f'<span style="color:{color}">{self._plain}</span>')
                return
            fm = self.fontMetrics()
            elided = fm.elidedText(self._plain, Qt.ElideRight, w - 8)
            safe = elided.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            super().setText(f'<span style="color:{color}">{safe}</span>')
        finally:
            self._applying = False


class StatusBarView(_ViewBase):
    """状态栏构建器（状态标签/进度条/引擎/时间）。"""

    def build(self):
        """构建状态栏（原 setup 内联代码搬移，4b.8 精简为状态标签+进度条）。"""
        self._status_bar = QStatusBar(self._mgr)
        self._status_bar.setContentsMargins(6, 2, 6, 2)

        self._status_label = ColoredStatusLabel(_("就绪"))
        self._status_label.setMinimumWidth(80)

        self._progress_bar = QProgressBar(self._status_bar)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setObjectName("progressAnimated")
        self._progress_bar.setMaximumWidth(140)
        self._progress_bar.setMinimumWidth(80)
        self._progress_bar.setMaximumHeight(18)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("")
        self._progress_bar.setTextVisible(False)

        self._progress_anim = QPropertyAnimation(self._progress_bar, b"value")
        self._progress_anim.setDuration(300)
        self._progress_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._status_bar.addPermanentWidget(self._progress_bar)
        self._status_bar.addWidget(self._status_label, 1)
        self._progress_bar.setVisible(True)

        self._mgr.setStatusBar(self._status_bar)
        return self._status_bar

    def _set_progress_animated(self, value: int):
        """平滑动画更新进度条。"""
        if hasattr(self, "_progress_anim"):
            self._progress_anim.stop()
            self._progress_anim.setStartValue(self._progress_bar.value())
            self._progress_anim.setEndValue(value)
            self._progress_anim.start()
        else:
            self._progress_bar.setValue(value)
