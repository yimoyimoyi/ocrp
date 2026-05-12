# -*- coding: utf-8 -*-
"""主窗口 —— ORCP OCR 处理工具。
引擎/纠错/模板选择 → 顶端菜单栏；处理参数/提示词模板管理 → 配置面板。"""

import os, sys, json
from pathlib import Path
from typing import Optional, List, Dict

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QComboBox, QCheckBox,
    QPushButton, QSpinBox, QDoubleSpinBox, QFileDialog,
    QFrame, QMessageBox, QStatusBar, QProgressBar, QSplitter,
    QTextEdit, QAction, QActionGroup, QDialog, QDialogButtonBox,
    QFormLayout, QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, QTimer

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config_manager import ConfigManager
from style_loader import load_qss_theme, scale_stylesheet
from core.ocr_engine import OCREngineManager
from core.ai_correction import AICorrector, load_correction_config
from core.frame_processor import FrameProcessor
from core.filter_manager import FilterManager
from core.result_processor import polish_results, export_results, sort_results_by_order
from core.prompt_manager import PromptTemplateManager
from core.workflow_manager import WorkflowManager
from ui.video_preview import VideoPreviewWidget
from ui.region_manager import RegionManagerWidget
from ui.config_panel import ConfigPanel
from ui.result_table import ResultTableWidget
from ui.workers import (VideoProcessWorker, AICorrectionWorker,
                        BatchCorrectionWorker, ImageProcessWorker,
                        BatchProcessWorker, AudioProcessWorker)
from core.asr_engine import ASREngineManager

WIN_TITLE = "ORCP - OCR 处理工具"


class EngineConfigDialog(QDialog):
    """引擎配置对话框（从菜单栏唤起）。
    内置可用性检测、模型列表获取、保存预设。
    """

    def __init__(self, engine_name: str, config: dict, is_local: bool = False,
                 engine_manager=None, parent=None):
        super().__init__(parent)
        self._engine_name = engine_name
        self._engine_mgr = engine_manager
        self.setWindowTitle(f"引擎配置 - {engine_name}")
        self.setMinimumWidth(440)
        self._result = dict(config)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(6)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("sk-xxx")
        self._api_key_edit.setText(config.get("api_key", ""))
        form.addRow("API Key:", self._api_key_edit)

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        self._base_url_edit.setText(config.get("base_url", ""))
        form.addRow("Base URL:", self._base_url_edit)

        self._model_edit = QComboBox()
        self._model_edit.setEditable(True)
        self._model_edit.setInsertPolicy(QComboBox.NoInsert)
        self._model_edit.lineEdit().setPlaceholderText("gpt-4o")
        self._model_edit.setEditText(config.get("model", ""))
        form.addRow("模型:", self._model_edit)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 300)
        self._timeout_spin.setValue(config.get("timeout", 30))
        self._timeout_spin.setSuffix(" 秒")
        form.addRow("超时:", self._timeout_spin)

        self._gpu_check = QCheckBox("启用 GPU 加速")
        self._gpu_check.setChecked(config.get("use_gpu", False))
        self._gpu_check.setVisible(is_local)
        form.addRow("", self._gpu_check)

        layout.addLayout(form)

        # 保存预设按钮
        save_preset_row = QHBoxLayout()
        btn_save_preset = QPushButton("💾 保存为 API 预设")
        btn_save_preset.setToolTip("将当前 API Key / Base URL / 模型/超时保存为预设，供纠错等功能快速使用")
        btn_save_preset.clicked.connect(self._on_save_preset)
        save_preset_row.addWidget(btn_save_preset)
        save_preset_row.addStretch()
        layout.addLayout(save_preset_row)

        # 检测行
        check_row = QHBoxLayout()
        check_row.setSpacing(4)
        self._status_label = QLabel("")
        check_row.addWidget(self._status_label, 1)
        btn_check = QPushButton("🔄 检测可用性")
        btn_check.clicked.connect(self._on_check)
        check_row.addWidget(btn_check)
        btn_models = QPushButton("📋 获取模型")
        btn_models.clicked.connect(self._on_get_models)
        check_row.addWidget(btn_models)
        layout.addLayout(check_row)

        layout.addSpacing(8)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_save_preset(self):
        """将当前配置保存为 API 预设（基于引擎名称或自定义名称）。"""
        from core.api_preset_manager import APIPresetManager
        mgr = APIPresetManager()
        cfg = {
            "api_key": self._api_key_edit.text(),
            "base_url": self._base_url_edit.text(),
            "model": self._model_edit.currentText(),
            "timeout": self._timeout_spin.value(),
        }
        name = f"{self._engine_name} 预设"
        mgr.add_preset(name, cfg)
        self._status_label.setText(f"✅ 已保存预设: {name}")

    def _on_check(self):
        self._status_label.setText("⏳ 检测中...")
        QApplication.processEvents()
        try:
            eng = self._engine_mgr.get_engine(self._engine_name) if self._engine_mgr else None
        except Exception:
            eng = None
        if eng:
            try:
                avail = eng.check_availability()
                self._status_label.setText("✅ 可用" if avail else "❌ 不可用")
            except Exception:
                self._status_label.setText("❌ 检测失败")
        else:
            self._status_label.setText("⚠ 引擎未初始化")

    def _on_get_models(self):
        self._status_label.setText("⏳ 获取模型列表...")
        QApplication.processEvents()
        try:
            eng = self._engine_mgr.get_engine(self._engine_name) if self._engine_mgr else None
        except Exception:
            eng = None
        if eng:
            try:
                models = eng.get_model_list()
                if models:
                    self.set_models(models)
                    self._status_label.setText(f"✅ {len(models)} 个模型")
                else:
                    self._status_label.setText("⚠ 未获取到模型")
            except Exception as e:
                self._status_label.setText(f"❌ 失败: {str(e)[:30]}")
        else:
            self._status_label.setText("⚠ 引擎未初始化")

    def _on_accept(self):
        self._result = {
            "api_key": self._api_key_edit.text(),
            "base_url": self._base_url_edit.text(),
            "model": self._model_edit.currentText(),
            "timeout": self._timeout_spin.value(),
            "use_gpu": self._gpu_check.isChecked(),
        }
        self.accept()

    def get_config(self) -> dict:
        return dict(self._result)

    def set_models(self, models: List[str]):
        if not models:
            return
        current = self._model_edit.currentText()
        self._model_edit.blockSignals(True)
        self._model_edit.clear()
        self._model_edit.addItems(models)
        if current:
            self._model_edit.setEditText(current)
        else:
            self._model_edit.setEditText(models[0])
        self._model_edit.blockSignals(False)


