# -*- coding: utf-8 -*-
"""参数设置对话框 —— 自身创建所有控件，与 ConfigPanel 通过数据同步。"""

import os, json
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QFormLayout, QComboBox, QTextEdit, QCheckBox,
    QSpinBox, QDoubleSpinBox, QLineEdit, QMessageBox,
    QListWidget, QListWidgetItem, QScrollArea,
    QSizePolicy, QFrame, QDialogButtonBox, QWidget, QAbstractItemView,
)
from PyQt5.QtCore import Qt, pyqtSignal
from typing import List

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.collapsible_group import CollapsibleGroup


def _safe_val(widget, default=None):
    """安全读取 widget 值（widget 可能已销毁）。"""
    try:
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText() or (default or "")
        if isinstance(widget, QLineEdit):
            return widget.text().strip() or (default or "")
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        return default
    except RuntimeError:
        return default


def _safe_set(widget, value, setter=None):
    """安全设置 widget 值。"""
    try:
        if setter:
            setter(value)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            idx = widget.findText(str(value))
            if idx >= 0:
                widget.setCurrentIndex(idx)
            elif widget.count() > 0:
                widget.setCurrentText(str(value))
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value) if value else "")
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(value) if value else 0)
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value) if value else 0.0)
        elif isinstance(widget, QTextEdit):
            widget.setPlainText(str(value) if value else "")
    except RuntimeError:
        pass


