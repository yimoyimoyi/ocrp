"""顶端快速开关工具栏构建器（开关/字幕模式/处理模式/清缓存）。

方法体从 main_window.py 原样搬移（AST 提取，零改动）。
通过 _ViewBase 与 MainWindow 双向委托共享状态。
"""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QLabel, QToolBar

from core.i18n import _
from core.utils import MODE_ASR_ONLY, MODE_OCR_ASR_FULL, MODE_OCR_ONLY
from ui.views.base import _ViewBase


class QuickToolbar(_ViewBase):
    """顶端快速开关工具栏构建器（开关/字幕模式/处理模式/清缓存）。"""

    def build(self):
        """构建顶端 QToolBar，包含常用功能快速开关。"""
        tb = QToolBar("快速开关")
        tb.setObjectName("quickToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)

        # ── 开关组 ──
        self._qt_corr = QAction(_("🔤 AI纠错"), self._mgr)
        self._qt_corr.setCheckable(True)
        self._qt_corr.setToolTip(_("启用/关闭 AI 纠错"))
        self._qt_corr.toggled.connect(self._on_qt_corr_toggled)
        tb.addAction(self._qt_corr)

        self._qt_hw = QAction(_("⚡ GPU"), self._mgr)
        self._qt_hw.setCheckable(True)
        self._qt_hw.setToolTip(_("启用/关闭 GPU 硬件加速"))
        self._qt_hw.toggled.connect(self._on_qt_hw_toggled)
        tb.addAction(self._qt_hw)

        self._qt_dedup = QAction(_("🔍 去重"), self._mgr)
        self._qt_dedup.setCheckable(True)
        self._qt_dedup.setToolTip(_("启用/关闭后处理相似度去重"))
        self._qt_dedup.toggled.connect(self._on_qt_dedup_toggled)
        tb.addAction(self._qt_dedup)

        self._qt_translate = QAction(_("🌐 翻译"), self._mgr)
        self._qt_translate.setCheckable(True)
        self._qt_translate.setToolTip(_("翻译模式：将 OCR 结果翻译为中文"))
        self._qt_translate.toggled.connect(self._on_qt_translate_toggled)
        tb.addAction(self._qt_translate)

        self._qt_sentinel = QAction(_("🛡 哨兵"), self._mgr)
        self._qt_sentinel.setCheckable(True)
        self._qt_sentinel.setToolTip(_("启用/关闭哨兵去重（字数骤降检测触发输出）"))
        self._qt_sentinel.toggled.connect(self._on_qt_sentinel_toggled)
        tb.addAction(self._qt_sentinel)

        tb.addSeparator()

        # ── 字幕模式下拉 ──
        self._qt_subtitle_label = QLabel(_("字幕"))
        tb.addWidget(self._qt_subtitle_label)
        self._qt_subtitle_mode = QComboBox()
        self._qt_subtitle_mode.addItems([_("流式"), _("常规")])
        self._qt_subtitle_mode.setToolTip(_("流式：哨兵去重实时输出\n常规：固定间隔采样").replace("\n", " | "))
        self._qt_subtitle_mode.currentTextChanged.connect(self._on_qt_subtitle_mode_changed)
        tb.addWidget(self._qt_subtitle_mode)

        tb.addSeparator()

        # ── 处理模式下拉 ──
        self._qt_process_label = QLabel(_("模式"))
        tb.addWidget(self._qt_process_label)
        self._qt_process_mode = QComboBox()
        self._qt_process_mode.addItems([_("OCR+ASR"), _("仅OCR"), _("仅ASR")])
        self._qt_process_mode.setToolTip(_("OCR+ASR：完整流程 | 仅OCR：纯图像识别 | 仅ASR：纯语音识别"))
        self._qt_process_mode.currentTextChanged.connect(self._on_qt_process_mode_changed)
        tb.addWidget(self._qt_process_mode)

        tb.addSeparator()

        # ── 清除缓存 ──
        self._qt_clear_cache = QAction(_("🗑 清缓存"), self._mgr)
        self._qt_clear_cache.setToolTip(_("清除所有缓存（LLM 响应缓存 + ASR 结果缓存）"))
        self._qt_clear_cache.triggered.connect(self._on_clear_cache)
        tb.addAction(self._qt_clear_cache)

        return tb

    def sync_quick_toggles(self):
        """从 ConfigPanel 同步所有快速开关状态。"""
        cp = self._config_panel
        self._qt_corr.blockSignals(True)
        self._qt_corr.setChecked(cp.corr_enabled)
        self._qt_corr.blockSignals(False)

        self._qt_hw.blockSignals(True)
        self._qt_hw.setChecked(self._config_mgr.get_hw_accel())
        self._qt_hw.blockSignals(False)

        self._qt_dedup.blockSignals(True)
        self._qt_dedup.setChecked(cp.post_sim_dedup)
        self._qt_dedup.blockSignals(False)

        self._qt_translate.blockSignals(True)
        self._qt_translate.setChecked(cp.corr_translate)
        self._qt_translate.blockSignals(False)

        self._qt_sentinel.blockSignals(True)
        self._qt_sentinel.setChecked(cp.sentinel_enabled)
        self._qt_sentinel.blockSignals(False)

        self._qt_subtitle_mode.blockSignals(True)
        self._qt_subtitle_mode.setCurrentIndex(0 if cp.subtitle_mode != "regular" else 1)
        self._qt_subtitle_mode.blockSignals(False)

        self._qt_process_mode.blockSignals(True)
        pm = cp.process_mode
        if MODE_OCR_ONLY in pm:
            self._qt_process_mode.setCurrentIndex(1)
        elif "仅语音" in pm:
            self._qt_process_mode.setCurrentIndex(2)
        else:
            self._qt_process_mode.setCurrentIndex(0)
        self._qt_process_mode.blockSignals(False)

        # 同步右侧面板控件
        self._sync_right_panel_from_config()

    def _on_qt_corr_toggled(self, checked: bool):
        self._config_panel.corr_enabled = checked
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_qt_hw_toggled(self, checked: bool):
        self._on_hw_accel_changed(checked)

    def _on_qt_dedup_toggled(self, checked: bool):
        self._config_panel.post_sim_dedup = checked
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_qt_translate_toggled(self, checked: bool):
        self._config_panel.corr_translate = checked
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_qt_sentinel_toggled(self, checked: bool):
        self._config_panel.sentinel_enabled = checked
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_qt_subtitle_mode_changed(self, text: str):
        idx = self._qt_subtitle_mode.currentIndex()
        self._config_panel.subtitle_mode = "stream" if idx == 0 else "regular"
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_qt_process_mode_changed(self, text: str):
        idx = self._qt_process_mode.currentIndex()
        modes = [MODE_OCR_ASR_FULL, MODE_OCR_ONLY, MODE_ASR_ONLY]
        full = modes[idx] if 0 <= idx < len(modes) else MODE_OCR_ASR_FULL
        self._config_panel.process_mode = full
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_clear_cache(self):
        """清除所有缓存。"""
        if self._message_service.question(
            _("清除缓存"),
            _("确定清除所有缓存？\n（LLM 响应缓存 + ASR 结果缓存）"),
        ):
            self._workflow.clear_all_caches()