class PresetManageDialog(QDialog):
    """API 预设管理对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API 预设管理")
        self.setMinimumWidth(500)
        from core.api_preset_manager import APIPresetManager
        self._mgr = APIPresetManager()
        self._mgr.reload()

        layout = QVBoxLayout(self)

        # 上排：预设列表
        row = QHBoxLayout()
        self._list = QListWidget()
        self._list.addItems(self._mgr.get_names())
        self._list.currentTextChanged.connect(self._on_selection)
        row.addWidget(self._list, 1)

        btn_col = QVBoxLayout()
        btn_add = QPushButton("+ 新建")
        btn_add.clicked.connect(self._on_add)
        btn_col.addWidget(btn_add)
        btn_del = QPushButton("- 删除")
        btn_del.clicked.connect(self._on_delete)
        btn_col.addWidget(btn_del)
        btn_col.addStretch()
        row.addLayout(btn_col)
        layout.addLayout(row)

        # 下排：编辑区
        form = QFormLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("预设名称")
        form.addRow("名称:", self._name_edit)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("http://127.0.0.1:8080")
        form.addRow("Base URL:", self._url_edit)

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("API Key（可选）")
        form.addRow("API Key:", self._key_edit)

        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("模型名（可选）")
        form.addRow("模型:", self._model_edit)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 300)
        self._timeout_spin.setValue(30)
        self._timeout_spin.setSuffix(" 秒")
        form.addRow("超时:", self._timeout_spin)

        layout.addLayout(form)

        btn_save = QPushButton("💾 保存")
        btn_save.clicked.connect(self._on_save)
        layout.addWidget(btn_save)

        layout.addSpacing(8)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_selection(self, name: str):
        preset = self._mgr.get_preset(name)
        if preset:
            self._name_edit.setText(name)
            self._url_edit.setText(preset.get("base_url", ""))
            self._key_edit.setText(preset.get("api_key", ""))
            self._model_edit.setText(preset.get("model", ""))
            self._timeout_spin.setValue(preset.get("timeout", 30))

    def _on_add(self):
        self._name_edit.clear()
        self._url_edit.clear()
        self._key_edit.clear()
        self._model_edit.clear()
        self._timeout_spin.setValue(30)
        self._list.clearSelection()

    def _on_delete(self):
        name = self._list.currentItem().text() if self._list.currentItem() else ""
        if not name:
            return
        reply = QMessageBox.question(self, "确认删除", f"确定要删除预设「{name}」吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._mgr.delete_preset(name)
            self._refresh_list()

    def _on_save(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入预设名称。")
            return
        cfg = {
            "api_key": self._key_edit.text(),
            "base_url": self._url_edit.text(),
            "model": self._model_edit.text(),
            "timeout": self._timeout_spin.value(),
        }
        existing = self._mgr.get_preset(name)
        if existing:
            self._mgr.update_preset(name, cfg)
        else:
            self._mgr.add_preset(name, cfg)
        self._refresh_list()
        # 重新选中
        for i in range(self._list.count()):
            if self._list.item(i).text() == name:
                self._list.setCurrentRow(i)
                break

    def _refresh_list(self):
        self._list.blockSignals(True)
        cur = self._list.currentItem().text() if self._list.currentItem() else ""
        self._list.clear()
        self._mgr.reload()
        names = self._mgr.get_names()
        self._list.addItems(names)
        if cur in names:
            items = self._list.findItems(cur, Qt.MatchExactly)
            if items:
                self._list.setCurrentRow(self._list.row(items[0]))
        self._list.blockSignals(False)

class CorrectionConfigDialog(QDialog):
    """AI 纠错配置对话框（从菜单栏唤起）—— 独立 API 配置。"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 纠错设置")
        self.setMinimumWidth(480)
        self._result = dict(config)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(6)

        self._enabled_check = QCheckBox("启用 AI 纠错")
        self._enabled_check.setChecked(config.get("enabled", False))
        form.addRow("", self._enabled_check)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("sk-xxx（可选）")
        self._api_key_edit.setText(config.get("api_key", ""))
        form.addRow("API Key:", self._api_key_edit)

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("http://127.0.0.1:8080")
        self._base_url_edit.setText(config.get("base_url", "http://127.0.0.1:8080"))
        form.addRow("Base URL:", self._base_url_edit)

        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("gpt-4o / gemma 等")
        self._model_edit.setText(config.get("model", ""))
        form.addRow("模型:", self._model_edit)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 300)
        self._timeout_spin.setValue(config.get("timeout", 30))
        self._timeout_spin.setSuffix(" 秒")
        form.addRow("超时:", self._timeout_spin)

        self._retry_spin = QSpinBox()
        self._retry_spin.setRange(0, 10)
        self._retry_spin.setValue(config.get("retry_on_failure", 2))
        form.addRow("重试次数:", self._retry_spin)

        layout.addLayout(form)

        # ── 自定义总结提示词 ──
        layout.addWidget(QLabel("总结/概括提示词（可选，留空则用默认）:"))
        self._summary_prompt_edit = QTextEdit()
        self._summary_prompt_edit.setPlainText(config.get("summary_prompt", ""))
        self._summary_prompt_edit.setMaximumHeight(60)
        self._summary_prompt_edit.setPlaceholderText("用于从全文提取环境上下文的提示词")
        layout.addWidget(self._summary_prompt_edit)

        # ── 自定义纠错 System Prompt ──
        layout.addWidget(QLabel("纠错系统提示词（可选，留空则用默认）:"))
        self._sys_prompt_edit = QTextEdit()
        self._sys_prompt_edit.setPlainText(config.get("correction_system_prompt", ""))
        self._sys_prompt_edit.setMaximumHeight(60)
        self._sys_prompt_edit.setPlaceholderText("注入到纠错请求的 system message")
        layout.addWidget(self._sys_prompt_edit)

        # ── 纠错用户提示词 ──
        layout.addWidget(QLabel("纠错用户提示词:"))
        self._prompt_edit = QTextEdit()
        self._prompt_edit.setPlainText(config.get("correction_prompt", ""))
        self._prompt_edit.setMaximumHeight(80)
        layout.addWidget(self._prompt_edit)

        # ── 输出格式 ──
        layout.addWidget(QLabel("输出格式标记（可选）:"))
        self._output_format_edit = QLineEdit()
        self._output_format_edit.setText(config.get("output_format", ""))
        self._output_format_edit.setPlaceholderText("[纠正后文本]")
        layout.addWidget(self._output_format_edit)

        layout.addSpacing(8)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_accept(self):
        self._result = {
            "enabled": self._enabled_check.isChecked(),
            "api_key": self._api_key_edit.text(),
            "base_url": self._base_url_edit.text(),
            "model": self._model_edit.text(),
            "timeout": self._timeout_spin.value(),
            "retry_on_failure": self._retry_spin.value(),
            "correction_prompt": self._prompt_edit.toPlainText(),
            "summary_prompt": self._summary_prompt_edit.toPlainText(),
            "correction_system_prompt": self._sys_prompt_edit.toPlainText(),
            "output_format": self._output_format_edit.text(),
        }
        self.accept()

    def get_config(self) -> dict:
        return dict(self._result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WIN_TITLE)
        self.setMinimumSize(960, 640)

        self._config_mgr = ConfigManager()
        self._engine_mgr = OCREngineManager()
        self._asr_mgr = ASREngineManager()
        self._corrector = AICorrector(load_correction_config(), engine_manager=self._engine_mgr)
        self._prompt_mgr = PromptTemplateManager()
        self._filter_mgr = FilterManager()

        # ── 业务流程管理器 ──
        self._workflow = WorkflowManager(self)

        self._filtered_count: int = 0

        self._frame_processor: Optional[FrameProcessor] = None
        self._all_raw_results: list = []
        self._asr_results: list = []
        self._correction_results: Dict[int, str] = {}
        self._correction_pending: set = set()
        self._custom_prompt: str = ""
        self._mode_params: dict = {}
        self._current_engine: str = "paddleocr"
        self._current_template: str = ""
        self._batch_files: List[str] = []

        self._theme = self._config_mgr.get_theme()

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_label = QLabel("就绪")
        self._engine_label = QLabel("  |  引擎: paddleocr")
        self._time_label = QLabel("")
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximumWidth(180)
        self._progress_bar.setMaximumHeight(16)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("")
        self._status_bar.addWidget(self._status_label, 1)
        self._status_bar.addPermanentWidget(self._time_label)
        self._status_bar.addPermanentWidget(self._progress_bar)
        self._status_bar.addPermanentWidget(self._engine_label)

        self.build_ui()
        self._build_menu_bar()
        self._restore_window_geometry()
        self._apply_theme()
        # 加载硬件加速（必须在 _refresh_engine_list 之前）
        hw = self._config_mgr.get_hw_accel()
        if hw:
            self._engine_mgr.set_hw_accel(True)
        self._refresh_engine_list()
        self._refresh_template_list()
        self._config_panel.set_filter_keywords(self._filter_mgr.get_keywords())
        self._config_panel.set_hw_accel(hw)
        self._video_preview.set_hw_accel(hw)
        self._load_correction_config_to_ui()
        # 恢复上次保存的配置参数
        self._restore_mode_params()
        # ── 配置 WorkflowManager ──
        self._configure_workflow()

    # ── 菜单栏 ──
    def _build_menu_bar(self):
        mb = self.menuBar()

        # ── 引擎菜单 ──
        self._engine_menu = mb.addMenu("引擎(&E)")
        self._engine_action_group = QActionGroup(self)
        self._engine_action_group.setExclusive(True)
        self._engine_menu.addSeparator()
        self._engine_config_action = QAction("引擎配置...", self)
        self._engine_config_action.triggered.connect(self._on_menu_engine_config)
        self._engine_menu.addAction(self._engine_config_action)

        # ── 纠错菜单 ──
        self._corr_menu = mb.addMenu("纠错(&C)")
        self._corr_config_action = QAction("纠错设置...", self)
        self._corr_config_action.triggered.connect(self._on_menu_correction_config)
        self._corr_menu.addAction(self._corr_config_action)
        self._corr_preset_action = QAction("API 预设管理...", self)
        self._corr_preset_action.triggered.connect(self._on_menu_preset_manage)
        self._corr_menu.addAction(self._corr_preset_action)

        # ── 模板菜单 ──
        self._template_menu = mb.addMenu("模板(&T)")
        self._template_action_group = QActionGroup(self)
        self._template_action_group.setExclusive(True)
        self._template_menu.addSeparator()
        self._template_import_action = QAction("📥 导入模板...", self)
        self._template_import_action.triggered.connect(self._on_template_import)
        self._template_menu.addAction(self._template_import_action)
        self._template_export_action = QAction("📤 导出模板...", self)
        self._template_export_action.triggered.connect(self._on_template_export)
        self._template_menu.addAction(self._template_export_action)

        # ── 批量菜单 ──
        self._batch_menu = mb.addMenu("批量(&B)")
        self._batch_clear_action = QAction("🗑 清空队列", self)
        self._batch_clear_action.triggered.connect(self._on_batch_clear)
        self._batch_menu.addAction(self._batch_clear_action)

    # ── 构建 UI ──
    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4); root.setSpacing(4)

        self._main_splitter = QSplitter(Qt.Horizontal)

        # 左侧：视频预览
        left = QWidget()
        ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0)
        self._video_preview = VideoPreviewWidget()
        self._video_preview.video_loaded.connect(self._on_video_loaded)
        self._video_preview.frame_captured.connect(self._on_frame_captured)
        self._video_preview.regions_changed.connect(self._on_preview_regions_changed)
        self._video_preview.files_dropped.connect(self._on_batch_files_dropped)

        vc = QHBoxLayout()
        self._btn_capture = QPushButton("📸 截取测试帧")
        self._btn_capture.clicked.connect(self._on_capture_test_frame)
        vc.addWidget(self._btn_capture)
        self._btn_open = QPushButton("📂 打开视频/图片")
        self._btn_open.clicked.connect(self._on_open_video)
        vc.addWidget(self._btn_open)
        self._btn_batch_clear = QPushButton("🗑 清空队列")
        self._btn_batch_clear.setObjectName("btnBatchClear")
        self._btn_batch_clear.clicked.connect(self._on_batch_clear)
        vc.addWidget(self._btn_batch_clear); vc.addStretch()
        ll.addLayout(vc); ll.addWidget(self._video_preview, 1)
        self._main_splitter.addWidget(left)

        # 右侧
        rs = QSplitter(Qt.Vertical)
        # 上部：区域管理器 + 配置面板（可拖拽拆分）
        self._right_top_splitter = QSplitter(Qt.Horizontal)
        self._right_top_splitter.setChildrenCollapsible(False)

        self._region_manager = RegionManagerWidget()
        self._region_manager.region_selected.connect(self._on_region_selected)
        self._region_manager.region_updated.connect(self._on_region_updated)
        self._region_manager.region_add_requested.connect(self._on_add_region_requested)
        self._region_manager.region_removed.connect(self._on_remove_region)
        self._region_manager.regions_cleared.connect(self._on_clear_regions)

        self._config_panel = ConfigPanel()
        self._config_panel.prompt_changed.connect(self._on_prompt_changed)
        self._config_panel.mode_changed.connect(self._on_mode_changed)
        self._config_panel.template_created.connect(self._on_config_template_selected)
        self._config_panel.template_saved.connect(self._on_config_template_saved)
        self._config_panel.template_deleted.connect(self._on_config_template_deleted)
        self._config_panel.hw_accel_changed.connect(self._on_hw_accel_changed)
        self._config_panel.filter_add_requested.connect(self._on_filter_add)
        self._config_panel.filter_remove_requested.connect(self._on_filter_remove)
        self._config_panel.extract_env_clicked.connect(self._on_extract_env)
        self._config_panel.collapse_requested.connect(self._on_config_panel_collapsed)

        self._right_top_splitter.addWidget(self._region_manager)
        self._right_top_splitter.addWidget(self._config_panel)
        self._right_top_splitter.setSizes([300, 200])

        rs.addWidget(self._right_top_splitter)

        self._result_table = ResultTableWidget()
        self._result_table.filter_requested.connect(self._on_result_filter)
        self._result_table.delete_filtered_requested.connect(self._on_delete_filtered_results)
        self._result_table.export_requested.connect(self._on_export)
        self._result_table.cell_edit_activated.connect(self._on_result_cell_edit)
        rs.addWidget(self._result_table)
        rs.setSizes([300, 400])
        self._main_splitter.addWidget(rs)

        saved = self._config_mgr.get_splitter_sizes()
        if saved: self._main_splitter.setSizes(saved)

        root.addWidget(self._main_splitter, 1)

        # 批量文件队列栏（仅显示队列数量）
        batch_bar = QFrame()
        batch_bar.setObjectName("batchBar")
        batch_bar.setFrameShape(QFrame.StyledPanel)
        batch_bl = QHBoxLayout(batch_bar)
        batch_bl.setContentsMargins(4, 2, 4, 2); batch_bl.setSpacing(4)
        batch_bl.addWidget(QLabel("📋 批量队列:"))
        self._batch_count_label = QLabel("(空)")
        batch_bl.addWidget(self._batch_count_label, 1)
        root.addWidget(batch_bar)

        # 底部栏
        bar = QFrame()
        bar.setObjectName("bottomBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(4, 2, 4, 0); bl.setSpacing(4)
        self._btn_start = QPushButton("▶ 开始处理")
        self._btn_start.setObjectName("btnStart")
        self._btn_start.clicked.connect(self._on_start_processing)
        bl.addWidget(self._btn_start)
        self._btn_correction = QPushButton("✏ 纠错选中")
        self._btn_correction.setObjectName("btnCorrection")
        self._btn_correction.setToolTip("对选中的结果行进行 AI 纠错")
        self._btn_correction.clicked.connect(self._on_correction_selected)
        bl.addWidget(self._btn_correction)
        self._btn_correction_all = QPushButton("✏ 纠正全部")
        self._btn_correction_all.setObjectName("btnCorrectionAll")
        self._btn_correction_all.setToolTip("对全部结果行进行 AI 纠错（忽略选中状态）")
        self._btn_correction_all.clicked.connect(self._on_correction_all)
        bl.addWidget(self._btn_correction_all)
        self._btn_pause = QPushButton("⏸ 暂停")
        self._btn_pause.setObjectName("btnPause")
        self._btn_pause.setEnabled(False)
        self._btn_pause.clicked.connect(self._on_pause_processing)
        bl.addWidget(self._btn_pause)
        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop_processing)
        bl.addWidget(self._btn_stop); bl.addStretch()
        self._btn_theme = QPushButton("🌙 暗色" if self._theme == "dark" else "☀ 亮色")
        self._btn_theme.setObjectName("btnTheme")
        self._btn_theme.clicked.connect(self._toggle_theme)
        bl.addWidget(self._btn_theme)
        root.addWidget(bar)

    # ── 主题 ──
    def _apply_theme(self, theme=None):
        self._theme = theme or self._theme
        self._config_mgr.set("theme", self._theme); self._config_mgr.save_settings()
        qss = scale_stylesheet(load_qss_theme(self._theme), self._config_mgr.get_scale())
        app = QApplication.instance()
        if app: app.setStyleSheet(qss)
        self._btn_theme.setText("☀ 亮色" if self._theme == "light" else "🌙 暗色")

    def _toggle_theme(self):
        self._apply_theme("light" if self._theme == "dark" else "dark")

    # ── 窗口状态 ──
    def _restore_window_geometry(self):
        g = self._config_mgr.get_window_geometry()
        if g: self.setGeometry(g.get("x", 100), g.get("y", 100),
                                g.get("width", 1280), g.get("height", 800))
        else: self.resize(1280, 800)

    def _save_window_geometry(self):
        g = self.geometry()
        self._config_mgr.set("window_geometry",
            {"x": g.x(), "y": g.y(), "width": g.width(), "height": g.height()})

    def _restore_mode_params(self):
        """从 settings.json 恢复 UI 配置参数。"""
        saved = self._config_mgr.get("mode_params", {})
        if saved:
            self._mode_params.update(saved)
            # 恢复自定义提示词
            self._custom_prompt = saved.get("corr_prompt", "")
            # 回填所有 UI 控件
            self._config_panel.apply_mode_params(saved)
        # 同步区域默认值（引擎/模板/提示词），确保新创建的区域使用当前提示词
        self._sync_region_defaults()

    def _save_mode_params(self):
        """保存当前 UI 配置参数到 settings.json。"""
        self._config_mgr.set("mode_params", dict(self._mode_params))
        self._config_mgr.set("splitter_sizes", self._main_splitter.sizes())
        self._config_mgr.save_settings()

    def closeEvent(self, ev):
        """关闭窗口 —— 优先隐藏 UI，后台静默清理。"""
        # ── 第一步：立即隐藏窗口，用户感知到 UI 已关闭 ──
        self.hide()

        # ── 第二步：保存状态 ──
        self._save_window_geometry()
        self._save_mode_params()

        # ── 第三步：后台线程静默清理所有子进程/线程 ──
        def _silent_cleanup():
            try:
                self._workflow.cleanup()
                vp = self._video_preview
                if vp:
                    if getattr(vp, '_is_playing', False):
                        vp._pause_video()
                    if vp._player_proc:
                        try:
                            vp._player_proc.kill()
                        except Exception:
                            pass
                    if vp._ffmpeg:
                        try:
                            vp._ffmpeg.close()
                        except Exception:
                            pass
                if self._asr_mgr:
                    try:
                        engine = self._asr_mgr.get_engine()
                        if engine and hasattr(engine, '_proc') and engine._proc:
                            engine._proc.kill()
                    except Exception:
                        pass
                print(f"[MainWindow] ✅ 后台清理完成")
            except Exception as e:
                print(f"[MainWindow] ⚠ 后台清理异常: {e}")

        import threading
        threading.Thread(target=_silent_cleanup, daemon=True).start()

        # 立即接受关闭事件（UI 已隐藏，后台静默清理）
        super().closeEvent(ev)

    # ── 刷新 ──
    def _refresh_engine_list(self):
        names = self._engine_mgr.get_engine_names()
        self._region_manager.set_engine_names(names)

        # 重建引擎菜单单选动作
        menu = self._engine_menu
        for a in self._engine_action_group.actions():
            self._engine_action_group.removeAction(a)
        for a in menu.actions():
            if a.isSeparator():
                continue
            if a is self._engine_config_action:
                continue
            menu.removeAction(a)

        for name in names:
            action = QAction(name, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, n=name: self._on_menu_engine_selected(n))
            self._engine_action_group.addAction(action)
            menu.insertAction(self._engine_menu.actions()[0], action)

        last = self._config_mgr.get_last_engine()
        if last in names:
            self._engine_mgr.set_current_engine(last)
            self._current_engine = last
        else:
            self._current_engine = names[0] if names else "paddleocr"

        for a in self._engine_action_group.actions():
            if a.text() == self._current_engine:
                a.setChecked(True)
                break

        self._engine_label.setText(f"  |  引擎: {self._current_engine}")
        eng = self._engine_mgr.get_current_engine()
        if eng:
            avail = eng.is_available()
            self._engine_label.setText(
                f"  |  引擎: {self._current_engine} {'✅' if avail else '⚠'}"
            )

    def _refresh_template_list(self):
        names = self._prompt_mgr.get_template_names()
        self._region_manager.set_template_names(names)
        self._config_panel.set_template_names(names)

        # 重建模板菜单单选动作
        menu = self._template_menu
        for a in self._template_action_group.actions():
            self._template_action_group.removeAction(a)
        for a in menu.actions():
            if a.isSeparator():
                continue
            if a in (self._template_import_action, self._template_export_action):
                continue
            menu.removeAction(a)

        for name in names:
            action = QAction(name, self)
            action.setCheckable(True)
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
                self._config_panel.set_template_prompt(t.get("prompt", ""))

    def _get_engine_config(self, name: str) -> dict:
        eng_cfg = self._engine_mgr._config.get("engines", {}).get(name, {})
        return dict(eng_cfg.get("config", {}))

    def _is_local_engine(self, name: str) -> bool:
        eng_cfg = self._engine_mgr._config.get("engines", {}).get(name, {})
        return eng_cfg.get("type") == "local"

    # ── 同步区域默认值 ──
    def _sync_region_defaults(self):
        """将当前引擎/模板/提示词同步为新增区域的默认值。"""
        prompt = self._custom_prompt or self._config_panel.prompt_text
        self._video_preview.set_region_defaults(
            engine=self._current_engine,
            prompt=prompt,
            template=self._current_template
        )

    # ── 菜单事件：引擎 ──
    def _on_menu_engine_selected(self, name: str):
        self._current_engine = name
        self._engine_mgr.set_current_engine(name)
        self._config_mgr.set("last_engine", name); self._config_mgr.save_settings()
        eng = self._engine_mgr.get_current_engine()
        avail = eng.is_available() if eng else False
        self._engine_label.setText(f"  |  引擎: {name} {'✅' if avail else '⚠'}")
        self._sync_region_defaults()

    def _on_menu_engine_config(self):
        name = self._current_engine
        cfg = self._get_engine_config(name)
        is_local = self._is_local_engine(name)
        dlg = EngineConfigDialog(name, cfg, is_local, engine_manager=self._engine_mgr, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            new_cfg = dlg.get_config()
            engs = self._engine_mgr._config.get("engines", {})
            if name in engs:
                ec = engs[name].get("config", {})
                ec.update(new_cfg)
                self._engine_mgr._engines.pop(name, None)
            self._status_label.setText(f"✅ 引擎 [{name}] 配置已更新")

    def _on_menu_check_engine(self):
        name = self._current_engine
        eng = self._engine_mgr.get_engine(name)
        if not eng:
            self._status_label.setText(f"❌ 未找到引擎 [{name}]")
            return
        self._status_label.setText(f"正在检测 [{name}] 可用性...")
        QApplication.processEvents()
        available = eng.check_availability()
        if available:
            self._status_label.setText(f"✅ [{name}] 可用，正在获取模型列表...")
            QApplication.processEvents()
            models = eng.get_model_list()
            if models:
                self._status_label.setText(f"✅ [{name}] 可用 | {len(models)} 个模型可用")
            else:
                self._status_label.setText(f"✅ [{name}] 可用（未获取到模型列表）")
        else:
            self._status_label.setText(f"❌ [{name}] 不可用，请检查 Base URL")
        eng2 = self._engine_mgr.get_current_engine()
        avail2 = eng2.is_available() if eng2 else False
        self._engine_label.setText(f"  |  引擎: {name} {'✅' if avail2 else '⚠'}")

    # ── 菜单事件：纠错 ──
    def _on_menu_correction_config(self):
        cfg = load_correction_config()
        cfg.setdefault("enabled", self._corrector.enabled)
        cfg.setdefault("api_key", "")
        cfg.setdefault("base_url", "http://127.0.0.1:8080")
        cfg.setdefault("model", "")
        cfg.setdefault("timeout", 30)
        cfg.setdefault("retry_on_failure", 2)
        cfg.setdefault("correction_prompt", "")

        dlg = CorrectionConfigDialog(cfg, self)
        if dlg.exec_() == QDialog.Accepted:
            new_cfg = dlg.get_config()
            preset_name = self._config_panel._corr_preset_combo.currentText() if hasattr(self._config_panel, '_corr_preset_combo') else ""
            self._corrector = AICorrector(new_cfg, engine_manager=self._engine_mgr, preset_name=preset_name)

            # 保存到文件
            corr_cfg = load_correction_config()
            corr_cfg.update(new_cfg)
            config_path = BASE_DIR / "config" / "ai_correction.json"
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(corr_cfg, f, ensure_ascii=False, indent=2)
            self._status_label.setText("✅ AI 纠错配置已更新")

    def _on_menu_preset_manage(self):
        """打开 API 预设管理对话框。"""
        dlg = PresetManageDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            pass
        # 无论是否确认都刷新预设下拉框
        from core.api_preset_manager import APIPresetManager
        names = APIPresetManager().get_names()
        if hasattr(self._config_panel, '_corr_preset_combo'):
            self._config_panel._corr_preset_combo.blockSignals(True)
            cur = self._config_panel._corr_preset_combo.currentText()
            self._config_panel._corr_preset_combo.clear()
            self._config_panel._corr_preset_combo.addItems(names)
            if cur in names:
                self._config_panel._corr_preset_combo.setCurrentText(cur)
            self._config_panel._corr_preset_combo.blockSignals(False)
        self._status_label.setText("✅ API 预设已更新")

    # ── 菜单事件：模板 ──
    def _on_menu_template_selected(self, name: str):
        self._current_template = name
        t = self._prompt_mgr.get_template_by_name(name)
        if t:
            self._config_panel.set_template_prompt(t.get("prompt", ""))
        self._config_panel.select_template(name)
        self._sync_region_defaults()

    # ── ConfigPanel 模板信号 ──
    def _on_config_template_selected(self, name: str):
        """配置面板选中模板 → 加载提示词 + 同步菜单。"""
        t = self._prompt_mgr.get_template_by_name(name)
        if t:
            self._config_panel.set_template_prompt(t.get("prompt", ""))
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
            self._config_panel.set_filter_keywords(self._filter_mgr.get_keywords())
            self._status_label.setText(f"✅ 已添加过滤器: {keyword}")

    def _on_filter_remove(self, keyword: str):
        if self._filter_mgr.remove_keyword(keyword):
            self._config_panel.set_filter_keywords(self._filter_mgr.get_keywords())
            self._status_label.setText(f"🗑 已移除过滤器: {keyword}")

    def _on_result_filter(self, raw_text: str):
        """表格行'加入过滤器'按钮 → 将整条 raw 文本加入过滤。"""
        if self._filter_mgr.add_keyword(raw_text):
            self._config_panel.set_filter_keywords(self._filter_mgr.get_keywords())
            self._status_label.setText(f"✅ 已添加过滤器: {raw_text[:40]}")

    def _load_correction_config_to_ui(self):
        # 仅用于初始化纠错引擎名称同步到菜单状态
        pass

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
        from PyQt5.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        self._update_batch_label()

    def _on_batch_clear(self):
        self._batch_files.clear()
        self._update_batch_label()
        # 清空预览区渲染帧，回到占位提示
        self._video_preview._display_pixmap = None
        self._video_preview._current_frame = None
        self._video_preview._video_path = None
        self._video_preview._is_image = False
        if self._video_preview._ffmpeg:
            try:
                self._video_preview._ffmpeg.close()
            except Exception:
                pass
            self._video_preview._ffmpeg = None
        self._video_preview._label._placeholder_text = "拖放视频文件到此处\n或点击「打开视频/图片」加载文件"
        self._video_preview._label.update()
        self._status_label.setText("已清空队列和预览")

    def _update_batch_label(self):
        n = len(self._batch_files)
        if n == 0:
            self._batch_count_label.setText("(空)")
        else:
            self._batch_count_label.setText(f"{n} 个文件")

    def _on_batch_progress_file(self, fname: str, idx: int, total: int):
        self._progress_bar.setValue(int(idx * 100 / total))
        self._status_label.setText(f"批量处理 [{idx}/{total}]: {fname}")

    def _on_batch_finished_one(self, file_path: str, results: list):
        self._status_label.setText(f"✅ 完成: {Path(file_path).name} ({len(results)} 条)")

    def _on_batch_finished_all(self, _=None):
        self._btn_correction.setEnabled(True)
        self._progress_bar.setValue(0)
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

    def _on_frame_captured(self, _):
        self._status_label.setText("测试帧已截取，在预览图上拖拽绘制矩形区域")

    def _on_extract_env(self):
        """手动提取全文环境并回填到总结提示词栏。"""
        results = self._result_table.get_results()
        if not results:
            QMessageBox.warning(self, "提示", "暂无识别结果可提取环境。")
            return
        self._status_label.setText("⏳ 正在提取全文环境...")
        QApplication.processEvents()
        all_texts = [r.get("raw", "") for r in results if r.get("raw", "").strip()]
        if not all_texts:
            self._status_label.setText("⚠ 无有效文本可提取环境")
            return
        env = self._corrector.extract_environment(all_texts)
        if env:
            # 回填到总结提示词栏
            if hasattr(self._config_panel, '_corr_summary_prompt_text'):
                self._config_panel._corr_summary_prompt_text.setPlainText(env)
            self._status_label.setText(f"✅ 全文环境已提取并回填到总结提示词栏")
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

    def _on_config_panel_collapsed(self):
        """配置面板折叠/展开时，自动调整上半部分 splitter 让区域管理器填满。"""
        collapsed = self._config_panel._collapsed
        if collapsed:
            # 将配置面板所占空间全部分配给区域管理器
            sizes = self._right_top_splitter.sizes()
            total = sum(sizes)
            self._right_top_splitter.setSizes([total, 0])
        else:
            # 恢复默认比例
            self._right_top_splitter.setSizes([300, 200])

    def _on_delete_filtered_results(self):
        """删除所有匹配过滤器关键词的结果行。"""
        results = self._result_table.get_results()
        if not results:
            return
        keywords = self._filter_mgr.get_keywords()
        if not keywords:
            QMessageBox.information(self, "提示", "请先在「后处理」标签页中添加需要过滤的关键词。")
            return
        # 从后向前遍历以避免索引问题
        deleted = 0
        for row in range(len(results) - 1, -1, -1):
            raw = results[row].get("raw", "")
            corrected = results[row].get("corrected", "")
            text = (raw + " " + corrected).lower()
            if any(kw.lower() in text for kw in keywords):
                self._result_table._table.removeRow(row)
                del results[row]
                deleted += 1
        if deleted:
            self._result_table._results = results
            self._result_table._update_count()
            self._status_label.setText(f"🗑 已删除 {deleted} 条包含关键词的结果")
        else:
            self._status_label.setText("⚠ 无匹配关键词的结果")

    def _on_result_cell_edit(self, row: int):
        """点击/编辑表格行后跳转到对应时间并渲染帧。"""
        results = self._result_table.get_results()
        if row < 0 or row >= len(results):
            return
        r = results[row]
        ts = r.get("time_sec", 0.0) or 0.0
        vp = self._video_preview
        # 图片禁止跳转
        if vp.is_image:
            return
        # 音频跳到开始处
        if self._is_audio_file():
            ts = 0.0
        # 停止播放
        if getattr(vp, '_is_playing', False):
            vp._stop_player()
            vp._is_playing = False
            vp._btn_play.setText("▶")
            vp._play_timer.stop()
        # 跳转并渲染帧
        vp.seek_to(ts)
        self._status_label.setText(f"已跳转到 {r.get('time', '--:--')}")

    def _on_prompt_changed(self, p):
        self._custom_prompt = p
        self._sync_region_defaults()
    def _on_mode_changed(self, p):
        old_params = getattr(self, '_last_mode_params', {})
        self._mode_params = p
        # 同步 ASR 配置到 asr_engines.json
        if any(k.startswith("asr_") for k in p):
            self._sync_asr_config(p)
        # 同步 AI 纠错配置到 ai_correction.json
        if any(k.startswith("corr_") for k in p):
            self._sync_correction_config(p)
        # 仅当预设名确实变化时才切换（避免每次 UI 操作都打印）
        if "corr_preset" in p and p["corr_preset"] != old_params.get("corr_preset", ""):
            self._corrector.apply_preset(p["corr_preset"])
        self._last_mode_params = dict(p)
        self._save_mode_params()  # 🔥 每次参数变化自动持久化

    def _sync_asr_config(self, params: dict):
        """将 UI 中的 ASR 参数同步写入 asr_engines.json。"""
        config_path = BASE_DIR / "config" / "asr_engines.json"
        cfg = self._asr_mgr._config
        cfg["enabled"] = params.get("asr_enabled", cfg.get("enabled", False))
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
        self._asr_mgr.reload_config()
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _sync_correction_config(self, params: dict):
        """将 UI 中的纠错参数同步写入 ai_correction.json。"""
        config_path = BASE_DIR / "config" / "ai_correction.json"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
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
            cfg.setdefault("prompts", {})["default"] = params["corr_prompt"]
        if "corr_summary_prompt" in params and params["corr_summary_prompt"]:
            cfg["summary_prompt"] = params["corr_summary_prompt"]
        if "corr_system_prompt" in params and params["corr_system_prompt"]:
            cfg["correction_system_prompt"] = params["corr_system_prompt"]
        if "corr_output_format" in params and params["corr_output_format"]:
            cfg["output_format"] = params["corr_output_format"]
        if "corr_stream" in params:
            cfg["stream_mode"] = params["corr_stream"]
        if "corr_json" in params:
            cfg["json_mode"] = params["corr_json"]
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self._corrector.reload_config()

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
        """加载纯音频文件。"""
        self._video_preview._video_path = path
        self._video_preview._is_image = False
        self._video_preview._current_frame = None
        self._video_preview._display_pixmap = None
        self._video_preview._label.update()
        self._video_preview._time_range_widget.hide()
        self._video_preview._play_bar_widget.hide()
        self._video_preview.video_loaded.emit(path)
        self._status_label.setText(f"已加载音频: {Path(path).name}（仅支持 ASR 和纠错）")
        self._video_preview._label._placeholder_text = "🎵 已加载音频文件\n仅支持语音识别和纠错"
        self._video_preview._label.update()
        self._video_preview.show_play_controls()

    # ── 统一处理入口（单文件 / 批量）──
    def _on_start_processing(self):
        if self._batch_files:
            # 批量处理模式
            first_file = self._batch_files[0]
            ext = Path(first_file).suffix.lower()
            if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
                self._video_preview.load_video(first_file)
            elif ext in ('.png', '.jpg', '.jpeg', '.bmp'):
                self._video_preview.load_image(first_file)
            from PyQt5.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            # 自动创建区域
            regions = [r for r in self._video_preview.regions if r.get("enabled", True)]
            if not regions:
                pix = self._video_preview._display_pixmap
                if pix and not pix.isNull():
                    w, h = pix.width(), pix.height()
                    self._video_preview.add_region(0, 0, w, h, "全帧")
                    regions = [self._video_preview.regions[-1]]
                else:
                    QMessageBox.warning(self, "提示", "无法获取视频帧，请先打开一个视频文件。")
                    return
            self._video_preview.regions = regions
            self._region_manager.regions = regions
            self._progress_bar.setValue(0)
            self._workflow.start_batch()
        else:
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
        wf._get_polished_results = lambda sim, ml: self._result_table.get_polished_results(
            post_sim_threshold=sim, post_min_text_len=ml)
        wf._get_table_row_count = lambda: self._result_table._table.rowCount()

        # ── 信号连接 ──
        wf.status_msg.connect(lambda m: self._status_label.setText(m))
        wf.progress_val.connect(lambda v: self._progress_bar.setValue(v))
        wf.time_display.connect(lambda t: self._time_label.setText(t))
        wf.buttons_enabled.connect(self._on_workflow_buttons)
        wf.error_dialog.connect(lambda t, m: QMessageBox.critical(self, t, m))
        wf.info_dialog.connect(lambda t, m: QMessageBox.information(self, t, m))
        wf.result_row.connect(self._on_process_result)
        wf.correction_updated.connect(self._on_correction_ready)
        wf.correction_stream_updated.connect(self._on_correction_stream)
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
        if "pause" in states:
            self._btn_pause.setEnabled(states["pause"])

    def _on_correction_selected(self):
        """对选中的表格行进行 AI 纠错（委托 WorkflowManager）。"""
        table = self._result_table._table
        selected_rows = set()
        for item in table.selectedItems():
            selected_rows.add(item.row())
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
        self._batch_correction_worker = None

    def _on_batch_correction_error(self, err):
        """批量纠错出错。"""
        self._btn_correction_all.setEnabled(True)
        self._btn_correction.setEnabled(True)
        self._status_label.setText(f"⚠ 批量纠错出错: {err}")
        self._batch_correction_worker = None

    def _on_process_result(self, ts, t_str, rname, ename, raw, conf: float = 0.0, end_sec: float = 0.0):
        # 过滤器：包含任一关键词则跳过
        if self._filter_mgr.matches(raw):
            self._filtered_count += 1
            return
        row = self._result_table.add_result(time_str=t_str, region=rname, engine=ename,
                                            raw_text=raw, time_sec=ts, confidence=conf, end_sec=end_sec)
        self._all_raw_results.append((ts, t_str, rname, ename, raw, conf, end_sec))

    def _on_correction_ready(self, row, raw, corrected):
        self._result_table.update_correction(row, corrected)
        self._correction_results[row] = corrected
        self._correction_pending.discard(row)

    def _on_correction_failed(self, row, _): self._correction_pending.discard(row)

    def _on_correction_stream(self, row, partial_text):
        """流式输出模式：实时更新表格中的纠错文本。"""
        self._result_table.update_correction(row, partial_text)

    def _on_process_finished(self, _):
        """OCR 处理完成回调（UI 级后处理 + 触发 WorkflowManager 流程结束）。"""
        self._btn_start.setEnabled(True); self._btn_stop.setEnabled(False)
        self._btn_correction.setEnabled(True); self._btn_correction_all.setEnabled(True)
        self._progress_bar.setValue(0)
        # 按时间戳升序排序（所有模式默认执行）
        if not self._video_preview.is_image:
            self._result_table.sort_by_time()
        # 区域顺序模板排序（可选叠加）
        region_order = self._mode_params.get("region_order", "")
        if not self._video_preview.is_image and region_order:
            self._result_table.sort_by_order(region_order)
        # ── 回填 end_sec + 置信度过滤 ──
        self._recalculate_end_seconds()
        n = self._result_table._table.rowCount()
        msg = f"✅ 处理完成: {n} 条结果"
        if self._filtered_count > 0:
            msg += f" | 过滤: {self._filtered_count} 条"
        self._filtered_count = 0
        self._status_label.setText(msg)
        # 通知 WorkflowManager 执行后处理（全量纠错等）
        self._workflow._on_process_finished(_)

    def _recalculate_end_seconds(self):
        """填充 OCR 去重结果的 end_sec；若置信度过滤启用则删除低置信度 PaddleOCR 行。"""
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

    def _on_process_error(self, err):
        self._btn_start.setEnabled(True); self._btn_stop.setEnabled(False); self._btn_pause.setEnabled(False)
        self._btn_pause.setText("⏸ 暂停")
        self._btn_correction.setEnabled(True); self._btn_correction_all.setEnabled(True)
        self._progress_bar.setValue(0)
        self._status_label.setText(f"❌ 处理失败: {err}")

    # ── 导出 ──
    def _on_export(self, fmt, path):
        # SRT 导出需要 subtitle_duration
        if fmt == "srt":
            sub_dur = self._mode_params.get("subtitle_duration", 3.0)
            results = self._result_table.get_results()
            polished = []
            for i, r in enumerate(results):
                ts = r.get("time_sec", 0.0) or 0.0
                end = r.get("end_sec", ts + sub_dur) or (ts + sub_dur)
                polished.append({
                    "time_sec": ts,
                    "end_sec": end,
                    "time": r.get("time", "--:--"),
                    "region": r.get("region", ""),
                    "engine": r.get("engine", ""),
                    "speaker": "NONE",
                    "content": r.get("raw", ""),
                    "raw": r.get("raw", ""),
                })
            cmap = {i: v for i, v in self._correction_results.items() if i < len(polished)}
        else:
            polished = self._result_table.get_polished_results(
                post_sim_threshold=self._mode_params.get("post_sim_threshold", 0.9),
                post_min_text_len=self._mode_params.get("post_min_text_len", 2))
            if not polished:
                return QMessageBox.information(self, "提示", "过滤后无有效结果可导出。")
            cmap = {}
            if self._correction_results:
                for pi in range(min(len(polished), len(self._result_table.get_results()))):
                    if pi in self._correction_results:
                        cmap[pi] = self._correction_results[pi]
        try:
            if not polished:
                QMessageBox.information(self, "提示", "无有效结果可导出。")
                return
            export_results(polished, path, fmt, bool(cmap), cmap,
                           keep_original=self._mode_params.get("export_keep_original", False))
            self._status_label.setText(f"✅ 已导出: {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
