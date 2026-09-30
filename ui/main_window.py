"""主窗口 —— ORCP OCR 处理工具。
引擎/模板选择 → 顶端菜单栏；所有参数设置 → 统一的「参数设置」对话框。"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction  # PySide6: QAction 位于 QtGui（PyQt5 在 QtWidgets）
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QSplitter,
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
from core.config_manager import MODE_PARAMS_DEFAULTS
from core.filter_manager import FilterManager
from core.i18n import LANGUAGE_DISPLAY_NAMES, SUPPORTED_LANGUAGES, LanguageManager, _
from core.ocr_engine import OCREngineManager
from core.prompt_manager import PromptTemplateManager
from core.result_processor import export_results
from core.settings import ConfigRegistry, RebuildRouter, UiStateConfig
from core.workflow import WorkflowManager
from ui.config_panel import ConfigPanel
from ui.dialogs import PresetManageDialog
from ui.display_dialog import DisplayDialog
from ui.result_table import ResultTableWidget
from ui.services import MessageService
from ui.settings_dialog import SettingsDialog
from ui.style_loader import (
    DEFAULT_DARK,
    DEFAULT_LIGHT,
    apply_theme,
    is_dark_theme,
)
from ui.theme_tokens import FONT_FAMILY
from ui.video_preview import VideoPreviewWidget
from ui.views import BottomBarView, MenuBarView, QuickToolbar, RightPanelView, StatusBarView

WIN_TITLE = _("ORCP - OCR 处理工具")

# 字号基准：所有固定尺寸按 (font_size - 基准) 线性补偿
BASE_FONT_SIZE = 13

# density_scale 档位阈值（降序判定：先判最大档，否则高档位被低档位抢先匹配而永不生效）
DENSITY_STEPS: tuple[tuple[float, str], ...] = (
    (1.2, "2"),
    (1.1, "1"),
)
DENSITY_SMALL_STEPS: tuple[tuple[float, str], ...] = (
    (0.9, "-2"),  # scale < 0.9
    (1.0, "-1"),  # scale < 1.0
)


def _density_for_scale(scale: float) -> str:
    """把 UI 缩放比例映射为 qt-material 的 density_scale 档位。

    回归：原实现按升序写成 ``if scale < 0.9 … elif scale > 1.1 … elif scale > 1.2``，
    ``scale > 1.1`` 会先匹配所有 >1.2 的值，导致 ``"2"`` 档**永不生效**。
    """
    for threshold, density in DENSITY_SMALL_STEPS:
        if scale < threshold:
            return density
    for threshold, density in DENSITY_STEPS:
        if scale > threshold:
            return density
    return "0"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowOpacity(0.0)
        self.hide()
        self.setUpdatesEnabled(False)
        self.setWindowTitle(WIN_TITLE)

    def setup(self):
        """构建完整 UI —— 与 __init__ 分离，确保窗口在完全就绪后才首次渲染。"""
        # 配置域注册中心（方案 A）：settings.json 由 ConfigManager 承载，
        # asr/corr/ocr 业务域由域对象承载（单一事实源）
        self._registry = ConfigRegistry()
        self._config_mgr = self._registry.config_mgr
        self._engine_mgr = OCREngineManager()
        self._asr_mgr = ASREngineManager()
        self._corrector = AICorrector(load_correction_config(), engine_manager=self._engine_mgr)
        self._prompt_mgr = PromptTemplateManager()
        self._filter_mgr = FilterManager()

        self._workflow = WorkflowManager(self)

        self._correction_results: dict[int, str] = {}
        self._correction_pending: set = set()
        self._custom_prompt: str = ""
        self._current_engine: str = "paddleocr"
        self._current_template: str = ""
        self._batch_files: list[str] = []
        self._paused: bool = False  # P1-5：暂停状态布尔标志（替代按钮文本判断）

        self._theme = self._config_mgr.get_theme()

        # ── 交互服务（弹窗封装，可注入测试）──
        self._message_service = MessageService(self)

        # ── 视图构建器（main_window 拆分，方法体在 ui/views/）──
        self._status_bar_view = StatusBarView(self)
        self._status_bar_view.build()
        self._menu_bar_view = MenuBarView(self)
        self._menu_bar_view.build()
        self._quick_toolbar_view = QuickToolbar(self)
        self._quick_toolbar = self._quick_toolbar_view.build()
        self.addToolBar(self._quick_toolbar)
        self._bottom_bar_view = BottomBarView(self)

        self._ui_views = [
            self._status_bar_view,
            self._menu_bar_view,
            self._quick_toolbar_view,
            self._bottom_bar_view,
        ]

        self.build_ui()
        self._apply_theme()

        hw = self._config_mgr.get_hw_accel()
        if hw:
            self._engine_mgr.set_hw_accel(True)
        self._refresh_engine_list()
        self._refresh_template_list()
        self._video_preview.set_hw_accel(hw)
        self._restore_mode_params()
        # 重建路由器（C5/C6 收编）：域变更 → 防抖 → 持久化 + 引擎重建。
        # 接线在 restore 之后——启动期的域对象载入不触发重建
        self._router = RebuildRouter(self)
        self._registry.asr.changed.connect(lambda k: self._router.notify("asr", k))
        self._registry.correction.changed.connect(lambda k: self._router.notify("correction", k))
        self._registry.ocr_engines.changed.connect(lambda k: self._router.notify("ocr", k))
        self._router.rebuild_requested.connect(self._on_engine_rebuild)
        self._configure_workflow()
        self.sync_quick_toggles()
        self._install_wheel_blocker()

        # 注册语言切换监听器
        LanguageManager().register_listener(self._on_language_changed)

        # 延迟保存窗口几何（窗口调整大小时防抖保存）
        from PySide6.QtCore import QTimer

        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(1000)
        self._geometry_save_timer.timeout.connect(self._save_window_geometry)

    def __getattr__(self, name):
        """视图方法委托：MainWindow 未定义时在视图构建器中查找。"""
        views = self.__dict__.get("_ui_views", ())
        for v in views:
            if name in type(v).__dict__:
                return getattr(v, name)
        raise AttributeError(name)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_geometry_save_timer"):
            self._geometry_save_timer.start()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "_geometry_save_timer"):
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
                    if isinstance(obj, QAbstractSpinBox | QComboBox):
                        event.ignore()
                        return True
                return super().eventFilter(obj, event)

        blocker = WheelBlocker(self)
        app.installEventFilter(blocker)
        self._wheel_blocker = blocker

    # ── 顶端快速开关工具栏 ──

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

        # ── 快速工具栏 ──
        if hasattr(self, "_qt_corr"):
            self._qt_corr.setText(_("🔤 AI纠错"))
            self._qt_corr.setToolTip(_("启用/关闭 AI 纠错"))
        if hasattr(self, "_qt_hw"):
            self._qt_hw.setText(_("⚡ GPU"))
            self._qt_hw.setToolTip(_("启用/关闭 GPU 硬件加速"))
        if hasattr(self, "_qt_dedup"):
            self._qt_dedup.setText(_("🔍 去重"))
            self._qt_dedup.setToolTip(_("启用/关闭后处理相似度去重"))
        if hasattr(self, "_qt_translate"):
            self._qt_translate.setText(_("🌐 翻译"))
            self._qt_translate.setToolTip(_("翻译模式：将 OCR 结果翻译为中文"))
        if hasattr(self, "_qt_sentinel"):
            self._qt_sentinel.setText(_("🛡 哨兵"))
            self._qt_sentinel.setToolTip(_("启用/关闭哨兵去重（字数骤降检测触发输出）"))
        if hasattr(self, "_qt_clear_cache"):
            self._qt_clear_cache.setText(_("🗑 清缓存"))
            self._qt_clear_cache.setToolTip(_("清除所有缓存（LLM 响应缓存 + ASR 结果缓存）"))

        # ── 快速工具栏标签 ──
        if hasattr(self, "_qt_subtitle_label"):
            self._qt_subtitle_label.setText(_("字幕"))
        if hasattr(self, "_qt_process_label"):
            self._qt_process_label.setText(_("模式"))

        # ── 按钮 ──
        if hasattr(self, "_btn_capture"):
            self._btn_capture.setText(_("📸 截取帧"))
            self._btn_capture.setToolTip(_("截取当前视频帧用于预览和区域绘制"))
        if hasattr(self, "_btn_open"):
            self._btn_open.setText(_("📂 打开文件"))
            self._btn_open.setToolTip(_("打开视频、音频或图片文件"))
        if hasattr(self, "_btn_batch_clear"):
            self._btn_batch_clear.setText(_("🗑 清空"))
        if hasattr(self, "_btn_start"):
            self._btn_start.setText(_("▶ 开始处理"))
        if hasattr(self, "_btn_pause"):
            # P1-5：按状态标志重设文本（此前枚举三种语言文本，新增语言即失效）
            self._btn_pause.setText(_("⏸ 暂停") if not self._paused else _("▶ 继续"))
        if hasattr(self, "_btn_stop"):
            self._btn_stop.setText(_("⏹ 停止"))
        if hasattr(self, "_btn_correction"):
            self._btn_correction.setText(_("✏ 纠错选中"))
        if hasattr(self, "_btn_correction_all"):
            self._btn_correction_all.setText(_("✏ 纠错全部"))
        if hasattr(self, "_btn_polish"):
            self._btn_polish.setText(_("✨ 润色选中"))
        if hasattr(self, "_btn_polish_all"):
            self._btn_polish_all.setText(_("✨ 润色全部"))

        # ── 菜单 ──
        if hasattr(self, "_settings_menu"):
            self._settings_menu.setTitle(_("参数设置(&P)"))
            menu_labels = [
                # R11：与 menu_bar.py 的 5 个直达 tab 入口保持同步
                # （「⚙ 全部参数...」已移除，此前 zip 截断导致语言切换后标签错位）
                _("基础设置..."),
                _("语音识别..."),
                _("OCR 字幕处理..."),
                _("AI 纠错..."),
                _("结果输出..."),
            ]
            for action, label in zip(self._settings_menu_actions, menu_labels, strict=False):
                action.setText(label)
        if hasattr(self, "_display_menu"):
            self._display_menu.setTitle(_("显示(&V)"))
            self._display_theme_action.setText(_("切换主题 (亮色/暗色)"))
            self._display_settings_action.setText(_("显示设置..."))
        if hasattr(self, "_corr_menu"):
            self._corr_menu.setTitle(_("纠错(&C)"))
            self._corr_preset_action.setText(_("API 预设管理..."))
        if hasattr(self, "_template_menu"):
            self._template_menu.setTitle(_("模板(&T)"))
            self._template_edit_action.setText(_("📝 编辑模板..."))
            self._template_import_action.setText(_("📥 导入模板..."))
            self._template_export_action.setText(_("📤 导出模板..."))
        if hasattr(self, "_batch_menu"):
            self._batch_menu.setTitle(_("批量(&B)"))
            self._batch_clear_action.setText(_("🗑 清空队列"))
        if hasattr(self, "_language_menu"):
            self._language_menu.setTitle(_("语言(&L)"))

        # ── 快速工具栏 combo（用 index 保持选中项，不依赖文本翻译）──
        if hasattr(self, "_qt_subtitle_mode"):
            self._qt_subtitle_mode.blockSignals(True)
            saved_idx = self._qt_subtitle_mode.currentIndex()
            self._qt_subtitle_mode.clear()
            self._qt_subtitle_mode.addItems([_("流式"), _("常规")])
            self._qt_subtitle_mode.setCurrentIndex(min(saved_idx, 1))
            self._qt_subtitle_mode.blockSignals(False)
        if hasattr(self, "_qt_process_mode"):
            self._qt_process_mode.blockSignals(True)
            saved_idx = self._qt_process_mode.currentIndex()
            self._qt_process_mode.clear()
            self._qt_process_mode.addItems([_("OCR+ASR"), _("仅OCR"), _("仅ASR")])
            self._qt_process_mode.setCurrentIndex(min(saved_idx, 2))
            self._qt_process_mode.blockSignals(False)

        if hasattr(self, "_region_group"):
            self._region_group._title_label.setText(_("📐 区域参数"))
        if hasattr(self, "_subtitle_group"):
            self._subtitle_group._title_label.setText(_("📝 字幕设置"))
        if hasattr(self, "_asr_group"):
            self._asr_group._title_label.setText(_("🎤 ASR 选项"))
        # R12（P1-2）：后处理组已删除

        # ── 右侧面板行标签（直接引用）──
        _label_updates = [
            ("_lbl_frame_interval", _("帧间隔:")),
            ("_lbl_asr_model", _("模型:")),
            ("_lbl_asr_lang", _("语言:")),
            ("_lbl_asr_region", _("区域名:")),
        ]
        for attr, text in _label_updates:
            lbl = getattr(self, attr, None)
            if lbl:
                lbl.setText(text)
        # checkbox / tooltip
        if hasattr(self, "_frame_interval_r"):
            self._frame_interval_r.setToolTip(_("每隔多少秒处理一帧"))
        if hasattr(self, "_asr_model_combo_r"):
            self._asr_model_combo_r.setToolTip(_("ASR 模型选择"))
        if hasattr(self, "_asr_lang_combo_r"):
            self._asr_lang_combo_r.setToolTip(_("识别语言"))
        if hasattr(self, "_asr_region_edit_r"):
            self._asr_region_edit_r.setToolTip(_("ASR 结果在表格中的区域名"))

        # ── 区域管理器 ──
        if hasattr(self, "_region_manager"):
            self._region_manager._retranslate_ui()

        # ── 结果表格 ──
        if hasattr(self, "_result_table"):
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
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
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
        vc.addWidget(self._btn_batch_clear)
        vc.addStretch()
        ll.addLayout(vc)
        ll.addSpacing(4)

        self._video_preview = VideoPreviewWidget()
        self._video_preview.video_loaded.connect(self._on_video_loaded)
        self._video_preview.frame_captured.connect(self._on_frame_captured)
        self._video_preview.regions_changed.connect(self._on_preview_regions_changed)
        self._video_preview.files_dropped.connect(self._on_batch_files_dropped)
        self._video_preview.position_changed.connect(self._on_video_position_changed)
        ll.addWidget(self._video_preview, 1)
        self._top_splitter.addWidget(left)

        # 右：区域参数 + ASR 选项（可折叠）→ RightPanelView 构建
        self._right_panel_view = RightPanelView(self)
        self._right_panel_view.build()
        self._ui_views.append(self._right_panel_view)

        self._main_splitter.addWidget(self._top_splitter)

        # ═══ 下半区：结果表格 + 底部操作栏 ═══
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)

        self._result_table = ResultTableWidget()
        self._result_table.filter_requested.connect(self._on_result_filter)
        self._result_table.delete_filtered_requested.connect(self._on_delete_filtered_results)
        self._result_table.export_requested.connect(self._on_export)
        self._result_table.cell_edit_activated.connect(self._on_result_cell_edit)
        bl.addWidget(self._result_table, 1)

        # 底部操作栏 → BottomBarView 构建
        bl.addWidget(self._bottom_bar_view.build())

        self._main_splitter.addWidget(bottom)
        self._main_splitter.setSizes([400, 600])
        self._main_splitter.setStretchFactor(0, 2)
        self._main_splitter.setStretchFactor(1, 3)

        root.addWidget(self._main_splitter, 1)

        # ── ConfigPanel（纯状态管理类，无 UI；注入域注册中心做读透合并）──
        self._config_panel = ConfigPanel(registry=self._registry)
        self._config_panel.prompt_changed.connect(self._on_prompt_changed)
        self._config_panel.mode_changed.connect(self._on_mode_changed)
        self._config_panel.template_saved.connect(self._on_config_template_saved)
        self._config_panel.template_deleted.connect(self._on_config_template_deleted)
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
            apply_theme(app, self._theme, font_family=FONT_FAMILY, density_scale=_density_for_scale(scale))
            # 追加字体大小覆盖（!important 确保覆盖 qt-material 的选择器）
            if font_size != BASE_FONT_SIZE:
                current = app.styleSheet()
                font_override = f"* {{ font-size: {font_size}px !important; }}"
                app.setStyleSheet(current + "\n" + font_override)
            # 动态调整固定尺寸控件
            self._apply_scale_to_fixed_widgets(font_size, scale)

    def _toggle_theme(self):
        if is_dark_theme(self._theme):
            self._apply_theme(DEFAULT_LIGHT)
        else:
            self._apply_theme(DEFAULT_DARK)

    # ── 参数设置对话框 ──
    def _open_settings(self, tab_index: int = -1):
        """打开参数设置对话框，合并处理参数 + 纠错 API 配置。"""
        # 外部修改检测（2.4）：会话内手改 config/*.json → 提示重载，避免 UI 覆盖
        changed = self._registry.check_external_changes()
        if changed:
            names = "、".join(changed)
            if self._message_service.question(
                _("配置文件已被外部修改"),
                _("文件 {names} 已在外部被修改。\n[是] 重新加载磁盘值并刷新界面\n[否] 忽略（继续使用当前值）").format(
                    names=names
                ),
            ):
                self._registry.reload_all()
                self._config_mgr.reload()
                self._refresh_engine_list()
                self._refresh_template_list()
                self.sync_quick_toggles()
                self._message_service.info_toast(_("✅ 已重新加载配置"))
        dlg = SettingsDialog(
            self._config_panel,
            registry=self._registry,
            parent=self,
            filter_keywords=self._filter_mgr.get_keywords(),
            engine_manager=self._engine_mgr,
            current_engine=self._current_engine,
        )
        if 0 <= tab_index < dlg._tabs.count():
            dlg._tabs.setCurrentIndex(tab_index)
        elif tab_index == -1:
            dlg._tabs.setCurrentIndex(0)

        self._restore_dialog_geometry(dlg, "settings_dialog_geometry")

        if dlg.exec() == QDialog.Accepted:
            self._save_dialog_geometry(dlg, "settings_dialog_geometry")
            # 保存处理参数：_sync_values_to_cp → apply_mode_params 已 emit
            # mode_changed → _on_mode_changed（P8 冗余清理，此处不再补调）
            # 保存业务参数（方案 A）：asr/corr 字段直接写域对象（单次原子写盘）
            dlg.commit_to_domains(self._registry)
            # 重建纠错器（从域对象读最新配置，含 stream/json/translate 模式保持）
            preset_name = self._config_panel.corr_preset_name
            self._corrector = AICorrector(
                self._registry.correction.get_all(),
                engine_manager=self._engine_mgr,
                preset_name=preset_name,
            )
            self._workflow._corrector = self._corrector  # 同步到工作流
            # 保存引擎配置（域对象写盘）
            eng_name, eng_cfg = dlg.get_engine_config()
            engs = self._registry.ocr_engines.get_all().get("engines", {})
            if eng_name in engs:
                self._registry.ocr_engines.set_engine_config(eng_name, eng_cfg)
                self._engine_mgr._engines.pop(eng_name, None)
                # 设置同步 P4 修复：对话框切换引擎后同步会话内当前引擎与下次启动恢复值
                self._current_engine = eng_name
                self._engine_mgr.set_current_engine(eng_name)
                self._config_mgr.set("last_engine", eng_name)
            self._sync_region_defaults()
            self._config_mgr.save_settings()
            self._status_label.setText(_("✅ 参数设置已更新"))
            self._message_service.info_toast(_("✅ 参数已保存"))
            self.sync_quick_toggles()

    def _on_reload_config(self):
        """菜单「重新加载配置」：读盘 + 刷新 UI + 引擎句柄重建（2.4）。"""
        self._registry.reload_all()
        self._config_mgr.reload()
        self._refresh_engine_list()
        self._refresh_template_list()
        self._restore_mode_params()
        self.sync_quick_toggles()
        self._message_service.info_toast(_("✅ 配置已重新加载"))

    def _on_engine_rebuild(self, targets: str):
        """引擎重建反馈（2.11：toast 告知重建代价已执行）。"""
        if "asr" in targets:
            self._message_service.info_toast(_("🔁 ASR 引擎已重新加载"))
        if "ocr" in targets:
            self._message_service.info_toast(_("🔁 OCR 引擎已重新加载"))

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
        if dlg.exec() == QDialog.Accepted:
            self._save_dialog_geometry(dlg, "display_dialog_geometry")
            cfg = dlg.get_config()
            self._save_theme_from_dialog(cfg["theme"], cfg["font_size"], cfg["ui_scale"])
            self._status_label.setText(_("✅ 显示设置已更新"))

    def _apply_theme_from_dialog(self, theme: str, font_size: int, scale: float):
        """预览主题（R11 取消语义修复：不写盘，取消时无持久化副作用）。"""
        self._theme = theme
        self._apply_theme(theme)

    def _save_theme_from_dialog(self, theme: str, font_size: int, scale: float):
        """显示设置对话框确定时持久化主题/字体/缩放。"""
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
        btn_h = max(28, int(34 * scale + (font_size - BASE_FONT_SIZE) * 1.5))
        for btn_attr in (
            "_btn_start",
            "_btn_pause",
            "_btn_stop",
            "_btn_correction",
            "_btn_correction_all",
            "_btn_polish",
            "_btn_polish_all",
        ):
            btn = getattr(self, btn_attr, None)
            if btn:
                btn.setFixedHeight(btn_h)

        # 工具栏按钮高度
        capture_h = max(26, int(30 * scale + (font_size - BASE_FONT_SIZE) * 1.2))
        for btn_attr in ("_btn_capture", "_btn_open", "_btn_batch_clear"):
            btn = getattr(self, btn_attr, None)
            if btn:
                btn.setFixedHeight(capture_h)

        # 状态栏高度：至少 28px，随字体和缩放增长
        # （基准统一为 BASE_FONT_SIZE；此处原为 12，与按钮的 13 不一致，实为笔误）
        bar_h = max(28, int(30 * scale + (font_size - BASE_FONT_SIZE) * 1.2))
        self._status_bar.setMinimumHeight(bar_h)

        # 进度条高度
        prog_h = max(14, int(18 * scale))
        self._progress_bar.setMaximumHeight(prog_h)

        # 分隔线高度（引用由 BottomBarView.build 持有，避免 findChildren 字符串反射）
        sep_h = max(18, int(24 * scale))
        for sep in getattr(self, "_bar_separators", ()):
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
                sb = self.statusBar()
                if sb:
                    sb.showMessage(_("模板: {name} — {desc}").format(name=name, desc=desc), 3000)
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
        from PySide6.QtCore import QByteArray

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
        if g:
            self.setGeometry(g.get("x", 100), g.get("y", 100), g.get("width", 1400), g.get("height", 900))
        else:
            self.resize(1400, 900)
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
            self._config_mgr.set("window_geometry", {"x": g.x(), "y": g.y(), "width": g.width(), "height": g.height()})
        self._config_mgr.set("splitter_sizes", self._main_splitter.sizes())
        self._config_mgr.set("top_splitter_sizes", self._top_splitter.sizes())
        self._config_mgr.save_settings()

    def _restore_mode_params(self):
        """从 settings.json + 域对象文件恢复参数（v2 方案 A）。

        asr_*/corr_* 业务参数以域对象为事实源（读透合并返回完整键集）；
        corr_preset 等 UI 状态键仍从 settings.json 恢复。
        """
        saved = self._config_mgr.get("mode_params", {})
        # 读透合并（D2 修复：saved 用户白名单值最后覆盖——merged 含域默认值，
        # 若先 update saved 会被 merged 全量覆盖回默认，恢复失效）
        merged = self._config_panel.get_mode_params()
        base = dict(MODE_PARAMS_DEFAULTS)
        base.update(merged)
        base.update({k: v for k, v in saved.items() if k in UiStateConfig.UI_STATE_MODE_KEYS})
        # 比较基准 = 最终全量，apply 时 diff 为零，不会误触发 ASR 引擎重建
        self._last_mode_params = dict(base)
        if saved or merged:
            # 恢复自定义提示词（域文件值优先，回退 saved）
            self._custom_prompt = merged.get("corr_prompt") or saved.get("corr_prompt", "")
            # 回填所有 UI 控件
            self._config_panel.apply_mode_params(base)
            # 显式应用 API 预设（幂等，apply_preset 可安全重复调用）
            saved_preset = saved.get("corr_preset", "")
            if saved_preset and self._corrector:
                self._corrector.apply_preset(saved_preset)
            # 恢复润色开关
            if "corr_polish" in base and self._corrector:
                self._corrector.polish_enabled = base["corr_polish"]
            # 恢复右侧面板控件
            self._restore_right_panel_params(saved)
        # 同步区域默认值（引擎/模板/提示词），确保新创建的区域使用当前提示词
        self._sync_region_defaults()

    def _schedule_mode_save(self):
        """延迟合并保存，避免频繁切换预设时连续写盘卡 UI。"""
        if hasattr(self, "_mode_save_timer"):
            self._mode_save_timer.start(300)
        else:
            from PySide6.QtCore import QTimer

            self._mode_save_timer = QTimer(self)
            self._mode_save_timer.setSingleShot(True)
            self._mode_save_timer.timeout.connect(self._save_mode_params)
            self._mode_save_timer.start(300)

    def _save_mode_params(self):
        """保存当前 UI 配置参数。

        v2（方案 A）：settings.json 只持久化 UI 状态白名单键
        （UiStateConfig.UI_STATE_MODE_KEYS，corr_summary_prompt 运行时通道除外）；
        业务参数（asr_*/corr_*）由 RebuildRouter 在域变更防抖后统一写盘。
        """
        try:
            full = self._config_panel.get_mode_params()
            # 显式白名单过滤（替代 startswith 前缀 + 例外键判断）
            from core.settings import UiStateConfig

            params = {
                k: v for k, v in full.items() if k in UiStateConfig.UI_STATE_MODE_KEYS and k != "corr_summary_prompt"
            }
            self._config_mgr.set("mode_params", params)
        except Exception as e:
            logger.error("保存配置失败: %s", e)
        try:
            self._config_mgr.save_settings()
        except Exception as e:
            logger.error("写盘 settings.json 失败: %s", e)

    def closeEvent(self, ev):
        """关闭窗口 —— 先保存状态，再隐藏 UI，后台静默清理。"""
        # ── 第一步：保存状态（必须在 hide 之前，否则 geometry 丢失）──
        self._save_window_geometry()
        self._save_mode_params()

        # ── 第二步：立即隐藏窗口，用户感知到 UI 已关闭 ──
        self.hide()

        # ── 移除全局滚轮拦截过滤器 ──
        app = QApplication.instance()
        if app and hasattr(self, "_wheel_blocker"):
            app.removeEventFilter(self._wheel_blocker)

        # ── 第三步：同步清理子进程（OCR/ASR），确保进程不残留 ──
        try:
            ocr_engine = self._engine_mgr.get_current_engine(warm_up=False)
            if ocr_engine and hasattr(ocr_engine, "_stop_server"):
                ocr_engine._stop_server()
        except Exception as e:
            logger.warning("OCR 引擎清理失败: %s", e)

        if self._asr_mgr:
            try:
                asr_engine = self._asr_mgr.get_engine()
                if asr_engine and hasattr(asr_engine, "_stop_server"):
                    asr_engine._stop_server()
            except Exception as e:
                logger.warning("ASR 引擎清理失败: %s", e)

        vp = self._video_preview
        if vp:
            if getattr(vp, "_player", None):
                try:
                    vp._player.stop()
                except Exception as e:
                    logger.warning("播放器清理失败: %s", e)
            if vp._ffmpeg:
                try:
                    vp._ffmpeg.close()
                except Exception as e:
                    logger.warning("FFmpeg 清理失败: %s", e)

        # ── 第四步：同步清理 worker 线程 ──
        # 此前把 cleanup() 丢进裸 threading.Thread「后台静默清理」。那是有害的：
        # 主线程随即从 closeEvent 返回并拆除 QApplication，而清理线程仍在访问
        # 主线程亲和（affinity）的 QThread/QObject，且进程退出会中途杀死它 ——
        # 典型的关窗崩溃 / 子进程残留来源。
        # 窗口已在第二步隐藏，用户已感知关闭；cleanup() 内部使用**全局**等待预算
        # （默认 3s）而非每线程 2s，因此这里同步调用不会把关闭拖成 N×2s。
        try:
            self._workflow.cleanup()
        except Exception as e:
            logger.warning("Worker 清理异常: %s", e)

        # 立即接受关闭事件
        super().closeEvent(ev)

    # ── 刷新 ──
    def _refresh_engine_list(self):
        names = self._engine_mgr.get_engine_names()
        self._region_manager.set_engine_names(names)
        # R12（P0-③）：同步右侧面板引擎切换下拉
        self._sync_engine_combo_r()

        last = self._config_mgr.get_last_engine()
        if last in names:
            self._engine_mgr.set_current_engine(last)
            self._current_engine = last
        else:
            self._current_engine = names[0] if names else "paddleocr"

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
            if a in (self._template_import_action, self._template_export_action, self._template_edit_action):
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

        # P1-7 修复：保留用户当前选择（保存/删除/导入模板后不再强制回退第一个模板）
        if names:
            target = cur if cur in names else names[0]
            self._current_template = target
            for a in self._template_action_group.actions():
                if a.text() == target:
                    a.setChecked(True)
                    break
            t = self._prompt_mgr.get_template_by_name(target)
            if t:
                self._config_panel.prompt_text = t.get("prompt", "")
            self._config_panel.select_template(target)
        else:
            self._current_template = ""

    # ── 同步区域默认值 ──
    def _sync_region_defaults(self):
        """将当前引擎/模板/提示词同步为新增区域的默认值。"""
        prompt = self._custom_prompt or self._config_panel.prompt_text
        self._video_preview.set_region_defaults(
            engine=self._current_engine, prompt=prompt, template=self._current_template
        )

    # ── 菜单事件：纠错 ──
    def _on_menu_preset_manage(self):
        """打开 API 预设管理对话框。"""
        dlg = PresetManageDialog(self)
        self._restore_dialog_geometry(dlg, "preset_dialog_geometry")
        if dlg.exec() == QDialog.Accepted:
            self._save_dialog_geometry(dlg, "preset_dialog_geometry")
        # R11：预设保存动作在对话框内完成且有自身反馈，此处不再无条件提示
        # "已更新"（此前未做任何修改也显示 ✅，误导用户）

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
        # P1-7：经 combo 统一同步（currentTextChanged → _on_template_quick_selected），
        # 使 combo/_current_template/菜单/prompt_text 四者一致指向刚保存的模板
        if name in self._prompt_mgr.get_template_names():
            self._template_combo.setCurrentText(name)
        self._status_label.setText(_("✅ 模板 [{name}] 已保存").format(name=name))

    def _on_config_template_deleted(self, name: str):
        """配置面板删除模板。"""
        self._prompt_mgr.remove_template(name)
        self._refresh_template_list()
        self._status_label.setText(_("🗑 模板 [{name}] 已删除").format(name=name))

    # ── 硬件加速 ──
    def _on_hw_accel_changed(self, enabled: bool):
        """统一控制 PaddleOCR + FFmpeg + ASR 的 GPU 开关。"""
        self._config_mgr.set("hw_accel", enabled)
        self._config_mgr.save_settings()
        self._engine_mgr.set_hw_accel(enabled)
        self._asr_mgr.set_hw_accel(enabled)
        self._video_preview.set_hw_accel(enabled)
        self._status_label.setText(_("✅ GPU 加速已启用") if enabled else _("🔲 GPU 加速已关闭"))

    # ── 过滤器管理 ──
    def _on_filter_add(self, keyword: str):
        if self._filter_mgr.add_keyword(keyword):
            self._status_label.setText(_("✅ 已添加过滤器: {keyword}").format(keyword=keyword))

    def _on_filter_remove(self, keyword: str):
        logger.info("收到删除过滤关键词请求: '%s'", keyword[:40])
        if self._filter_mgr.remove_keyword(keyword):
            self._status_label.setText(_("🗑 已移除过滤器: {keyword}").format(keyword=keyword))
        else:
            self._status_label.setText(_("⚠ 移除失败: 关键词 '{keyword}' 不存在").format(keyword=keyword[:30]))

    def _on_result_filter(self, raw_text: str):
        """表格行'加入过滤器'按钮 → 将整条 raw 文本加入过滤。"""
        if self._filter_mgr.add_keyword(raw_text):
            self._status_label.setText(_("✅ 已添加过滤器: {keyword}").format(keyword=raw_text[:40]))

    # ── 批量文件队列 ──
    def _on_batch_files_dropped(self, paths: list):
        """拖放多个文件到预览区时，替换整个批量队列并渲染第一个文件。"""
        self._batch_files = list(paths)
        first = self._batch_files[0]
        ext = Path(first).suffix.lower()
        if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
            self._video_preview.load_video(first)
        elif ext in (".png", ".jpg", ".jpeg", ".bmp"):
            self._video_preview.load_image(first)
        else:
            self._load_audio_file(first)
        # 注：此处此前调用 QCoreApplication.processEvents() 强制刷新。它是无用的
        # （预览加载是异步 QThread，处理事件不会让它更快完成）且有害 —— 它会在
        # 本处理函数尚未返回时重入事件循环，允许用户再次拖放/点击停止/关窗，
        # 造成状态错乱。只更新标签即可。
        self._update_batch_label()

    def _on_batch_clear(self):
        self._batch_files.clear()
        self._update_batch_label()
        self._video_preview.clear()
        self._status_label.setText(_("已清空队列和预览"))

    def _update_batch_label(self):
        n = len(self._batch_files)
        self._result_table.set_batch_count(n)

    # ── 事件 ──
    def _on_video_loaded(self, path):
        self._status_label.setText(_("已加载: {name}").format(name=Path(path).name))
        self._config_mgr.add_recent_video(path)
        self._config_mgr.set("last_directory", str(Path(path).parent))
        self._config_mgr.save_settings()
        self._region_manager.regions = self._video_preview.regions
        self._apply_right_panel_mode()

    def _on_region_group_toggled(self, collapsed: bool):
        """区域参数折叠/展开时，无需额外操作（滚动区域自动处理）。"""
        pass

    def _on_frame_captured(self, _):
        self._status_label.setText(_("测试帧已截取，在预览图上拖拽绘制矩形区域"))

    def _on_extract_env(self):
        """手动提取全文环境（后台异步，不阻塞 UI）。"""
        results = self._result_table.get_results()
        if not results:
            self._message_service.warning("提示", "暂无识别结果可提取环境。")
            return
        all_texts = [r.get("raw", "") for r in results if r.get("raw", "").strip()]
        if not all_texts:
            self._status_label.setText(_("⚠ 无有效文本可提取环境"))
            return
        self._status_label.setText(_("⏳ 正在提取全文环境..."))

        from core.workers import EnvExtractWorker

        self._env_worker = EnvExtractWorker(self._corrector, all_texts)
        self._env_worker.finished.connect(self._on_env_extracted)
        self._env_worker.error.connect(
            lambda e: self._status_label.setText(_("⚠ 环境提取失败: {err}").format(err=e[:30]))
        )
        self._env_worker.start()

    def _on_env_extracted(self, env: str):
        """环境提取完成（主线程回调）。"""
        if env:
            self._config_panel.corr_summary_prompt = env
            self._on_mode_changed(self._config_panel.get_mode_params())
            self._status_label.setText(_("✅ 全文环境已提取并回填"))
        else:
            self._status_label.setText(_("⚠ 环境提取失败，请检查 API 配置"))

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

    def _on_add_region_requested(self):
        self._status_label.setText(_("在视频预览上拖拽鼠标绘制矩形区域"))

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
            self._message_service.info("提示", "请先在「后处理」标签页中添加需要过滤的关键词。")
            return
        fm = self._filter_mgr
        deleted = self._result_table.delete_by_filter(lambda raw, corrected: fm.matches(raw + " " + corrected))
        if deleted:
            self._status_label.setText(_("🗑 已删除 {deleted} 条包含关键词的结果").format(deleted=deleted))
        else:
            self._status_label.setText(_("⚠ 无匹配关键词的结果"))

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
        # 停止播放但**不要回到起点**。
        # 此前这里调用 _on_stop_playback()（内部 seek_to(0.0)），它会 emit
        # position_changed(0.0)，结果表随即 sync_play_position(0) 高亮并滚动到覆盖
        # 0 秒的首行 —— 用户看到的就是"双击结果跳转到结果列表初始行"。
        # 音频分支尤其明显：它手工设置 _current_position/_set_slider 后**不再 emit**，
        # 于是视图永久停在首行（视频分支靠随后的 seek_to(ts) 侥幸纠正，但仍有闪跳）。
        vp.stop_playback(reset_position=False)
        if self._is_audio_file() and vp._audio_player:
            vp._audio_player.setPosition(int(ts * 1000))
        # 统一由 seek_to 负责：更新滑块/标签/当前位置，并 emit position_changed(ts)
        # 驱动结果表高亮与滚动到目标行（音频下 _do_seek 无 ffmpeg 会自然跳过）。
        vp.seek_to(ts)
        self._status_label.setText(_("已跳转到 {time}").format(time=r.get("time", "--:--")))

    def _on_video_position_changed(self, ts: float):
        """视频播放或跳转时同步表格高亮及画面字幕叠加。"""
        sub_text = self._result_table.sync_play_position(ts)
        self._video_preview.set_subtitle_overlay(sub_text)

    def _on_prompt_changed(self, p):
        self._custom_prompt = p
        self._sync_region_defaults()

    def _on_mode_changed(self, p):
        # R11 单源化：ConfigPanel._params 是唯一事实来源，此处仅做差异计算与副作用，
        # 不再维护 MainWindow._mode_params 镜像（此前双源导致模板选择等路径保存旧值）
        old_params = getattr(self, "_last_mode_params", {})
        # 仅当预设名确实变化时才切换
        if "corr_preset" in p and p["corr_preset"] != old_params.get("corr_preset", ""):
            self._corrector.apply_preset(p["corr_preset"])
        # 润色开关（R12：corr_use_template 孤儿键已删除）
        if "corr_polish" in p:
            self._corrector.polish_enabled = p["corr_polish"]
        # 设置同步 R10 修复：translate/stream/json 立即同步到 corrector 实例
        # （此前仅工具栏路径更新 _mode_params，corrector 不感知）
        # 注意：corr_extract_env 不得同步到 corrector._extract_env —— 前者是
        # "自动提取环境"开关（由 workflow._maybe_extract_env 直接消费 mp 值），
        # 后者是"手动管理环境上下文"标志（同步会导致自动提取被 _should_skip_env_extraction 跳过）
        if "corr_translate" in p:
            self._corrector.translate_mode = p["corr_translate"]
        if "corr_stream" in p:
            self._corrector.stream_mode = p["corr_stream"]
        if "corr_json" in p:
            self._corrector.json_mode = p["corr_json"]
        # OCR 版本变更（UI 白名单键）→ 路由到重建（批次 3：业务键差异
        # 由域对象 changed → RebuildRouter 接管，此处仅剩 UI 键路由）
        if p.get("s_ocr_version") != old_params.get("s_ocr_version"):
            self._router.notify_ui_key("s_ocr_version")
        self._last_mode_params = dict(p)
        # 延迟写盘合并多次连续变更（UI 状态白名单）
        self._schedule_mode_save()

    # ── 模板 ──
    def _on_template_edit(self):
        """打开模板编辑器弹窗。"""
        self._config_panel._open_template_editor()

    # ── 模板导入/导出 ──
    def _on_template_import(self):
        d = self._config_mgr.get_last_directory() or ""
        p = self._message_service.open_file("导入模板", d, "JSON 文件 (*.json);;所有文件 (*.*)")
        if not p:
            return
        try:
            count = self._prompt_mgr.import_templates(p)
            self._refresh_template_list()
            self._status_label.setText(_("✅ 已导入 {count} 个模板").format(count=count))
        except Exception as e:
            self._message_service.error("导入失败", _("模板导入失败:\n{err}").format(err=e))

    def _on_template_export(self):
        d = self._config_mgr.get_last_directory() or ""
        p = self._message_service.save_file(
            "导出模板", d + "/prompt_templates_export.json", "JSON 文件 (*.json);;所有文件 (*.*)"
        )
        if not p:
            return
        try:
            self._prompt_mgr.export_templates(p)
            self._status_label.setText(_("✅ 已导出模板到: {name}").format(name=Path(p).name))
        except Exception as e:
            self._message_service.error("导出失败", _("模板导出失败:\n{err}").format(err=e))

    def _on_capture_test_frame(self):
        self._video_preview.capture_test_frame()

    def _on_open_video(self):
        d = self._config_mgr.get_last_directory() or ""
        files = self._message_service.open_files(
            "打开文件",
            d,
            "媒体文件 (*.mp4 *.mkv *.avi *.mov *.webm *.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.opus "
            "*.png *.jpg *.jpeg *.bmp);;"
            "视频 (*.mp4 *.mkv *.avi *.mov *.webm);;"
            "音频 (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.opus);;"
            "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)",
        )
        if not files:
            return
        # 切换文件时清空结果表格
        self._result_table.clear_results()
        if len(files) == 1:
            # 单文件 → 加载到预览区
            p = files[0]
            ext = Path(p).suffix.lower()
            from core.asr_engine import SUPPORTED_AUDIO_EXTS

            if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
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
            if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
                self._video_preview.load_video(first)
            elif ext in (".png", ".jpg", ".jpeg", ".bmp"):
                self._video_preview.load_image(first)
            # 同 _on_batch_files_dropped：移除无用的 processEvents()
            # （重入事件循环会让用户在队列替换中途再次拖放/停止/关窗）
            self._update_batch_label()

    def _load_audio_file(self, path: str):
        """加载纯音频文件 —— 使用与视频一致的播放控件。"""
        self._video_preview.load_audio(path)
        self._status_label.setText(_("已加载音频: {name}（仅支持 ASR 和纠错）").format(name=Path(path).name))

    # ── 统一处理入口（单文件 / 批量）──
    def _on_start_processing(self):
        self._workflow.start_processing()
        self._btn_pause.setEnabled(True)
        self._paused = False
        self._btn_pause.setText(_("⏸ 暂停"))

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
        """暂停/继续（P1-5：布尔状态判断，en/ja 下逻辑不再反转）。"""
        if not self._paused:
            self._workflow.pause_processing()
        else:
            self._workflow.resume_processing()
        self._paused = not self._paused
        self._btn_pause.setText(_("⏸ 暂停") if not self._paused else _("▶ 继续"))

    def _on_process_log(self, m):
        self._status_label.setText(m)

    def _on_process_progress(self, cur, total, qs, sentinel):
        if total > 0:
            # 走统一的平滑动画入口：此前直接 setValue，OCR 逐帧进度会突跳
            # （而 workflow 的 progress_val 是动画的，两条路径表现不一致）
            self._set_progress_animated(min(100, int(cur * 100 / total)))
        m1, s1 = divmod(int(cur), 60)
        m2, s2 = divmod(int(total), 60)
        self._status_label.setText(
            _("处理中... {cur}s / {total}s | 哨兵: {sentinel}").format(cur=cur, total=total, sentinel=sentinel)
        )

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
        # P1-6 补充：批量队列推进需更新队列标签，方法定义在 MainWindow（委托链解析不到）
        wf._update_batch_label = self._update_batch_label
        wf._set_regions = lambda regions: (
            setattr(self._video_preview, "regions", regions),
            setattr(self._region_manager, "regions", regions),
        )
        wf._get_time_range = lambda: (self._video_preview.time_start, self._video_preview.time_end)
        wf._get_current_frame = lambda: self._video_preview.current_frame
        wf._get_roi_image = lambda ri: self._video_preview.get_roi_image(ri)
        wf._get_current_engine = lambda: self._current_engine
        wf._get_current_template = lambda: self._current_template
        wf._get_custom_prompt = lambda: self._custom_prompt
        wf._get_config_prompt = lambda: self._config_panel.prompt_text
        # R11 单源化：从 ConfigPanel 读取实时参数（替代 _mode_params 镜像）
        wf._get_mode_params = lambda: self._config_panel.get_mode_params()
        wf._get_is_audio_file = lambda: self._is_audio_file()
        wf._get_results = lambda: self._result_table.get_results()
        wf._clear_results_table = lambda: self._result_table.clear_results()
        wf._clear_results_by_type = lambda rgn, eng: self._result_table.clear_by_type(rgn, eng)
        wf._asr_region_name = lambda: self._config_panel.get_mode_params().get("asr_region_name", "语音")
        wf._sort_results_table = lambda order: self._result_table.sort_by_order(order)
        wf._sort_by_time = lambda: self._result_table.sort_by_time()
        wf._get_polished_results = lambda sim, ml, dedup=True: self._result_table.get_polished_results(
            post_sim_threshold=sim, post_min_text_len=ml, post_sim_dedup=dedup
        )
        wf._get_table_row_count = lambda: self._result_table._table.model().rowCount()

        # ── 信号连接 ──
        wf.status_msg.connect(lambda m: self._status_label.setText(m))
        wf.progress_val.connect(self._set_progress_animated)
        wf.buttons_enabled.connect(self._on_workflow_buttons)
        wf.error_dialog.connect(lambda t, m: self._message_service.error(t, m))
        wf.info_dialog.connect(lambda t, m: self._message_service.info(t, m))
        wf.result_row.connect(self._on_process_result)
        wf.correction_updated.connect(self._on_correction_ready)
        wf.correction_stream_updated.connect(self._on_correction_stream)
        wf.polish_updated.connect(self._on_polish_ready)
        # M14 死链清理：batch_progress/batch_file_done 从不 emit，连接与槽一并删除
        wf.batch_all_done.connect(lambda: self._update_batch_label())
        wf.process_finished.connect(self._on_workflow_process_finished)

    def _on_workflow_process_finished(self):
        """处理会话完成（process_finished 信号，同步 DirectConnection）。

        设置同步 R5 修复：ASR 运行中改设置时引擎重建被延迟
        （RebuildRouter._asr_pending），在此补建；随后执行原有的 end_sec 回填。
        """
        self._router.on_process_finished()
        self._recalculate_end_seconds()

    def _on_correction_selected(self):
        """对选中的表格行进行 AI 纠错（委托 WorkflowManager）。"""
        selected_rows = self._result_table.get_selected_rows()
        if not selected_rows:
            self._message_service.warning("提示", "请先在表格中选中需要纠错的行（可多选）。")
            return
        self._workflow.correct_selected(selected_rows)

    def _on_correction_all(self):
        """对全部结果行进行 AI 纠错（委托 WorkflowManager）。"""
        self._workflow.correct_all()

    def _on_batch_correction_finished(self):
        """批量纠错全部完成。"""
        self.set_correction_enabled(True)
        n = self._result_table._table.model().rowCount()
        self._status_label.setText(_("✅ 完成: {n} 条结果 | 批量纠错完成").format(n=n))

    def _on_batch_correction_error(self, err):
        """批量纠错出错。"""
        self.set_correction_enabled(True)
        self._status_label.setText(_("⚠ 批量纠错出错: {err}").format(err=err))

    def _on_process_result(self, ts, t_str, rname, ename, raw, conf: float = 0.0, end_sec: float = 0.0):
        # 过滤器：包含任一关键词则跳过（WorkflowManager 已过滤，此处为双重保险）
        if self._filter_mgr.matches(raw):
            return
        # 视频模式下按时间顺序插入，图片模式下追加到末尾
        is_image = self._video_preview.is_image if hasattr(self, "_video_preview") else False
        self._result_table.add_result(
            time_str=t_str,
            region=rname,
            engine=ename,
            raw_text=raw,
            time_sec=ts,
            confidence=conf,
            end_sec=end_sec,
            sorted_insert=not is_image,
        )

    def _on_correction_ready(self, row, raw, corrected):
        self._result_table.update_correction_result(row, corrected)  # 纠错→col5
        self._correction_results[row] = corrected
        self._correction_pending.discard(row)

    def _on_correction_failed(self, row, _):
        self._correction_pending.discard(row)

    def _on_correction_stream(self, row, partial_text):
        """流式输出模式：实时更新表格中的纠错文本（col5）。"""
        self._result_table.update_correction_result(row, partial_text)

    def _on_polish_selected(self):
        """对选中行进行润色（委托 WorkflowManager）。"""
        selected_rows = self._result_table.get_selected_rows()
        if not selected_rows:
            self._message_service.warning("提示", "请先在表格中选中需要润色的行（可多选）。")
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
        # ── 置信度阈值过滤（P0-T6 修复：统一走 model reset，removeRow 为空操作）──
        if self._config_panel.get_mode_params().get("post_conf_enabled", False):
            threshold = self._config_panel.get_mode_params().get("post_conf_threshold", 0.6)
            self._result_table.remove_rows_by_predicate(
                lambda r: r.get("engine", "") == "paddleocr" and (r.get("confidence", 1.0) or 0.0) < threshold
            )

        # ── end_sec 回填 ──
        results = self._result_table._results
        if not results:
            return
        from collections import defaultdict

        sub_dur = self._config_panel.get_mode_params().get("subtitle_duration", 3.0)
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
        # 经统一点复位为空闲态（此前手工逐个 setEnabled 且漏了 polish/polish_all，
        # 处理出错后润色按钮会一直保持禁用）
        self.reset_workflow_buttons()
        self._paused = False
        self._btn_pause.setText(_("⏸ 暂停"))
        self._progress_bar.setValue(0)
        self._status_label.setText(_("❌ 处理失败: {err}").format(err=err))

    # ── 导出 ──
    def _on_export(self, fmt, path):
        polished = self._result_table.get_polished_results(
            post_sim_threshold=self._config_panel.get_mode_params().get("post_sim_threshold", 0.9),
            post_min_text_len=self._config_panel.get_mode_params().get("post_min_text_len", 2),
        )
        if not polished:
            return self._message_service.info("提示", "过滤后无有效结果可导出。")

        # 统一补充 end_sec（OCR 结果可能缺少该字段）
        sub_dur = self._config_panel.get_mode_params().get("subtitle_duration", 3.0)
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
            srt_mode_map = {
                "仅纠正结果": "corrected",
                "仅原文": "original",
                "双语对照（原文+纠正）": "dual",
                "原文 换行 纠正": "dual",
            }
            srt_mode = srt_mode_map.get(
                self._config_panel.get_mode_params().get("srt_export_mode", "仅纠正结果"), "corrected"
            )
            export_results(
                polished,
                path,
                fmt,
                bool(cmap),
                cmap,
                # R12：export_keep_original 无 UI 无默认值（恒 False），死读清理
                srt_mode=srt_mode,
            )
            self._status_label.setText(_("✅ 已导出: {name}").format(name=Path(path).name))
        except Exception as e:
            self._message_service.error("导出失败", str(e))
