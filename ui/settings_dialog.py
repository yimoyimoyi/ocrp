# -*- coding: utf-8 -*-
"""参数设置对话框 —— 整合处理参数 + 纠错 API 配置。"""

import os, json
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QFormLayout, QComboBox, QTextEdit, QCheckBox,
    QSpinBox, QDoubleSpinBox, QLineEdit, QMessageBox,
    QListWidget, QListWidgetItem, QScrollArea, QGroupBox,
    QSizePolicy, QFrame, QDialogButtonBox, QWidget, QAbstractItemView,
)
from PyQt5.QtCore import Qt, pyqtSignal
from typing import List

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SettingsDialog(QDialog):
    """参数设置对话框，集中管理处理参数 + 纠错 API 配置。"""

    def __init__(self, config_panel, correction_config: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙ 参数设置")
        self.setMinimumSize(600, 540)
        self._cp = config_panel
        self._corr_cfg = correction_config or {}
        self._borrowed: list = []  # 记录从 ConfigPanel 借用的控件

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs, 1)

        self._build_tabs()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _wrap_scroll(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    def _borrow(self, widget):
        """记录从 ConfigPanel 借用的 widget，用于关闭时归还。"""
        if isinstance(widget, QWidget):
            self._borrowed.append(widget)

    def _build_tabs(self):
        cp = self._cp

        # ── Tab 1: 基础设置（全局核心参数）──
        tab1 = QWidget(); t1 = QFormLayout(tab1); t1.setSpacing(4)
        self._add_basic_fields(t1, cp)
        self._tabs.addTab(self._wrap_scroll(tab1), "基础设置")

        # ── Tab 2: 语音识别（ASR + 字幕模式）──
        tab2 = QWidget(); t2 = QVBoxLayout(tab2); t2.setSpacing(4)
        self._add_asr_fields(t2, cp)
        self._tabs.addTab(self._wrap_scroll(tab2), "语音识别")

        # ── Tab 3: OCR 字幕处理（后处理 + 过滤 + 排序）──
        tab3 = QWidget(); t3 = QVBoxLayout(tab3); t3.setSpacing(4)
        self._add_ocr_fields(t3, cp)
        self._tabs.addTab(self._wrap_scroll(tab3), "OCR 字幕处理")

        # ── Tab 4: AI 纠错（行为 + API 连接 + 提示词）──
        tab4 = QWidget(); t4 = QFormLayout(tab4); t4.setSpacing(4)
        self._add_correction_fields(t4, cp)
        self._tabs.addTab(self._wrap_scroll(tab4), "AI 纠错")

        # ── Tab 5: 结果输出（排序规则）──
        tab5 = QWidget(); t5 = QVBoxLayout(tab5); t5.setSpacing(4)
        self._add_sort_fields(t5, cp)
        self._tabs.addTab(self._wrap_scroll(tab5), "结果输出")

    def _add_basic_fields(self, layout, cp):
        for w in (cp._frame_interval_spin, cp._process_mode_combo,
                  cp._hw_accel_check, cp._subtitle_duration_spin,
                  cp._srt_export_combo):
            self._borrow(w)
        layout.addRow("帧间隔:", cp._frame_interval_spin)
        layout.addRow("处理模式:", cp._process_mode_combo)
        layout.addRow("", cp._hw_accel_check)

        sep1 = QLabel("── 字幕输出 ──"); sep1.setStyleSheet("color: #888;")
        layout.addRow("", sep1)
        layout.addRow("字幕时长:", cp._subtitle_duration_spin)
        layout.addRow("SRT 导出:", cp._srt_export_combo)

        sep2 = QLabel("── 流程失败重试 ──"); sep2.setStyleSheet("color: #888;")
        layout.addRow("", sep2)
        for w in (cp._ocr_retry_spin, cp._ocr_timeout_spin):
            self._borrow(w)
        layout.addRow("OCR 重试次数:", cp._ocr_retry_spin)
        layout.addRow("OCR 超时:", cp._ocr_timeout_spin)

    def _add_sentinel_fields(self, layout, cp):
        self._borrow(cp._subtitle_mode_combo)
        self._borrow(cp._s_group)
        self._borrow(cp._r_group)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("字幕模式:"))
        mode_row.addWidget(cp._subtitle_mode_combo, 1)
        layout.addLayout(mode_row)
        layout.addWidget(cp._s_group)
        layout.addWidget(cp._r_group)

    def _add_postprocess_fields(self, layout, cp):
        for w in (cp._post_conf_check, cp._post_conf_threshold,
                  cp._post_sim_threshold, cp._post_min_text_len,
                  cp._filter_input, cp._btn_filter_add,
                  cp._filter_list, cp._btn_filter_del, cp._btn_filter_clear):
            self._borrow(w)
        layout.addRow("", cp._post_conf_check)
        layout.addRow("置信度阈值:", cp._post_conf_threshold)
        layout.addRow("去重相似度阈值:", cp._post_sim_threshold)
        layout.addRow("最小文字长度:", cp._post_min_text_len)

        sep = QLabel("── 关键词过滤 ──"); sep.setStyleSheet("color: #888;")
        layout.addRow("", sep)
        add_row = QHBoxLayout(); add_row.setSpacing(4)
        add_row.addWidget(cp._filter_input, 1)
        add_row.addWidget(cp._btn_filter_add)
        layout.addRow("", add_row)
        layout.addRow("", cp._filter_list)
        filter_btns = QHBoxLayout()
        filter_btns.addWidget(cp._btn_filter_del)
        filter_btns.addWidget(cp._btn_filter_clear)
        filter_btns.addStretch()
        layout.addRow("", filter_btns)

    def _add_sort_fields(self, layout, cp):
        self._borrow(cp._sort_list)
        layout.addWidget(QLabel("排序规则（可拖动调整顺序）:"))
        layout.addWidget(cp._sort_list, 1)

    def _add_ocr_fields(self, layout, cp):
        # ── 后处理 ──
        for w in (cp._post_conf_check, cp._post_conf_threshold,
                  cp._post_sim_threshold, cp._post_min_text_len,
                  cp._filter_input, cp._btn_filter_add,
                  cp._filter_list, cp._btn_filter_del, cp._btn_filter_clear,
                  cp._post_sim_dedup, cp._post_keep_longest):
            self._borrow(w)

        layout.addWidget(QLabel("OCR 后处理与过滤规则："))
        form = QFormLayout(); form.setSpacing(4)
        form.addRow("", cp._post_sim_dedup)
        form.addRow("", cp._post_keep_longest)
        form.addRow("", cp._post_conf_check)
        form.addRow("置信度阈值:", cp._post_conf_threshold)
        form.addRow("去重相似度阈值:", cp._post_sim_threshold)
        form.addRow("最小文字长度:", cp._post_min_text_len)
        layout.addLayout(form)

        sep = QLabel("── 关键词过滤 ──"); sep.setStyleSheet("color: #888;")
        layout.addWidget(sep)
        add_row = QHBoxLayout(); add_row.setSpacing(4)
        add_row.addWidget(cp._filter_input, 1)
        add_row.addWidget(cp._btn_filter_add)
        layout.addLayout(add_row)
        layout.addWidget(cp._filter_list)
        filter_btns = QHBoxLayout()
        filter_btns.addWidget(cp._btn_filter_del)
        filter_btns.addWidget(cp._btn_filter_clear)
        filter_btns.addStretch()
        layout.addLayout(filter_btns)
        layout.addStretch()

    def _add_asr_fields(self, layout, cp):
        # ── 字幕模式 ──
        self._borrow(cp._subtitle_mode_combo)
        self._borrow(cp._s_group)
        self._borrow(cp._r_group)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("字幕模式:"))
        mode_row.addWidget(cp._subtitle_mode_combo, 1)
        layout.addLayout(mode_row)
        layout.addWidget(cp._s_group)
        layout.addWidget(cp._r_group)

        # ── ASR 模型 ──
        sep_asr = QLabel("── ASR 语音识别引擎 ──"); sep_asr.setStyleSheet("color: #888;")
        layout.addWidget(sep_asr)
        for w in (cp._asr_model_dir_edit, cp._asr_model_combo,
                  cp._asr_lang_combo, cp._asr_beam_spin,
                  cp._asr_word_ts_check, cp._asr_condition_check,
                  cp._asr_no_speech_spin, cp._asr_comp_ratio_spin,
                  cp._asr_temp_edit, cp._asr_hotwords_edit,
                  cp._asr_prompt_edit, cp._asr_vad_check,
                  cp._asr_vad_silence_spin, cp._asr_vad_thresh_spin,
                  cp._asr_region_edit):
            self._borrow(w)
        form = QFormLayout(); form.setSpacing(4)
        form.addRow("模型目录:", cp._asr_model_dir_edit)
        form.addRow("可用模型:", cp._asr_model_combo)
        btn_refresh = QPushButton("🔄 刷新模型列表")
        btn_refresh.clicked.connect(cp._refresh_asr_models)
        form.addRow("", btn_refresh)
        form.addRow("语言:", cp._asr_lang_combo)
        form.addRow("区域名:", cp._asr_region_edit)
        layout.addLayout(form)

        gf = QGroupBox("解码参数"); gfl = QFormLayout(gf); gfl.setSpacing(4)
        gfl.addRow("Beam Size:", cp._asr_beam_spin)
        gfl.addRow("", cp._asr_word_ts_check)
        gfl.addRow("", cp._asr_condition_check)
        gfl.addRow("无语音阈值:", cp._asr_no_speech_spin)
        gfl.addRow("压缩比阈值:", cp._asr_comp_ratio_spin)
        gfl.addRow("温度:", cp._asr_temp_edit)
        gfl.addRow("热词:", cp._asr_hotwords_edit)
        gfl.addRow("初始提示:", cp._asr_prompt_edit)
        layout.addWidget(gf)

        vg = QGroupBox("VAD (语音活动检测)"); vgl = QFormLayout(vg); vgl.setSpacing(4)
        vgl.addRow("", cp._asr_vad_check)
        vgl.addRow("最小静音:", cp._asr_vad_silence_spin)
        vgl.addRow("VAD 阈值:", cp._asr_vad_thresh_spin)
        layout.addWidget(vg)
        layout.addStretch()

    def _add_correction_fields(self, layout, cp):
        for w in (cp._corr_enabled_check, cp._corr_translate_check,
                  cp._corr_stream_check, cp._corr_json_check,
                  cp._corr_extract_env_check, cp._btn_extract_env,
                  cp._corr_summary_prompt_text, cp._corr_system_prompt_text,
                  cp._corr_output_format_edit, cp._corr_preset_combo,
                  cp._corr_batch_spin, cp._corr_context_spin,
                  cp._corr_retry_spin, cp._corr_prompt_text,
                  cp._post_sim_dedup, cp._post_keep_longest):
            self._borrow(w)

        layout.addRow("", cp._corr_enabled_check)

        sep_beh = QLabel("── 纠错行为 ──"); sep_beh.setStyleSheet("color: #888;")
        layout.addRow("", sep_beh)
        layout.addRow("", cp._corr_translate_check)
        layout.addRow("", cp._corr_stream_check)
        layout.addRow("", cp._corr_json_check)
        layout.addRow("", cp._corr_extract_env_check)
        layout.addRow("", cp._btn_extract_env)
        layout.addRow("总结提示词:", cp._corr_summary_prompt_text)
        layout.addRow("系统提示词:", cp._corr_system_prompt_text)
        layout.addRow("输出格式:", cp._corr_output_format_edit)
        layout.addRow("API 预设:", cp._corr_preset_combo)
        layout.addRow("批量条数:", cp._corr_batch_spin)
        layout.addRow("上下文窗口:", cp._corr_context_spin)
        layout.addRow("失败重试:", cp._corr_retry_spin)
        layout.addRow("用户提示词:", cp._corr_prompt_text)

        sep_api = QLabel("── API 连接 ──"); sep_api.setStyleSheet("color: #888;")
        layout.addRow("", sep_api)
        self._corr_api_key = QLineEdit()
        self._corr_api_key.setPlaceholderText("sk-xxx（可选）")
        self._corr_api_key.setText(self._corr_cfg.get("api_key", ""))
        layout.addRow("API Key:", self._corr_api_key)

        self._corr_api_url = QLineEdit()
        self._corr_api_url.setPlaceholderText("http://127.0.0.1:8080")
        self._corr_api_url.setText(self._corr_cfg.get("base_url", "http://127.0.0.1:8080"))
        layout.addRow("Base URL:", self._corr_api_url)

        self._corr_api_model = QLineEdit()
        self._corr_api_model.setPlaceholderText("gpt-4o / gemma 等")
        self._corr_api_model.setText(self._corr_cfg.get("model", ""))
        layout.addRow("模型:", self._corr_api_model)

        self._corr_api_timeout = QSpinBox()
        self._corr_api_timeout.setRange(1, 300)
        self._corr_api_timeout.setValue(self._corr_cfg.get("timeout", 30))
        self._corr_api_timeout.setSuffix(" 秒")
        layout.addRow("超时:", self._corr_api_timeout)

        self._corr_api_retry = QSpinBox()
        self._corr_api_retry.setRange(0, 10)
        self._corr_api_retry.setValue(self._corr_cfg.get("retry_on_failure", 2))
        layout.addRow("重试次数:", self._corr_api_retry)

        sep_adv = QLabel("── 提示词高级配置 ──"); sep_adv.setStyleSheet("color: #888;")
        layout.addRow("", sep_adv)
        layout.addRow(QLabel("总结/概括提示词:"))
        self._corr_api_summary = QTextEdit()
        self._corr_api_summary.setPlainText(self._corr_cfg.get("summary_prompt", ""))
        self._corr_api_summary.setMaximumHeight(60)
        self._corr_api_summary.setPlaceholderText("用于从全文提取环境上下文的提示词")
        layout.addRow(self._corr_api_summary)

        layout.addRow(QLabel("纠错系统提示词:"))
        self._corr_api_sys = QTextEdit()
        self._corr_api_sys.setPlainText(self._corr_cfg.get("correction_system_prompt", ""))
        self._corr_api_sys.setMaximumHeight(60)
        self._corr_api_sys.setPlaceholderText("注入到纠错请求的 system message")
        layout.addRow(self._corr_api_sys)

        layout.addRow(QLabel("输出格式标记:"))
        self._corr_api_format = QLineEdit()
        self._corr_api_format.setText(self._corr_cfg.get("output_format", ""))
        self._corr_api_format.setPlaceholderText("[纠正后文本]")
        layout.addRow(self._corr_api_format)

    def get_corr_api_config(self) -> dict:
        return {
            "enabled": self._cp._corr_enabled_check.isChecked(),
            "api_key": self._corr_api_key.text(),
            "base_url": self._corr_api_url.text(),
            "model": self._corr_api_model.text(),
            "timeout": self._corr_api_timeout.value(),
            "retry_on_failure": self._corr_api_retry.value(),
            "summary_prompt": self._corr_api_summary.toPlainText(),
            "correction_system_prompt": self._corr_api_sys.toPlainText(),
            "output_format": self._corr_api_format.text(),
        }

    def _on_accept(self):
        """确认时触发参数应用 + 保存纠错 API 配置。"""
        self._cp._on_apply_mode()
        self.accept()

    def closeEvent(self, event):
        """关闭前将所有借用的控件归还给 ConfigPanel，避免被 Qt 销毁。"""
        for w in self._borrowed:
            try:
                w.setParent(self._cp)
            except RuntimeError:
                pass
        self._borrowed.clear()
        super().closeEvent(event)