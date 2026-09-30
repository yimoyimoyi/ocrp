"""底部操作栏构建器（处理控制/纠错/润色按钮）。

方法体从 main_window.py 原样搬移（AST 提取，零改动）。
通过 _ViewBase 与 MainWindow 双向委托共享状态。
"""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

from core.i18n import _
from ui.views.base import _ViewBase


class BottomBarView(_ViewBase):
    """底部操作栏构建器（处理控制/纠错/润色按钮）。"""

    def _on_workflow_buttons(self, states: dict):
        """根据 WorkflowManager 信号更新按钮状态（唯一入口）。"""
        if "start" in states:
            self._btn_start.setEnabled(states["start"])
        if "stop" in states:
            self._btn_stop.setEnabled(states["stop"])
        if "correction" in states:
            self._btn_correction.setEnabled(states["correction"])
        if "correction_all" in states:
            self._btn_correction_all.setEnabled(states["correction_all"])
        if "polish" in states:
            self._btn_polish.setEnabled(states["polish"])
        if "polish_all" in states:
            self._btn_polish_all.setEnabled(states["polish_all"])
        if "pause" in states:
            self._btn_pause.setEnabled(states["pause"])

    def reset_workflow_buttons(self):
        """把按钮恢复为空闲态（用 WorkflowManager.IDLE_BUTTON_STATES 单一来源）。"""
        from core.workflow.manager import IDLE_BUTTON_STATES

        self._on_workflow_buttons(dict(IDLE_BUTTON_STATES))

    def set_correction_enabled(self, enabled: bool):
        """仅切换纠错按钮组（不触碰开始/停止/暂停）。

        批量纠错结束或出错时使用：此时视频/音频处理可能仍在进行，不能整体复位。
        """
        self._on_workflow_buttons({"correction": enabled, "correction_all": enabled})

    def build(self):
        """构建底部操作栏（原 build_ui 内联代码搬移）。"""
        # 分隔线引用（main_window._apply_scaled_metrics 需要按缩放调整高度）。
        # 此前用 findChildren(QFrame) + objectName 字符串比对查找，objectName
        # 改名就静默失效（分隔线高度不再跟随字体/缩放）。
        self._bar_separators: list[QFrame] = []

        # 底部操作栏
        bar = QFrame()
        bar.setObjectName("bottomBar")
        bbl = QHBoxLayout(bar)
        bbl.setContentsMargins(10, 4, 10, 4)
        bbl.setSpacing(6)

        # ── 处理控制组 ──
        self._btn_start = QPushButton(_("▶ 开始处理"))
        self._btn_start.setObjectName("btnStart")
        self._btn_start.setFixedHeight(34)
        self._btn_start.setMinimumWidth(100)
        self._btn_start.clicked.connect(self._on_start_processing)
        bbl.addWidget(self._btn_start)
        self._btn_pause = QPushButton(_("⏸ 暂停"))
        self._btn_pause.setObjectName("btnPause")
        self._btn_pause.setFixedHeight(34)
        self._btn_pause.setMinimumWidth(70)
        self._btn_pause.setEnabled(False)
        self._btn_pause.clicked.connect(self._on_pause_processing)
        bbl.addWidget(self._btn_pause)
        self._btn_stop = QPushButton(_("⏹ 停止"))
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setFixedHeight(34)
        self._btn_stop.setMinimumWidth(70)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop_processing)
        bbl.addWidget(self._btn_stop)

        # ── 分隔线 ──
        sep1 = QFrame()
        sep1.setObjectName("barSeparator")
        sep1.setFrameShape(QFrame.VLine)
        sep1.setFixedHeight(24)
        bbl.addWidget(sep1)
        self._bar_separators.append(sep1)

        # ── AI 纠错组 ──
        self._btn_correction = QPushButton(_("✏ 纠错选中"))
        self._btn_correction.setObjectName("btnCorrection")
        self._btn_correction.setFixedHeight(34)
        self._btn_correction.clicked.connect(self._on_correction_selected)
        bbl.addWidget(self._btn_correction)
        self._btn_correction_all = QPushButton(_("✏ 纠错全部"))
        self._btn_correction_all.setObjectName("btnCorrectionAll")
        self._btn_correction_all.setFixedHeight(34)
        self._btn_correction_all.clicked.connect(self._on_correction_all)
        bbl.addWidget(self._btn_correction_all)

        # ── 分隔线 ──
        sep2 = QFrame()
        sep2.setObjectName("barSeparator")
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFixedHeight(24)
        bbl.addWidget(sep2)
        self._bar_separators.append(sep2)

        # ── 润色组 ──
        self._btn_polish = QPushButton(_("✨ 润色选中"))
        self._btn_polish.setObjectName("btnPolish")
        self._btn_polish.setFixedHeight(34)
        self._btn_polish.clicked.connect(self._on_polish_selected)
        bbl.addWidget(self._btn_polish)
        self._btn_polish_all = QPushButton(_("✨ 润色全部"))
        self._btn_polish_all.setObjectName("btnPolishAll")
        self._btn_polish_all.setFixedHeight(34)
        self._btn_polish_all.clicked.connect(self._on_polish_all)
        bbl.addWidget(self._btn_polish_all)

        bbl.addStretch()
        return bar
