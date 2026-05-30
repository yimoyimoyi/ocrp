"""主窗口 —— ORCP OCR 处理工具。
引擎/模板选择 → 顶端菜单栏；所有参数设置 → 统一的「参数设置」对话框。"""

import json
import os
import sys
from pathlib import Path

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QAction,
    QActionGroup,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.ai_correction import AICorrector, load_correction_config
from core.asr_engine import ASREngineManager
from core.config_manager import ConfigManager
from core.filter_manager import FilterManager
from core.i18n import LANGUAGE_DISPLAY_NAMES, SUPPORTED_LANGUAGES, LanguageManager, _
from core.ocr_engine import OCREngineManager
from core.prompt_manager import PromptTemplateManager
from core.result_processor import export_results
from core.utils import MODE_ASR_ONLY, MODE_OCR_ASR_FULL, MODE_OCR_ONLY
from core.workflow_manager import WorkflowManager
from ui.collapsible_group import CollapsibleGroup
from ui.config_panel import ConfigPanel
from ui.dialogs import PresetManageDialog
from ui.display_dialog import DisplayDialog
from ui.region_manager import RegionManagerWidget
from ui.result_table import ResultTableWidget
from ui.settings_dialog import SettingsDialog
from ui.style_loader import (
    DEFAULT_DARK,
    DEFAULT_LIGHT,
    apply_theme,
    is_dark_theme,
)
from ui.video_preview import VideoPreviewWidget

WIN_TITLE = _("ORCP - OCR 处理工具")


# ── 状态栏颜色映射 ──
_STATUS_COLORS: dict[str, str] = {
    "✅": "#4caf50",       # 绿
    "❌": "#f44336",       # 红
    "⚠": "#ff9800",       # 橙
    "⏳": "#2196f3",       # 蓝
    "🔲": "#78909c",       # 灰
    "🗑": "#78909c",       # 灰
    "▸": "#ffffff",        # 白
    "默认": "#b0bec5",     # 淡灰
}


def _detect_status_color(text: str) -> str:
    """根据消息前缀返回对应颜色。"""
    for prefix, color in _STATUS_COLORS.items():
        if text.startswith(prefix):
            return color
    return "#b0bec5"


