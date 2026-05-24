"""配置面板 —— 处理参数 / 哨兵参数 / 提示词模板 / 过滤器"""
import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.i18n import _


class ConfigPanel(QWidget):
    prompt_changed = pyqtSignal(str)
    mode_changed = pyqtSignal(dict)
    hw_accel_changed = pyqtSignal(bool)
    template_created = pyqtSignal(str)
    template_saved = pyqtSignal(str, str)
    template_deleted = pyqtSignal(str)
    filter_add_requested = pyqtSignal(str)
    filter_remove_requested = pyqtSignal(str)
    extract_env_clicked = pyqtSignal()  # 提取全文环境按钮
    collapse_requested = pyqtSignal()   # 折叠/展开配置面板

    def __init__(self, parent=None):
        super().__init__(parent)
        self._template_names: list[str] = ["通用OCR"]
        self._region_names: list[str] = []
        self._collapsed = False
        self._init_ui()

    @property
    def prompt_text(self): return self._prompt_edit.toPlainText()

    def get_mode_params(self) -> dict:
        """收集所有模式参数，返回字典。"""
        # 收集排序规则行 → "前缀：区域名：后缀" 每行格式
        order_lines = []
        for i in range(self._sort_list.count()):
            item = self._sort_list.item(i)
            widget = self._sort_list.itemWidget(item)
            if widget is None:
                continue
            children = widget.findChildren((QLineEdit, QLabel))
            if len(children) >= 2:
                prefix = children[0].text().strip() if isinstance(children[0], QLineEdit) else ""
                rname = children[1].text() if isinstance(children[1], QLabel) else ""
                suffix = children[2].text().strip() if len(children) > 2 and isinstance(children[2], QLineEdit) else ""
                if not rname:
                    continue
                line = rname
                if prefix:
                    line = f"{prefix}：{line}"
                if suffix:
                    line = f"{line}：{suffix}"
                order_lines.append(line)
        order_text = "\n".join(order_lines)

        from ui.widget_helpers import safe_read_widget

        def _w(attr, default=None):
            return safe_read_widget(getattr(self, attr, None), default)

        return {
            "frame_interval": self._frame_interval_spin.value(),
            "process_mode": self._process_mode_combo.currentText(),
            "sentinel_enabled": self._s_sentinel_check.isChecked(),
            "subtitle_mode": self._subtitle_mode_combo.currentText(),
            # ── 流式参数 ──
            "s_drop_ratio": self._s_drop_ratio_spin.value(),
            "s_buffer_size": self._s_buffer_spin.value(),
            "s_sim_threshold": self._s_sim_spin.value(),
            "s_min_text_len": self._s_min_text_spin.value(),
            "s_filter_keywords": _w("_s_filter_edit", ""),
            "s_ocr_version": self._s_ocr_version_combo.currentText(),
            # ── 常规参数 ──
            "r_dedup": self._r_dedup_check.isChecked(),
            "r_sim_threshold": self._r_sim_spin.value(),
            "r_buffer_size": self._r_buffer_spin.value(),
            "r_min_text_len": self._r_min_text_spin.value(),
            "r_filter_keywords": _w("_r_filter_edit", ""),
            "r_interval": self._r_interval_spin.value(),
            "subtitle_duration": self._subtitle_duration_spin.value(),
            "region_order": order_text,
            "srt_export_mode": _w("_srt_export_combo", "仅纠正结果"),
            "post_keep_longest": self._post_keep_longest.isChecked(),
            "post_sim_dedup": self._post_sim_dedup.isChecked(),
            "post_conf_enabled": self._post_conf_check.isChecked(),
            "post_conf_threshold": self._post_conf_threshold.value(),
            "post_sim_threshold": self._post_sim_threshold.value(),
            "post_min_text_len": self._post_min_text_len.value(),
            # ── OCR 重试参数 ──
            "ocr_retry": _w("_ocr_retry_spin", 2),
            "ocr_timeout": _w("_ocr_timeout_spin", 60),
            # ── AI 纠错参数 ──
            "corr_enabled": _w("_corr_enabled_check", False),
            "corr_batch_size": _w("_corr_batch_spin", 5),
            "corr_context_window": _w("_corr_context_spin", 3),
            "corr_retry": _w("_corr_retry_spin", 2),
            "corr_prompt": _w("_corr_prompt_text", ""),
            "corr_extract_env": _w("_corr_extract_env_check", False),
            "corr_summary_prompt": _w("_corr_summary_prompt_text", ""),
            "corr_system_prompt": _w("_corr_system_prompt_text", ""),
            "corr_output_format": _w("_corr_output_format_edit", ""),
            "corr_translate": _w("_corr_translate_check", False),
            "corr_stream": _w("_corr_stream_check", False),
            "corr_json": _w("_corr_json_check", False),
            "corr_preset": _w("_corr_preset_combo", ""),
            # ── ASR 参数 ──
            "asr_enabled": _w("_asr_enabled_check", False),
            "asr_model_size": _w("_asr_model_combo", "large-v3"),
            "asr_model_path": self._asr_model_combo.currentData() or "",
            "asr_language": _w("_asr_lang_combo", "zh"),
            "asr_vad": _w("_asr_vad_check", False),
            "asr_word_ts": _w("_asr_word_ts_check", True),
            "asr_region_name": _w("_asr_region_edit", "语音"),
            "asr_beam_size": _w("_asr_beam_spin", 5),
            "asr_initial_prompt": _w("_asr_prompt_edit", ""),
            "asr_condition_prev": _w("_asr_condition_check", True),
            "asr_no_speech_thresh": _w("_asr_no_speech_spin", 0.6),
            "asr_comp_ratio_thresh": _w("_asr_comp_ratio_spin", 2.4),
            "asr_temperature": _w("_asr_temp_edit", "0.0,0.2,0.4,0.6,0.8,1.0"),
            "asr_hotwords": _w("_asr_hotwords_edit", ""),
            "asr_vad_min_silence": _w("_asr_vad_silence_spin", 500),
            "asr_vad_threshold": _w("_asr_vad_thresh_spin", 0.5),
        }

    def apply_mode_params(self, params: dict):
        """将保存的参数回填到各 UI 控件。"""
        self.blockSignals(True)

        # ── Tab 1: 处理参数 ──
        if "frame_interval" in params:
            self._frame_interval_spin.setValue(params["frame_interval"])
        if "process_mode" in params:
            self._process_mode_combo.setCurrentText(params["process_mode"])
        if "sentinel_enabled" in params:
            self._s_sentinel_check.setChecked(params["sentinel_enabled"])
        if "subtitle_mode" in params:
            self._subtitle_mode_combo.setCurrentText(params["subtitle_mode"])
        if "post_sim_dedup" in params:
            self._post_sim_dedup.setChecked(params["post_sim_dedup"])
        if "post_keep_longest" in params:
            self._post_keep_longest.setChecked(params["post_keep_longest"])
        if "corr_enabled" in params:
            self._corr_enabled_check.setChecked(params["corr_enabled"])
        if "subtitle_duration" in params:
            self._subtitle_duration_spin.setValue(params["subtitle_duration"])
        if "srt_export_mode" in params:
            self._srt_export_combo.setCurrentText(params["srt_export_mode"])
        if "ocr_retry" in params:
            self._ocr_retry_spin.setValue(params["ocr_retry"])
        if "ocr_timeout" in params:
            self._ocr_timeout_spin.setValue(params["ocr_timeout"])

        # ── Tab 2: 字幕设置 ──
        if "s_drop_ratio" in params:
            self._s_drop_ratio_spin.setValue(params["s_drop_ratio"])
        if "s_buffer_size" in params:
            self._s_buffer_spin.setValue(params["s_buffer_size"])
        if "s_sim_threshold" in params:
            self._s_sim_spin.setValue(params["s_sim_threshold"])
        if "s_min_text_len" in params:
            self._s_min_text_spin.setValue(params["s_min_text_len"])
        if "s_filter_keywords" in params:
            self._s_filter_edit.setText(params["s_filter_keywords"])
        if "s_ocr_version" in params:
            self._s_ocr_version_combo.setCurrentText(params["s_ocr_version"])
        if "r_dedup" in params:
            self._r_dedup_check.setChecked(params["r_dedup"])
        if "r_sim_threshold" in params:
            self._r_sim_spin.setValue(params["r_sim_threshold"])
        if "r_buffer_size" in params:
            self._r_buffer_spin.setValue(params["r_buffer_size"])
        if "r_min_text_len" in params:
            self._r_min_text_spin.setValue(params["r_min_text_len"])
        if "r_filter_keywords" in params:
            self._r_filter_edit.setText(params["r_filter_keywords"])
        if "r_interval" in params:
            self._r_interval_spin.setValue(params["r_interval"])

        # ── Tab 3: 后处理 ──
        if "post_conf_enabled" in params:
            self._post_conf_check.setChecked(params["post_conf_enabled"])
        if "post_conf_threshold" in params:
            self._post_conf_threshold.setValue(params["post_conf_threshold"])
        if "post_sim_threshold" in params:
            self._post_sim_threshold.setValue(params["post_sim_threshold"])
        if "post_min_text_len" in params:
            self._post_min_text_len.setValue(params["post_min_text_len"])

        # ── Tab 5: AI 纠错 ──
        if "corr_batch_size" in params:
            self._corr_batch_spin.setValue(params["corr_batch_size"])
        if "corr_context_window" in params:
            self._corr_context_spin.setValue(params["corr_context_window"])
        if "corr_retry" in params:
            self._corr_retry_spin.setValue(params["corr_retry"])
        if "corr_prompt" in params:
            self._corr_prompt_text.setPlainText(params["corr_prompt"])
        if "corr_extract_env" in params:
            self._corr_extract_env_check.setChecked(params["corr_extract_env"])
        if "corr_summary_prompt" in params:
            self._corr_summary_prompt_text.setPlainText(params["corr_summary_prompt"])
        if "corr_system_prompt" in params:
            self._corr_system_prompt_text.setPlainText(params["corr_system_prompt"])
        if "corr_output_format" in params:
            self._corr_output_format_edit.setText(params["corr_output_format"])
        if "corr_translate" in params:
            self._corr_translate_check.setChecked(params["corr_translate"])
        if "corr_stream" in params:
            self._corr_stream_check.setChecked(params["corr_stream"])
        if "corr_json" in params:
            self._corr_json_check.setChecked(params["corr_json"])
        if "corr_preset" in params:
            self._corr_preset_combo.setCurrentText(params["corr_preset"])

        # ── Tab 6: 语音识别 ──
        if "asr_model_size" in params:
            idx = self._asr_model_combo.findText(params["asr_model_size"])
            if idx >= 0:
                self._asr_model_combo.setCurrentIndex(idx)
        if "asr_language" in params:
            idx = self._asr_lang_combo.findText(params["asr_language"])
            if idx >= 0:
                self._asr_lang_combo.setCurrentIndex(idx)
        if "asr_vad" in params:
            self._asr_vad_check.setChecked(params["asr_vad"])
        if "asr_word_ts" in params:
            self._asr_word_ts_check.setChecked(params["asr_word_ts"])
        if "asr_region_name" in params:
            self._asr_region_edit.setText(params["asr_region_name"])
        if "asr_beam_size" in params:
            self._asr_beam_spin.setValue(params["asr_beam_size"])
        if "asr_initial_prompt" in params:
            self._asr_prompt_edit.setText(params["asr_initial_prompt"])
        if "asr_condition_prev" in params:
            self._asr_condition_check.setChecked(params["asr_condition_prev"])
        if "asr_no_speech_thresh" in params:
            self._asr_no_speech_spin.setValue(params["asr_no_speech_thresh"])
        if "asr_comp_ratio_thresh" in params:
            self._asr_comp_ratio_spin.setValue(params["asr_comp_ratio_thresh"])
        if "asr_temperature" in params:
            self._asr_temp_edit.setText(params["asr_temperature"])
        if "asr_hotwords" in params:
            self._asr_hotwords_edit.setText(params["asr_hotwords"])
        if "asr_vad_min_silence" in params:
            self._asr_vad_silence_spin.setValue(params["asr_vad_min_silence"])
        if "asr_vad_threshold" in params:
            self._asr_vad_thresh_spin.setValue(params["asr_vad_threshold"])

        self.blockSignals(False)

    # ── UI ──
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._init_basic_tab()        # Tab 1: 处理参数
        self._init_sentinel_tab()     # Tab 2: 字幕设置
        self._init_postprocess_tab()  # Tab 3: 后处理
        self._init_sort_tab()         # Tab 4: 结果排序
        self._init_template_tab()     # Tab 5: 提示词模板
        self._init_asr_tab()          # Tab 6: 语音识别
        self._init_correction_tab()   # Tab 7: AI 纠错

        root.addWidget(self._tabs, 1)

    def _wrap_scroll(self, widget):
        """将 tab 内容包裹在 QScrollArea 中，防止内容溢出。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    def _add_tab_with_scroll(self, tab, name):
        """添加 tab 并包裹滚动区域。"""
        self._tabs.addTab(self._wrap_scroll(tab), name)

    # ── Tab 1: 处理参数 ──
    def _init_basic_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab); layout.setSpacing(4)

        # ── 帧间隔（OCR + 哨兵共享） ──
        self._frame_interval_spin = QDoubleSpinBox()
        self._frame_interval_spin.setRange(0.02, 10.0)
        self._frame_interval_spin.setSingleStep(0.1)
        self._frame_interval_spin.setDecimals(2)
        self._frame_interval_spin.setValue(0.1)
        self._frame_interval_spin.setSuffix(" 秒")
        self._frame_interval_spin.setToolTip("每隔多少秒处理一帧，OCR 和哨兵共用此值")
        layout.addRow("帧间隔:", self._frame_interval_spin)

        # ── 处理模式 ──
        self._process_mode_combo = QComboBox()
        self._process_mode_combo.addItems([
            "OCR + ASR（完整流程）",
            "仅 OCR",
            "仅语音识别 (ASR)",
        ])
        self._process_mode_combo.setToolTip("选择开始处理时运行的流程模式")
        layout.addRow("处理模式:", self._process_mode_combo)

        # ── 后处理开关 ──
        self._post_sim_dedup = QCheckBox(_("后处理相似度去重"))
        self._post_sim_dedup.setChecked(True)
        layout.addRow("", self._post_sim_dedup)

        self._post_keep_longest = QCheckBox(_("保留最长文本"))
        self._post_keep_longest.setChecked(False)
        layout.addRow("", self._post_keep_longest)

        # ── AI 纠错开关 ──
        self._corr_enabled_check = QCheckBox(_("启用 AI 纠错"))
        self._corr_enabled_check.setChecked(False)
        layout.addRow("", self._corr_enabled_check)

        self._subtitle_duration_spin = QDoubleSpinBox()
        self._subtitle_duration_spin.setRange(0.5, 30.0)
        self._subtitle_duration_spin.setSingleStep(0.5)
        self._subtitle_duration_spin.setValue(3.0)
        self._subtitle_duration_spin.setSuffix(" 秒")
        self._subtitle_duration_spin.setToolTip("OCR 字幕默认显示时长")
        layout.addRow("字幕时长:", self._subtitle_duration_spin)

        # ── SRT 导出模式 ──
        self._srt_export_combo = QComboBox()
        self._srt_export_combo.addItems(["仅纠正结果", "仅原文", "双语对照（原文+纠正）", "原文 换行 纠正"])
        self._srt_export_combo.setToolTip(
            "SRT 导出时的字幕内容模式：\n"
            "仅纠正结果 = AI 纠错后的文本\n"
            "仅原文 = 原始 OCR/ASR 文本\n"
            "双语对照 = 原文在上，纠正在下\n"
            "原文 换行 纠正 = 原文在上，换行后显示纠正文本")
        layout.addRow("SRT 导出:", self._srt_export_combo)

        # ── 失败重试参数 ──
        sep_retry = QLabel(_("── 流程失败重试 ──"))
        sep_retry.setObjectName("sectionSep")
        layout.addRow("", sep_retry)

        self._ocr_retry_spin = QSpinBox()
        self._ocr_retry_spin.setRange(0, 10)
        self._ocr_retry_spin.setValue(2)
        self._ocr_retry_spin.setToolTip("API OCR 引擎识别失败时的最大重试次数")
        layout.addRow("OCR 重试次数:", self._ocr_retry_spin)

        self._ocr_timeout_spin = QSpinBox()
        self._ocr_timeout_spin.setRange(5, 300)
        self._ocr_timeout_spin.setValue(60)
        self._ocr_timeout_spin.setSuffix(" 秒")
        self._ocr_timeout_spin.setToolTip("API OCR 引擎请求超时时间")
        layout.addRow("OCR 超时:", self._ocr_timeout_spin)

        btn = QPushButton(_("应用处理参数"))
        btn.clicked.connect(self._on_apply_mode)
        layout.addRow("", btn)
        self._add_tab_with_scroll(tab, _("处理参数"))

    # ── Tab 2: 字幕设置（流式 + 常规，独立配置） ──
    def _init_sentinel_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab); layout.setSpacing(4)

        # ── 字幕模式选择 ──
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(_("字幕模式:")))
        self._subtitle_mode_combo = QComboBox()
        self._subtitle_mode_combo.addItems(["流式字幕（去重）", "常规字幕（固定间隔）"])
        self._subtitle_mode_combo.setToolTip("流式：AI 去重后输出，适合对话流\n常规：按固定间隔输出每一帧，不丢字")
        self._subtitle_mode_combo.currentTextChanged.connect(self._on_subtitle_mode_changed)
        mode_row.addWidget(self._subtitle_mode_combo, 1)
        layout.addLayout(mode_row)

        # ── 流式参数组（包裹在 QWidget 中，整体 hide/show） ──
        self._s_group = QWidget()
        s_layout = QFormLayout(self._s_group); s_layout.setSpacing(4)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_sep = QLabel(_("── 流式参数（哨兵去重） ──"))
        s_sep.setObjectName("sectionSep")
        s_layout.addRow("", s_sep)

        self._s_sentinel_check = QCheckBox(_("启用哨兵去重（骤降/缓冲区/相似度）"))
        self._s_sentinel_check.setChecked(True)
        s_layout.addRow("", self._s_sentinel_check)

        self._s_drop_ratio_spin = QDoubleSpinBox()
        self._s_drop_ratio_spin.setRange(0.01, 1.0); self._s_drop_ratio_spin.setSingleStep(0.05)
        self._s_drop_ratio_spin.setDecimals(2); self._s_drop_ratio_spin.setValue(0.5)
        self._s_drop_ratio_spin.setToolTip("文本长度骤降到上一帧的此比例时强制触发输出")
        s_layout.addRow("字数骤降比:", self._s_drop_ratio_spin)

        self._s_buffer_spin = QSpinBox()
        self._s_buffer_spin.setRange(1, 100); self._s_buffer_spin.setValue(8)
        self._s_buffer_spin.setToolTip("连续相同文本的缓冲区大小，超过后强制输出")
        s_layout.addRow("连续缓冲区:", self._s_buffer_spin)

        self._s_sim_spin = QDoubleSpinBox()
        self._s_sim_spin.setRange(0.0, 1.0); self._s_sim_spin.setSingleStep(0.05)
        self._s_sim_spin.setDecimals(2); self._s_sim_spin.setValue(0.85)
        s_layout.addRow("相似度阈值:", self._s_sim_spin)

        self._s_min_text_spin = QSpinBox()
        self._s_min_text_spin.setRange(1, 100); self._s_min_text_spin.setValue(2)
        s_layout.addRow("最小文字长度:", self._s_min_text_spin)

        self._s_filter_edit = QLineEdit()
        self._s_filter_edit.setPlaceholderText("过滤关键词，逗号分隔（可选）")
        self._s_filter_edit.setToolTip("匹配关键词的结果将被过滤，不输出")
        s_layout.addRow("过滤关键词:", self._s_filter_edit)

        self._s_ocr_version_combo = QComboBox()
        self._s_ocr_version_combo.addItems([
            "跟随全局", "PP-OCRv4 (最快)", "PP-OCRv5_mobile (平衡)", "PP-OCRv5_server (高精度)"
        ])
        self._s_ocr_version_combo.setToolTip(
            "哨兵模式专用 OCR 模型版本\n"
            "跟随全局：使用引擎配置中的版本\n"
            "v4/快速：速度快 3-5x，适合实时字幕检测\n"
            "mobile/平衡：速度与精度均衡\n"
            "server/高精度：最慢但识别最准，适合离线批处理"
        )
        s_layout.addRow("哨兵 OCR 版本:", self._s_ocr_version_combo)
        layout.addWidget(self._s_group)

        # ── 常规参数组（包裹在 QWidget 中，整体 hide/show） ──
        self._r_group = QWidget()
        r_layout = QFormLayout(self._r_group); r_layout.setSpacing(4)
        r_layout.setContentsMargins(0, 0, 0, 0)
        r_sep = QLabel(_("── 常规参数（基本去重） ──"))
        r_sep.setObjectName("sectionSep")
        r_layout.addRow("", r_sep)

        self._r_dedup_check = QCheckBox(_("启用基本去重（相似文本合并）"))
        self._r_dedup_check.setChecked(True)
        r_layout.addRow("", self._r_dedup_check)

        self._r_sim_spin = QDoubleSpinBox()
        self._r_sim_spin.setRange(0.0, 1.0); self._r_sim_spin.setSingleStep(0.05)
        self._r_sim_spin.setDecimals(2); self._r_sim_spin.setValue(0.9)
        r_layout.addRow("相似度阈值:", self._r_sim_spin)

        self._r_buffer_spin = QSpinBox()
        self._r_buffer_spin.setRange(1, 100); self._r_buffer_spin.setValue(5)
        self._r_buffer_spin.setToolTip("连续相同文本的缓冲区大小，超过后强制输出")
        r_layout.addRow("连续缓冲区:", self._r_buffer_spin)

        self._r_min_text_spin = QSpinBox()
        self._r_min_text_spin.setRange(1, 100); self._r_min_text_spin.setValue(2)
        r_layout.addRow("最小文字长度:", self._r_min_text_spin)

        self._r_filter_edit = QLineEdit()
        self._r_filter_edit.setPlaceholderText("过滤关键词，逗号分隔（可选）")
        self._r_filter_edit.setToolTip("匹配关键词的结果将被过滤，不输出")
        r_layout.addRow("过滤关键词:", self._r_filter_edit)

        self._r_interval_spin = QDoubleSpinBox()
        self._r_interval_spin.setRange(0.1, 60.0); self._r_interval_spin.setSingleStep(0.5)
        self._r_interval_spin.setDecimals(1); self._r_interval_spin.setValue(2.0)
        self._r_interval_spin.setSuffix(" 秒")
        self._r_interval_spin.setToolTip("每隔多少秒输出一次当前帧的全部识别结果")
        r_layout.addRow("输出间隔:", self._r_interval_spin)
        layout.addWidget(self._r_group)

        btn = QPushButton(_("应用字幕参数"))
        btn.clicked.connect(self._on_apply_mode)
        layout.addWidget(btn)
        layout.addStretch()
        self._add_tab_with_scroll(tab, _("字幕设置"))

        # 初始显示流式组
        self._on_subtitle_mode_changed(self._subtitle_mode_combo.currentText())

    def _on_subtitle_mode_changed(self, mode: str):
        is_streaming = "流式" in mode
        self._s_group.setVisible(is_streaming)
        self._r_group.setVisible(not is_streaming)

    # ── Tab 3: 结果排序 ──
    def _init_sort_tab(self):
        from PyQt5.QtWidgets import QAbstractItemView
        tab = QWidget()
        layout = QVBoxLayout(tab); layout.setSpacing(4)

        layout.addWidget(QLabel(_("排序规则（可拖动调整顺序，编辑前缀/后缀，点✕删除行）:")))

        self._sort_list = QListWidget()
        self._sort_list.setDragDropMode(QAbstractItemView.InternalMove)
        self._sort_list.setDefaultDropAction(Qt.MoveAction)
        self._sort_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._sort_list.setMinimumHeight(150)
        self._sort_list.model().rowsMoved.connect(lambda *_: self._on_apply_mode())
        layout.addWidget(self._sort_list, 1)

        btn = QPushButton(_("应用排序规则"))
        btn.clicked.connect(self._on_apply_mode)
        layout.addWidget(btn)
        self._add_tab_with_scroll(tab, _("结果排序"))

    def _add_sort_row(self, name: str, prefix: str = "", suffix: str = ""):
        """添加一个排序行到 QListWidget。"""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 1, 2, 1)
        row_layout.setSpacing(4)

        prefix_edit = QLineEdit(prefix)
        prefix_edit.setPlaceholderText("前缀")
        prefix_edit.setMaximumWidth(80)
        prefix_edit.textChanged.connect(self._on_apply_mode)
        row_layout.addWidget(prefix_edit)

        chip = QLabel(name)
        chip.setObjectName("regionChip")
        chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout.addWidget(chip)

        suffix_edit = QLineEdit(suffix)
        suffix_edit.setPlaceholderText("后缀")
        suffix_edit.setMaximumWidth(80)
        suffix_edit.textChanged.connect(self._on_apply_mode)
        row_layout.addWidget(suffix_edit)

        btn_x = QPushButton(_("✕"))
        btn_x.setMaximumWidth(22)
        btn_x.setMaximumHeight(22)
        btn_x.clicked.connect(lambda: self._remove_sort_item(row))
        row_layout.addWidget(btn_x)

        row_layout.addStretch()

        item = QListWidgetItem()
        item.setSizeHint(row.sizeHint())
        item.setData(Qt.UserRole, name)  # store region name
        self._sort_list.addItem(item)
        self._sort_list.setItemWidget(item, row)

    def _remove_sort_item(self, row: QWidget):
        """删除排序行。"""
        for i in range(self._sort_list.count()):
            if self._sort_list.itemWidget(self._sort_list.item(i)) is row:
                self._sort_list.takeItem(i)
                break
        self._on_apply_mode()

    def _rebuild_sort_rows_from_regions(self):
        """根据当前区域列表重建排序行（保留已有数据）。"""
        # 收集现有的排序行数据
        existing = {}
        for i in range(self._sort_list.count()):
            item = self._sort_list.item(i)
            widget = self._sort_list.itemWidget(item)
            if widget:
                children = widget.findChildren((QLineEdit, QLabel))
                if len(children) >= 2:
                    rname = children[1].text()
                    prefix = children[0].text().strip() if isinstance(children[0], QLineEdit) else ""
                    suffix = children[2].text().strip() if len(children) > 2 and isinstance(children[2], QLineEdit) else ""
                    existing[rname] = (prefix, suffix)

        self._sort_list.clear()
        for name in self._region_names:
            prefix, suffix = existing.get(name, ("", ""))
            self._add_sort_row(name, prefix, suffix)
        self._on_apply_mode()

    # ── Tab 3: 后处理（含过滤器） ──
    def _init_postprocess_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab); layout.setSpacing(4)

        # ── 置信度阈值过滤 ──
        self._post_conf_check = QCheckBox(_("启用置信度过滤（仅 PaddleOCR）"))
        self._post_conf_check.setChecked(False)
        layout.addRow("", self._post_conf_check)

        self._post_conf_threshold = QDoubleSpinBox()
        self._post_conf_threshold.setRange(0.0, 1.0); self._post_conf_threshold.setSingleStep(0.05)
        self._post_conf_threshold.setDecimals(2); self._post_conf_threshold.setValue(0.6)
        layout.addRow("置信度阈值:", self._post_conf_threshold)

        self._post_sim_threshold = QDoubleSpinBox()
        self._post_sim_threshold.setRange(0.0, 1.0); self._post_sim_threshold.setSingleStep(0.05)
        self._post_sim_threshold.setDecimals(2); self._post_sim_threshold.setValue(0.9)
        layout.addRow("去重相似度阈值:", self._post_sim_threshold)

        self._post_min_text_len = QSpinBox()
        self._post_min_text_len.setRange(1, 100); self._post_min_text_len.setValue(2)
        layout.addRow("最小文字长度:", self._post_min_text_len)

        # ── 过滤器（后处理子集） ──
        sep_filter = QLabel(_("── 关键词过滤 ──"))
        sep_filter.setObjectName("sectionSep")
        layout.addRow("", sep_filter)

        add_row = QHBoxLayout(); add_row.setSpacing(4)
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("输入要过滤的关键词，回车添加...")
        self._filter_input.returnPressed.connect(self._on_add_filter)
        add_row.addWidget(self._filter_input, 1)
        self._btn_filter_add = QPushButton(_("➕ 添加"))
        self._btn_filter_add.clicked.connect(self._on_add_filter)
        add_row.addWidget(self._btn_filter_add)
        layout.addRow("", add_row)

        self._filter_list = QListWidget()
        self._filter_list.setMinimumHeight(60)
        self._filter_list.setMaximumHeight(160)
        self._filter_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addRow("", self._filter_list)

        filter_btn_row = QHBoxLayout(); filter_btn_row.setSpacing(4)
        self._btn_filter_del = QPushButton(_("🗑 删除选中"))
        self._btn_filter_del.clicked.connect(self._on_remove_filter)
        filter_btn_row.addWidget(self._btn_filter_del)
        self._btn_filter_clear = QPushButton(_("清空全部"))
        self._btn_filter_clear.clicked.connect(self._on_clear_filters)
        filter_btn_row.addWidget(self._btn_filter_clear)
        filter_btn_row.addStretch()
        layout.addRow("", filter_btn_row)

        btn = QPushButton(_("应用后处理设置"))
        btn.clicked.connect(self._on_apply_mode)
        layout.addRow("", btn)
        self._add_tab_with_scroll(tab, _("后处理"))

    # ── Tab 5: 提示词模板 ──
    def _init_template_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab); layout.setSpacing(4)

        sel_row = QHBoxLayout(); sel_row.setSpacing(4)
        sel_row.addWidget(QLabel(_("模板:")))
        self._template_combo = QComboBox()
        self._template_combo.setEditable(False)
        self._template_combo.currentTextChanged.connect(self._on_template_selected)
        sel_row.addWidget(self._template_combo, 1)
        layout.addLayout(sel_row)

        layout.addWidget(QLabel(_("提示词内容:")))
        self._prompt_edit = QTextEdit()
        self._prompt_edit.setPlaceholderText("输入提示词...")
        self._prompt_edit.textChanged.connect(self._on_prompt_changed)
        layout.addWidget(self._prompt_edit, 1)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        for text, slot in [("➕ 新建", self._on_new_template),
                           ("💾 保存", self._on_save_template),
                           ("✏ 重命名", self._on_rename_template),
                           ("🗑 删除", self._on_delete_template)]:
            b = QPushButton(text); b.clicked.connect(slot); btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self._add_tab_with_scroll(tab, _("提示词模板"))

    # ── Tab 6: 语音识别 ──
    def _init_asr_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab); layout.setSpacing(4)

        # ── 模型目录 ──
        self._asr_model_dir_edit = QLineEdit("models/asr")
        self._asr_model_dir_edit.setPlaceholderText("留空使用默认缓存")
        self._asr_model_dir_edit.setToolTip("模型本地存放目录，自动扫描子目录中可用的模型")
        layout.addRow("模型目录:", self._asr_model_dir_edit)

        # ── 可用模型列表（动态扫描） ──
        self._asr_model_combo = QComboBox()
        self._asr_model_combo.setEditable(False)
        self._asr_model_combo.setToolTip("选择要加载和调用的 ASR 模型（仅显示目录中找到的模型）")
        self._refresh_asr_models()
        layout.addRow("可用模型:", self._asr_model_combo)

        btn_refresh = QPushButton(_("🔄 刷新模型列表"))
        btn_refresh.clicked.connect(self._refresh_asr_models)
        layout.addRow("", btn_refresh)

        self._asr_lang_combo = QComboBox()
        self._asr_lang_combo.setEditable(False)
        self._asr_lang_combo.addItems(["auto", "zh", "en", "ja", "ko"])
        self._asr_lang_combo.setCurrentText("zh")
        self._asr_lang_combo.setToolTip("auto = 自动检测语言")
        layout.addRow("语言:", self._asr_lang_combo)

        # ── 解码参数 ──
        gf = QGroupBox("解码参数")
        gfl = QFormLayout(gf); gfl.setSpacing(4)

        self._asr_beam_spin = QSpinBox(); self._asr_beam_spin.setRange(1, 20)
        self._asr_beam_spin.setValue(5)
        self._asr_beam_spin.setToolTip("Beam size，越大精度越高但越慢")
        gfl.addRow("Beam Size:", self._asr_beam_spin)

        self._asr_word_ts_check = QCheckBox(_("字级时间戳"))
        self._asr_word_ts_check.setChecked(True)
        gfl.addRow("", self._asr_word_ts_check)

        self._asr_condition_check = QCheckBox(_("基于上文条件解码"))
        self._asr_condition_check.setChecked(True)
        self._asr_condition_check.setToolTip("condition_on_previous_text")
        gfl.addRow("", self._asr_condition_check)

        self._asr_no_speech_spin = QDoubleSpinBox()
        self._asr_no_speech_spin.setRange(0.0, 1.0); self._asr_no_speech_spin.setSingleStep(0.1)
        self._asr_no_speech_spin.setValue(0.6)
        self._asr_no_speech_spin.setToolTip("无语音段阈值，越高越容易跳过无声音片段")
        gfl.addRow("无语音阈值:", self._asr_no_speech_spin)

        self._asr_comp_ratio_spin = QDoubleSpinBox()
        self._asr_comp_ratio_spin.setRange(0.0, 10.0); self._asr_comp_ratio_spin.setSingleStep(0.1)
        self._asr_comp_ratio_spin.setValue(2.4)
        self._asr_comp_ratio_spin.setToolTip("压缩比阈值，控制重复文本过滤")
        gfl.addRow("压缩比阈值:", self._asr_comp_ratio_spin)

        self._asr_temp_edit = QLineEdit("0.0,0.2,0.4,0.6,0.8,1.0")
        self._asr_temp_edit.setToolTip("温度参数（逗号分隔），越低越确定")
        gfl.addRow("温度:", self._asr_temp_edit)

        self._asr_hotwords_edit = QLineEdit()
        self._asr_hotwords_edit.setPlaceholderText("热词，逗号分隔")
        self._asr_hotwords_edit.setToolTip("热词列表，提升特定词汇的识别率")
        gfl.addRow("热词:", self._asr_hotwords_edit)

        self._asr_prompt_edit = QLineEdit()
        self._asr_prompt_edit.setPlaceholderText("初始提示词，如: 以下是普通话的转录")
        self._asr_prompt_edit.setToolTip("initial_prompt，用于引导输出风格")
        gfl.addRow("初始提示:", self._asr_prompt_edit)
        layout.addRow(gf)

        # ── VAD 参数 ──
        vg = QGroupBox("VAD (语音活动检测)")
        vgl = QFormLayout(vg); vgl.setSpacing(4)

        self._asr_vad_check = QCheckBox(_("启用 VAD（跳过静音段）"))
        self._asr_vad_check.setChecked(False)
        self._asr_vad_check.setToolTip("自动检测并跳过静音部分，加速处理")
        vgl.addRow("", self._asr_vad_check)

        self._asr_vad_silence_spin = QSpinBox()
        self._asr_vad_silence_spin.setRange(100, 5000); self._asr_vad_silence_spin.setSingleStep(100)
        self._asr_vad_silence_spin.setValue(500); self._asr_vad_silence_spin.setSuffix(" ms")
        self._asr_vad_silence_spin.setToolTip("最小静音时长，超过该时长切断段落")
        vgl.addRow("最小静音:", self._asr_vad_silence_spin)

        self._asr_vad_thresh_spin = QDoubleSpinBox()
        self._asr_vad_thresh_spin.setRange(0.0, 1.0); self._asr_vad_thresh_spin.setSingleStep(0.05)
        self._asr_vad_thresh_spin.setValue(0.5)
        self._asr_vad_thresh_spin.setToolTip("VAD 阈值，越高对语音越敏感")
        vgl.addRow("VAD 阈值:", self._asr_vad_thresh_spin)
        layout.addRow(vg)

        # ── 输出 ──
        self._asr_region_edit = QLineEdit("语音")
        self._asr_region_edit.setPlaceholderText("语音")
        self._asr_region_edit.setToolTip("ASR 结果在表格中显示的区域名称")
        layout.addRow("区域名:", self._asr_region_edit)

        btn = QPushButton(_("应用语音识别设置"))
        btn.clicked.connect(self._on_apply_mode)
        layout.addRow("", btn)
        self._add_tab_with_scroll(tab, _("语音识别"))

    # ── Tab 8: AI 纠错 ──
    def _init_correction_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab); layout.setSpacing(4)

        # ── 翻译模式开关 ──
        self._corr_translate_check = QCheckBox(_("🌐 翻译模式（将结果翻译为中文，纠错提示词仅作参考）"))
        self._corr_translate_check.setChecked(False)
        self._corr_translate_check.setToolTip("开启后 LLM 将把 OCR 结果翻译为中文，用户自定义纠错提示词仅作为风格参考")
        self._corr_translate_check.toggled.connect(self._on_apply_mode)
        layout.addRow("", self._corr_translate_check)

        # ── 流式输出模式 ──
        self._corr_stream_check = QCheckBox(_("🔴 流式输出模式（实时逐字显示 API 响应）"))
        self._corr_stream_check.setChecked(False)
        self._corr_stream_check.setToolTip("开启后 API 纠错结果将实时逐字显示在表格中，关闭后等待完整响应再更新")
        self._corr_stream_check.toggled.connect(self._on_apply_mode)
        layout.addRow("", self._corr_stream_check)

        # ── JSON 输出模式 ──
        self._corr_json_check = QCheckBox(_("📋 JSON 输出模式（API 返回结构化 JSON 格式）"))
        self._corr_json_check.setChecked(False)
        self._corr_json_check.setToolTip("开启后 API 将以 JSON 格式返回纠错结果，便于程序化处理")
        self._corr_json_check.toggled.connect(self._on_apply_mode)
        layout.addRow("", self._corr_json_check)

        self._corr_extract_env_check = QCheckBox(_("提取全文环境（领域/氛围/内容摘要作为纠错参考）"))
        self._corr_extract_env_check.setChecked(False)
        self._corr_extract_env_check.setToolTip("纠错前先用 AI 分析全文领域、氛围、主题，注入 system prompt 提升纠错准确率")
        layout.addRow("", self._corr_extract_env_check)

        # ── 立即提取按钮 ──
        self._btn_extract_env = QPushButton(_("🔍 立即提取全文环境"))
        self._btn_extract_env.setToolTip("立即用当前全文结果和自定义总结提示词提取环境上下文，结果回填到下方提示词栏")
        self._btn_extract_env.clicked.connect(self._on_extract_env_clicked)
        layout.addRow("", self._btn_extract_env)

        # ── 全文总结提示词（可自定义） ──
        self._corr_summary_prompt_text = QTextEdit()
        self._corr_summary_prompt_text.setPlaceholderText(
            "自定义全文总结/概括提示词，用于提取环境上下文（可选）")
        self._corr_summary_prompt_text.setMaximumHeight(100)
        self._corr_summary_prompt_text.setToolTip(
            "此提示词用于对全文进行总结概括，结果将作为纠错的 system prompt 注入。\n"
            "留空则使用默认提示词。")
        layout.addRow("总结提示词:", self._corr_summary_prompt_text)

        # ── 纠错 System Prompt（可自定义） ──
        self._corr_system_prompt_text = QTextEdit()
        self._corr_system_prompt_text.setPlaceholderText(
            "自定义纠错系统提示词（system prompt），控制纠错行为（可选）")
        self._corr_system_prompt_text.setMaximumHeight(100)
        self._corr_system_prompt_text.setToolTip(
            "此 system prompt 会注入到每次纠错请求中。\n"
            "留空则使用默认值。")
        layout.addRow("纠错系统提示词:", self._corr_system_prompt_text)

        # ── 输出格式（可自定义） ──
        self._corr_output_format_edit = QLineEdit()
        self._corr_output_format_edit.setPlaceholderText("[纠正后文本]")
        self._corr_output_format_edit.setToolTip(
            "指定纠错结果的输出格式标记，用于从 API 响应中提取最终结果。\n"
            "留空则不剔除任何格式标记。")
        layout.addRow("输出格式标记:", self._corr_output_format_edit)

        # ── API 预设 ──
        self._corr_preset_combo = QComboBox()
        self._corr_preset_combo.setToolTip("选择纠错使用的 API 连接预设")
        from core.api_preset_manager import APIPresetManager
        self._corr_preset_combo.addItems(APIPresetManager().get_names())
        default_preset = APIPresetManager().get_default_name()
        if default_preset:
            self._corr_preset_combo.setCurrentText(default_preset)
        layout.addRow("API 预设:", self._corr_preset_combo)

        self._corr_batch_spin = QSpinBox()
        self._corr_batch_spin.setRange(1, 50)
        self._corr_batch_spin.setValue(5)
        self._corr_batch_spin.setSuffix(" 条/次")
        self._corr_batch_spin.setToolTip("每次纠错批处理的文本条目数")
        layout.addRow("批量条数:", self._corr_batch_spin)

        self._corr_context_spin = QSpinBox()
        self._corr_context_spin.setRange(0, 10)
        self._corr_context_spin.setValue(3)
        self._corr_context_spin.setSuffix(" 条")
        self._corr_context_spin.setToolTip("每条结果纠错时参考的上下文条数")
        layout.addRow("上下文窗口:", self._corr_context_spin)

        self._corr_retry_spin = QSpinBox()
        self._corr_retry_spin.setRange(0, 10)
        self._corr_retry_spin.setValue(2)
        layout.addRow("失败重试次数:", self._corr_retry_spin)

        self._corr_prompt_text = QTextEdit()
        self._corr_prompt_text.setPlaceholderText("自定义纠错提示词（可选，覆盖 correction_prompt）")
        self._corr_prompt_text.setMaximumHeight(100)
        layout.addRow("纠错用户提示词:", self._corr_prompt_text)

        btn = QPushButton(_("应用纠错设置"))
        btn.clicked.connect(self._on_apply_mode)
        layout.addRow("", btn)
        self._add_tab_with_scroll(tab, _("AI 纠错"))

    # ── 折叠/展开（保留兼容，由外部调用） ──
    def _on_collapse_clicked(self):
        self.collapse_requested.emit()

    # ── ASR 模型刷新 ──
    def _refresh_asr_models(self):
        """扫描本地模型目录并填充可用模型列表。"""
        from pathlib import Path

        from core.asr_engine import scan_local_asr_models
        model_dir = self._asr_model_dir_edit.text().strip() or "models/asr"
        base = Path(__file__).parent.parent
        full_dir = str(base / model_dir) if not os.path.isabs(model_dir) else model_dir
        models = scan_local_asr_models(full_dir)
        # 去重
        seen = set()
        unique = []
        for m in models:
            norm = os.path.normcase(os.path.normpath(m))
            if norm not in seen:
                seen.add(norm)
                unique.append(m)
        self._asr_model_combo.blockSignals(True)
        self._asr_model_combo.clear()
        if unique:
            # 显示简短名称
            for path in unique:
                display = os.path.basename(path) if os.path.isdir(path) else path
                self._asr_model_combo.addItem(display, path)
            self._asr_model_combo.setCurrentIndex(0)
        else:
            self._asr_model_combo.addItem("（未找到本地模型，使用默认 large-v3）")
        self._asr_model_combo.blockSignals(False)

    # ── 事件 ──
    def _on_prompt_changed(self):
        self.prompt_changed.emit(self._prompt_edit.toPlainText())
    def _on_apply_mode(self):
        self.mode_changed.emit(self.get_mode_params())
    def _on_extract_env_clicked(self):
        """点击「立即提取全文环境」按钮。"""
        self.extract_env_clicked.emit()

    def _on_template_selected(self, name: str):
        if name: self.template_created.emit(name)
    def _on_new_template(self):
        name, ok = self._get_text_dialog("新建模板", "模板名称:")
        if ok and name.strip():
            if name not in self._template_names:
                self._template_names.append(name); self._template_combo.addItem(name)
            self._template_combo.setCurrentText(name)
            self._prompt_edit.clear()
            self.template_saved.emit(name, "")
    def _on_save_template(self):
        name = self._template_combo.currentText()
        if name: self.template_saved.emit(name, self._prompt_edit.toPlainText())
    def _on_rename_template(self):
        old = self._template_combo.currentText()
        if not old: return
        new, ok = self._get_text_dialog("重命名模板", "新名称:", old)
        if ok and new.strip() and new != old:
            if new in self._template_names:
                QMessageBox.warning(self, "重命名失败", f"模板名称 '{new}' 已存在。"); return
            idx = self._template_names.index(old)
            self._template_names[idx] = new
            self._template_combo.setItemText(self._template_combo.currentIndex(), new)
            self.template_deleted.emit(old)
            self.template_saved.emit(new, self._prompt_edit.toPlainText())
    def _on_delete_template(self):
        name = self._template_combo.currentText()
        if not name: return
        if QMessageBox.question(self, "确认删除", f"确定要删除模板 '{name}' 吗？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            idx = self._template_combo.currentIndex()
            self._template_combo.removeItem(idx)
            self._template_names.remove(name)
            self.template_deleted.emit(name)

    @staticmethod
    def _get_text_dialog(title, label, default=""):
        from PyQt5.QtWidgets import QInputDialog, QLineEdit
        return QInputDialog.getText(None, title, label, QLineEdit.Normal, default)

    def _on_add_filter(self):
        kw = self._filter_input.text().strip()
        if kw: self.filter_add_requested.emit(kw); self._filter_input.clear()
    def _on_remove_filter(self):
        item = self._filter_list.currentItem()
        if item: self.filter_remove_requested.emit(item.text())
    def _on_clear_filters(self):
        if QMessageBox.question(self, "确认清空", "确定要清空所有过滤关键词吗？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            for i in range(self._filter_list.count()):
                self.filter_remove_requested.emit(self._filter_list.item(i).text())

    # ── 公共接口 ──
    def set_template_prompt(self, text):
        self._prompt_edit.setPlainText(text)
    def set_template_names(self, names: list[str]):
        self._template_names = list(names)
        cur = self._template_combo.currentText()
        self._template_combo.blockSignals(True); self._template_combo.clear()
        self._template_combo.addItems(names)
        if cur in names: self._template_combo.setCurrentText(cur)
        self._template_combo.blockSignals(False)
    def select_template(self, name: str):
        idx = self._template_combo.findText(name)
        if idx >= 0: self._template_combo.setCurrentIndex(idx)
    def set_filter_keywords(self, keywords: list[str]):
        self._filter_list.clear(); self._filter_list.addItems(keywords)

    def set_region_names(self, names: list[str]):
        self._region_names = list(names)
        self._rebuild_sort_rows_from_regions()
