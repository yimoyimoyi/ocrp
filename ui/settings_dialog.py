"""参数设置对话框 —— 唯一的设置 UI 入口，通过 ConfigPanel 公共 API 同步数据。"""

import os
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
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

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.i18n import _
from core.utils import fetch_models_from_url, populate_model_combo
from ui.collapsible_group import CollapsibleGroup
from ui.widget_helpers import safe_set_widget


def _safe_set(widget, value, setter=None):
    """安全设置 widget 值。"""
    if setter:
        try:
            setter(value)
        except RuntimeError:
            pass
    else:
        safe_set_widget(widget, value)


class SettingsDialog(QDialog):
    """参数设置对话框，集中管理处理参数 + 纠错 API 配置。"""

    def __init__(self, config_panel, correction_config: dict = None, parent=None,
                 filter_keywords: list[str] | None = None,
                 engine_manager=None, current_engine: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_("⚙ 参数设置"))
        self.setMinimumSize(800, 640)
        self.resize(860, 700)
        self.setObjectName("settingsDialog")
        self._cp = config_panel
        self._corr_cfg = correction_config or {}
        self._sort_items: list = []
        self._filter_items: list = []
        self._initial_filter_keywords = filter_keywords or []
        self._engine_mgr = engine_manager
        self._current_engine = current_engine

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setTabPosition(QTabWidget.North)
        layout.addWidget(self._tabs, 1)

        self._build_tabs()
        self._load_initial_values()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _load_initial_values(self):
        """从 ConfigPanel 的公共属性读取所有参数初始值。"""
        cp = self._cp
        mp = cp.get_mode_params()

        # ── 基础设置 ──
        _safe_set(self._frame_interval, mp.get("frame_interval", 0.1))
        _safe_set(self._process_mode, mp.get("process_mode", "OCR + ASR（完整流程）"))
        _safe_set(self._subtitle_duration, mp.get("subtitle_duration", 3.0))
        _safe_set(self._srt_export, mp.get("srt_export_mode", "仅纠正结果"))
        _safe_set(self._post_sim_dedup, mp.get("post_sim_dedup", True))
        _safe_set(self._corr_enabled, mp.get("corr_enabled", False))

        # ── 字幕模式 ──
        subtitle_mode = mp.get("subtitle_mode", "流式字幕（去重）")
        _safe_set(self._subtitle_mode, subtitle_mode)
        self._on_subtitle_mode_changed(subtitle_mode)

        # ── 流式参数 ──
        _safe_set(self._s_sentinel, mp.get("sentinel_enabled", True))
        _safe_set(self._s_drop_ratio, mp.get("s_drop_ratio", 0.5))
        _safe_set(self._s_buffer, mp.get("s_buffer_size", 8))
        _safe_set(self._s_sim, mp.get("s_sim_threshold", 0.85))
        _safe_set(self._s_min_text, mp.get("s_min_text_len", 2))

        # ── 常规参数 ──
        _safe_set(self._r_dedup, mp.get("r_dedup", True))
        _safe_set(self._r_sim, mp.get("r_sim_threshold", 0.9))
        _safe_set(self._r_buffer, mp.get("r_buffer_size", 5))
        _safe_set(self._r_min_text, mp.get("r_min_text_len", 2))
        _safe_set(self._r_interval, mp.get("r_interval", 2.0))

        # ── 后处理 ──
        _safe_set(self._post_conf_check, mp.get("post_conf_enabled", False))
        _safe_set(self._post_conf_threshold, mp.get("post_conf_threshold", 0.6))
        _safe_set(self._post_sim_threshold, mp.get("post_sim_threshold", 0.9))
        _safe_set(self._post_min_text_len, mp.get("post_min_text_len", 2))

        # ── 过滤器 ──
        self._filter_items.clear()
        self._filter_list.clear()
        self._filter_original = list(self._initial_filter_keywords)
        for kw in self._initial_filter_keywords:
            self._filter_items.append(kw)
            self._filter_list.addItem(kw)

        # ── AI 纠错 ──
        _safe_set(self._corr_translate, mp.get("corr_translate", False))
        _safe_set(self._corr_stream, mp.get("corr_stream", False))
        _safe_set(self._corr_json, mp.get("corr_json", False))
        _safe_set(self._corr_extract_env, mp.get("corr_extract_env", False))
        _safe_set(self._corr_polish, self._corr_cfg.get("enable_polish", False))
        _safe_set(self._corr_summary_prompt, mp.get("corr_summary_prompt", ""))
        _safe_set(self._corr_system_prompt, mp.get("corr_system_prompt", ""))
        _safe_set(self._corr_output_format, mp.get("corr_output_format", ""))
        _safe_set(self._corr_preset, mp.get("corr_preset", ""))
        _safe_set(self._corr_batch, mp.get("corr_batch_size", 5))
        _safe_set(self._corr_context, mp.get("corr_context_window", 3))
        _safe_set(self._corr_retry, mp.get("corr_retry", 2))
        _safe_set(self._corr_concurrency, mp.get("corr_concurrency", 4))
        _safe_set(self._corr_rpm, mp.get("corr_rpm", 30))
        _safe_set(self._seg_time_gap, mp.get("seg_time_gap", 3.0))
        _safe_set(self._corr_prompt, mp.get("corr_prompt", ""))

        # ── ASR ──
        _safe_set(self._asr_model_dir, mp.get("asr_model_dir", "models/asr"))
        self._refresh_asr_models()
        model_path = mp.get("asr_model_path", "")
        if model_path:
            self._select_combo_by_data(self._asr_model, model_path)
        _safe_set(self._asr_lang, mp.get("asr_language", "zh"))
        _safe_set(self._asr_beam, mp.get("asr_beam_size", 5))
        _safe_set(self._asr_word_ts, mp.get("asr_word_ts", True))
        _safe_set(self._asr_condition, mp.get("asr_condition_prev", True))
        _safe_set(self._asr_no_speech, mp.get("asr_no_speech_thresh", 0.6))
        _safe_set(self._asr_comp_ratio, mp.get("asr_comp_ratio_thresh", 2.4))
        _safe_set(self._asr_temp, mp.get("asr_temperature", "0.0,0.2,0.4,0.6,0.8,1.0"))
        _safe_set(self._asr_hotwords, mp.get("asr_hotwords", ""))
        _safe_set(self._asr_prompt, mp.get("asr_initial_prompt", ""))
        _safe_set(self._asr_vad, mp.get("asr_vad", False))
        _safe_set(self._asr_vad_silence, mp.get("asr_vad_min_silence", 500))
        _safe_set(self._asr_vad_thresh, mp.get("asr_vad_threshold", 0.5))
        _safe_set(self._asr_region, mp.get("asr_region_name", "语音"))

        # ── 排序 ──
        self._sort_items.clear()
        self._sort_list.clear()
        for prefix, name, suffix in cp.get_sort_rules():
            self._sort_items.append((prefix, name, suffix))
            self._add_sort_row(name, prefix, suffix)

    def _sync_values_to_cp(self):
        """将对话框中的值通过 ConfigPanel 公共 API 写回。"""
        params = {}

        # ── 基础设置 ──
        params["frame_interval"] = self._frame_interval.value()
        params["process_mode"] = self._process_mode.currentText()
        params["subtitle_duration"] = self._subtitle_duration.value()
        params["srt_export_mode"] = self._srt_export.currentText()
        params["post_sim_dedup"] = self._post_sim_dedup.isChecked()
        params["corr_enabled"] = self._corr_enabled.isChecked()

        # ── 字幕模式 ──
        params["subtitle_mode"] = self._subtitle_mode.currentText()
        params["sentinel_enabled"] = self._s_sentinel.isChecked()

        # ── 流式参数 ──
        params["s_drop_ratio"] = self._s_drop_ratio.value()
        params["s_buffer_size"] = self._s_buffer.value()
        params["s_sim_threshold"] = self._s_sim.value()
        params["s_min_text_len"] = self._s_min_text.value()

        # ── 常规参数 ──
        params["r_dedup"] = self._r_dedup.isChecked()
        params["r_sim_threshold"] = self._r_sim.value()
        params["r_buffer_size"] = self._r_buffer.value()
        params["r_min_text_len"] = self._r_min_text.value()
        params["r_interval"] = self._r_interval.value()

        # ── 后处理 ──
        params["post_conf_enabled"] = self._post_conf_check.isChecked()
        params["post_conf_threshold"] = self._post_conf_threshold.value()
        params["post_sim_threshold"] = self._post_sim_threshold.value()
        params["post_min_text_len"] = self._post_min_text_len.value()

        # ── AI 纠错 ──
        params["corr_translate"] = self._corr_translate.isChecked()
        params["corr_stream"] = self._corr_stream.isChecked()
        params["corr_json"] = self._corr_json.isChecked()
        params["corr_extract_env"] = self._corr_extract_env.isChecked()
        params["corr_summary_prompt"] = self._corr_summary_prompt.toPlainText()
        params["corr_system_prompt"] = self._corr_system_prompt.toPlainText()
        params["corr_output_format"] = self._corr_output_format.text()
        params["corr_preset"] = self._corr_preset.currentText()
        params["corr_batch_size"] = self._corr_batch.value()
        params["corr_context_window"] = self._corr_context.value()
        params["corr_retry"] = self._corr_retry.value()
        params["corr_concurrency"] = self._corr_concurrency.value()
        params["corr_rpm"] = self._corr_rpm.value()
        params["seg_time_gap"] = self._seg_time_gap.value()
        params["corr_prompt"] = self._corr_prompt.toPlainText()

        # ── ASR ──
        params["asr_model_dir"] = self._asr_model_dir.text().strip() or "models/asr"
        params["asr_model_path"] = self._asr_model.currentData() or ""
        params["asr_language"] = self._asr_lang.currentText()
        params["asr_beam_size"] = self._asr_beam.value()
        params["asr_word_ts"] = self._asr_word_ts.isChecked()
        params["asr_condition_prev"] = self._asr_condition.isChecked()
        params["asr_no_speech_thresh"] = self._asr_no_speech.value()
        params["asr_comp_ratio_thresh"] = self._asr_comp_ratio.value()
        params["asr_temperature"] = self._asr_temp.text()
        params["asr_hotwords"] = self._asr_hotwords.text()
        params["asr_initial_prompt"] = self._asr_prompt.text()
        params["asr_vad"] = self._asr_vad.isChecked()
        params["asr_vad_min_silence"] = self._asr_vad_silence.value()
        params["asr_vad_threshold"] = self._asr_vad_thresh.value()
        params["asr_region_name"] = self._asr_region.text()

        # ── 排序 ──
        self._collect_sort_items()
        params["region_order"] = "\n".join(
            f"{prefix}：{name}：{suffix}" if prefix and suffix
            else f"{prefix}：{name}" if prefix
            else f"{name}：{suffix}" if suffix
            else name
            for prefix, name, suffix in self._sort_items if name
        )

        # 通过公共 API 写入 ConfigPanel
        cp = self._cp
        cp.apply_mode_params(params)

        # ── 过滤器差异同步 ──
        original = getattr(self, '_filter_original', [])
        current = list(self._filter_items)
        for kw in set(original) - set(current):
            cp.filter_remove_requested.emit(kw)
        for kw in set(current) - set(original):
            cp.filter_add_requested.emit(kw)

        # ── 排序规则同步 ──
        cp.set_sort_rules(list(self._sort_items))

    # ── helpers ──
    def _wrap_scroll(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    # ── 引擎配置 ──
    def _on_engine_changed(self, name: str):
        """引擎切换时更新字段可见性和值。"""
        if not self._engine_mgr or not name:
            return
        eng_cfg = self._engine_mgr._config.get("engines", {}).get(name, {})
        cfg = eng_cfg.get("config", {})
        is_local = eng_cfg.get("type") == "local"
        is_paddle = name == "paddleocr"
        # 填充字段
        self._eng_api_key.setText(cfg.get("api_key", ""))
        self._eng_base_url.setText(cfg.get("base_url", ""))
        self._eng_model.setEditText(cfg.get("model", ""))
        self._eng_timeout.setValue(cfg.get("timeout", 30))
        self._eng_gpu.setChecked(cfg.get("device") == "gpu" or cfg.get("use_gpu", False))
        ver = cfg.get("ocr_version") or ""
        if "v4" in ver:
            self._eng_paddle_version.setCurrentIndex(2)
        elif "mobile" in ver:
            self._eng_paddle_version.setCurrentIndex(1)
        else:
            self._eng_paddle_version.setCurrentIndex(0)
        self._eng_angle.setChecked(cfg.get("use_angle_cls", True))
        # 可见性
        self._eng_api_key.setVisible(not is_local)
        self._eng_base_url.setVisible(not is_local)
        self._eng_model.setVisible(not is_local)
        self._eng_model_status.setVisible(not is_local)
        self._eng_timeout.setVisible(not is_local)
        self._eng_gpu.setVisible(is_local)
        self._eng_paddle_version.setVisible(is_paddle)
        self._eng_angle.setVisible(is_paddle)
        self._eng_save_preset.setVisible(not is_local)

    def _on_fetch_eng_models(self):
        """从当前引擎 Base URL 获取可用模型列表。"""
        base_url = self._eng_base_url.text().strip()
        if not base_url:
            self._eng_model_status.setText(_("⚠ 请输入 URL"))
            return
        self._eng_model_status.setText(_("⏳ 获取中..."))
        import threading

        from PyQt5.QtCore import QObject as _QObject
        class _Bridge(_QObject):
            done = pyqtSignal(object)
            err = pyqtSignal(str)
        bridge = _Bridge(self)
        bridge.done.connect(self._on_eng_models_done)
        bridge.err.connect(lambda m: self._eng_model_status.setText(f"❌ {m[:20]}"))
        api_key = self._eng_api_key.text()
        def _fetch():
            try:
                models = fetch_models_from_url(base_url, api_key)
                bridge.done.emit(models)
            except Exception as e:
                bridge.err.emit(str(e))
        threading.Thread(target=_fetch, daemon=True).start()

    def _on_eng_models_done(self, models):
        if models:
            populate_model_combo(self._eng_model, models)
            self._eng_model_status.setText(f"✅ {len(models)} 个")
        else:
            self._eng_model_status.setText(_("⚠ 未获取到"))

    def _on_save_eng_preset(self):
        """将当前引擎 API 配置保存为预设。"""
        from core.api_preset_manager import APIPresetManager
        mgr = APIPresetManager()
        name = f"{self._engine_combo.currentText()} 预设"
        mgr.add_preset(name, {
            "api_key": self._eng_api_key.text(),
            "base_url": self._eng_base_url.text(),
            "model": self._eng_model.currentText(),
            "timeout": self._eng_timeout.value(),
        })
        self._eng_model_status.setText(f"✅ 已保存: {name}")

    def get_engine_config(self) -> tuple[str, dict]:
        """返回 (engine_name, config_dict) 供主窗口保存。"""
        name = self._engine_combo.currentText()
        ver_map = {0: None, 1: "PP-OCRv5_mobile", 2: "PP-OCRv4"}
        cfg = {
            "api_key": self._eng_api_key.text(),
            "base_url": self._eng_base_url.text(),
            "model": self._eng_model.currentText(),
            "timeout": self._eng_timeout.value(),
            "device": "gpu" if self._eng_gpu.isChecked() else "cpu",
            "ocr_version": ver_map.get(self._eng_paddle_version.currentIndex()),
            "use_angle_cls": self._eng_angle.isChecked(),
        }
        return name, cfg

    # ── Tab 构建 ──
    def _build_tabs(self):
        self._tabs.addTab(self._wrap_scroll(self._build_basic_tab()), "⚙ 基础")
        self._tabs.addTab(self._wrap_scroll(self._build_asr_tab()), "🎙 语音识别")
        self._tabs.addTab(self._wrap_scroll(self._build_ocr_tab()), "🔤 OCR 处理")
        self._tabs.addTab(self._wrap_scroll(self._build_correction_tab()), "✏ AI 纠错")
        self._tabs.addTab(self._wrap_scroll(self._build_sort_tab()), "📊 结果输出")

    # ── Tab 1: 基础设置 ──
    def _build_basic_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 处理模式组 ──
        mode_group = CollapsibleGroup(_("处理模式"))
        mf = QFormLayout()
        mf.setSpacing(8)
        self._process_mode = QComboBox()
        self._process_mode.addItems(["OCR + ASR（完整流程）", "仅 OCR", "仅语音识别 (ASR)"])
        self._process_mode.setToolTip("选择开始处理时运行的流程模式")
        mf.addRow("处理模式:", self._process_mode)
        self._frame_interval = QDoubleSpinBox()
        self._frame_interval.setRange(0.02, 10.0)
        self._frame_interval.setSingleStep(0.1)
        self._frame_interval.setDecimals(2)
        self._frame_interval.setValue(0.1)
        self._frame_interval.setSuffix(" 秒")
        self._frame_interval.setToolTip("每隔多少秒处理一帧")
        mf.addRow("帧间隔:", self._frame_interval)
        mode_group.addLayout(mf)
        layout.addWidget(mode_group)

        # ── OCR 引擎组 ──
        engine_group = CollapsibleGroup(_("OCR 引擎"))
        ef = QFormLayout()
        ef.setSpacing(8)
        self._engine_combo = QComboBox()
        if self._engine_mgr:
            self._engine_combo.addItems(self._engine_mgr.get_engine_names())
            if self._current_engine:
                self._engine_combo.setCurrentText(self._current_engine)
        self._engine_combo.currentTextChanged.connect(self._on_engine_changed)
        ef.addRow(_("引擎:"), self._engine_combo)
        self._eng_api_key = QLineEdit()
        self._eng_api_key.setPlaceholderText("sk-xxx")
        self._eng_api_key.setEchoMode(QLineEdit.Password)
        ef.addRow(_("API Key:"), self._eng_api_key)
        self._eng_base_url = QLineEdit()
        self._eng_base_url.setPlaceholderText("https://api.openai.com/v1")
        ef.addRow(_("Base URL:"), self._eng_base_url)
        model_row = QHBoxLayout()
        self._eng_model = QComboBox()
        self._eng_model.setEditable(True)
        self._eng_model.setInsertPolicy(QComboBox.NoInsert)
        self._eng_model.lineEdit().setPlaceholderText("gpt-4o")
        self._eng_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        model_row.addWidget(self._eng_model, 1)
        self._eng_model_status = QLabel("")
        self._eng_model_status.setMinimumWidth(80)
        btn_fetch = QPushButton(_("📋 获取模型"))
        btn_fetch.clicked.connect(self._on_fetch_eng_models)
        model_row.addWidget(self._eng_model_status)
        model_row.addWidget(btn_fetch)
        ef.addRow(_("模型:"), model_row)
        self._eng_timeout = QSpinBox()
        self._eng_timeout.setRange(1, 300)
        self._eng_timeout.setValue(30)
        self._eng_timeout.setSuffix(" 秒")
        ef.addRow(_("超时:"), self._eng_timeout)
        self._eng_gpu = QCheckBox(_("启用 GPU 加速"))
        ef.addRow("", self._eng_gpu)
        self._eng_paddle_version = QComboBox()
        self._eng_paddle_version.addItems(["PP-OCRv5_server (高精度/慢)", "PP-OCRv5_mobile (平衡)", "PP-OCRv4 (快速)"])
        ef.addRow(_("模型版本:"), self._eng_paddle_version)
        self._eng_angle = QCheckBox(_("启用角度检测"))
        self._eng_angle.setChecked(True)
        ef.addRow("", self._eng_angle)
        self._eng_save_preset = QPushButton(_("💾 保存为 API 预设"))
        self._eng_save_preset.setToolTip("将当前 API 配置保存为预设，供纠错等功能使用")
        self._eng_save_preset.clicked.connect(self._on_save_eng_preset)
        ef.addRow("", self._eng_save_preset)
        engine_group.addLayout(ef)
        layout.addWidget(engine_group)
        # 初始化引擎字段可见性
        self._on_engine_changed(self._engine_combo.currentText())

        # ── 输出控制组 ──
        out_group = CollapsibleGroup(_("输出控制"))
        of = QFormLayout()
        of.setSpacing(8)
        self._subtitle_duration = QDoubleSpinBox()
        self._subtitle_duration.setRange(0.5, 30.0)
        self._subtitle_duration.setSingleStep(0.5)
        self._subtitle_duration.setValue(3.0)
        self._subtitle_duration.setSuffix(" 秒")
        of.addRow("字幕时长:", self._subtitle_duration)
        self._srt_export = QComboBox()
        self._srt_export.addItems(["仅纠正结果", "仅原文", "双语对照（原文+纠正）", "原文 换行 纠正"])
        self._srt_export.setToolTip("SRT 导出时的字幕内容模式")
        of.addRow("SRT 导出:", self._srt_export)
        out_group.addLayout(of)
        layout.addWidget(out_group)

        layout.addStretch()
        return tab

    # ── Tab 2: 语音识别 ──
    def _build_asr_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 字幕模式选择 ──
        mode_group = CollapsibleGroup(_("字幕模式"))
        mode_form = QFormLayout()
        mode_form.setSpacing(8)
        self._subtitle_mode = QComboBox()
        self._subtitle_mode.addItems(["流式字幕（去重）", "常规字幕（固定间隔）"])
        self._subtitle_mode.setToolTip("流式：哨兵去重实时输出\n常规：固定间隔采样")
        self._subtitle_mode.currentTextChanged.connect(self._on_subtitle_mode_changed)
        mode_form.addRow("字幕模式:", self._subtitle_mode)
        mode_group.addLayout(mode_form)
        layout.addWidget(mode_group)

        # ── 流式参数组 ──
        self._s_group = CollapsibleGroup("流式参数（哨兵去重）")
        s_layout = QFormLayout()
        s_layout.setSpacing(8)
        self._s_sentinel = QCheckBox("启用哨兵去重（骤降/缓冲区/相似度）")
        self._s_sentinel.setChecked(True)
        s_layout.addRow("", self._s_sentinel)
        self._s_drop_ratio = QDoubleSpinBox()
        self._s_drop_ratio.setRange(0.01, 1.0)
        self._s_drop_ratio.setSingleStep(0.05)
        self._s_drop_ratio.setDecimals(2)
        self._s_drop_ratio.setValue(0.5)
        self._s_drop_ratio.setToolTip("文本长度骤降到上一帧的此比例时强制触发输出")
        s_layout.addRow("字数骤降比:", self._s_drop_ratio)
        self._s_buffer = QSpinBox()
        self._s_buffer.setRange(1, 100)
        self._s_buffer.setValue(8)
        self._s_buffer.setToolTip("连续相同文本的缓冲区大小，超过后强制输出")
        s_layout.addRow("连续缓冲区:", self._s_buffer)
        self._s_sim = QDoubleSpinBox()
        self._s_sim.setRange(0.0, 1.0)
        self._s_sim.setSingleStep(0.05)
        self._s_sim.setDecimals(2)
        self._s_sim.setValue(0.85)
        s_layout.addRow("相似度阈值:", self._s_sim)
        self._s_min_text = QSpinBox()
        self._s_min_text.setRange(1, 100)
        self._s_min_text.setValue(2)
        s_layout.addRow("最小文字长度:", self._s_min_text)
        self._s_group.addLayout(s_layout)
        layout.addWidget(self._s_group)

        # ── 常规参数组 ──
        self._r_group = CollapsibleGroup("常规参数（固定间隔）")
        r_layout = QFormLayout()
        r_layout.setSpacing(8)
        self._r_dedup = QCheckBox("启用基本去重（相似文本合并）")
        self._r_dedup.setChecked(True)
        r_layout.addRow("", self._r_dedup)
        self._r_sim = QDoubleSpinBox()
        self._r_sim.setRange(0.0, 1.0)
        self._r_sim.setSingleStep(0.05)
        self._r_sim.setDecimals(2)
        self._r_sim.setValue(0.9)
        r_layout.addRow("相似度阈值:", self._r_sim)
        self._r_buffer = QSpinBox()
        self._r_buffer.setRange(1, 100)
        self._r_buffer.setValue(5)
        r_layout.addRow("连续缓冲区:", self._r_buffer)
        self._r_min_text = QSpinBox()
        self._r_min_text.setRange(1, 100)
        self._r_min_text.setValue(2)
        r_layout.addRow("最小文字长度:", self._r_min_text)
        self._r_interval = QDoubleSpinBox()
        self._r_interval.setRange(0.1, 60.0)
        self._r_interval.setSingleStep(0.5)
        self._r_interval.setDecimals(1)
        self._r_interval.setValue(2.0)
        self._r_interval.setSuffix(" 秒")
        self._r_interval.setToolTip("每隔多少秒输出一次当前帧的全部识别结果")
        r_layout.addRow("输出间隔:", self._r_interval)
        self._r_group.addLayout(r_layout)
        layout.addWidget(self._r_group)

        # ── ASR 模型配置 ──
        asr_group = CollapsibleGroup(_("ASR 语音识别引擎"))
        asr_form = QFormLayout()
        asr_form.setSpacing(8)
        self._asr_model_dir = QLineEdit("models/asr")
        self._asr_model_dir.setPlaceholderText("留空使用默认缓存")
        asr_form.addRow("模型目录:", self._asr_model_dir)
        self._asr_model = QComboBox()
        self._asr_model.setEditable(False)
        asr_form.addRow("可用模型:", self._asr_model)
        btn_refresh = QPushButton("🔄 刷新模型列表")
        btn_refresh.clicked.connect(self._refresh_asr_models)
        asr_form.addRow("", btn_refresh)
        self._asr_lang = QComboBox()
        self._asr_lang.setEditable(False)
        self._asr_lang.addItems(["auto", "zh", "en", "ja", "ko"])
        self._asr_lang.setCurrentText("zh")
        asr_form.addRow("语言:", self._asr_lang)
        self._asr_region = QLineEdit("语音")
        self._asr_region.setToolTip("ASR 结果在表格中显示的区域名称")
        asr_form.addRow("区域名:", self._asr_region)
        asr_group.addLayout(asr_form)
        layout.addWidget(asr_group)

        # ── 解码参数 ──
        gf = CollapsibleGroup("解码参数", collapsed=True)
        gfl = QFormLayout()
        gfl.setSpacing(6)
        self._asr_beam = QSpinBox()
        self._asr_beam.setRange(1, 20)
        self._asr_beam.setValue(5)
        self._asr_beam.setToolTip("Beam size，越大精度越高但越慢")
        gfl.addRow("Beam Size:", self._asr_beam)
        self._asr_word_ts = QCheckBox("字级时间戳")
        self._asr_word_ts.setChecked(True)
        gfl.addRow("", self._asr_word_ts)
        self._asr_condition = QCheckBox("基于上文条件解码")
        self._asr_condition.setChecked(True)
        gfl.addRow("", self._asr_condition)
        self._asr_no_speech = QDoubleSpinBox()
        self._asr_no_speech.setRange(0.0, 1.0)
        self._asr_no_speech.setSingleStep(0.1)
        self._asr_no_speech.setValue(0.6)
        self._asr_no_speech.setToolTip("越高越容易跳过无声音片段")
        gfl.addRow("无语音阈值:", self._asr_no_speech)
        self._asr_comp_ratio = QDoubleSpinBox()
        self._asr_comp_ratio.setRange(0.0, 10.0)
        self._asr_comp_ratio.setSingleStep(0.1)
        self._asr_comp_ratio.setValue(2.4)
        gfl.addRow("压缩比阈值:", self._asr_comp_ratio)
        self._asr_temp = QLineEdit("0.0,0.2,0.4,0.6,0.8,1.0")
        self._asr_temp.setPlaceholderText("0.0,0.2,0.4,0.6,0.8,1.0")
        self._asr_temp.setToolTip("温度参数（逗号分隔），越低越确定")
        gfl.addRow("温度:", self._asr_temp)
        self._asr_hotwords = QLineEdit()
        self._asr_hotwords.setPlaceholderText("热词，逗号分隔")
        self._asr_hotwords.setToolTip("提升特定词汇的识别率")
        gfl.addRow("热词:", self._asr_hotwords)
        self._asr_prompt = QLineEdit()
        self._asr_prompt.setPlaceholderText("初始提示词，如: 以下是普通话的转录")
        gfl.addRow("初始提示:", self._asr_prompt)
        gf.addLayout(gfl)
        layout.addWidget(gf)

        # ── VAD 参数 ──
        vg = CollapsibleGroup("VAD (语音活动检测)", collapsed=True)
        vgl = QFormLayout()
        vgl.setSpacing(6)
        self._asr_vad = QCheckBox("启用 VAD（跳过静音段）")
        self._asr_vad.setChecked(False)
        self._asr_vad.setToolTip("自动检测并跳过静音部分，加速处理")
        vgl.addRow("", self._asr_vad)
        self._asr_vad_silence = QSpinBox()
        self._asr_vad_silence.setRange(100, 5000)
        self._asr_vad_silence.setSingleStep(100)
        self._asr_vad_silence.setValue(500)
        self._asr_vad_silence.setSuffix(" ms")
        vgl.addRow("最小静音:", self._asr_vad_silence)
        self._asr_vad_thresh = QDoubleSpinBox()
        self._asr_vad_thresh.setRange(0.0, 1.0)
        self._asr_vad_thresh.setSingleStep(0.05)
        self._asr_vad_thresh.setValue(0.5)
        vgl.addRow("VAD 阈值:", self._asr_vad_thresh)
        vg.addLayout(vgl)
        layout.addWidget(vg)

        layout.addStretch()
        return tab

    def _on_subtitle_mode_changed(self, mode: str):
        is_streaming = "流式" in mode
        self._s_group.setVisible(is_streaming)
        self._r_group.setVisible(not is_streaming)

    # faster-whisper 标准模型大小（可自动下载）
    _STANDARD_ASR_MODELS = [
        "tiny", "tiny.en", "base", "base.en", "small", "small.en",
        "medium", "medium.en", "large-v1", "large-v2", "large-v3",
        "distil-small.en", "distil-medium.en", "distil-large-v2",
    ]

    def _refresh_asr_models(self):
        from core.asr_engine import scan_local_asr_models
        model_dir = self._asr_model_dir.text().strip() or "models/asr"
        base = BASE_DIR
        full_dir = str(base / model_dir) if not os.path.isabs(model_dir) else model_dir
        local_models = scan_local_asr_models(full_dir)
        self._asr_model.blockSignals(True)
        self._asr_model.clear()
        # 添加本地已下载的模型（显示完整路径，data 为完整路径）
        for path in local_models:
            display = os.path.basename(path) if os.path.isdir(path) else path
            self._asr_model.addItem(f"📁 {display}", path)
        # 添加标准模型大小（data 为模型名称，首次使用时自动下载）
        for size in self._STANDARD_ASR_MODELS:
            # 跳过已作为本地模型添加的
            if any(os.path.basename(p) == size for p in local_models):
                continue
            self._asr_model.addItem(f"⬇ {size}（在线下载）", size)
        if self._asr_model.count() > 0:
            self._asr_model.setCurrentIndex(0)
        self._asr_model.blockSignals(False)

    @staticmethod
    def _select_combo_by_data(combo: QComboBox, data_value: str):
        """通过 item data 值设置 QComboBox 选中项（而非显示文本）。"""
        for i in range(combo.count()):
            if combo.itemData(i) == data_value:
                combo.setCurrentIndex(i)
                return

    # ── Tab 3: OCR 字幕处理 ──
    def _build_ocr_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 后处理参数 ──
        post_group = CollapsibleGroup(_("后处理参数"))
        pf = QFormLayout()
        pf.setSpacing(8)
        self._post_sim_dedup = QCheckBox("启用相似度去重（合并相似文本）")
        self._post_sim_dedup.setChecked(True)
        pf.addRow("", self._post_sim_dedup)
        self._post_conf_check = QCheckBox("启用置信度过滤（仅 PaddleOCR）")
        self._post_conf_check.setChecked(False)
        pf.addRow("", self._post_conf_check)
        self._post_conf_threshold = QDoubleSpinBox()
        self._post_conf_threshold.setRange(0.0, 1.0)
        self._post_conf_threshold.setSingleStep(0.05)
        self._post_conf_threshold.setDecimals(2)
        self._post_conf_threshold.setValue(0.6)
        pf.addRow("置信度阈值:", self._post_conf_threshold)
        self._post_sim_threshold = QDoubleSpinBox()
        self._post_sim_threshold.setRange(0.0, 1.0)
        self._post_sim_threshold.setSingleStep(0.05)
        self._post_sim_threshold.setDecimals(2)
        self._post_sim_threshold.setValue(0.9)
        pf.addRow("去重相似度阈值:", self._post_sim_threshold)
        self._post_min_text_len = QSpinBox()
        self._post_min_text_len.setRange(1, 100)
        self._post_min_text_len.setValue(2)
        pf.addRow("最小文字长度:", self._post_min_text_len)
        post_group.addLayout(pf)
        layout.addWidget(post_group)

        # ── 关键词过滤 ──
        filter_group = CollapsibleGroup(_("关键词过滤"))
        fl = QVBoxLayout()
        fl.setSpacing(6)

        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("输入要过滤的关键词，回车添加...")
        self._filter_input.returnPressed.connect(self._on_add_filter)
        add_row.addWidget(self._filter_input, 1)
        btn_add = QPushButton(_("➕ 添加"))
        btn_add.clicked.connect(self._on_add_filter)
        add_row.addWidget(btn_add)
        fl.addLayout(add_row)

        self._filter_list = QListWidget()
        self._filter_list.setMinimumHeight(80)
        self._filter_list.setMaximumHeight(180)
        fl.addWidget(self._filter_list)

        filter_btns = QHBoxLayout()
        filter_btns.setSpacing(4)
        btn_del = QPushButton(_("🗑 删除选中"))
        btn_del.clicked.connect(self._on_remove_filter)
        filter_btns.addWidget(btn_del)
        btn_clear = QPushButton(_("清空全部"))
        btn_clear.clicked.connect(self._on_clear_filters)
        filter_btns.addWidget(btn_clear)
        filter_btns.addStretch()
        fl.addLayout(filter_btns)
        filter_group.addLayout(fl)
        layout.addWidget(filter_group)

        layout.addStretch()
        return tab

    def _on_add_filter(self):
        kw = self._filter_input.text().strip()
        if kw and kw not in self._filter_items:
            self._filter_items.append(kw)
            self._filter_list.addItem(kw)
            self._filter_input.clear()

    def _on_remove_filter(self):
        item = self._filter_list.currentItem()
        if item:
            text = item.text()
            if text in self._filter_items:
                self._filter_items.remove(text)
            row = self._filter_list.row(item)
            self._filter_list.takeItem(row)

    def _on_clear_filters(self):
        if QMessageBox.question(self, "确认清空", "确定要清空所有过滤关键词吗？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._filter_items.clear()
            self._filter_list.clear()

    # ── Tab 4: AI 纠错 ──
    def _build_correction_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 行为模式 ──
        mode_group = CollapsibleGroup(_("纠错模式"))
        mf = QVBoxLayout()
        mf.setSpacing(6)
        self._corr_enabled = QCheckBox("启用 AI 纠错")
        self._corr_enabled.setChecked(False)
        self._corr_enabled.setToolTip("总开关：开启后将使用 LLM 对 OCR 结果进行纠错")
        mf.addWidget(self._corr_enabled)
        self._corr_translate = QCheckBox("🌐 翻译模式（将结果翻译为中文）")
        self._corr_translate.setToolTip("开启后 LLM 将把 OCR 结果翻译为中文，纠错提示词仅作参考")
        mf.addWidget(self._corr_translate)
        self._corr_stream = QCheckBox("🔴 流式输出模式（实时逐字显示 API 响应）")
        mf.addWidget(self._corr_stream)
        self._corr_json = QCheckBox("📋 JSON 输出模式（API 返回结构化 JSON）")
        mf.addWidget(self._corr_json)
        self._corr_extract_env = QCheckBox("提取全文环境（领域/氛围/内容摘要作为参考）")
        mf.addWidget(self._corr_extract_env)
        self._corr_polish = QCheckBox("✨ 润色模式（纠错/翻译后二次润色质量）")
        self._corr_polish.setToolTip("开启后 LLM 将对纠错/翻译结果进行二次润色，使表达更自然流畅")
        mf.addWidget(self._corr_polish)
        self._btn_extract_env = QPushButton("🔍 立即提取全文环境")
        self._btn_extract_env.clicked.connect(lambda: self._cp.extract_env_clicked.emit())
        mf.addWidget(self._btn_extract_env)
        self._corr_summary_prompt = QTextEdit()
        self._corr_summary_prompt.setPlaceholderText("点击上方按钮自动提取环境信息，也可手动编辑...")
        self._corr_summary_prompt.setMaximumHeight(80)
        self._corr_summary_prompt.setMinimumHeight(50)
        self._corr_summary_prompt.setToolTip("自动提取的全文环境信息（领域/氛围/摘要），可手动修改，不随设置保存")
        mf.addWidget(self._corr_summary_prompt)
        mode_group.addLayout(mf)
        layout.addWidget(mode_group)

        # ── 提示词配置 ──
        prompt_group = CollapsibleGroup("提示词配置", collapsed=True)
        pf = QFormLayout()
        pf.setSpacing(8)
        self._corr_system_prompt = QTextEdit()
        self._corr_system_prompt.setPlaceholderText("自定义纠错系统提示词（可选）")
        self._corr_system_prompt.setMaximumHeight(100)
        self._corr_system_prompt.setMinimumHeight(60)
        pf.addRow("系统提示词:", self._corr_system_prompt)
        self._corr_prompt = QTextEdit()
        self._corr_prompt.setPlaceholderText("自定义纠错提示词（可选）")
        self._corr_prompt.setMaximumHeight(100)
        self._corr_prompt.setMinimumHeight(60)
        pf.addRow("用户提示词:", self._corr_prompt)
        self._corr_output_format = QLineEdit()
        self._corr_output_format.setPlaceholderText("[纠正后文本]")
        pf.addRow("输出格式:", self._corr_output_format)
        prompt_group.addLayout(pf)
        layout.addWidget(prompt_group)

        # ── 批量参数 ──
        batch_group = CollapsibleGroup(_("批量参数"))
        bf = QFormLayout()
        bf.setSpacing(8)
        self._corr_preset = QComboBox()
        self._corr_preset.setToolTip("选择纠错使用的 API 连接预设")
        from core.api_preset_manager import APIPresetManager
        preset_mgr = APIPresetManager()
        self._corr_preset.addItems(preset_mgr.get_names())
        default_name = preset_mgr.get_default_name()
        if default_name:
            self._corr_preset.setCurrentText(default_name)
        self._corr_preset.currentTextChanged.connect(self._on_preset_changed)
        bf.addRow("API 预设:", self._corr_preset)
        self._corr_batch = QSpinBox()
        self._corr_batch.setRange(1, 50)
        self._corr_batch.setValue(5)
        self._corr_batch.setSuffix(" 条/次")
        bf.addRow("批量条数:", self._corr_batch)
        self._corr_context = QSpinBox()
        self._corr_context.setRange(0, 10)
        self._corr_context.setValue(3)
        self._corr_context.setSuffix(" 条")
        bf.addRow("上下文窗口:", self._corr_context)
        self._corr_retry = QSpinBox()
        self._corr_retry.setRange(0, 10)
        self._corr_retry.setValue(2)
        bf.addRow("失败重试:", self._corr_retry)
        self._corr_concurrency = QSpinBox()
        self._corr_concurrency.setRange(1, 8)
        self._corr_concurrency.setValue(4)
        self._corr_concurrency.setSuffix(" 并发")
        self._corr_concurrency.setToolTip("同时运行的批次数（滑动窗口并发）")
        bf.addRow("并发数:", self._corr_concurrency)
        self._corr_rpm = QSpinBox()
        self._corr_rpm.setRange(0, 120)
        self._corr_rpm.setValue(30)
        self._corr_rpm.setSuffix(" RPM")
        self._corr_rpm.setToolTip("每分钟最大请求数，0 表示不限制")
        bf.addRow("RPM 限制:", self._corr_rpm)
        self._seg_time_gap = QDoubleSpinBox()
        self._seg_time_gap.setRange(0.0, 60.0)
        self._seg_time_gap.setValue(3.0)
        self._seg_time_gap.setSuffix(" 秒")
        self._seg_time_gap.setToolTip("上下文窗口中，跳过时间间隔超过此值的行")
        bf.addRow("上下文时间间隔:", self._seg_time_gap)
        batch_group.addLayout(bf)
        layout.addWidget(batch_group)

        # ── API 连接 ──
        api_group = CollapsibleGroup(_("API 连接"), collapsed=True)
        af = QFormLayout()
        af.setSpacing(6)
        self._corr_api_key = QLineEdit()
        self._corr_api_key.setPlaceholderText(_("sk-xxx（可选）"))
        self._corr_api_key.setEchoMode(QLineEdit.Password)
        self._corr_api_key.setText(self._corr_cfg.get("api_key", ""))
        af.addRow("API Key:", self._corr_api_key)
        self._corr_api_url = QLineEdit()
        self._corr_api_url.setPlaceholderText(_("http://127.0.0.1:8080"))
        self._corr_api_url.setText(self._corr_cfg.get("base_url", "http://127.0.0.1:8080"))
        af.addRow("Base URL:", self._corr_api_url)
        model_row = QHBoxLayout()
        self._corr_api_model = QComboBox()
        self._corr_api_model.setEditable(True)
        self._corr_api_model.setInsertPolicy(QComboBox.NoInsert)
        self._corr_api_model.lineEdit().setPlaceholderText(_("gpt-4o / gemma 等"))
        self._corr_api_model.setEditText(self._corr_cfg.get("model", ""))
        self._corr_api_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        model_row.addWidget(self._corr_api_model, 1)
        self._corr_model_status = QLabel("")
        self._corr_model_status.setMinimumWidth(100)
        btn_corr_models = QPushButton(_("📋 获取模型"))
        btn_corr_models.setToolTip("从 Base URL 获取可用模型列表")
        btn_corr_models.clicked.connect(self._on_fetch_corr_models)
        model_row.addWidget(self._corr_model_status)
        model_row.addWidget(btn_corr_models)
        af.addRow("模型:", model_row)
        self._corr_api_timeout = QSpinBox()
        self._corr_api_timeout.setRange(1, 300)
        self._corr_api_timeout.setValue(self._corr_cfg.get("timeout", 30))
        self._corr_api_timeout.setSuffix(" 秒")
        af.addRow("超时:", self._corr_api_timeout)
        self._corr_api_retry = QSpinBox()
        self._corr_api_retry.setRange(0, 10)
        self._corr_api_retry.setValue(self._corr_cfg.get("retry_on_failure", 2))
        af.addRow("重试次数:", self._corr_api_retry)
        api_group.addLayout(af)
        layout.addWidget(api_group)

        layout.addStretch()
        return tab

    # ── Tab 5: 结果输出 ──
    def _build_sort_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 排序规则 ──
        sort_group = CollapsibleGroup(_("排序规则"))
        sl = QVBoxLayout()
        sl.setSpacing(6)

        hint = QLabel("拖动调整顺序，编辑前缀/后缀，点 ✕ 删除行")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        sl.addWidget(hint)

        self._sort_list = QListWidget()
        self._sort_list.setDragDropMode(QAbstractItemView.InternalMove)
        self._sort_list.setDefaultDropAction(Qt.MoveAction)
        self._sort_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._sort_list.setMinimumHeight(200)
        sl.addWidget(self._sort_list, 1)
        sort_group.addLayout(sl)
        layout.addWidget(sort_group)

        layout.addStretch()
        return tab

    def _add_sort_row(self, name: str, prefix: str = "", suffix: str = ""):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 1, 2, 1)
        row_layout.setSpacing(4)

        prefix_edit = QLineEdit(prefix)
        prefix_edit.setObjectName("sortPrefix")
        prefix_edit.setPlaceholderText("前缀")
        prefix_edit.setMaximumWidth(80)
        row_layout.addWidget(prefix_edit)

        chip = QLabel(name)
        chip.setObjectName("regionChip")
        chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout.addWidget(chip)

        suffix_edit = QLineEdit(suffix)
        suffix_edit.setObjectName("sortSuffix")
        suffix_edit.setPlaceholderText("后缀")
        suffix_edit.setMaximumWidth(80)
        row_layout.addWidget(suffix_edit)

        btn_x = QPushButton("✕")
        btn_x.setMaximumWidth(22)
        btn_x.setMaximumHeight(22)
        btn_x.clicked.connect(lambda: self._remove_sort_item(row))
        row_layout.addWidget(btn_x)
        row_layout.addStretch()

        item = QListWidgetItem()
        item.setSizeHint(row.sizeHint())
        self._sort_list.addItem(item)
        self._sort_list.setItemWidget(item, row)

    def _remove_sort_item(self, row: QWidget):
        for i in range(self._sort_list.count()):
            if self._sort_list.itemWidget(self._sort_list.item(i)) is row:
                info = self._get_sort_row_info(row)
                if info in self._sort_items:
                    self._sort_items.remove(info)
                self._sort_list.takeItem(i)
                break

    def _get_sort_row_info(self, row: QWidget):
        prefix_edit = row.findChild(QLineEdit, "sortPrefix")
        suffix_edit = row.findChild(QLineEdit, "sortSuffix")
        chip = row.findChild(QLabel, "regionChip")
        if chip:
            prefix = prefix_edit.text().strip() if prefix_edit else ""
            name = chip.text()
            suffix = suffix_edit.text().strip() if suffix_edit else ""
            return (prefix, name, suffix)
        return ("", "", "")

    def _collect_sort_items(self):
        """收集排序列表中的当前数据。"""
        self._sort_items.clear()
        for i in range(self._sort_list.count()):
            row = self._sort_list.itemWidget(self._sort_list.item(i))
            if row:
                info = self._get_sort_row_info(row)
                if info[1]:
                    self._sort_items.append(info)

    # ── API ──
    def _on_preset_changed(self, name: str):
        """预设切换时回填 API 连接字段。"""
        if not name:
            return
        from core.api_preset_manager import APIPresetManager
        preset = APIPresetManager().get_preset(name)
        if not preset:
            return
        self._corr_api_key.setText(preset.get("api_key", ""))
        self._corr_api_url.setText(preset.get("base_url", "http://127.0.0.1:8080"))
        self._corr_api_model.setEditText(preset.get("model", ""))
        self._corr_api_timeout.setValue(preset.get("timeout", 30))

    def _sync_preset(self):
        """将当前 API 连接字段回写到选中预设。"""
        from core.api_preset_manager import APIPresetManager
        preset_name = self._corr_preset.currentText()
        if preset_name:
            APIPresetManager().update_preset(preset_name, {
                "api_key": self._corr_api_key.text(),
                "base_url": self._corr_api_url.text(),
                "model": self._corr_api_model.currentText(),
                "timeout": self._corr_api_timeout.value(),
            })

    def get_corr_api_config(self) -> dict:
        """获取 API 连接配置（纯读取）。"""
        return {
            "enabled": self._corr_enabled.isChecked(),
            "api_key": self._corr_api_key.text(),
            "base_url": self._corr_api_url.text(),
            "model": self._corr_api_model.currentText(),
            "timeout": self._corr_api_timeout.value(),
            "retry_on_failure": self._corr_api_retry.value(),
            "summary_prompt": self._corr_summary_prompt.toPlainText(),
            "correction_system_prompt": self._corr_system_prompt.toPlainText(),
            "output_format": self._corr_output_format.text(),
        }

    def _on_fetch_corr_models(self):
        """从纠错 API 连接的 Base URL 获取可用模型列表。"""
        base_url = self._corr_api_url.text().strip()
        if not base_url:
            self._corr_model_status.setText("⚠ 请先输入 Base URL")
            return
        self._corr_model_status.setText("⏳ 获取中...")

        import threading

        class _FetchBridge(QObject):
            done = pyqtSignal(object)
            err = pyqtSignal(str)

        bridge = _FetchBridge()
        bridge.done.connect(self._on_corr_fetch_done)
        bridge.err.connect(lambda msg: self._corr_model_status.setText(f"❌ {msg[:20]}"))
        api_key = self._corr_api_key.text()

        def _fetch():
            try:
                models = fetch_models_from_url(base_url, api_key)
                bridge.done.emit(models)
            except Exception as e:
                bridge.err.emit(str(e))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_corr_fetch_done(self, models):
        if models:
            self._set_corr_model_list(models)
            self._corr_model_status.setText(f"✅ {len(models)} 个")
        else:
            self._corr_model_status.setText("⚠ 未获取到模型")

    def _set_corr_model_list(self, models: list[str]):
        """填充纠错模型下拉列表。"""
        populate_model_combo(self._corr_api_model, models)

    def _on_accept(self):
        """确认时：同步数据到 ConfigPanel 并触发应用。"""
        self._sync_values_to_cp()
        self._sync_preset()
        self._cp.set_polish_enabled(self._corr_polish.isChecked())
        self.accept()