class ColoredStatusLabel(QLabel):
    """自动根据消息前缀着色的状态标签。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.RichText)

    def setText(self, text: str):
        color = _detect_status_color(text)
        super().setText(f'<span style="color:{color}">{text}</span>')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowOpacity(0.0)
        self.hide()
        self.setUpdatesEnabled(False)
        self.setWindowTitle(WIN_TITLE)

    def setup(self):
        """构建完整 UI —— 与 __init__ 分离，确保窗口在完全就绪后才首次渲染。"""
        self._config_mgr = ConfigManager()
        self._engine_mgr = OCREngineManager()
        self._asr_mgr = ASREngineManager()
        self._corrector = AICorrector(load_correction_config(), engine_manager=self._engine_mgr)
        self._prompt_mgr = PromptTemplateManager()
        self._filter_mgr = FilterManager()

        self._workflow = WorkflowManager(self)

        self._correction_results: dict[int, str] = {}
        self._correction_pending: set = set()
        self._custom_prompt: str = ""
        self._mode_params: dict = {}
        self._current_engine: str = "paddleocr"
        self._current_template: str = ""
        self._batch_files: list[str] = []
        self._asr_params_changed: bool = False

        self._theme = self._config_mgr.get_theme()

        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)
        self._status_bar.setContentsMargins(6, 2, 6, 2)

        self._status_label = ColoredStatusLabel(_("就绪"))
        self._status_label.setMinimumWidth(120)
        self._engine_label = QLabel(_("  |  引擎: paddleocr"))
        self._time_label = QLabel("")
        self._time_label.setMinimumWidth(80)

        self._progress_bar = QProgressBar(self._status_bar)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setObjectName("progressAnimated")
        self._progress_bar.setMaximumWidth(200)
        self._progress_bar.setMinimumWidth(120)
        self._progress_bar.setMaximumHeight(18)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("")
        self._progress_bar.setTextVisible(False)

        from PyQt5.QtCore import QEasingCurve, QPropertyAnimation
        self._progress_anim = QPropertyAnimation(self._progress_bar, b"value")
        self._progress_anim.setDuration(300)
        self._progress_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._status_bar.addPermanentWidget(self._progress_bar)
        self._status_bar.addPermanentWidget(self._engine_label)
        self._status_bar.addWidget(self._status_label, 1)
        self._status_bar.addPermanentWidget(self._time_label)
        self._progress_bar.setVisible(True)

        self.build_ui()
        self._build_menu_bar()
        self._quick_toolbar = self._build_quick_toolbar()
        self.addToolBar(self._quick_toolbar)
        self._apply_theme()

        hw = self._config_mgr.get_hw_accel()
        if hw:
            self._engine_mgr.set_hw_accel(True)
        self._refresh_engine_list()
        self._refresh_template_list()
        self._video_preview.set_hw_accel(hw)
        self._restore_mode_params()
        self._configure_workflow()
        self.sync_quick_toggles()
        self._install_wheel_blocker()

        # 注册语言切换监听器
        LanguageManager().register_listener(self._on_language_changed)

        # 延迟保存窗口几何（窗口调整大小时防抖保存）
        from PyQt5.QtCore import QTimer
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(1000)
        self._geometry_save_timer.timeout.connect(self._save_window_geometry)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_geometry_save_timer'):
            self._geometry_save_timer.start()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, '_geometry_save_timer'):
            self._geometry_save_timer.start()

    def _install_wheel_blocker(self):
        """安装全局事件过滤器，完全阻止滚轮改变 SpinBox/ComboBox 的值。"""
        app = QApplication.instance()
        if app is None:
            return

        class WheelBlocker(QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setVisible(False)

            def eventFilter(self, obj, event):
                if event.type() == QEvent.Wheel:
                    if isinstance(obj, (QAbstractSpinBox, QComboBox)):
                        event.ignore()
                        return True
                return super().eventFilter(obj, event)

        blocker = WheelBlocker(self)
        app.installEventFilter(blocker)
        self._wheel_blocker = blocker

    def _restart_ocr_engine(self):
        """OCR 设置变更后标记需要重建（延迟到实际处理时加载）。"""
        self._engine_mgr.reload_config()
        logger.info("OCR 配置已重载，引擎将在下次处理时重建")

    def _restart_asr_engine(self):
        """ASR 设置变更后重建引擎实例（后台线程）。"""
        import threading

        def _restart():
            if self._asr_mgr:
                self._asr_mgr.reload_config()
                asr = self._asr_mgr.get_engine()
                if asr:
                    logger.info("ASR 引擎已重建")

        threading.Thread(target=_restart, daemon=True).start()

    # ── 顶端快速开关工具栏 ──
    def _build_quick_toolbar(self):
        """构建顶端 QToolBar，包含常用功能快速开关。"""
        tb = QToolBar("快速开关")
        tb.setObjectName("quickToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)

        # ── 开关组 ──
        self._qt_corr = QAction(_("🔤 AI纠错"), self)
        self._qt_corr.setCheckable(True)
        self._qt_corr.setToolTip(_("启用/关闭 AI 纠错"))
        self._qt_corr.toggled.connect(self._on_qt_corr_toggled)
        tb.addAction(self._qt_corr)

        self._qt_hw = QAction(_("⚡ GPU"), self)
        self._qt_hw.setCheckable(True)
        self._qt_hw.setToolTip(_("启用/关闭 GPU 硬件加速"))
        self._qt_hw.toggled.connect(self._on_qt_hw_toggled)
        tb.addAction(self._qt_hw)

        self._qt_dedup = QAction(_("🔍 去重"), self)
        self._qt_dedup.setCheckable(True)
        self._qt_dedup.setToolTip(_("启用/关闭后处理相似度去重"))
        self._qt_dedup.toggled.connect(self._on_qt_dedup_toggled)
        tb.addAction(self._qt_dedup)

        self._qt_translate = QAction(_("🌐 翻译"), self)
        self._qt_translate.setCheckable(True)
        self._qt_translate.setToolTip(_("翻译模式：将 OCR 结果翻译为中文"))
        self._qt_translate.toggled.connect(self._on_qt_translate_toggled)
        tb.addAction(self._qt_translate)

        self._qt_sentinel = QAction(_("🛡 哨兵"), self)
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
        self._qt_clear_cache = QAction(_("🗑 清缓存"), self)
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
        is_streaming = "流式" in cp.subtitle_mode
        self._qt_subtitle_mode.setCurrentIndex(0 if is_streaming else 1)
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

    def _sync_right_panel_from_config(self):
        """从 ConfigPanel 同步右侧面板控件状态。"""
        cp = self._config_panel
        mp = cp.get_mode_params()

        # 字幕模式
        self._subtitle_mode_combo_r.blockSignals(True)
        internal_mode = mp.get("subtitle_mode", "流式字幕（去重）")
        if "流式" in internal_mode:
            self._subtitle_mode_combo_r.setCurrentText(_("流式字幕（去重）"))
        else:
            self._subtitle_mode_combo_r.setCurrentText(_("常规字幕（固定间隔）"))
        self._subtitle_mode_combo_r.blockSignals(False)

        # 帧间隔
        self._frame_interval_r.blockSignals(True)
        self._frame_interval_r.setValue(mp.get("frame_interval", 0.1))
        self._frame_interval_r.blockSignals(False)

        # 后处理选项
        self._post_sim_dedup_r.blockSignals(True)
        self._post_sim_dedup_r.setChecked(mp.get("post_sim_dedup", True))
        self._post_sim_dedup_r.blockSignals(False)

        self._post_sim_threshold_r.blockSignals(True)
        self._post_sim_threshold_r.setValue(mp.get("post_sim_threshold", 0.9))
        self._post_sim_threshold_r.blockSignals(False)

        self._post_min_text_len_r.blockSignals(True)
        self._post_min_text_len_r.setValue(mp.get("post_min_text_len", 2))
        self._post_min_text_len_r.blockSignals(False)

        self._post_conf_check_r.blockSignals(True)
        self._post_conf_check_r.setChecked(mp.get("post_conf_enabled", False))
        self._post_conf_check_r.blockSignals(False)

        self._post_conf_threshold_r.blockSignals(True)
        self._post_conf_threshold_r.setValue(mp.get("post_conf_threshold", 0.6))
        self._post_conf_threshold_r.blockSignals(False)

        # ASR 组可见性：仅 OCR 模式时隐藏
        self._asr_group.setVisible(MODE_OCR_ONLY not in mp.get("process_mode", ""))

    # ── 快速开关事件 ──
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
        full = "流式字幕（去重）" if idx == 0 else "常规字幕（固定间隔）"
        self._config_panel.subtitle_mode = full
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_qt_process_mode_changed(self, text: str):
        idx = self._qt_process_mode.currentIndex()
        modes = [MODE_OCR_ASR_FULL, MODE_OCR_ONLY, MODE_ASR_ONLY]
        full = modes[idx] if 0 <= idx < len(modes) else MODE_OCR_ASR_FULL
        self._config_panel.process_mode = full
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_clear_cache(self):
        """清除所有缓存。"""
        reply = QMessageBox.question(
            self, _("清除缓存"),
            _("确定清除所有缓存？\n（LLM 响应缓存 + ASR 结果缓存）"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._workflow.clear_all_caches()

    # ── 菜单栏 ──
    def _build_menu_bar(self):
        mb = self.menuBar()

        # ── 参数设置菜单 ──
        self._settings_menu = mb.addMenu(_("参数设置(&P)"))
        self._settings_menu_actions = []
        for label, tab_idx in [
            (_("⚙ 全部参数..."), -1),
            (_("基础设置..."), 0),
            (_("语音识别..."), 1),
            (_("OCR 字幕处理..."), 2),
            (_("AI 纠错..."), 3),
            (_("结果输出..."), 4),
        ]:
            action = QAction(label, self)
            action.triggered.connect(lambda checked, idx=tab_idx: self._open_settings(idx))
            self._settings_menu.addAction(action)
            self._settings_menu_actions.append(action)
            if label == _("⚙ 全部参数..."):
                self._settings_menu.addSeparator()

        # ── 显示菜单 ──
        self._display_menu = mb.addMenu(_("显示(&V)"))
        self._display_theme_action = QAction(_("切换主题 (亮色/暗色)"), self)
        self._display_theme_action.triggered.connect(self._toggle_theme)
        self._display_menu.addAction(self._display_theme_action)
        self._display_menu.addSeparator()
        self._display_settings_action = QAction(_("显示设置..."), self)
        self._display_settings_action.triggered.connect(self._open_display_settings)
        self._display_menu.addAction(self._display_settings_action)

        # ── 纠错快捷菜单 ──
        self._corr_menu = mb.addMenu(_("纠错(&C)"))
        self._corr_preset_action = QAction(_("API 预设管理..."), self)
        self._corr_preset_action.triggered.connect(self._on_menu_preset_manage)
        self._corr_menu.addAction(self._corr_preset_action)

        # ── 模板菜单 ──
        self._template_menu = mb.addMenu(_("模板(&T)"))
        self._template_action_group = QActionGroup(self)
        self._template_action_group.setExclusive(True)
        self._template_menu.addSeparator()
        self._template_edit_action = QAction(_("📝 编辑模板..."), self)
        self._template_edit_action.triggered.connect(self._on_template_edit)
        self._template_menu.addAction(self._template_edit_action)
        self._template_menu.addSeparator()
        self._template_import_action = QAction(_("📥 导入模板..."), self)
        self._template_import_action.triggered.connect(self._on_template_import)
        self._template_menu.addAction(self._template_import_action)
        self._template_export_action = QAction(_("📤 导出模板..."), self)
        self._template_export_action.triggered.connect(self._on_template_export)
        self._template_menu.addAction(self._template_export_action)

        # ── 批量菜单 ──
        self._batch_menu = mb.addMenu(_("批量(&B)"))
        self._batch_clear_action = QAction(_("🗑 清空队列"), self)
        self._batch_clear_action.triggered.connect(self._on_batch_clear)
        self._batch_menu.addAction(self._batch_clear_action)

        # ── 语言菜单 ──
        self._language_menu = mb.addMenu(_("语言(&L)"))
        self._lang_action_group = QActionGroup(self)
        self._lang_action_group.setExclusive(True)
        current_lang = LanguageManager().current_language
        for code, display in LANGUAGE_DISPLAY_NAMES.items():
            action = QAction(display, self)
            action.setCheckable(True)
            action.setChecked(code == current_lang)
            action.triggered.connect(lambda checked, c=code: self._on_switch_language(c))
            self._lang_action_group.addAction(action)
            self._language_menu.addAction(action)

    def _on_switch_language(self, lang_code: str):
        """切换语言。"""
        if lang_code not in SUPPORTED_LANGUAGES:
            return
        if not LanguageManager().switch_language(lang_code):
            return
        # 持久化保存语言设置（_on_language_changed 监听器会自动处理 UI 更新）
        self._config_mgr.set_language(lang_code)

    def _retranslate_ui(self):
        """重新翻译所有用户可见字符串（语言切换时调用）。"""
        # ── 窗口标题 ──
        self.setWindowTitle(_("ORCP - OCR 处理工具"))

        # ── 状态栏 ──
        self._status_label.setText(_("就绪"))
        current_engine = getattr(self, '_current_engine', 'paddleocr')
        self._engine_label.setText(_("  |  引擎: paddleocr").replace("paddleocr", current_engine))

        # ── 快速工具栏 ──
        if hasattr(self, '_qt_corr'):
            self._qt_corr.setText(_("🔤 AI纠错"))
            self._qt_corr.setToolTip(_("启用/关闭 AI 纠错"))
        if hasattr(self, '_qt_hw'):
            self._qt_hw.setText(_("⚡ GPU"))
            self._qt_hw.setToolTip(_("启用/关闭 GPU 硬件加速"))
        if hasattr(self, '_qt_dedup'):
            self._qt_dedup.setText(_("🔍 去重"))
            self._qt_dedup.setToolTip(_("启用/关闭后处理相似度去重"))
        if hasattr(self, '_qt_translate'):
            self._qt_translate.setText(_("🌐 翻译"))
            self._qt_translate.setToolTip(_("翻译模式：将 OCR 结果翻译为中文"))
        if hasattr(self, '_qt_sentinel'):
            self._qt_sentinel.setText(_("🛡 哨兵"))
            self._qt_sentinel.setToolTip(_("启用/关闭哨兵去重（字数骤降检测触发输出）"))
        if hasattr(self, '_qt_clear_cache'):
            self._qt_clear_cache.setText(_("🗑 清缓存"))
            self._qt_clear_cache.setToolTip(_("清除所有缓存（LLM 响应缓存 + ASR 结果缓存）"))

        # ── 快速工具栏标签 ──
        if hasattr(self, '_qt_subtitle_label'):
            self._qt_subtitle_label.setText(_("字幕"))
        if hasattr(self, '_qt_process_label'):
            self._qt_process_label.setText(_("模式"))

        # ── 按钮 ──
        if hasattr(self, '_btn_capture'):
            self._btn_capture.setText(_("📸 截取帧"))
            self._btn_capture.setToolTip(_("截取当前视频帧用于预览和区域绘制"))
        if hasattr(self, '_btn_open'):
            self._btn_open.setText(_("📂 打开文件"))
            self._btn_open.setToolTip(_("打开视频、音频或图片文件"))
        if hasattr(self, '_btn_batch_clear'):
            self._btn_batch_clear.setText(_("🗑 清空"))
        if hasattr(self, '_btn_start'):
            self._btn_start.setText(_("▶ 开始处理"))
        if hasattr(self, '_btn_pause'):
            is_paused = self._btn_pause.text() in ("▶ 继续", "▶ Resume", "▶ 再開")
            self._btn_pause.setText(_("⏸ 暂停") if not is_paused else _("▶ 继续"))
        if hasattr(self, '_btn_stop'):
            self._btn_stop.setText(_("⏹ 停止"))
        if hasattr(self, '_btn_correction'):
            self._btn_correction.setText(_("✏ 纠错选中"))
        if hasattr(self, '_btn_correction_all'):
            self._btn_correction_all.setText(_("✏ 纠错全部"))
        if hasattr(self, '_btn_polish'):
            self._btn_polish.setText(_("✨ 润色选中"))
        if hasattr(self, '_btn_polish_all'):
            self._btn_polish_all.setText(_("✨ 润色全部"))

        # ── 菜单 ──
        if hasattr(self, '_settings_menu'):
            self._settings_menu.setTitle(_("参数设置(&P)"))
            menu_labels = [
                _("⚙ 全部参数..."),
                _("基础设置..."),
                _("语音识别..."),
                _("OCR 字幕处理..."),
                _("AI 纠错..."),
                _("结果输出..."),
            ]
            for action, label in zip(self._settings_menu_actions, menu_labels, strict=False):
                action.setText(label)
        if hasattr(self, '_display_menu'):
            self._display_menu.setTitle(_("显示(&V)"))
            self._display_theme_action.setText(_("切换主题 (亮色/暗色)"))
            self._display_settings_action.setText(_("显示设置..."))
        if hasattr(self, '_corr_menu'):
            self._corr_menu.setTitle(_("纠错(&C)"))
            self._corr_preset_action.setText(_("API 预设管理..."))
        if hasattr(self, '_template_menu'):
            self._template_menu.setTitle(_("模板(&T)"))
            self._template_edit_action.setText(_("📝 编辑模板..."))
            self._template_import_action.setText(_("📥 导入模板..."))
            self._template_export_action.setText(_("📤 导出模板..."))
        if hasattr(self, '_batch_menu'):
            self._batch_menu.setTitle(_("批量(&B)"))
            self._batch_clear_action.setText(_("🗑 清空队列"))
        if hasattr(self, '_language_menu'):
            self._language_menu.setTitle(_("语言(&L)"))

        # ── 快速工具栏 combo（用 index 保持选中项，不依赖文本翻译）──
        if hasattr(self, '_qt_subtitle_mode'):
            self._qt_subtitle_mode.blockSignals(True)
            saved_idx = self._qt_subtitle_mode.currentIndex()
            self._qt_subtitle_mode.clear()
            self._qt_subtitle_mode.addItems([_("流式"), _("常规")])
            self._qt_subtitle_mode.setCurrentIndex(min(saved_idx, 1))
            self._qt_subtitle_mode.blockSignals(False)
        if hasattr(self, '_qt_process_mode'):
            self._qt_process_mode.blockSignals(True)
            saved_idx = self._qt_process_mode.currentIndex()
            self._qt_process_mode.clear()
            self._qt_process_mode.addItems([_("OCR+ASR"), _("仅OCR"), _("仅ASR")])
            self._qt_process_mode.setCurrentIndex(min(saved_idx, 2))
            self._qt_process_mode.blockSignals(False)

        if hasattr(self, '_region_group'):
            self._region_group._title_label.setText(_("📐 区域参数"))
        if hasattr(self, '_subtitle_group'):
            self._subtitle_group._title_label.setText(_("📝 字幕设置"))
        if hasattr(self, '_asr_group'):
            self._asr_group._title_label.setText(_("🎤 ASR 选项"))
        if hasattr(self, '_post_group'):
            self._post_group._title_label.setText(_("🔧 后处理"))

        # ── 右侧字幕模式下拉（用 index 保持选中项）──
        if hasattr(self, '_subtitle_mode_combo_r'):
            self._subtitle_mode_combo_r.blockSignals(True)
            saved_idx = self._subtitle_mode_combo_r.currentIndex()
            self._subtitle_mode_combo_r.clear()
            self._subtitle_mode_combo_r.addItems([_("流式字幕（去重）"), _("常规字幕（固定间隔）")])
            self._subtitle_mode_combo_r.setCurrentIndex(min(saved_idx, 1))
            self._subtitle_mode_combo_r.blockSignals(False)

        # ── 右侧面板行标签（直接引用）──
        _label_updates = [
            ('_lbl_subtitle_mode', _("模式:")),
            ('_lbl_frame_interval', _("帧间隔:")),
            ('_lbl_asr_model', _("模型:")),
            ('_lbl_asr_lang', _("语言:")),
            ('_lbl_asr_region', _("区域名:")),
            ('_lbl_post_sim_threshold', _("相似度阈值:")),
            ('_lbl_post_min_text_len', _("最小文字长度:")),
            ('_lbl_post_conf_threshold', _("置信度阈值:")),
        ]
        for attr, text in _label_updates:
            lbl = getattr(self, attr, None)
            if lbl:
                lbl.setText(text)
        # checkbox / tooltip
        if hasattr(self, '_post_sim_dedup_r'):
            self._post_sim_dedup_r.setText(_("相似度去重"))
        if hasattr(self, '_post_conf_check_r'):
            self._post_conf_check_r.setText(_("置信度过滤"))
        if hasattr(self, '_frame_interval_r'):
            self._frame_interval_r.setToolTip(_("每隔多少秒处理一帧"))
        if hasattr(self, '_asr_model_combo_r'):
            self._asr_model_combo_r.setToolTip(_("ASR 模型选择"))
        if hasattr(self, '_asr_lang_combo_r'):
            self._asr_lang_combo_r.setToolTip(_("识别语言"))
        if hasattr(self, '_asr_region_edit_r'):
            self._asr_region_edit_r.setToolTip(_("ASR 结果在表格中的区域名"))
        if hasattr(self, '_post_sim_threshold_r'):
            self._post_sim_threshold_r.setToolTip(_("相似度高于此阈值的结果将被去重合并"))
        if hasattr(self, '_post_min_text_len_r'):
            self._post_min_text_len_r.setToolTip(_("小于此长度的结果将被过滤"))
        if hasattr(self, '_post_conf_threshold_r'):
            self._post_conf_threshold_r.setToolTip(_("仅 PaddleOCR：置信度低于此阈值的结果将被过滤"))

        # ── 区域管理器 ──
        if hasattr(self, '_region_manager'):
            self._region_manager._retranslate_ui()

        # ── 结果表格 ──
        if hasattr(self, '_result_table'):
            self._result_table._retranslate_strings()

    def _on_language_changed(self, lang_code: str):
        """语言切换监听器回调。"""
        # 更新语言菜单选中状态
        for a in self._lang_action_group.actions():
            for code, display in LANGUAGE_DISPLAY_NAMES.items():
                if a.text() == display:
                    a.setChecked(code == lang_code)
                    break
        self._retranslate_ui()

    # ── 构建 UI ──
    def build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── 主拆分器：上（视频+控制） / 下（结果+操作） ──
        self._main_splitter = QSplitter(Qt.Vertical)
        self._main_splitter.setObjectName("mainSplitter")
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setOpaqueResize(True)
        self._main_splitter.setHandleWidth(4)

        # ═══ 上半区：视频预览 + 右侧区域管理/快速控制 ═══
        self._top_splitter = QSplitter(Qt.Horizontal)
        self._top_splitter.setObjectName("topSplitter")
        self._top_splitter.setChildrenCollapsible(False)
        self._top_splitter.setOpaqueResize(True)
        self._top_splitter.setHandleWidth(4)

        # 左：视频预览 + 工具栏
        left = QWidget()
        ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(6)
        vc = QHBoxLayout()
        vc.setSpacing(6)
        self._btn_capture = QPushButton(_("📸 截取帧"))
        self._btn_capture.setToolTip(_("截取当前视频帧用于预览和区域绘制"))
        self._btn_capture.setFixedHeight(30)
        self._btn_capture.clicked.connect(self._on_capture_test_frame)
        vc.addWidget(self._btn_capture)
        self._btn_open = QPushButton(_("📂 打开文件"))
        self._btn_open.setToolTip(_("打开视频、音频或图片文件"))
        self._btn_open.setFixedHeight(30)
        self._btn_open.clicked.connect(self._on_open_video)
        vc.addWidget(self._btn_open)
        self._btn_batch_clear = QPushButton(_("🗑 清空"))
        self._btn_batch_clear.setObjectName("btnBatchClear")
        self._btn_batch_clear.setFixedHeight(30)
        self._btn_batch_clear.clicked.connect(self._on_batch_clear)
        vc.addWidget(self._btn_batch_clear); vc.addStretch()
        ll.addLayout(vc); ll.addSpacing(4)

        self._video_preview = VideoPreviewWidget()
        self._video_preview.video_loaded.connect(self._on_video_loaded)
        self._video_preview.frame_captured.connect(self._on_frame_captured)
        self._video_preview.regions_changed.connect(self._on_preview_regions_changed)
        self._video_preview.files_dropped.connect(self._on_batch_files_dropped)
        ll.addWidget(self._video_preview, 1)
        self._top_splitter.addWidget(left)

        # 右：区域参数 + ASR 选项（可折叠）
        self._right_panel = QFrame()
        self._right_panel.setObjectName("rightPanel")
        self._right_panel.setFrameShape(QFrame.NoFrame)
        self._right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        rl = QVBoxLayout(self._right_panel); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)

        # 整体滚动区域
        from PyQt5.QtWidgets import QScrollArea
        self._right_scroll = QScrollArea()
        self._right_scroll.setWidgetResizable(True)
        self._right_scroll.setFrameShape(QFrame.NoFrame)
        self._right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scl = QVBoxLayout(scroll_content)
        scl.setContentsMargins(6, 6, 6, 6)
        scl.setSpacing(4)

        # ── RegionManager ──
        self._region_manager = RegionManagerWidget()
        self._region_manager.region_selected.connect(self._on_region_selected)
        self._region_manager.region_updated.connect(self._on_region_updated)
        self._region_manager.region_add_requested.connect(self._on_add_region_requested)
        self._region_manager.region_removed.connect(self._on_remove_region)
        self._region_manager.regions_cleared.connect(self._on_clear_regions)

        # 快速模板/提示词行
        self._tpl_bar = QFrame()
        self._tpl_bar.setObjectName("tplBar")
        tpl_bl = QHBoxLayout(self._tpl_bar)
        tpl_bl.setContentsMargins(4, 4, 4, 4); tpl_bl.setSpacing(4)
        tpl_bl.addWidget(QLabel(_("模板:")))
        self._template_combo = QComboBox()
        self._template_combo.currentTextChanged.connect(self._on_template_quick_selected)
        tpl_bl.addWidget(self._template_combo, 1)

        # 区域参数折叠组
        self._region_group = CollapsibleGroup(_("📐 区域参数"))
        self._region_group.addWidget(self._region_manager)
        self._region_group.content_layout().addWidget(self._tpl_bar)
        scl.addWidget(self._region_group)

        # ── 字幕设置折叠组 ──
        self._subtitle_group = CollapsibleGroup(_("📝 字幕设置"))
        subtitle_form = QWidget()
        subtitle_layout = QFormLayout(subtitle_form)
        subtitle_layout.setSpacing(6)

        self._subtitle_mode_combo_r = QComboBox()
        self._subtitle_mode_combo_r.addItems([_("流式字幕（去重）"), _("常规字幕（固定间隔）")])
        self._subtitle_mode_combo_r.setToolTip(_("流式：哨兵去重实时输出\n常规：固定间隔采样"))
        self._subtitle_mode_combo_r.currentTextChanged.connect(self._on_subtitle_mode_r_changed)
        self._lbl_subtitle_mode = QLabel(_("模式:"))
        subtitle_layout.addRow(self._lbl_subtitle_mode, self._subtitle_mode_combo_r)

        self._frame_interval_r = QDoubleSpinBox()
        self._frame_interval_r.setRange(0.02, 10.0)
        self._frame_interval_r.setSingleStep(0.1)
        self._frame_interval_r.setDecimals(2)
        self._frame_interval_r.setValue(0.1)
        self._frame_interval_r.setSuffix(_(" 秒"))
        self._frame_interval_r.setToolTip(_("每隔多少秒处理一帧"))
        self._frame_interval_r.valueChanged.connect(self._on_frame_interval_r_changed)
        self._lbl_frame_interval = QLabel(_("帧间隔:"))
        subtitle_layout.addRow(self._lbl_frame_interval, self._frame_interval_r)

        self._subtitle_group.addWidget(subtitle_form)
        scl.addWidget(self._subtitle_group)

        # ASR 折叠组
        self._asr_group = CollapsibleGroup(_("🎤 ASR 选项"), collapsed=True)
        asr_form = QWidget()
        self._asr_form = asr_layout = QFormLayout(asr_form)
        asr_layout.setSpacing(6)

        self._asr_model_combo_r = QComboBox()
        self._asr_model_combo_r.setEditable(False)
        self._asr_model_combo_r.setToolTip(_("ASR 模型选择"))
        self._populate_asr_model_combo(self._asr_model_combo_r)
        self._lbl_asr_model = QLabel(_("模型:"))
        asr_layout.addRow(self._lbl_asr_model, self._asr_model_combo_r)

        self._asr_lang_combo_r = QComboBox()
        self._asr_lang_combo_r.setEditable(False)
        self._asr_lang_combo_r.addItems(["auto", "zh", "en", "ja", "ko"])
        self._asr_lang_combo_r.setCurrentText("zh")
        self._asr_lang_combo_r.setToolTip(_("识别语言"))
        self._lbl_asr_lang = QLabel(_("语言:"))
        asr_layout.addRow(self._lbl_asr_lang, self._asr_lang_combo_r)

        self._asr_region_edit_r = QLineEdit(_("语音"))
        self._asr_region_edit_r.setToolTip(_("ASR 结果在表格中的区域名"))
        self._lbl_asr_region = QLabel(_("区域名:"))
        asr_layout.addRow(self._lbl_asr_region, self._asr_region_edit_r)

        # 同步到 config_panel
        self._asr_model_combo_r.currentTextChanged.connect(self._on_asr_r_changed)
        self._asr_lang_combo_r.currentTextChanged.connect(self._on_asr_r_changed)
        self._asr_region_edit_r.textChanged.connect(self._on_asr_r_changed)

        self._asr_group.addWidget(asr_form)
        scl.addWidget(self._asr_group)

        # ── 后处理折叠组 ──
        self._post_group = CollapsibleGroup(_("🔧 后处理"), collapsed=True)
        post_form = QWidget()
        self._post_form = post_layout = QFormLayout(post_form)
        post_layout.setSpacing(6)

        self._post_sim_dedup_r = QCheckBox(_("相似度去重"))
        self._post_sim_dedup_r.setChecked(True)
        self._post_sim_dedup_r.toggled.connect(self._on_post_option_r_changed)
        post_layout.addRow("", self._post_sim_dedup_r)

        self._post_sim_threshold_r = QDoubleSpinBox()
        self._post_sim_threshold_r.setRange(0.0, 1.0)
        self._post_sim_threshold_r.setSingleStep(0.05)
        self._post_sim_threshold_r.setDecimals(2)
        self._post_sim_threshold_r.setValue(0.9)
        self._post_sim_threshold_r.setToolTip(_("相似度高于此阈值的结果将被去重合并"))
        self._post_sim_threshold_r.valueChanged.connect(self._on_post_option_r_changed)
        self._lbl_post_sim_threshold = QLabel(_("相似度阈值:"))
        post_layout.addRow(self._lbl_post_sim_threshold, self._post_sim_threshold_r)

        self._post_min_text_len_r = QSpinBox()
        self._post_min_text_len_r.setRange(1, 100)
        self._post_min_text_len_r.setValue(2)
        self._post_min_text_len_r.setToolTip(_("小于此长度的结果将被过滤"))
        self._post_min_text_len_r.valueChanged.connect(self._on_post_option_r_changed)
        self._lbl_post_min_text_len = QLabel(_("最小文字长度:"))
        post_layout.addRow(self._lbl_post_min_text_len, self._post_min_text_len_r)

        self._post_conf_check_r = QCheckBox(_("置信度过滤"))
        self._post_conf_check_r.setChecked(False)
        self._post_conf_check_r.toggled.connect(self._on_post_option_r_changed)
        post_layout.addRow("", self._post_conf_check_r)

        self._post_conf_threshold_r = QDoubleSpinBox()
        self._post_conf_threshold_r.setRange(0.0, 1.0)
        self._post_conf_threshold_r.setSingleStep(0.05)
        self._post_conf_threshold_r.setDecimals(2)
        self._post_conf_threshold_r.setValue(0.6)
        self._post_conf_threshold_r.setToolTip(_("仅 PaddleOCR：置信度低于此阈值的结果将被过滤"))
        self._post_conf_threshold_r.valueChanged.connect(self._on_post_option_r_changed)
        self._lbl_post_conf_threshold = QLabel(_("置信度阈值:"))
        post_layout.addRow(self._lbl_post_conf_threshold, self._post_conf_threshold_r)

        self._post_group.addWidget(post_form)
        scl.addWidget(self._post_group)

        # 底部弹性空间
        scl.addStretch()

        # 将滚动内容设置到滚动区域
        self._right_scroll.setWidget(scroll_content)
        rl.addWidget(self._right_scroll)

        # 折叠时动态切换 stretch：展开→区域组填充，折叠→底部占位填充
        self._region_group.toggled.connect(self._on_region_group_toggled)

        self._top_splitter.addWidget(self._right_panel)
        self._top_splitter.setSizes([720, 320])
        self._top_splitter.setStretchFactor(0, 7)
        self._top_splitter.setStretchFactor(1, 3)

        self._main_splitter.addWidget(self._top_splitter)

        # ═══ 下半区：结果表格 + 底部操作栏 ═══
        bottom = QWidget()
        bl = QVBoxLayout(bottom); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(6)

        self._result_table = ResultTableWidget()
        self._result_table.filter_requested.connect(self._on_result_filter)
        self._result_table.delete_filtered_requested.connect(self._on_delete_filtered_results)
        self._result_table.export_requested.connect(self._on_export)
        self._result_table.cell_edit_activated.connect(self._on_result_cell_edit)
        bl.addWidget(self._result_table, 1)

        # 底部操作栏
        bar = QFrame()
        bar.setObjectName("bottomBar")
        bbl = QHBoxLayout(bar)
        bbl.setContentsMargins(10, 8, 10, 8)
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
        sep1 = QFrame(); sep1.setObjectName("barSeparator")
        sep1.setFrameShape(QFrame.VLine); sep1.setFixedHeight(24)
        bbl.addWidget(sep1)

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
        sep2 = QFrame(); sep2.setObjectName("barSeparator")
        sep2.setFrameShape(QFrame.VLine); sep2.setFixedHeight(24)
        bbl.addWidget(sep2)

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
        bl.addWidget(bar)

        self._main_splitter.addWidget(bottom)
        self._main_splitter.setSizes([400, 600])
        self._main_splitter.setStretchFactor(0, 2)
        self._main_splitter.setStretchFactor(1, 3)

        root.addWidget(self._main_splitter, 1)

        # ── ConfigPanel（纯状态管理类，无 UI）──
        self._config_panel = ConfigPanel()
        self._config_panel.prompt_changed.connect(self._on_prompt_changed)
        self._config_panel.mode_changed.connect(self._on_mode_changed)
        self._config_panel.template_created.connect(self._on_config_template_selected)
        self._config_panel.template_saved.connect(self._on_config_template_saved)
        self._config_panel.template_deleted.connect(self._on_config_template_deleted)
        self._config_panel.template_selected_for_correction.connect(
            lambda c: self._corrector.set_template_content(c) if self._corrector else None)
        self._config_panel.hw_accel_changed.connect(self._on_hw_accel_changed)
        self._config_panel.filter_add_requested.connect(self._on_filter_add)
        self._config_panel.filter_remove_requested.connect(self._on_filter_remove)
        self._config_panel.extract_env_clicked.connect(self._on_extract_env)

    # ── 主题 ──
    def _apply_theme(self, theme=None):
        self._theme = theme or self._theme
        self._config_mgr.set("theme", self._theme)
        self._config_mgr.save_settings()
        app = QApplication.instance()
        if app:
            scale = self._config_mgr.get_scale()
            font_size = self._config_mgr.get_font_size()
            font_family = "Microsoft YaHei UI"
            density = "0"
            if scale < 0.9:
                density = "-2"
            elif scale < 1.0:
                density = "-1"
            elif scale > 1.1:
                density = "1"
            elif scale > 1.2:
                density = "2"
            apply_theme(app, self._theme, font_family=font_family, density_scale=density)
            # 追加字体大小覆盖
            if font_size != 13:
                current = app.styleSheet()
                font_override = f"* {{ font-size: {font_size}px; }}"
                app.setStyleSheet(current + "\n" + font_override)
            # 动态调整固定尺寸控件
            self._apply_scale_to_fixed_widgets(font_size, scale)

    def _toggle_theme(self):
        if is_dark_theme(self._theme):
            self._apply_theme(DEFAULT_LIGHT)
        else:
            self._apply_theme(DEFAULT_DARK)

    def _set_progress_animated(self, value: int):
        """平滑动画更新进度条。"""
        if hasattr(self, '_progress_anim'):
            self._progress_anim.stop()
            self._progress_anim.setStartValue(self._progress_bar.value())
            self._progress_anim.setEndValue(value)
            self._progress_anim.start()
        else:
            self._progress_bar.setValue(value)

    # ── 参数设置对话框 ──
    def _open_settings(self, tab_index: int = -1):
        """打开参数设置对话框，合并处理参数 + 纠错 API 配置。"""
        corr_cfg = load_correction_config()
        corr_cfg.setdefault("enabled", self._corrector.enabled)
        corr_cfg.setdefault("api_key", "")
        corr_cfg.setdefault("base_url", "http://127.0.0.1:8080")
        corr_cfg.setdefault("model", "")
        corr_cfg.setdefault("timeout", 30)
        corr_cfg.setdefault("retry_on_failure", 2)

        dlg = SettingsDialog(self._config_panel, correction_config=corr_cfg, parent=self,
                             filter_keywords=self._filter_mgr.get_keywords(),
                             engine_manager=self._engine_mgr, current_engine=self._current_engine)
        if 0 <= tab_index < dlg._tabs.count():
            dlg._tabs.setCurrentIndex(tab_index)
        elif tab_index == -1:
            dlg._tabs.setCurrentIndex(0)

        self._restore_dialog_geometry(dlg, "settings_dialog_geometry")

        if dlg.exec_() == QDialog.Accepted:
            self._save_dialog_geometry(dlg, "settings_dialog_geometry")
            # 保存处理参数
            self._on_mode_changed(self._config_panel.get_mode_params())
            # 保存纠错 API 配置
            api_cfg = dlg.get_corr_api_config()
            preset_name = self._config_panel.corr_preset_name
            self._corrector = AICorrector(api_cfg, engine_manager=self._engine_mgr, preset_name=preset_name)
            self._workflow._corrector = self._corrector  # 同步到工作流
            corr_file_cfg = load_correction_config()
            corr_file_cfg.update(api_cfg)
            config_path = BASE_DIR / "config" / "ai_correction.json"
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(corr_file_cfg, f, ensure_ascii=False, indent=2)
            # 保存引擎配置
            eng_name, eng_cfg = dlg.get_engine_config()
            engs = self._engine_mgr._config.get("engines", {})
            if eng_name in engs:
                engs[eng_name].setdefault("config", {}).update(eng_cfg)
                self._engine_mgr._engines.pop(eng_name, None)
            ocr_cfg_path = BASE_DIR / "config" / "ocr_engines.json"
            try:
                with open(ocr_cfg_path, "w", encoding="utf-8") as f:
                    json.dump(self._engine_mgr._config, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error("保存引擎配置失败: %s", e)
            self._status_label.setText("✅ 参数设置已更新")
            self.sync_quick_toggles()

    def _open_display_settings(self):
        """打开显示设置对话框。"""
        dlg = DisplayDialog(
            theme=self._theme,
            font_size=self._config_mgr.get_font_size(),
            ui_scale=self._config_mgr.get_scale(),
            parent=self,
        )
        dlg.theme_applied.connect(self._apply_theme_from_dialog)
        self._restore_dialog_geometry(dlg, "display_dialog_geometry")
        if dlg.exec_() == QDialog.Accepted:
            self._save_dialog_geometry(dlg, "display_dialog_geometry")
            dlg.get_config()  # 触发配置收集
            self._status_label.setText("✅ 显示设置已更新")

    def _apply_theme_from_dialog(self, theme: str, font_size: int, scale: float):
        """从显示设置对话框应用主题/字体/缩放。"""
        self._theme = theme
        self._config_mgr.set("theme", theme)
        self._config_mgr.set("font_size", font_size)
        self._config_mgr.set("ui_scale", scale)
        self._config_mgr.save_settings()
        # 重新应用完整主题（含 density_scale + font_size + 控件尺寸）
        self._apply_theme(theme)

    def _apply_scale_to_fixed_widgets(self, font_size: int, scale: float):
        """根据字号和缩放比例动态调整固定尺寸的控件，避免文字挤压。"""
        # 按钮高度：基准 34px，字号每增大 1px 高度 +2px，缩放额外影响
        btn_h = max(28, int(34 * scale + (font_size - 13) * 1.5))
        for btn_attr in ('_btn_start', '_btn_pause', '_btn_stop',
                         '_btn_correction', '_btn_correction_all',
                         '_btn_polish', '_btn_polish_all'):
            btn = getattr(self, btn_attr, None)
            if btn:
                btn.setFixedHeight(btn_h)

        # 工具栏按钮高度
        capture_h = max(26, int(30 * scale + (font_size - 13) * 1.2))
        for btn_attr in ('_btn_capture', '_btn_open', '_btn_batch_clear'):
            btn = getattr(self, btn_attr, None)
            if btn:
                btn.setFixedHeight(capture_h)

        # 状态栏高度
        bar_h = max(22, int(26 * scale + (font_size - 13) * 0.8))
        self._status_bar.setMinimumHeight(bar_h)

        # 进度条高度
        prog_h = max(14, int(18 * scale))
        self._progress_bar.setMaximumHeight(prog_h)

        # 分隔线高度
        sep_h = max(18, int(24 * scale))
        for sep in self._main_splitter.findChildren(QFrame):
            if sep.objectName() == "barSeparator":
                sep.setFixedHeight(sep_h)

    def _on_template_quick_selected(self, name: str):
        """快速模板下拉框选中。"""
        if not name:
            return
        self._current_template = name
        t = self._prompt_mgr.get_template_by_name(name)
        if t:
            prompt = t.get("prompt", "")
            self._config_panel.prompt_text = prompt
            # 在状态栏显示模板描述（如果有）
            desc = t.get("description", "")
            if desc:
                self.statusBar().showMessage(f"模板: {name} — {desc}", 3000)
        self._config_panel.select_template(name)
        self._sync_region_defaults()
        # 同步菜单栏
        for a in self._template_action_group.actions():
            a.setChecked(a.text() == name)

    # ── 对话框几何存取（Qt 原生 saveGeometry/restoreGeometry）──
    def _restore_dialog_geometry(self, dlg: QDialog, key: str):
        """从 settings 恢复对话框几何（兼容旧 [w,h] 格式）。"""
        val = self._config_mgr.get(key, "")
        if not val:
            return
        from PyQt5.QtCore import QByteArray
        if isinstance(val, list):
            # 旧格式 [width, height] → 只恢复尺寸
            dlg.resize(val[0], val[1])
        else:
            dlg.restoreGeometry(QByteArray.fromBase64(val.encode()))

    def _save_dialog_geometry(self, dlg: QDialog, key: str):
        """保存对话框几何到 settings 并立即写盘。"""
        geo = dlg.saveGeometry().toBase64().data().decode()
        self._config_mgr.set(key, geo)
        self._config_mgr.save_settings()

    # ── 窗口状态 ──
    def _restore_window_geometry(self):
        g = self._config_mgr.get_window_geometry()
        if g: self.setGeometry(g.get("x", 100), g.get("y", 100),
                                g.get("width", 1400), g.get("height", 900))
        else: self.resize(1400, 900)
        if self._config_mgr.get("window_maximized", False):
            self.showMaximized()
        # 恢复拆分器尺寸
        main_sizes = self._config_mgr.get_splitter_sizes()
        if main_sizes and len(main_sizes) == 2:
            self._main_splitter.setSizes(main_sizes)
        top_sizes = self._config_mgr.get("top_splitter_sizes")
        if top_sizes and len(top_sizes) == 2:
            self._top_splitter.setSizes(top_sizes)
        # 恢复配置面板最后选中的标签页（ConfigPanel 已改为纯状态类，不再有 _tabs）

    def _save_window_geometry(self):
        self._config_mgr.set("window_maximized", self.isMaximized())
        if not self.isMaximized():
            g = self.geometry()
            self._config_mgr.set("window_geometry",
                {"x": g.x(), "y": g.y(), "width": g.width(), "height": g.height()})
        self._config_mgr.set("splitter_sizes", self._main_splitter.sizes())
        self._config_mgr.set("top_splitter_sizes", self._top_splitter.sizes())
        self._config_mgr.save_settings()

    def _restore_mode_params(self):
        """从 settings.json 恢复 UI 配置参数。"""
        saved = self._config_mgr.get("mode_params", {})
        if saved:
            self._mode_params.update(saved)
            # 恢复自定义提示词
            self._custom_prompt = saved.get("corr_prompt", "")
            # 回填所有 UI 控件
            self._config_panel.apply_mode_params(saved)
            # 显式应用 API 预设：apply_mode_params 中 blockSignals(True) 阻止了
            # _corr_preset_combo.currentTextChanged 信号，导致 apply_preset 未被调用
            saved_preset = saved.get("corr_preset", "")
            if saved_preset and self._corrector:
                self._corrector.apply_preset(saved_preset)
            # 恢复润色开关
            if "corr_polish" in saved and self._corrector:
                self._corrector.polish_enabled = saved["corr_polish"]
            # 恢复右侧面板控件
            self._restore_right_panel_params(saved)
        # 同步区域默认值（引擎/模板/提示词），确保新创建的区域使用当前提示词
        self._sync_region_defaults()

    def _restore_right_panel_params(self, saved: dict):
        """恢复右侧面板控件的值。"""
        # 字幕模式
        subtitle_mode = saved.get("subtitle_mode", "流式字幕（去重）")
        self._subtitle_mode_combo_r.blockSignals(True)
        if "流式" in subtitle_mode:
            self._subtitle_mode_combo_r.setCurrentText(_("流式字幕（去重）"))
        else:
            self._subtitle_mode_combo_r.setCurrentText(_("常规字幕（固定间隔）"))
        self._subtitle_mode_combo_r.blockSignals(False)

        # 帧间隔
        frame_interval = saved.get("frame_interval", 0.1)
        self._frame_interval_r.blockSignals(True)
        self._frame_interval_r.setValue(frame_interval)
        self._frame_interval_r.blockSignals(False)

        # 后处理选项
        self._post_sim_dedup_r.blockSignals(True)
        self._post_sim_dedup_r.setChecked(saved.get("post_sim_dedup", True))
        self._post_sim_dedup_r.blockSignals(False)

        self._post_sim_threshold_r.blockSignals(True)
        self._post_sim_threshold_r.setValue(saved.get("post_sim_threshold", 0.9))
        self._post_sim_threshold_r.blockSignals(False)

        self._post_min_text_len_r.blockSignals(True)
        self._post_min_text_len_r.setValue(saved.get("post_min_text_len", 2))
        self._post_min_text_len_r.blockSignals(False)

        self._post_conf_check_r.blockSignals(True)
        self._post_conf_check_r.setChecked(saved.get("post_conf_enabled", False))
        self._post_conf_check_r.blockSignals(False)

        self._post_conf_threshold_r.blockSignals(True)
        self._post_conf_threshold_r.setValue(saved.get("post_conf_threshold", 0.6))
        self._post_conf_threshold_r.blockSignals(False)

        # ASR 组可见性：仅 OCR 模式时隐藏
        self._asr_group.setVisible(MODE_OCR_ONLY not in saved.get("process_mode", ""))

    def _schedule_mode_save(self):
        """延迟合并保存，避免频繁切换预设时连续写盘卡 UI。"""
        if hasattr(self, '_mode_save_timer'):
            self._mode_save_timer.start(300)
        else:
            from PyQt5.QtCore import QTimer
            self._mode_save_timer = QTimer(self)
            self._mode_save_timer.setSingleShot(True)
            self._mode_save_timer.timeout.connect(self._save_mode_params)
            self._mode_save_timer.start(300)

    def _save_mode_params(self):
        """保存当前 UI 配置参数到 settings.json。"""
        params = {k: v for k, v in self._mode_params.items()
                  if k != "corr_summary_prompt"}  # 环境提示词不持久化
        self._config_mgr.set("mode_params", params)
        # 仅当 ASR 参数实际变更时才同步（避免每次保存都阻塞 UI）
        if getattr(self, '_asr_params_changed', False):
            self._sync_asr_config(params)
            self._asr_params_changed = False
        # 同步纠错配置
        if any(k.startswith("corr_") for k in params):
            self._sync_correction_config(params)
        self._config_mgr.save_settings()

    def closeEvent(self, ev):
        """关闭窗口 —— 先保存状态，再隐藏 UI，后台静默清理。"""
        # ── 第一步：保存状态（必须在 hide 之前，否则 geometry 丢失）──
        self._save_window_geometry()
        self._save_mode_params()

        # ── 第二步：立即隐藏窗口，用户感知到 UI 已关闭 ──
        self.hide()

        # ── 移除全局滚轮拦截过滤器 ──
        app = QApplication.instance()
        if app and hasattr(self, '_wheel_blocker'):
            app.removeEventFilter(self._wheel_blocker)

        # ── 第三步：同步清理子进程（OCR/ASR），确保进程不残留 ──
        try:
            ocr_engine = self._engine_mgr.get_current_engine(warm_up=False)
            if ocr_engine and hasattr(ocr_engine, '_stop_server'):
                ocr_engine._stop_server()
        except Exception as e:
            logger.warning("OCR 引擎清理失败: %s", e)

        if self._asr_mgr:
            try:
                asr_engine = self._asr_mgr.get_engine()
                if asr_engine and hasattr(asr_engine, '_stop_server'):
                    asr_engine._stop_server()
            except Exception as e:
                logger.warning("ASR 引擎清理失败: %s", e)

        vp = self._video_preview
        if vp:
            if getattr(vp, '_player', None):
                try:
                    vp._player.stop()
                except Exception as e:
                    logger.warning("播放器清理失败: %s", e)
            if vp._ffmpeg:
                try:
                    vp._ffmpeg.close()
                except Exception as e:
                    logger.warning("FFmpeg 清理失败: %s", e)

        # ── 第四步：后台清理 worker 线程（不阻塞关闭）──
        def _cleanup_workers():
            try:
                self._workflow.cleanup()
            except Exception as e:
                logger.warning("Worker 清理异常: %s", e)

        import threading
        threading.Thread(target=_cleanup_workers, daemon=True).start()

        # 立即接受关闭事件
        super().closeEvent(ev)

    # ── 刷新 ──
    def _refresh_engine_list(self):
        names = self._engine_mgr.get_engine_names()
        self._region_manager.set_engine_names(names)

        last = self._config_mgr.get_last_engine()
        if last in names:
            self._engine_mgr.set_current_engine(last)
            self._current_engine = last
        else:
            self._current_engine = names[0] if names else "paddleocr"

        self._engine_label.setText(f"  |  引擎: {self._current_engine}")
        eng = self._engine_mgr.get_current_engine(warm_up=False)
        if eng:
            avail = eng.is_available()
            self._engine_label.setText(
                f"  |  引擎: {self._current_engine} {'✅' if avail else '⚠'}"
            )

    def _refresh_template_list(self):
        names = self._prompt_mgr.get_template_names()
        self._region_manager.set_template_names(names)
        self._config_panel.set_template_names(names)
        # 注入模板内容供 AI 纠错 Tab 快速填入
        contents = {}
        for name in names:
            t = self._prompt_mgr.get_template_by_name(name)
            if t and t.get("prompt"):
                contents[name] = t["prompt"]
        self._config_panel.set_template_contents(contents)

        # 更新快速模板下拉框
        self._template_combo.blockSignals(True)
        cur = self._template_combo.currentText()
        self._template_combo.clear()
        self._template_combo.addItems(names)
        if cur in names:
            self._template_combo.setCurrentText(cur)
        elif names:
            self._template_combo.setCurrentIndex(0)
        self._template_combo.blockSignals(False)

        # 重建模板菜单单选动作
        menu = self._template_menu
        for a in self._template_action_group.actions():
            self._template_action_group.removeAction(a)
        for a in menu.actions():
            if a.isSeparator():
                continue
            if a in (self._template_import_action, self._template_export_action,
                      self._template_edit_action):
                continue
            menu.removeAction(a)

        for name in names:
            action = QAction(name, self)
            action.setCheckable(True)
            # 模板描述作为 tooltip
            t = self._prompt_mgr.get_template_by_name(name)
            if t and t.get("description"):
                action.setToolTip(t["description"])
            action.triggered.connect(lambda checked, n=name: self._on_menu_template_selected(n))
            self._template_action_group.addAction(action)
            menu.insertAction(menu.actions()[0], action)

        if names:
            first = names[0]
            self._current_template = first
            for a in self._template_action_group.actions():
                if a.text() == first:
                    a.setChecked(True)
                    break
            t = self._prompt_mgr.get_template_by_name(first)
            if t:
                self._config_panel.prompt_text = t.get("prompt", "")

    # ── 同步区域默认值 ──
    def _sync_region_defaults(self):
        """将当前引擎/模板/提示词同步为新增区域的默认值。"""
        prompt = self._custom_prompt or self._config_panel.prompt_text
        self._video_preview.set_region_defaults(
            engine=self._current_engine,
            prompt=prompt,
            template=self._current_template
        )

    # ── 菜单事件：纠错 ──
    def _on_menu_preset_manage(self):
        """打开 API 预设管理对话框。"""
        dlg = PresetManageDialog(self)
        self._restore_dialog_geometry(dlg, "preset_dialog_geometry")
        if dlg.exec_() == QDialog.Accepted:
            self._save_dialog_geometry(dlg, "preset_dialog_geometry")
        # 无论是否确认都刷新预设状态
        self._status_label.setText("✅ API 预设已更新")

    # ── 菜单事件：模板 ──
    def _on_menu_template_selected(self, name: str):
        self._current_template = name
        t = self._prompt_mgr.get_template_by_name(name)
        if t:
            self._config_panel.prompt_text = t.get("prompt", "")
        self._config_panel.select_template(name)
        self._sync_region_defaults()

    # ── ConfigPanel 模板信号 ──
    def _on_config_template_selected(self, name: str):
        """配置面板选中模板 → 加载提示词 + 同步菜单。"""
        t = self._prompt_mgr.get_template_by_name(name)
        if t:
            self._config_panel.prompt_text = t.get("prompt", "")
        self._current_template = name
        for a in self._template_action_group.actions():
            a.setChecked(a.text() == name)
        self._sync_region_defaults()

    def _on_config_template_saved(self, name: str, prompt: str):
        """配置面板保存模板。"""
        t = self._prompt_mgr.get_template_by_name(name) or {}
        t["name"] = name
        t["prompt"] = prompt
        self._prompt_mgr.add_template(t)
        self._refresh_template_list()
        self._config_panel.select_template(name)
        self._current_template = name
        self._status_label.setText(f"✅ 模板 [{name}] 已保存")

    def _on_config_template_deleted(self, name: str):
        """配置面板删除模板。"""
        self._prompt_mgr.remove_template(name)
        self._refresh_template_list()
        self._status_label.setText(f"🗑 模板 [{name}] 已删除")

    # ── 硬件加速 ──
    def _on_hw_accel_changed(self, enabled: bool):
        """统一控制 PaddleOCR + FFmpeg + ASR 的 GPU 开关。"""
        self._config_mgr.set("hw_accel", enabled)
        self._config_mgr.save_settings()
        self._engine_mgr.set_hw_accel(enabled)
        self._asr_mgr.set_hw_accel(enabled)
        self._video_preview.set_hw_accel(enabled)
        self._status_label.setText(f"{'✅ GPU 加速已启用' if enabled else '🔲 GPU 加速已关闭'}")

    # ── 过滤器管理 ──
    def _on_filter_add(self, keyword: str):
        if self._filter_mgr.add_keyword(keyword):
            self._status_label.setText(f"✅ 已添加过滤器: {keyword}")

    def _on_filter_remove(self, keyword: str):
        logger.info("收到删除过滤关键词请求: '%s'", keyword[:40])
        if self._filter_mgr.remove_keyword(keyword):
            self._status_label.setText(f"🗑 已移除过滤器: {keyword}")
        else:
            self._status_label.setText(f"⚠ 移除失败: 关键词 '{keyword[:30]}' 不存在")

    def _on_result_filter(self, raw_text: str):
        """表格行'加入过滤器'按钮 → 将整条 raw 文本加入过滤。"""
        if self._filter_mgr.add_keyword(raw_text):
            self._status_label.setText(f"✅ 已添加过滤器: {raw_text[:40]}")


    # ── 批量文件队列 ──
    def _on_batch_files_dropped(self, paths: list):
        """拖放多个文件到预览区时，替换整个批量队列并渲染第一个文件。"""
        self._batch_files = list(paths)
        first = self._batch_files[0]
        ext = Path(first).suffix.lower()
        if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
            self._video_preview.load_video(first)
        elif ext in ('.png', '.jpg', '.jpeg', '.bmp'):
            self._video_preview.load_image(first)
        else:
            self._load_audio_file(first)
        from PyQt5.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        self._update_batch_label()

    def _on_batch_clear(self):
        self._batch_files.clear()
        self._update_batch_label()
        self._video_preview.clear()
        self._status_label.setText("已清空队列和预览")

    def _update_batch_label(self):
        n = len(self._batch_files)
        self._result_table.set_batch_count(n)

    def _on_batch_progress_file(self, fname: str, idx: int, total: int):
        self._set_progress_animated(int(idx * 100 / total))
        self._status_label.setText(f"批量处理 [{idx}/{total}]: {fname}")

    def _on_batch_finished_one(self, file_path: str, results: list):
        self._status_label.setText(f"✅ 完成: {Path(file_path).name} ({len(results)} 条)")

    def _on_batch_finished_all(self, _=None):
        self._btn_correction.setEnabled(True)
        self._set_progress_animated(0)
        n = len(self._batch_files)
        self._status_label.setText(f"✅ 批量处理完成: {n} 个文件 → output/")
        self._batch_files.clear()
        self._update_batch_label()

    # ── 事件 ──
    def _on_video_loaded(self, path):
        self._status_label.setText(f"已加载: {Path(path).name}")
        self._config_mgr.add_recent_video(path)
        self._config_mgr.set("last_directory", str(Path(path).parent))
        self._config_mgr.save_settings()
        self._region_manager.regions = self._video_preview.regions
        self._apply_right_panel_mode()

    def _apply_right_panel_mode(self):
        """根据当前文件类型调整右侧面板可见内容。"""
        is_image = self._video_preview.is_image
        is_audio = getattr(self._video_preview, '_is_audio', False)

        if is_audio:
            self._region_group.hide()
            self._subtitle_group.show()
            self._asr_group.show()
            self._post_group.show()
            self._sync_asr_from_config()
        elif is_image:
            self._region_group.show()
            self._subtitle_group.hide()
            self._asr_group.hide()
            self._post_group.show()
        else:
            self._region_group.show()
            self._subtitle_group.show()
            self._asr_group.show()
            self._post_group.show()
            self._sync_asr_from_config()

    def _on_region_group_toggled(self, collapsed: bool):
        """区域参数折叠/展开时，无需额外操作（滚动区域自动处理）。"""
        pass

    def _populate_asr_model_combo(self, combo: QComboBox):
        """填充 ASR 模型 combo：本地已下载 + 标准模型大小。"""
        from core.asr_engine import scan_local_asr_models
        model_dir = str(BASE_DIR / "models" / "asr")
        local_models = scan_local_asr_models(model_dir)
        combo.blockSignals(True)
        combo.clear()
        for path in local_models:
            display = os.path.basename(path) if os.path.isdir(path) else path
            combo.addItem(f"📁 {display}", path)
        standard = [
            "tiny", "tiny.en", "base", "base.en", "small", "small.en",
            "medium", "medium.en", "large-v1", "large-v2", "large-v3",
            "distil-small.en", "distil-medium.en", "distil-large-v2",
        ]
        for size in standard:
            if any(os.path.basename(p) == size for p in local_models):
                continue
            combo.addItem(f"⬇ {size}（在线下载）", size)
        combo.blockSignals(False)

    def _sync_asr_from_config(self):
        """从 ConfigPanel 的 ASR 状态同步到右侧面板紧凑控件。"""
        cp = self._config_panel
        # 同步模型选择
        self._populate_asr_model_combo(self._asr_model_combo_r)
        model_path = cp.asr_model or cp.asr_model_size
        if model_path:
            self._asr_model_combo_r.blockSignals(True)
            for i in range(self._asr_model_combo_r.count()):
                if self._asr_model_combo_r.itemData(i) == model_path:
                    self._asr_model_combo_r.setCurrentIndex(i)
                    break
            self._asr_model_combo_r.blockSignals(False)
        # 同步语言
        self._asr_lang_combo_r.blockSignals(True)
        self._asr_lang_combo_r.setCurrentText(cp.asr_language)
        self._asr_lang_combo_r.blockSignals(False)
        # 同步区域名
        self._asr_region_edit_r.blockSignals(True)
        self._asr_region_edit_r.setText(cp.asr_region_name)
        self._asr_region_edit_r.blockSignals(False)

    def _on_asr_r_changed(self):
        """右侧 ASR 控件变更 → 同步到 ConfigPanel。"""
        cp = self._config_panel
        # 同步模型选择
        model_data = self._asr_model_combo_r.currentData()
        if model_data:
            cp.asr_model = model_data
        # 同步语言和区域名
        cp.asr_language = self._asr_lang_combo_r.currentText()
        cp.asr_region_name = self._asr_region_edit_r.text()
        self._on_mode_changed(cp.get_mode_params())

    def _on_subtitle_mode_r_changed(self, text: str):
        """右侧字幕模式变更 → 同步到 ConfigPanel。"""
        self._config_panel.subtitle_mode = text
        self._on_mode_changed(self._config_panel.get_mode_params())
        # 更新快速工具栏
        self.sync_quick_toggles()

    def _on_frame_interval_r_changed(self, value: float):
        """右侧帧间隔变更 → 同步到 ConfigPanel。"""
        params = self._config_panel.get_mode_params()
        params["frame_interval"] = value
        self._config_panel.apply_mode_params(params)
        self._on_mode_changed(self._config_panel.get_mode_params())

    def _on_post_option_r_changed(self):
        """右侧后处理选项变更 → 同步到 ConfigPanel。"""
        params = self._config_panel.get_mode_params()
        params["post_sim_dedup"] = self._post_sim_dedup_r.isChecked()
        params["post_sim_threshold"] = self._post_sim_threshold_r.value()
        params["post_min_text_len"] = self._post_min_text_len_r.value()
        params["post_conf_enabled"] = self._post_conf_check_r.isChecked()
        params["post_conf_threshold"] = self._post_conf_threshold_r.value()
        self._config_panel.apply_mode_params(params)
        self._on_mode_changed(self._config_panel.get_mode_params())
        # 更新快速工具栏
        self.sync_quick_toggles()

    def _on_frame_captured(self, _):
        self._status_label.setText("测试帧已截取，在预览图上拖拽绘制矩形区域")

    def _on_extract_env(self):
        """手动提取全文环境（后台异步，不阻塞 UI）。"""
        results = self._result_table.get_results()
        if not results:
            QMessageBox.warning(self, "提示", "暂无识别结果可提取环境。")
            return
        all_texts = [r.get("raw", "") for r in results if r.get("raw", "").strip()]
        if not all_texts:
            self._status_label.setText("⚠ 无有效文本可提取环境")
            return
        self._status_label.setText("⏳ 正在提取全文环境...")

        from ui.workers import EnvExtractWorker
        self._env_worker = EnvExtractWorker(self._corrector, all_texts)
        self._env_worker.finished.connect(self._on_env_extracted)
        self._env_worker.error.connect(lambda e: self._status_label.setText(f"⚠ 环境提取失败: {e[:30]}"))
        self._env_worker.start()

    def _on_env_extracted(self, env: str):
        """环境提取完成（主线程回调）。"""
        if env:
            self._config_panel.corr_summary_prompt = env
            self._on_mode_changed(self._config_panel.get_mode_params())
            self._status_label.setText("✅ 全文环境已提取并回填")
        else:
            self._status_label.setText("⚠ 环境提取失败，请检查 API 配置")

    def _on_preview_regions_changed(self, regions):
        self._region_manager._block_signals(True)
        self._region_manager.regions = regions
        self._region_manager._block_signals(False)
        # 同步区域名到结果排序下拉框
        names = [r.get("name", "") for r in regions]
        self._config_panel.set_region_names(names)

    def _on_region_selected(self, idx):
        self._video_preview.select_region(idx)

    def _on_region_updated(self, idx, props):
        self._video_preview.update_region(idx, props, emit_signal=False)
    def _on_add_region_requested(self): self._status_label.setText("在视频预览上拖拽鼠标绘制矩形区域")
    def _on_remove_region(self, idx):
        self._video_preview.remove_region(idx)
        self._region_manager.regions = self._video_preview.regions
    def _on_clear_regions(self):
        self._video_preview.clear_regions()
        self._region_manager.regions = []

    def _on_delete_filtered_results(self):
        """删除所有匹配过滤器关键词的结果行。"""
        if not self._result_table.get_results():
            return
        if not self._filter_mgr.get_keywords():
            QMessageBox.information(self, "提示", "请先在「后处理」标签页中添加需要过滤的关键词。")
            return
        fm = self._filter_mgr
        deleted = self._result_table.delete_by_filter(
            lambda raw, corrected: fm.matches(raw + " " + corrected)
        )
        if deleted:
            self._status_label.setText(f"🗑 已删除 {deleted} 条包含关键词的结果")
        else:
            self._status_label.setText("⚠ 无匹配关键词的结果")

    def _on_result_cell_edit(self, row: int):
        """点击/编辑表格行后跳转到对应时间。"""
        results = self._result_table.get_results()
        if row < 0 or row >= len(results):
            return
        r = results[row]
        ts = r.get("time_sec", 0.0) or 0.0
        vp = self._video_preview
        # 图片禁止跳转
        if vp.is_image:
            return
        # 跳转并渲染帧
        if self._is_audio_file():
            # 音频：停止播放 → 设置位置 → 更新 UI
            vp._on_stop_playback()
            vp._current_position = ts
            vp._set_slider(ts)
            vp._update_preview_label()
            if vp._audio_player:
                vp._audio_player.setPosition(int(ts * 1000))
        else:
            vp._on_stop_playback()
            vp.seek_to(ts)
        self._status_label.setText(f"已跳转到 {r.get('time', '--:--')}")

    def _on_prompt_changed(self, p):
        self._custom_prompt = p
        self._sync_region_defaults()
    def _on_mode_changed(self, p):
        old_params = getattr(self, '_last_mode_params', {})
        self._mode_params = p
        # 仅当预设名确实变化时才切换
        if "corr_preset" in p and p["corr_preset"] != old_params.get("corr_preset", ""):
            self._corrector.apply_preset(p["corr_preset"])
        # 润色开关 + 模板模式
        if "corr_polish" in p:
            self._corrector.polish_enabled = p["corr_polish"]
        if "corr_use_template" in p:
            self._corrector.use_template = p["corr_use_template"]
        # 标记是否有 ASR 参数实际变更
        asr_keys = {k: p.get(k) for k in p if k.startswith("asr_")}
        old_asr_keys = {k: old_params.get(k) for k in old_params if k.startswith("asr_")}
        self._asr_params_changed = (asr_keys != old_asr_keys)
        # OCR 版本变更 → 重建 OCR 引擎
        if p.get("s_ocr_version") != old_params.get("s_ocr_version"):
            self._restart_ocr_engine()
        # ASR 参数变更 → 重建 ASR 引擎
        if self._asr_params_changed:
            self._restart_asr_engine()
        self._last_mode_params = dict(p)
        # 延迟写盘合并多次连续变更
        self._schedule_mode_save()

    def _sync_asr_config(self, params: dict):
        """将 UI 中的 ASR 参数同步写入 asr_engines.json。"""
        config_path = BASE_DIR / "config" / "asr_engines.json"
        cfg = self._asr_mgr._config
        # asr_model_path 可能是完整本地路径或标准模型名称（如 large-v3）
        model_path = params.get("asr_model_path", "")
        if model_path:
            if os.path.isabs(model_path) or os.sep in model_path:
                # 本地模型完整路径 → 提取目录名作为 model_size，父目录作为 model_dir
                cfg["model_size"] = os.path.basename(model_path)
                parent = os.path.dirname(model_path)
                if parent:
                    cfg["model_dir"] = parent
            else:
                # 标准模型名称（如 large-v3）→ 直接作为 model_size
                cfg["model_size"] = model_path
        else:
            cfg["model_size"] = params.get("asr_model_size", cfg.get("model_size", "large-v3"))
        cfg["language"] = params.get("asr_language", cfg.get("language", "zh"))
        cfg["vad_enabled"] = params.get("asr_vad", cfg.get("vad_enabled", False))
        cfg["vad_min_silence_ms"] = params.get("asr_vad_min_silence", cfg.get("vad_min_silence_ms", 500))
        cfg["vad_threshold"] = params.get("asr_vad_threshold", cfg.get("vad_threshold", 0.5))
        cfg["word_timestamps"] = params.get("asr_word_ts", cfg.get("word_timestamps", True))
        cfg["asr_region_name"] = params.get("asr_region_name", cfg.get("asr_region_name", "语音"))
        cfg["model_dir"] = params.get("asr_model_dir", cfg.get("model_dir", ""))
        cfg["beam_size"] = params.get("asr_beam_size", cfg.get("beam_size", 5))
        cfg["initial_prompt"] = params.get("asr_initial_prompt", cfg.get("initial_prompt", ""))
        cfg["condition_on_previous_text"] = params.get("asr_condition_prev", cfg.get("condition_on_previous_text", True))
        cfg["no_speech_threshold"] = params.get("asr_no_speech_thresh", cfg.get("no_speech_threshold", 0.6))
        cfg["compression_ratio_threshold"] = params.get("asr_comp_ratio_thresh", cfg.get("compression_ratio_threshold", 2.4))
        cfg["temperature"] = params.get("asr_temperature", cfg.get("temperature", "0.0,0.2,0.4,0.6,0.8,1.0"))
        cfg["hotwords"] = params.get("asr_hotwords", cfg.get("hotwords", ""))
        # 1. 先写文件（主线程，快）
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存 ASR 配置失败: %s", e)
        # 2. reload + 同步引擎参数放后台线程（避免子进程启停阻塞 UI）
        cfg_copy = dict(cfg)
        asr_mgr = self._asr_mgr
        def _apply_asr():
            asr_mgr.reload_config()
            eng = asr_mgr.get_engine()
            if eng and hasattr(eng, 'sync_params_from_config'):
                eng.sync_params_from_config(cfg_copy)
        import threading
        threading.Thread(target=_apply_asr, daemon=True).start()

    def _sync_correction_config(self, params: dict):
        """将 UI 中的纠错参数同步写入 ai_correction.json。"""
        config_path = BASE_DIR / "config" / "ai_correction.json"
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            logger.warning("读取纠错配置失败: %s", e)
            cfg = {}
        if "corr_enabled" in params:
            cfg["enabled"] = params["corr_enabled"]
        if "corr_batch_size" in params:
            cfg["batch_size"] = params["corr_batch_size"]
        if "corr_context_window" in params:
            cfg["context_window"] = params["corr_context_window"]
        if "corr_retry" in params:
            cfg["retry"] = params["corr_retry"]
        if "corr_prompt" in params:
            cfg["correction_prompt"] = params["corr_prompt"]
        if params.get("corr_summary_prompt"):
            cfg["summary_prompt"] = params["corr_summary_prompt"]
        if params.get("corr_system_prompt"):
            cfg["correction_system_prompt"] = params["corr_system_prompt"]
        if params.get("corr_output_format"):
            cfg["output_format"] = params["corr_output_format"]
        if "corr_stream" in params:
            cfg["stream_mode"] = params["corr_stream"]
        if "corr_json" in params:
            cfg["json_mode"] = params["corr_json"]
        if "corr_polish" in params:
            cfg["enable_polish"] = params["corr_polish"]
        if "corr_use_template" in params:
            cfg["use_template"] = params["corr_use_template"]
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存纠错配置失败: %s", e)
        self._corrector.reload_config()

    # ── 模板 ──
    def _on_template_edit(self):
        """打开模板编辑器弹窗。"""
        self._config_panel._open_template_editor()

    # ── 模板导入/导出 ──
    def _on_template_import(self):
        d = self._config_mgr.get_last_directory() or ""
        p, _ = QFileDialog.getOpenFileName(
            self, "导入模板", d, "JSON 文件 (*.json);;所有文件 (*.*)")
        if not p:
            return
        try:
            count = self._prompt_mgr.import_templates(p)
            self._refresh_template_list()
            self._status_label.setText(f"✅ 已导入 {count} 个模板")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"模板导入失败:\n{e}")

    def _on_template_export(self):
        d = self._config_mgr.get_last_directory() or ""
        p, _ = QFileDialog.getSaveFileName(
            self, "导出模板", d + "/prompt_templates_export.json",
            "JSON 文件 (*.json);;所有文件 (*.*)")
        if not p:
            return
        try:
            self._prompt_mgr.export_templates(p)
            self._status_label.setText(f"✅ 已导出模板到: {Path(p).name}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"模板导出失败:\n{e}")

    def _on_capture_test_frame(self): self._video_preview.capture_test_frame()

    def _on_open_video(self):
        d = self._config_mgr.get_last_directory() or ""
        files, _ = QFileDialog.getOpenFileNames(self, "打开文件", d,
            "媒体文件 (*.mp4 *.mkv *.avi *.mov *.webm *.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.opus "
            "*.png *.jpg *.jpeg *.bmp);;"
            "视频 (*.mp4 *.mkv *.avi *.mov *.webm);;"
            "音频 (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.opus);;"
            "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)")
        if not files:
            return
        if len(files) == 1:
            # 单文件 → 加载到预览区
            p = files[0]
            ext = Path(p).suffix.lower()
            from core.asr_engine import SUPPORTED_AUDIO_EXTS
            if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
                self._video_preview.load_video(p)
            elif ext in SUPPORTED_AUDIO_EXTS:
                self._load_audio_file(p)
            else:
                self._video_preview.load_image(p)
            # 清除批量队列
            self._batch_files.clear()
            self._update_batch_label()
        else:
            # 多文件 → 替换批量队列 + 渲染第一个
            self._batch_files = list(files)
            first = self._batch_files[0]
            ext = Path(first).suffix.lower()
            if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
                self._video_preview.load_video(first)
            elif ext in ('.png', '.jpg', '.jpeg', '.bmp'):
                self._video_preview.load_image(first)
            from PyQt5.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            self._update_batch_label()

    def _load_audio_file(self, path: str):
        """加载纯音频文件 —— 使用与视频一致的播放控件。"""
        self._video_preview.load_audio(path)
        self._status_label.setText(f"已加载音频: {Path(path).name}（仅支持 ASR 和纠错）")

    # ── 统一处理入口（单文件 / 批量）──
    def _on_start_processing(self):
        self._workflow.start_processing()
        self._btn_pause.setEnabled(True)
        self._btn_pause.setText("⏸ 暂停")

    def _is_audio_file(self) -> bool:
        """判断当前加载的是否为纯音频文件。"""
        vp = self._video_preview.video_path
        if not vp:
            return False
        ext = Path(vp).suffix.lower()
        from core.asr_engine import SUPPORTED_AUDIO_EXTS
        return ext in SUPPORTED_AUDIO_EXTS

    def _on_stop_processing(self):
        self._workflow.stop_processing()

    def _on_pause_processing(self):
        btn = self._btn_pause
        if btn.text() == "⏸ 暂停":
            self._workflow.pause_processing()
            btn.setText("▶ 继续")
        else:
            self._workflow.resume_processing()
            btn.setText("⏸ 暂停")

    def _on_process_log(self, m): self._status_label.setText(m)

    def _on_process_progress(self, cur, total, qs, sentinel):
        if total > 0: self._progress_bar.setValue(min(100, int(cur * 100 / total)))
        m1, s1 = divmod(int(cur), 60)
        m2, s2 = divmod(int(total), 60)
        self._time_label.setText(f" {m1:02d}:{s1:02d} / {m2:02d}:{s2:02d} ")
        self._status_label.setText(f"处理中... {cur}s / {total}s | 哨兵: {sentinel}")

    # ── WorkflowManager 配置 ──
    def _configure_workflow(self):
        """向 WorkflowManager 注入所有依赖和 UI 访问器，并连接信号。"""
        wf = self._workflow

        # 管理器
        wf._engine_mgr = self._engine_mgr
        wf._asr_mgr = self._asr_mgr
        wf._corrector = self._corrector
        wf._filter_mgr = self._filter_mgr
        wf._config_mgr = self._config_mgr

        # UI 访问器
        wf._get_video_path = lambda: self._video_preview.video_path
        wf._get_audio_cache_path = lambda: self._video_preview.audio_cache_path
        wf._get_is_image = lambda: self._video_preview.is_image
        wf._get_regions = lambda: self._video_preview.regions
        wf._get_batch_files = lambda: self._batch_files
        wf._set_regions = lambda regions: (
            setattr(self._video_preview, 'regions', regions),
            setattr(self._region_manager, 'regions', regions),
        )
        wf._get_time_range = lambda: (self._video_preview.time_start, self._video_preview.time_end)
        wf._get_current_frame = lambda: self._video_preview.current_frame
        wf._get_roi_image = lambda ri: self._video_preview.get_roi_image(ri)
        wf._get_current_engine = lambda: self._current_engine
        wf._get_current_template = lambda: self._current_template
        wf._get_custom_prompt = lambda: self._custom_prompt
        wf._get_config_prompt = lambda: self._config_panel.prompt_text
        wf._get_mode_params = lambda: self._mode_params
        wf._get_is_audio_file = lambda: self._is_audio_file()
        wf._get_results = lambda: self._result_table.get_results()
        wf._clear_results_table = lambda: self._result_table.clear_results()
        wf._clear_results_by_type = lambda rgn, eng: self._result_table.clear_by_type(rgn, eng)
        wf._asr_region_name = lambda: self._mode_params.get("asr_region_name", "语音")
        wf._sort_results_table = lambda order: self._result_table.sort_by_order(order)
        wf._sort_by_time = lambda: self._result_table.sort_by_time()
        wf._get_polished_results = lambda sim, ml, dedup=True: self._result_table.get_polished_results(
            post_sim_threshold=sim, post_min_text_len=ml, post_sim_dedup=dedup)
        wf._get_table_row_count = lambda: self._result_table._table.rowCount()

        # ── 信号连接 ──
        wf.status_msg.connect(lambda m: self._status_label.setText(m))
        wf.progress_val.connect(self._set_progress_animated)
        wf.time_display.connect(lambda t: self._time_label.setText(t))
        wf.buttons_enabled.connect(self._on_workflow_buttons)
        wf.error_dialog.connect(lambda t, m: QMessageBox.critical(self, t, m))
        wf.info_dialog.connect(lambda t, m: QMessageBox.information(self, t, m))
        wf.result_row.connect(self._on_process_result)
        wf.correction_updated.connect(self._on_correction_ready)
        wf.correction_stream_updated.connect(self._on_correction_stream)
        wf.polish_updated.connect(self._on_polish_ready)
        wf.batch_progress.connect(self._on_batch_progress_file)
        wf.batch_file_done.connect(self._on_batch_finished_one)
        wf.batch_all_done.connect(lambda: self._update_batch_label())
        wf.process_finished.connect(self._recalculate_end_seconds)

    def _on_workflow_buttons(self, states: dict):
        """根据 WorkflowManager 信号更新按钮状态。"""
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

    def _on_correction_selected(self):
        """对选中的表格行进行 AI 纠错（委托 WorkflowManager）。"""
        selected_rows = self._result_table.get_selected_rows()
        if not selected_rows:
            QMessageBox.warning(self, "提示", "请先在表格中选中需要纠错的行（可多选）。")
            return
        self._workflow.correct_selected(selected_rows)

    def _on_correction_all(self):
        """对全部结果行进行 AI 纠错（委托 WorkflowManager）。"""
        self._workflow.correct_all()

    def _on_batch_correction_finished(self):
        """批量纠错全部完成。"""
        self._btn_correction_all.setEnabled(True)
        self._btn_correction.setEnabled(True)
        n = self._result_table._table.rowCount()
        self._status_label.setText(f"✅ 完成: {n} 条结果 | 批量纠错完成")

    def _on_batch_correction_error(self, err):
        """批量纠错出错。"""
        self._btn_correction_all.setEnabled(True)
        self._btn_correction.setEnabled(True)
        self._status_label.setText(f"⚠ 批量纠错出错: {err}")

    def _on_process_result(self, ts, t_str, rname, ename, raw, conf: float = 0.0, end_sec: float = 0.0):
        # 过滤器：包含任一关键词则跳过（WorkflowManager 已过滤，此处为双重保险）
        if self._filter_mgr.matches(raw):
            return
        # 视频模式下按时间顺序插入，图片模式下追加到末尾
        is_image = self._video_preview.is_image if hasattr(self, '_video_preview') else False
        self._result_table.add_result(time_str=t_str, region=rname, engine=ename,
                                            raw_text=raw, time_sec=ts, confidence=conf, end_sec=end_sec,
                                            sorted_insert=not is_image)

    def _on_correction_ready(self, row, raw, corrected):
        self._result_table.update_correction_result(row, corrected)  # 纠错→col5
        self._correction_results[row] = corrected
        self._correction_pending.discard(row)

    def _on_correction_failed(self, row, _): self._correction_pending.discard(row)

    def _on_correction_stream(self, row, partial_text):
        """流式输出模式：实时更新表格中的纠错文本（col5）。"""
        self._result_table.update_correction_result(row, partial_text)

    def _on_polish_selected(self):
        """对选中行进行润色（委托 WorkflowManager）。"""
        selected_rows = self._result_table.get_selected_rows()
        if not selected_rows:
            QMessageBox.warning(self, "提示", "请先在表格中选中需要润色的行（可多选）。")
            return
        self._workflow.polish_selected(selected_rows)

    def _on_polish_all(self):
        """对全部行进行润色（委托 WorkflowManager）。"""
        self._workflow.polish_all()

    def _on_polish_ready(self, row, original, polished):
        """润色结果回调：写入 col6（corrected 字段）。"""
        self._result_table.update_correction(row, polished)

    def _recalculate_end_seconds(self):
        """填充 OCR 结果的 end_sec（通过 process_finished DirectConnection 同步调用，在纠错之前执行）。"""
        # ── 置信度阈值过滤 ──
        if self._mode_params.get("post_conf_enabled", False):
            threshold = self._mode_params.get("post_conf_threshold", 0.6)
            results = self._result_table._results
            table = self._result_table._table
            for row in range(len(results) - 1, -1, -1):
                r = results[row]
                if r.get("engine", "") == "paddleocr":
                    conf = r.get("confidence", 1.0)
                    if conf < threshold:
                        table.removeRow(row)
                        del results[row]

        # ── end_sec 回填 ──
        results = self._result_table._results
        if not results:
            return
        from collections import defaultdict
        sub_dur = self._mode_params.get("subtitle_duration", 3.0)
        groups: dict = defaultdict(list)
        for i, r in enumerate(results):
            groups[r.get("region", "")].append(i)
        for indices in groups.values():
            for j in range(len(indices) - 1):
                cur_idx = indices[j]
                nxt_idx = indices[j + 1]
                nxt_ts = results[nxt_idx].get("time_sec", 0.0) or 0.0
                cur_ts = results[cur_idx].get("time_sec", 0.0) or 0.0
                if nxt_ts > cur_ts:
                    results[cur_idx]["end_sec"] = nxt_ts
            # 最后一项：用 subtitle_duration 作为 end_sec
            last_idx = indices[-1]
            last_r = results[last_idx]
            if not last_r.get("end_sec", 0.0):
                last_r["end_sec"] = (last_r.get("time_sec", 0.0) or 0.0) + sub_dur

    def _on_process_error(self, err):
        self._btn_start.setEnabled(True); self._btn_stop.setEnabled(False); self._btn_pause.setEnabled(False)
        self._btn_pause.setText("⏸ 暂停")
        self._btn_correction.setEnabled(True); self._btn_correction_all.setEnabled(True)
        self._progress_bar.setValue(0)
        self._status_label.setText(f"❌ 处理失败: {err}")

    # ── 导出 ──
    def _on_export(self, fmt, path):
        polished = self._result_table.get_polished_results(
            post_sim_threshold=self._mode_params.get("post_sim_threshold", 0.9),
            post_min_text_len=self._mode_params.get("post_min_text_len", 2))
        if not polished:
            return QMessageBox.information(self, "提示", "过滤后无有效结果可导出。")

        # 统一补充 end_sec（OCR 结果可能缺少该字段）
        sub_dur = self._mode_params.get("subtitle_duration", 3.0)
        for p in polished:
            ts = p.get("time_sec", 0.0) or 0.0
            end = p.get("end_sec", 0.0) or 0.0
            if end <= ts:
                p["end_sec"] = ts + sub_dur

        # 纠错结果在 segmented 字段（列5），润色结果在 corrected 字段（列6）
        cmap = {}
        for pi, p in enumerate(polished):
            # 优先级：corrected(润色) > segmented(纠错) > raw(原文)
            polished_text = p.get("corrected", "").strip()
            corrected_text = p.get("segmented", "").strip()
            if polished_text:
                cmap[pi] = polished_text
            elif corrected_text:
                cmap[pi] = corrected_text
        try:
            srt_mode_map = {"仅纠正结果": "corrected", "仅原文": "original", "双语对照（原文+纠正）": "dual", "原文 换行 纠正": "dual"}
            srt_mode = srt_mode_map.get(self._mode_params.get("srt_export_mode", "仅纠正结果"), "corrected")
            export_results(polished, path, fmt, bool(cmap), cmap,
                           keep_original=self._mode_params.get("export_keep_original", False),
                           srt_mode=srt_mode)
            self._status_label.setText(f"✅ 已导出: {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
