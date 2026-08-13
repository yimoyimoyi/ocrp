"""右侧面板构建器（区域参数/字幕设置/ASR/后处理折叠组）。

方法体从 main_window.py 原样搬移（AST 提取，零改动）。
通过 _ViewBase 与 MainWindow 双向委托共享状态。
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.i18n import _
from core.utils import MODE_OCR_ONLY
from ui.collapsible_group import CollapsibleGroup
from ui.region_manager import RegionManagerWidget
from ui.views.base import _ViewBase

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class RightPanelView(_ViewBase):
    """右侧面板构建器（区域参数/字幕设置/ASR/后处理折叠组）。"""

    def _sync_right_panel_from_config(self):
        """从 ConfigPanel 同步右侧面板控件状态。"""
        cp = self._config_panel
        mp = cp.get_mode_params()

        # 帧间隔
        self._frame_interval_r.blockSignals(True)
        self._frame_interval_r.setValue(mp.get("frame_interval", 0.1))
        self._frame_interval_r.blockSignals(False)

        # R12（P1-2）：后处理组已删除（与设置页 tab3 完全重复，主开关在快速工具栏）
        # ASR 组可见性：仅 OCR 模式时隐藏
        self._asr_group.setVisible(MODE_OCR_ONLY not in mp.get("process_mode", ""))

    def _restore_right_panel_params(self, saved: dict):
        """恢复右侧面板控件的值。"""
        # 帧间隔
        frame_interval = saved.get("frame_interval", 0.1)
        self._frame_interval_r.blockSignals(True)
        self._frame_interval_r.setValue(frame_interval)
        self._frame_interval_r.blockSignals(False)

        # R12（P1-2）：后处理组已删除
        # ASR 组可见性：仅 OCR 模式时隐藏
        self._asr_group.setVisible(MODE_OCR_ONLY not in saved.get("process_mode", ""))

    def _apply_right_panel_mode(self):
        """根据当前文件类型调整右侧面板可见内容。"""
        is_image = self._video_preview.is_image
        is_audio = getattr(self._video_preview, "_is_audio", False)

        if is_audio:
            self._region_group.hide()
            self._subtitle_group.show()
            self._asr_group.show()
            self._sync_asr_from_config()
        elif is_image:
            self._region_group.show()
            self._subtitle_group.hide()
            self._asr_group.hide()
        else:
            self._region_group.show()
            self._subtitle_group.show()
            self._asr_group.show()
            self._sync_asr_from_config()

    def _populate_asr_model_combo(self, combo: QComboBox):
        """填充 ASR 模型 combo：本地已下载 + 标准模型大小（O12 共享构建）。"""
        from core.asr_engine import build_asr_model_items, scan_local_asr_models

        model_dir = str(BASE_DIR / "models" / "asr")
        local_models = scan_local_asr_models(model_dir)
        combo.blockSignals(True)
        combo.clear()
        for display, data in build_asr_model_items(local_models):
            combo.addItem(display, data)
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

    def _sync_engine_combo_r(self):
        """同步引擎下拉列表（由 MainWindow._refresh_engine_list 调用）。"""
        names = self._engine_mgr.get_engine_names()
        cur = self._current_engine
        self._engine_combo_r.blockSignals(True)
        self._engine_combo_r.clear()
        self._engine_combo_r.addItems(names)
        if cur in names:
            self._engine_combo_r.setCurrentText(cur)
        self._engine_combo_r.blockSignals(False)

    def _on_engine_r_changed(self, name: str):
        """右侧引擎切换 → 更新当前引擎并路由重建（R12 P0-③；v3 收编 RebuildRouter）。"""
        if not name or name == self._current_engine:
            return
        self._current_engine = name
        self._engine_mgr.set_current_engine(name)
        self._config_mgr.set("last_engine", name)
        self._router.notify("ocr", "engine")
        self._sync_region_defaults()

    def _on_asr_r_changed(self):
        """右侧 ASR 控件变更 → 同步到 ConfigPanel。

        模型切换走域对象 apply_model_selection + 路由重建（asr_model_path 是
        UI 状态键，不经域对象 changed，需显式通知）；语言/区域名经域对象
        setter 触发 changed → router 路由。
        """
        cp = self._config_panel
        # 同步模型选择
        model_data = self._asr_model_combo_r.currentData()
        if model_data and model_data != cp.asr_model:
            cp.asr_model = model_data
            # 推导域对象 model_size/model_dir 并路由 ASR 重建（防抖合并）
            self._registry.asr.apply_model_selection(model_data)
            self._router.notify("asr", "model_size")
        # 同步语言和区域名
        cp.asr_language = self._asr_lang_combo_r.currentText()
        cp.asr_region_name = self._asr_region_edit_r.text()
        self._on_mode_changed(cp.get_mode_params())

    def _on_frame_interval_r_changed(self, value: float):
        """右侧帧间隔变更 → 同步到 ConfigPanel。

        apply_mode_params 内部已 emit mode_changed → _on_mode_changed，
        无需手动补调（设置同步 P8 冗余清理）。
        """
        params = self._config_panel.get_mode_params()
        params["frame_interval"] = value
        self._config_panel.apply_mode_params(params)

    def build(self):
        """构建右侧面板（原 build_ui 内联代码搬移）。"""
        # 右：区域参数 + ASR 选项（可折叠）
        self._right_panel = QFrame()
        self._right_panel.setObjectName("rightPanel")
        self._right_panel.setFrameShape(QFrame.NoFrame)
        self._right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        rl = QVBoxLayout(self._right_panel)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # 整体滚动区域
        self._right_scroll = QScrollArea()
        self._right_scroll.setWidgetResizable(True)
        self._right_scroll.setFrameShape(QFrame.NoFrame)
        self._right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scl = QVBoxLayout(scroll_content)
        scl.setContentsMargins(6, 6, 6, 6)
        scl.setSpacing(4)

        # ── OCR 引擎快速切换（R12 P0-③：最高频操作从设置对话框 4-5 步收敛到 1 步）──
        self._engine_bar = QFrame()
        self._engine_bar.setObjectName("tplBar")
        eng_bl = QHBoxLayout(self._engine_bar)
        eng_bl.setContentsMargins(4, 4, 4, 4)
        eng_bl.setSpacing(4)
        eng_bl.addWidget(QLabel(_("引擎:")))
        self._engine_combo_r = QComboBox()
        self._engine_combo_r.currentTextChanged.connect(self._on_engine_r_changed)
        eng_bl.addWidget(self._engine_combo_r, 1)
        scl.addWidget(self._engine_bar)

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
        tpl_bl.setContentsMargins(4, 4, 4, 4)
        tpl_bl.setSpacing(4)
        tpl_bl.addWidget(QLabel(_("模板:")))
        self._template_combo = QComboBox()
        self._template_combo.currentTextChanged.connect(self._on_template_quick_selected)
        tpl_bl.addWidget(self._template_combo, 1)

        # 区域参数折叠组
        self._region_group = CollapsibleGroup(_("📐 区域参数"))
        self._region_group.addWidget(self._region_manager)
        self._region_group.content_layout().addWidget(self._tpl_bar)
        scl.addWidget(self._region_group)

        # ── 字幕设置折叠组（字幕模式已在快速工具栏唯一入口，4b.7 收敛）──
        self._subtitle_group = CollapsibleGroup(_("📝 字幕设置"))
        subtitle_form = QWidget()
        subtitle_layout = QFormLayout(subtitle_form)
        subtitle_layout.setSpacing(6)

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

        # R12（P1-2）：后处理折叠组已删除（与设置页 tab3 完全重复，
        # 主开关已在快速工具栏唯一入口；阈值项收敛到设置页）

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
        return self._right_panel