class SettingsDialog(QDialog):
    """参数设置对话框，集中管理处理参数 + 纠错 API 配置。"""

    def __init__(self, config_panel, correction_config: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙ 参数设置")
        self.setMinimumSize(760, 620)
        self.setObjectName("settingsDialog")
        self._cp = config_panel
        self._corr_cfg = correction_config or {}
        self._sort_items: list = []
        self._filter_items: list = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

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
        """从 ConfigPanel 读取所有参数初始值。"""
        cp = self._cp
        # ── 基础设置 ──
        _safe_set(self._frame_interval, _safe_val(cp._frame_interval_spin, 0.1))
        _safe_set(self._process_mode, _safe_val(cp._process_mode_combo, "OCR + ASR（完整流程）"))
        _safe_set(self._hw_accel, _safe_val(cp._hw_accel_check, False))
        _safe_set(self._subtitle_duration, _safe_val(cp._subtitle_duration_spin, 3.0))
        _safe_set(self._srt_export, _safe_val(cp._srt_export_combo, "仅纠正结果"))
        _safe_set(self._ocr_retry, _safe_val(cp._ocr_retry_spin, 2))
        _safe_set(self._ocr_timeout, _safe_val(cp._ocr_timeout_spin, 60))
        _safe_set(self._post_sim_dedup, _safe_val(cp._post_sim_dedup, True))
        _safe_set(self._post_keep_longest, _safe_val(cp._post_keep_longest, False))
        _safe_set(self._corr_enabled, _safe_val(cp._corr_enabled_check, False))

        # ── 字幕模式 ──
        subtitle_mode = _safe_val(cp._subtitle_mode_combo, "流式字幕（去重）")
        _safe_set(self._subtitle_mode, subtitle_mode)
        self._on_subtitle_mode_changed(subtitle_mode)

        # ── 流式参数 ──
        _safe_set(self._s_sentinel, _safe_val(cp._s_sentinel_check, True))
        _safe_set(self._s_drop_ratio, _safe_val(cp._s_drop_ratio_spin, 0.5))
        _safe_set(self._s_buffer, _safe_val(cp._s_buffer_spin, 8))
        _safe_set(self._s_sim, _safe_val(cp._s_sim_spin, 0.85))
        _safe_set(self._s_min_text, _safe_val(cp._s_min_text_spin, 2))
        _safe_set(self._s_filter, _safe_val(cp._s_filter_edit, ""))

        # ── 常规参数 ──
        _safe_set(self._r_dedup, _safe_val(cp._r_dedup_check, True))
        _safe_set(self._r_sim, _safe_val(cp._r_sim_spin, 0.9))
        _safe_set(self._r_buffer, _safe_val(cp._r_buffer_spin, 5))
        _safe_set(self._r_min_text, _safe_val(cp._r_min_text_spin, 2))
        _safe_set(self._r_filter, _safe_val(cp._r_filter_edit, ""))
        _safe_set(self._r_interval, _safe_val(cp._r_interval_spin, 2.0))

        # ── 后处理 ──
        _safe_set(self._post_conf_check, _safe_val(cp._post_conf_check, False))
        _safe_set(self._post_conf_threshold, _safe_val(cp._post_conf_threshold, 0.6))
        _safe_set(self._post_sim_threshold, _safe_val(cp._post_sim_threshold, 0.9))
        _safe_set(self._post_min_text_len, _safe_val(cp._post_min_text_len, 2))

        # ── 过滤器 ──
        self._filter_items.clear()
        self._filter_list.clear()
        try:
            for i in range(cp._filter_list.count()):
                item = cp._filter_list.item(i)
                if item:
                    self._filter_items.append(item.text())
                    self._filter_list.addItem(item.text())
        except RuntimeError:
            pass

        # ── AI 纠错 ──
        _safe_set(self._corr_translate, _safe_val(cp._corr_translate_check, False))
        _safe_set(self._corr_stream, _safe_val(cp._corr_stream_check, False))
        _safe_set(self._corr_json, _safe_val(cp._corr_json_check, False))
        _safe_set(self._corr_extract_env, _safe_val(cp._corr_extract_env_check, False))
        _safe_set(self._corr_summary_prompt, _safe_val(cp._corr_summary_prompt_text, ""))
        _safe_set(self._corr_system_prompt, _safe_val(cp._corr_system_prompt_text, ""))
        _safe_set(self._corr_output_format, _safe_val(cp._corr_output_format_edit, ""))
        _safe_set(self._corr_preset, _safe_val(cp._corr_preset_combo, ""))
        _safe_set(self._corr_batch, _safe_val(cp._corr_batch_spin, 5))
        _safe_set(self._corr_context, _safe_val(cp._corr_context_spin, 3))
        _safe_set(self._corr_retry, _safe_val(cp._corr_retry_spin, 2))
        _safe_set(self._corr_prompt, _safe_val(cp._corr_prompt_text, ""))

        # ── ASR ──
        _safe_set(self._asr_model_dir, _safe_val(cp._asr_model_dir_edit, "models/asr"))
        self._refresh_asr_models()
        model_size = _safe_val(cp._asr_model_combo, "")
        if model_size:
            _safe_set(self._asr_model, model_size)
        _safe_set(self._asr_lang, _safe_val(cp._asr_lang_combo, "zh"))
        _safe_set(self._asr_beam, _safe_val(cp._asr_beam_spin, 5))
        _safe_set(self._asr_word_ts, _safe_val(cp._asr_word_ts_check, True))
        _safe_set(self._asr_condition, _safe_val(cp._asr_condition_check, True))
        _safe_set(self._asr_no_speech, _safe_val(cp._asr_no_speech_spin, 0.6))
        _safe_set(self._asr_comp_ratio, _safe_val(cp._asr_comp_ratio_spin, 2.4))
        _safe_set(self._asr_temp, _safe_val(cp._asr_temp_edit, "0.0,0.2,0.4,0.6,0.8,1.0"))
        _safe_set(self._asr_hotwords, _safe_val(cp._asr_hotwords_edit, ""))
        _safe_set(self._asr_prompt, _safe_val(cp._asr_prompt_edit, ""))
        _safe_set(self._asr_vad, _safe_val(cp._asr_vad_check, False))
        _safe_set(self._asr_vad_silence, _safe_val(cp._asr_vad_silence_spin, 500))
        _safe_set(self._asr_vad_thresh, _safe_val(cp._asr_vad_thresh_spin, 0.5))
        _safe_set(self._asr_region, _safe_val(cp._asr_region_edit, "语音"))

        # ── 排序 ──
        self._sort_items.clear()
        self._sort_list.clear()
        try:
            for i in range(cp._sort_list.count()):
                item = cp._sort_list.item(i)
                widget = cp._sort_list.itemWidget(item)
                if widget:
                    children = widget.findChildren((QLineEdit, QLabel))
                    if len(children) >= 2:
                        prefix = children[0].text().strip() if isinstance(children[0], QLineEdit) else ""
                        name = children[1].text() if isinstance(children[1], QLabel) else ""
                        suffix = children[2].text().strip() if len(children) > 2 and isinstance(children[2], QLineEdit) else ""
                        self._sort_items.append((prefix, name, suffix))
                        self._add_sort_row(name, prefix, suffix)
        except RuntimeError:
            pass

    def _sync_values_to_cp(self):
        """将对话框中的值写回 ConfigPanel 的控件。"""
        cp = self._cp
        _safe_set(cp._frame_interval_spin, _safe_val(self._frame_interval, 0.1))
        _safe_set(cp._process_mode_combo, _safe_val(self._process_mode, "OCR + ASR（完整流程）"))
        _safe_set(cp._hw_accel_check, _safe_val(self._hw_accel, False))
        _safe_set(cp._subtitle_duration_spin, _safe_val(self._subtitle_duration, 3.0))
        _safe_set(cp._srt_export_combo, _safe_val(self._srt_export, "仅纠正结果"))
        _safe_set(cp._ocr_retry_spin, _safe_val(self._ocr_retry, 2))
        _safe_set(cp._ocr_timeout_spin, _safe_val(self._ocr_timeout, 60))
        _safe_set(cp._post_sim_dedup, _safe_val(self._post_sim_dedup, True))
        _safe_set(cp._post_keep_longest, _safe_val(self._post_keep_longest, False))
        _safe_set(cp._corr_enabled_check, _safe_val(self._corr_enabled, False))

        _safe_set(cp._subtitle_mode_combo, _safe_val(self._subtitle_mode, "流式字幕（去重）"))
        _safe_set(cp._s_sentinel_check, _safe_val(self._s_sentinel, True))
        _safe_set(cp._s_drop_ratio_spin, _safe_val(self._s_drop_ratio, 0.5))
        _safe_set(cp._s_buffer_spin, _safe_val(self._s_buffer, 8))
        _safe_set(cp._s_sim_spin, _safe_val(self._s_sim, 0.85))
        _safe_set(cp._s_min_text_spin, _safe_val(self._s_min_text, 2))
        _safe_set(cp._s_filter_edit, _safe_val(self._s_filter, ""))
        _safe_set(cp._r_dedup_check, _safe_val(self._r_dedup, True))
        _safe_set(cp._r_sim_spin, _safe_val(self._r_sim, 0.9))
        _safe_set(cp._r_buffer_spin, _safe_val(self._r_buffer, 5))
        _safe_set(cp._r_min_text_spin, _safe_val(self._r_min_text, 2))
        _safe_set(cp._r_filter_edit, _safe_val(self._r_filter, ""))
        _safe_set(cp._r_interval_spin, _safe_val(self._r_interval, 2.0))

        _safe_set(cp._post_conf_check, _safe_val(self._post_conf_check, False))
        _safe_set(cp._post_conf_threshold, _safe_val(self._post_conf_threshold, 0.6))
        _safe_set(cp._post_sim_threshold, _safe_val(self._post_sim_threshold, 0.9))
        _safe_set(cp._post_min_text_len, _safe_val(self._post_min_text_len, 2))

        # 同步过滤器
        try:
            cp._filter_list.clear()
            for kw in self._filter_items:
                cp._filter_list.addItem(kw)
        except RuntimeError:
            pass

        _safe_set(cp._corr_translate_check, _safe_val(self._corr_translate, False))
        _safe_set(cp._corr_stream_check, _safe_val(self._corr_stream, False))
        _safe_set(cp._corr_json_check, _safe_val(self._corr_json, False))
        _safe_set(cp._corr_extract_env_check, _safe_val(self._corr_extract_env, False))
        _safe_set(cp._corr_summary_prompt_text, _safe_val(self._corr_summary_prompt, ""))
        _safe_set(cp._corr_system_prompt_text, _safe_val(self._corr_system_prompt, ""))
        _safe_set(cp._corr_output_format_edit, _safe_val(self._corr_output_format, ""))
        _safe_set(cp._corr_preset_combo, _safe_val(self._corr_preset, ""))
        _safe_set(cp._corr_batch_spin, _safe_val(self._corr_batch, 5))
        _safe_set(cp._corr_context_spin, _safe_val(self._corr_context, 3))
        _safe_set(cp._corr_retry_spin, _safe_val(self._corr_retry, 2))
        _safe_set(cp._corr_prompt_text, _safe_val(self._corr_prompt, ""))

        _safe_set(cp._asr_model_dir_edit, _safe_val(self._asr_model_dir, "models/asr"))
        _safe_set(cp._asr_model_combo, _safe_val(self._asr_model, ""))
        _safe_set(cp._asr_lang_combo, _safe_val(self._asr_lang, "zh"))
        _safe_set(cp._asr_beam_spin, _safe_val(self._asr_beam, 5))
        _safe_set(cp._asr_word_ts_check, _safe_val(self._asr_word_ts, True))
        _safe_set(cp._asr_condition_check, _safe_val(self._asr_condition, True))
        _safe_set(cp._asr_no_speech_spin, _safe_val(self._asr_no_speech, 0.6))
        _safe_set(cp._asr_comp_ratio_spin, _safe_val(self._asr_comp_ratio, 2.4))
        _safe_set(cp._asr_temp_edit, _safe_val(self._asr_temp, "0.0,0.2,0.4,0.6,0.8,1.0"))
        _safe_set(cp._asr_hotwords_edit, _safe_val(self._asr_hotwords, ""))
        _safe_set(cp._asr_prompt_edit, _safe_val(self._asr_prompt, ""))
        _safe_set(cp._asr_vad_check, _safe_val(self._asr_vad, False))
        _safe_set(cp._asr_vad_silence_spin, _safe_val(self._asr_vad_silence, 500))
        _safe_set(cp._asr_vad_thresh_spin, _safe_val(self._asr_vad_thresh, 0.5))
        _safe_set(cp._asr_region_edit, _safe_val(self._asr_region, "语音"))

        # 同步排序
        try:
            cp._sort_list.clear()
            for prefix, name, suffix in self._sort_items:
                cp._add_sort_row(name, prefix, suffix)
        except RuntimeError:
            pass

    # ── helpers ──
    def _wrap_scroll(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

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
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 处理模式组 ──
        mode_group = CollapsibleGroup("处理模式")
        mf = QFormLayout()
        mf.setSpacing(6)
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
        self._hw_accel = QCheckBox("硬件加速 (GPU)")
        self._hw_accel.setToolTip("FFmpeg 视频解码 + PaddleOCR 均启用 GPU 加速")
        self._hw_accel.setChecked(False)
        mf.addRow("", self._hw_accel)
        mode_group.addLayout(mf)
        layout.addWidget(mode_group)

        # ── 输出控制组 ──
        out_group = CollapsibleGroup("输出控制")
        of = QFormLayout()
        of.setSpacing(6)
        self._subtitle_duration = QDoubleSpinBox()
        self._subtitle_duration.setRange(0.5, 30.0)
        self._subtitle_duration.setSingleStep(0.5)
        self._subtitle_duration.setValue(3.0)
        self._subtitle_duration.setSuffix(" 秒")
        of.addRow("字幕时长:", self._subtitle_duration)
        self._srt_export = QComboBox()
        self._srt_export.addItems(["仅纠正结果", "仅原文", "双语对照（原文+纠正）"])
        self._srt_export.setToolTip("SRT 导出时的字幕内容模式")
        of.addRow("SRT 导出:", self._srt_export)
        out_group.addLayout(of)
        layout.addWidget(out_group)

        # ── 后处理组 ──
        post_group = CollapsibleGroup("后处理")
        pf = QFormLayout()
        pf.setSpacing(6)
        self._post_sim_dedup = QCheckBox("后处理相似度去重")
        self._post_sim_dedup.setChecked(True)
        pf.addRow("", self._post_sim_dedup)
        self._post_keep_longest = QCheckBox("保留最长文本")
        self._post_keep_longest.setChecked(False)
        pf.addRow("", self._post_keep_longest)
        self._corr_enabled = QCheckBox("启用 AI 纠错")
        self._corr_enabled.setChecked(False)
        pf.addRow("", self._corr_enabled)
        post_group.addLayout(pf)
        layout.addWidget(post_group)

        # ── 容错重试组 ──
        retry_group = CollapsibleGroup("容错重试")
        rf = QFormLayout()
        rf.setSpacing(6)
        self._ocr_retry = QSpinBox()
        self._ocr_retry.setRange(0, 10)
        self._ocr_retry.setValue(2)
        self._ocr_retry.setToolTip("API OCR 引擎识别失败时的最大重试次数")
        rf.addRow("OCR 重试次数:", self._ocr_retry)
        self._ocr_timeout = QSpinBox()
        self._ocr_timeout.setRange(5, 300)
        self._ocr_timeout.setValue(60)
        self._ocr_timeout.setSuffix(" 秒")
        rf.addRow("OCR 超时:", self._ocr_timeout)
        retry_group.addLayout(rf)
        layout.addWidget(retry_group)

        layout.addStretch()
        return tab

    # ── Tab 2: 语音识别 ──
    def _build_asr_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 字幕模式选择 ──
        mode_group = CollapsibleGroup("字幕模式")
        mode_form = QFormLayout()
        mode_form.setSpacing(6)
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
        s_layout.setSpacing(6)
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
        self._s_filter = QLineEdit()
        self._s_filter.setPlaceholderText("过滤关键词，逗号分隔（可选）")
        s_layout.addRow("过滤关键词:", self._s_filter)
        self._s_group.addLayout(s_layout)
        layout.addWidget(self._s_group)

        # ── 常规参数组 ──
        self._r_group = CollapsibleGroup("常规参数（固定间隔）")
        r_layout = QFormLayout()
        r_layout.setSpacing(6)
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
        self._r_filter = QLineEdit()
        self._r_filter.setPlaceholderText("过滤关键词，逗号分隔（可选）")
        r_layout.addRow("过滤关键词:", self._r_filter)
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
        asr_group = CollapsibleGroup("ASR 语音识别引擎")
        asr_form = QFormLayout()
        asr_form.setSpacing(6)
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

    def _refresh_asr_models(self):
        from core.asr_engine import scan_local_asr_models
        model_dir = self._asr_model_dir.text().strip() or "models/asr"
        base = BASE_DIR
        full_dir = str(base / model_dir) if not os.path.isabs(model_dir) else model_dir
        models = scan_local_asr_models(full_dir)
        self._asr_model.blockSignals(True)
        self._asr_model.clear()
        if models:
            for path in models:
                display = os.path.basename(path) if os.path.isdir(path) else path
                self._asr_model.addItem(display, path)
            self._asr_model.setCurrentIndex(0)
        else:
            self._asr_model.addItem("（未找到本地模型，使用默认 large-v3）")
        self._asr_model.blockSignals(False)

    # ── Tab 3: OCR 字幕处理 ──
    def _build_ocr_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 后处理参数 ──
        post_group = CollapsibleGroup("后处理参数")
        pf = QFormLayout()
        pf.setSpacing(6)
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
        filter_group = CollapsibleGroup("关键词过滤")
        fl = QVBoxLayout()
        fl.setSpacing(6)

        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("输入要过滤的关键词，回车添加...")
        self._filter_input.returnPressed.connect(self._on_add_filter)
        add_row.addWidget(self._filter_input, 1)
        btn_add = QPushButton("➕ 添加")
        btn_add.clicked.connect(self._on_add_filter)
        add_row.addWidget(btn_add)
        fl.addLayout(add_row)

        self._filter_list = QListWidget()
        self._filter_list.setMinimumHeight(80)
        self._filter_list.setMaximumHeight(180)
        fl.addWidget(self._filter_list)

        filter_btns = QHBoxLayout()
        filter_btns.setSpacing(4)
        btn_del = QPushButton("🗑 删除选中")
        btn_del.clicked.connect(self._on_remove_filter)
        filter_btns.addWidget(btn_del)
        btn_clear = QPushButton("清空全部")
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
        if kw:
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
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 行为模式 ──
        mode_group = CollapsibleGroup("纠错模式")
        mf = QVBoxLayout()
        mf.setSpacing(4)
        self._corr_translate = QCheckBox("🌐 翻译模式（将结果翻译为中文）")
        self._corr_translate.setToolTip("开启后 LLM 将把 OCR 结果翻译为中文，纠错提示词仅作参考")
        mf.addWidget(self._corr_translate)
        self._corr_stream = QCheckBox("🔴 流式输出模式（实时逐字显示 API 响应）")
        mf.addWidget(self._corr_stream)
        self._corr_json = QCheckBox("📋 JSON 输出模式（API 返回结构化 JSON）")
        mf.addWidget(self._corr_json)
        self._corr_extract_env = QCheckBox("提取全文环境（领域/氛围/内容摘要作为参考）")
        mf.addWidget(self._corr_extract_env)
        self._btn_extract_env = QPushButton("🔍 立即提取全文环境")
        self._btn_extract_env.clicked.connect(lambda: self._cp.extract_env_clicked.emit())
        mf.addWidget(self._btn_extract_env)
        mode_group.addLayout(mf)
        layout.addWidget(mode_group)

        # ── 提示词配置 ──
        prompt_group = CollapsibleGroup("提示词配置", collapsed=True)
        pf = QFormLayout()
        pf.setSpacing(6)
        self._corr_summary_prompt = QTextEdit()
        self._corr_summary_prompt.setPlaceholderText("自定义全文总结/概括提示词（可选）")
        self._corr_summary_prompt.setMaximumHeight(100)
        self._corr_summary_prompt.setMinimumHeight(60)
        pf.addRow("总结提示词:", self._corr_summary_prompt)
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
        batch_group = CollapsibleGroup("批量参数")
        bf = QFormLayout()
        bf.setSpacing(6)
        self._corr_preset = QComboBox()
        self._corr_preset.setToolTip("选择纠错使用的 API 连接预设")
        from core.api_preset_manager import APIPresetManager
        self._corr_preset.addItems(APIPresetManager().get_names())
        default_name = APIPresetManager().get_default_name()
        if default_name:
            self._corr_preset.setCurrentText(default_name)
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
        batch_group.addLayout(bf)
        layout.addWidget(batch_group)

        # ── API 连接 ──
        api_group = CollapsibleGroup("API 连接", collapsed=True)
        af = QFormLayout()
        af.setSpacing(6)
        self._corr_api_key = QLineEdit()
        self._corr_api_key.setPlaceholderText("sk-xxx（可选）")
        self._corr_api_key.setText(self._corr_cfg.get("api_key", ""))
        af.addRow("API Key:", self._corr_api_key)
        self._corr_api_url = QLineEdit()
        self._corr_api_url.setPlaceholderText("http://127.0.0.1:8080")
        self._corr_api_url.setText(self._corr_cfg.get("base_url", "http://127.0.0.1:8080"))
        af.addRow("Base URL:", self._corr_api_url)
        self._corr_api_model = QLineEdit()
        self._corr_api_model.setPlaceholderText("gpt-4o / gemma 等")
        self._corr_api_model.setText(self._corr_cfg.get("model", ""))
        af.addRow("模型:", self._corr_api_model)
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
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 排序规则 ──
        sort_group = CollapsibleGroup("排序规则")
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
        row_layout.setContentsMargins(2, 1, 2, 1); row_layout.setSpacing(4)

        prefix_edit = QLineEdit(prefix)
        prefix_edit.setPlaceholderText("前缀"); prefix_edit.setMaximumWidth(80)
        row_layout.addWidget(prefix_edit)

        chip = QLabel(name)
        chip.setObjectName("regionChip")
        chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout.addWidget(chip)

        suffix_edit = QLineEdit(suffix)
        suffix_edit.setPlaceholderText("后缀"); suffix_edit.setMaximumWidth(80)
        row_layout.addWidget(suffix_edit)

        btn_x = QPushButton("✕"); btn_x.setMaximumWidth(22); btn_x.setMaximumHeight(22)
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
        children = row.findChildren((QLineEdit, QLabel))
        if len(children) >= 2:
            prefix = children[0].text().strip() if isinstance(children[0], QLineEdit) else ""
            name = children[1].text() if isinstance(children[1], QLabel) else ""
            suffix = children[2].text().strip() if len(children) > 2 and isinstance(children[2], QLineEdit) else ""
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
    def get_corr_api_config(self) -> dict:
        return {
            "enabled": self._corr_enabled.isChecked(),
            "api_key": self._corr_api_key.text(),
            "base_url": self._corr_api_url.text(),
            "model": self._corr_api_model.text(),
            "timeout": self._corr_api_timeout.value(),
            "retry_on_failure": self._corr_api_retry.value(),
            "summary_prompt": self._corr_summary_prompt.toPlainText(),
            "correction_system_prompt": self._corr_system_prompt.toPlainText(),
            "output_format": self._corr_output_format.text(),
        }

    def _on_accept(self):
        """确认时：同步数据到 ConfigPanel 并触发应用。"""
        self._collect_sort_items()
        self._sync_values_to_cp()
        self._cp._on_apply_mode()
        self.accept()
