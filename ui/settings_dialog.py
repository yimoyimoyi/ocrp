"""参数设置对话框 —— 字段描述器驱动的数据驱动表单。

UI 控件由 _FIELDS / _ENGINE_FIELDS 描述表声明式生成：
  - _build_field()        按 spec 构建控件并绑定到 self.<attr>
  - _load_initial_values() 通用循环：描述表 → 控件
  - _sync_values_to_cp()   通用循环：控件 → mode_params
特殊面板（引擎联动 / 关键词过滤 / 排序拖放列表）保留专用构建方法。
"""

import os
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
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

# ══════════════════════════════════════════════════════════════════
# 字段描述表
#
# 每个字段 spec 的键：
#   key         mode_params（或 _corr_cfg，若 source="corr"）中的键
#   tab/group   所属 tab 与分组（分组顺序见 _TAB_GROUPS）
#   attr        控件绑定属性名（self.<attr>）
#   widget      combo / combo_edit / combo_data / preset_combo / spin /
#               double_spin / line / check / text / button /
#               btn_asr_refresh / corr_model_row
#   label       行标签（form 布局）；check 的行标签为空字符串，文本在 text
#   options     下拉选项（原文，tr_options=True 时构建期翻译）
#   default     默认值；min/max/step/decimals/suffix 数值范围
#   tooltip/placeholder/suffix/text  显示文本（tr_* 标志控制是否翻译）
#   source      "corr" 表示读写 correction_config 而非 mode_params
#   load=False  不参与 _load_initial_values（如引擎字段、按钮行）
#   sync=False  不参与 _sync_values_to_cp
#   load_map    加载值预处理回调；on_load 加载后回调（方法名）
#   sync_get    收集值回调（widget）-> value
# ══════════════════════════════════════════════════════════════════


def _map_subtitle_mode(value) -> str:
    """字幕模式加载：兼容内部标识（stream/regular，4c.3）与历史翻译文本。"""
    v = str(value)
    if v == "regular" or "常规" in v:
        return _("常规字幕（固定间隔）")
    return _("流式字幕（去重）")  # stream / 旧流式文本


def _clean_summary_load(value) -> str:
    """环境提示词加载：清洗历史版本写入的字面量 "None"（旧 P2 bug 污染）。"""
    v = str(value or "")
    if v.strip().lower() in ("none", "null"):
        return ""
    return v


_FIELDS: list[dict] = [
    # ── Tab 1: 基础设置 ──
    dict(
        key="process_mode",
        tab="basic",
        group="处理模式",
        attr="_process_mode",
        widget="combo",
        label="处理模式:",
        tr_label=True,
        options=("OCR + ASR（完整流程）", "仅 OCR", "仅语音识别 (ASR)"),
        tr_options=True,
        default="OCR + ASR（完整流程）",
        tooltip="选择开始处理时运行的流程模式",
        tr_tooltip=True,
    ),
    dict(
        key="frame_interval",
        tab="basic",
        group="处理模式",
        attr="_frame_interval",
        widget="double_spin",
        label="帧间隔:",
        tr_label=True,
        min=0.02,
        max=10.0,
        step=0.1,
        decimals=2,
        default=0.1,
        suffix=" 秒",
        tr_suffix=True,
        tooltip="每隔多少秒处理一帧",
        tr_tooltip=True,
    ),
    dict(
        key="subtitle_duration",
        tab="basic",
        group="输出控制",
        attr="_subtitle_duration",
        widget="double_spin",
        label="字幕时长:",
        tr_label=True,
        min=0.5,
        max=30.0,
        step=0.5,
        decimals=2,
        default=3.0,
        suffix=" 秒",
        tr_suffix=True,
    ),
    dict(
        key="srt_export_mode",
        tab="basic",
        group="输出控制",
        attr="_srt_export",
        widget="combo",
        label="SRT 导出:",
        tr_label=True,
        options=("仅纠正结果", "仅原文", "双语对照（原文+纠正）", "原文 换行 纠正"),
        tr_options=True,
        default="仅纠正结果",
        tooltip="SRT 导出时的字幕内容模式",
        tr_tooltip=True,
    ),
    # ── Tab 2: 语音识别 ──
    dict(
        key="subtitle_mode",
        tab="asr",
        group="字幕模式",
        attr="_subtitle_mode",
        widget="combo",
        label="字幕模式:",
        tr_label=True,
        options=("流式字幕（去重）", "常规字幕（固定间隔）"),
        tr_options=True,
        default="流式字幕（去重）",
        tooltip="流式：哨兵去重实时输出\n常规：固定间隔采样",
        tr_tooltip=True,
        load_map=_map_subtitle_mode,
        on_load="_on_subtitle_mode_changed",
        on_change="_on_subtitle_mode_changed",
    ),
    dict(
        key="sentinel_enabled",
        tab="asr",
        group="流式参数（哨兵去重）",
        attr="_s_sentinel",
        widget="check",
        text="启用哨兵去重（骤降/缓冲区/相似度）",
        tr_text=True,
        label="",
        default=True,
    ),
    dict(
        key="s_drop_ratio",
        tab="asr",
        group="流式参数（哨兵去重）",
        attr="_s_drop_ratio",
        widget="double_spin",
        label="字数骤降比:",
        tr_label=True,
        min=0.01,
        max=1.0,
        step=0.05,
        decimals=2,
        default=0.5,
        tooltip="文本长度骤降到上一帧的此比例时强制触发输出",
        tr_tooltip=True,
    ),
    dict(
        key="s_buffer_size",
        tab="asr",
        group="流式参数（哨兵去重）",
        attr="_s_buffer",
        widget="spin",
        label="连续缓冲区:",
        tr_label=True,
        min=1,
        max=100,
        default=8,
        tooltip="连续相同文本的缓冲区大小，超过后强制输出",
        tr_tooltip=True,
    ),
    dict(
        key="s_sim_threshold",
        tab="asr",
        group="流式参数（哨兵去重）",
        attr="_s_sim",
        widget="double_spin",
        label="相似度阈值:",
        tr_label=True,
        min=0.0,
        max=1.0,
        step=0.05,
        decimals=2,
        default=0.85,
    ),
    dict(
        key="s_min_text_len",
        tab="asr",
        group="流式参数（哨兵去重）",
        attr="_s_min_text",
        widget="spin",
        label="最小文字长度:",
        tr_label=True,
        min=1,
        max=100,
        default=2,
    ),
    dict(
        key="r_dedup",
        tab="asr",
        group="常规参数（固定间隔）",
        attr="_r_dedup",
        widget="check",
        text="启用基本去重（相似文本合并）",
        tr_text=True,
        label="",
        default=True,
    ),
    dict(
        key="r_sim_threshold",
        tab="asr",
        group="常规参数（固定间隔）",
        attr="_r_sim",
        widget="double_spin",
        label="相似度阈值:",
        tr_label=True,
        min=0.0,
        max=1.0,
        step=0.05,
        decimals=2,
        default=0.9,
    ),
    dict(
        key="r_buffer_size",
        tab="asr",
        group="常规参数（固定间隔）",
        attr="_r_buffer",
        widget="spin",
        label="连续缓冲区:",
        tr_label=True,
        min=1,
        max=100,
        default=5,
    ),
    dict(
        key="r_min_text_len",
        tab="asr",
        group="常规参数（固定间隔）",
        attr="_r_min_text",
        widget="spin",
        label="最小文字长度:",
        tr_label=True,
        min=1,
        max=100,
        default=2,
    ),
    dict(
        key="r_interval",
        tab="asr",
        group="常规参数（固定间隔）",
        attr="_r_interval",
        widget="double_spin",
        label="输出间隔:",
        tr_label=True,
        min=0.1,
        max=60.0,
        step=0.5,
        decimals=1,
        default=2.0,
        suffix=" 秒",
        tr_suffix=True,
        tooltip="每隔多少秒输出一次当前帧的全部识别结果",
        tr_tooltip=True,
    ),
    dict(
        key="asr_model_dir",
        tab="asr",
        group="ASR 语音识别引擎",
        attr="_asr_model_dir",
        widget="line",
        label="模型目录:",
        tr_label=True,
        default="models/asr",
        placeholder="留空使用默认缓存",
        tr_placeholder=True,
        sync_get=lambda w: w.text().strip() or "models/asr",
    ),
    dict(
        key="asr_model_path",
        tab="asr",
        group="ASR 语音识别引擎",
        attr="_asr_model",
        widget="combo_data",
        label="可用模型:",
        tr_label=True,
        default="",
        on_load="_load_asr_model_value",
    ),
    dict(
        tab="asr",
        group="ASR 语音识别引擎",
        attr="_asr_refresh_btn",
        widget="btn_asr_refresh",
        label="",
    ),
    dict(
        key="asr_language",
        tab="asr",
        group="ASR 语音识别引擎",
        attr="_asr_lang",
        widget="combo",
        label="语言:",
        tr_label=True,
        options=("auto", "zh", "en", "ja", "ko"),
        default="zh",
        init_text="zh",
    ),
    dict(
        key="asr_region_name",
        tab="asr",
        group="ASR 语音识别引擎",
        attr="_asr_region",
        widget="line",
        label="区域名:",
        tr_label=True,
        default="语音",
        tooltip="ASR 结果在表格中显示的区域名称",
        tr_tooltip=True,
    ),
    dict(
        key="asr_beam_size",
        tab="asr",
        group="解码参数",
        attr="_asr_beam",
        widget="spin",
        label="Beam Size:",
        tr_label=True,
        min=1,
        max=20,
        default=5,
        tooltip="Beam size，越大精度越高但越慢",
        tr_tooltip=True,
    ),
    dict(
        key="asr_word_ts",
        tab="asr",
        group="解码参数",
        attr="_asr_word_ts",
        widget="check",
        text="字级时间戳",
        tr_text=True,
        label="",
        default=True,
    ),
    dict(
        key="asr_condition_prev",
        tab="asr",
        group="解码参数",
        attr="_asr_condition",
        widget="check",
        text="基于上文条件解码",
        tr_text=True,
        label="",
        default=True,
    ),
    dict(
        key="asr_no_speech_thresh",
        tab="asr",
        group="解码参数",
        attr="_asr_no_speech",
        widget="double_spin",
        label="无语音阈值:",
        tr_label=True,
        min=0.0,
        max=1.0,
        step=0.1,
        default=0.6,
        tooltip="越高越容易跳过无声音片段",
        tr_tooltip=True,
    ),
    dict(
        key="asr_comp_ratio_thresh",
        tab="asr",
        group="解码参数",
        attr="_asr_comp_ratio",
        widget="double_spin",
        label="压缩比阈值:",
        tr_label=True,
        min=0.0,
        max=10.0,
        step=0.1,
        default=2.4,
    ),
    dict(
        key="asr_temperature",
        tab="asr",
        group="解码参数",
        attr="_asr_temp",
        widget="line",
        label="温度:",
        tr_label=True,
        default="0.0,0.2,0.4,0.6,0.8,1.0",
        placeholder="0.0,0.2,0.4,0.6,0.8,1.0",
        tr_placeholder=True,
        tooltip="温度参数（逗号分隔），越低越确定",
        tr_tooltip=True,
    ),
    dict(
        key="asr_hotwords",
        tab="asr",
        group="解码参数",
        attr="_asr_hotwords",
        widget="line",
        label="热词:",
        tr_label=True,
        placeholder="热词，逗号分隔",
        tr_placeholder=True,
        tooltip="提升特定词汇的识别率",
        tr_tooltip=True,
    ),
    dict(
        key="asr_initial_prompt",
        tab="asr",
        group="解码参数",
        attr="_asr_prompt",
        widget="line",
        label="初始提示:",
        tr_label=True,
        placeholder="初始提示词，如: 以下是普通话的转录",
        tr_placeholder=True,
    ),
    dict(
        key="asr_vad",
        tab="asr",
        group="VAD (语音活动检测)",
        attr="_asr_vad",
        widget="check",
        text="启用 VAD（跳过静音段）",
        tr_text=True,
        label="",
        tr_label=True,
        default=False,
        tooltip="自动检测并跳过静音部分，加速处理",
        tr_tooltip=True,
    ),
    dict(
        key="asr_vad_min_silence",
        tab="asr",
        group="VAD (语音活动检测)",
        attr="_asr_vad_silence",
        widget="spin",
        label="最小静音:",
        tr_label=True,
        min=100,
        max=5000,
        step=100,
        default=500,
        suffix=" ms",
        tr_suffix=True,
    ),
    dict(
        key="asr_vad_threshold",
        tab="asr",
        group="VAD (语音活动检测)",
        attr="_asr_vad_thresh",
        widget="double_spin",
        label="VAD 阈值:",
        tr_label=True,
        min=0.0,
        max=1.0,
        step=0.05,
        default=0.5,
    ),
    # ── Tab 3: OCR 字幕处理 ──
    dict(
        key="post_sim_dedup",
        tab="ocr",
        group="后处理参数",
        attr="_post_sim_dedup",
        widget="check",
        text="启用相似度去重（合并相似文本）",
        tr_text=True,
        label="",
        default=True,
    ),
    dict(
        key="post_conf_enabled",
        tab="ocr",
        group="后处理参数",
        attr="_post_conf_check",
        widget="check",
        text="启用置信度过滤（仅 PaddleOCR）",
        tr_text=True,
        label="",
        default=False,
    ),
    dict(
        key="post_conf_threshold",
        tab="ocr",
        group="后处理参数",
        attr="_post_conf_threshold",
        widget="double_spin",
        label="置信度阈值:",
        tr_label=True,
        min=0.0,
        max=1.0,
        step=0.05,
        decimals=2,
        default=0.6,
    ),
    dict(
        key="post_sim_threshold",
        tab="ocr",
        group="后处理参数",
        attr="_post_sim_threshold",
        widget="double_spin",
        label="去重相似度阈值:",
        tr_label=True,
        min=0.0,
        max=1.0,
        step=0.05,
        decimals=2,
        default=0.9,
    ),
    dict(
        key="post_min_text_len",
        tab="ocr",
        group="后处理参数",
        attr="_post_min_text_len",
        widget="spin",
        label="最小文字长度:",
        tr_label=True,
        min=1,
        max=100,
        default=2,
    ),
    # ── Tab 4: AI 纠错 ──
    dict(
        key="corr_enabled",
        tab="correction",
        group="纠错模式",
        attr="_corr_enabled",
        widget="check",
        text="启用 AI 纠错",
        tr_text=True,
        default=False,
        tooltip="总开关：开启后将使用 LLM 对 OCR 结果进行纠错",
        tr_tooltip=True,
    ),
    dict(
        key="corr_translate",
        tab="correction",
        group="纠错模式",
        attr="_corr_translate",
        widget="check",
        text="🌐 翻译模式（将结果翻译为中文）",
        tr_text=True,
        default=False,
        tooltip="开启后 LLM 将把 OCR 结果翻译为中文，纠错提示词仅作参考",
        tr_tooltip=True,
    ),
    dict(
        key="corr_stream",
        tab="correction",
        group="纠错模式",
        attr="_corr_stream",
        widget="check",
        text="🔴 流式输出模式（实时逐字显示 API 响应）",
        tr_text=True,
        default=False,
    ),
    dict(
        key="corr_json",
        tab="correction",
        group="纠错模式",
        attr="_corr_json",
        widget="check",
        text="📋 JSON 输出模式（API 返回结构化 JSON）",
        tr_text=True,
        default=False,
    ),
    dict(
        key="corr_extract_env",
        tab="correction",
        group="纠错模式",
        attr="_corr_extract_env",
        widget="check",
        text="提取全文环境（领域/氛围/内容摘要作为参考）",
        tr_text=True,
        default=False,
    ),
    dict(
        key="enable_polish",
        tab="correction",
        group="纠错模式",
        attr="_corr_polish",
        widget="check",
        text="✨ 润色模式（纠错/翻译后二次润色质量）",
        tr_text=True,
        default=False,
        source="corr",
        sync=False,
        tooltip="开启后 LLM 将对纠错/翻译结果进行二次润色，使表达更自然流畅",
        tr_tooltip=True,
    ),
    dict(
        tab="correction",
        group="纠错模式",
        attr="_btn_extract_env",
        widget="button",
        text="🔍 立即提取全文环境",
        on_click="_on_extract_env_clicked",
    ),
    dict(
        key="corr_summary_prompt",
        tab="correction",
        group="纠错模式",
        attr="_corr_summary_prompt",
        widget="text",
        min_height=50,
        max_height=80,
        # source="corr"：与 ai_correction.json 的 summary_prompt 同源读写，
        # 避免从未持久化的 mode_params 读到 "None"（设置同步 P2/P3 修复）
        source="corr",
        default="",
        load_map=_clean_summary_load,  # 清洗历史脏值 "None"
        placeholder="点击上方按钮自动提取环境信息，也可手动编辑...",
        tr_placeholder=True,
        tooltip="自动提取的全文环境信息（领域/氛围/摘要），可手动修改，随设置保存",
        tr_tooltip=True,
    ),
    dict(
        key="corr_system_prompt",
        tab="correction",
        group="提示词配置",
        attr="_corr_system_prompt",
        widget="text",
        label="系统提示词:",
        tr_label=True,
        min_height=60,
        max_height=100,
        placeholder="自定义纠错系统提示词（可选）",
        tr_placeholder=True,
    ),
    dict(
        key="corr_prompt",
        tab="correction",
        group="提示词配置",
        attr="_corr_prompt",
        widget="text",
        label="用户提示词:",
        tr_label=True,
        min_height=60,
        max_height=100,
        placeholder="自定义纠错提示词（可选）",
        tr_placeholder=True,
    ),
    dict(
        key="corr_output_format",
        tab="correction",
        group="提示词配置",
        attr="_corr_output_format",
        widget="line",
        label="输出格式:",
        tr_label=True,
        placeholder="[纠正后文本]",
        tr_placeholder=True,
    ),
    dict(
        key="corr_preset",
        tab="correction",
        group="批量参数",
        attr="_corr_preset",
        widget="preset_combo",
        label="API 预设:",
        tr_label=True,
        tooltip="选择纠错使用的 API 连接预设",
        tr_tooltip=True,
    ),
    dict(
        key="corr_batch_size",
        tab="correction",
        group="批量参数",
        attr="_corr_batch",
        widget="spin",
        label="批量条数:",
        tr_label=True,
        min=1,
        max=50,
        default=5,
        suffix=" 条/次",
        tr_suffix=True,
    ),
    dict(
        key="corr_retry",
        tab="correction",
        group="批量参数",
        attr="_corr_retry",
        widget="spin",
        label="失败重试:",
        tr_label=True,
        min=0,
        max=10,
        default=2,
    ),
    dict(
        key="corr_concurrency",
        tab="correction",
        group="批量参数",
        attr="_corr_concurrency",
        widget="spin",
        label="并发数:",
        tr_label=True,
        min=1,
        max=8,
        default=4,
        suffix=" 并发",
        tr_suffix=True,
        tooltip="同时运行的批次数（滑动窗口并发）",
        tr_tooltip=True,
    ),
    dict(
        key="corr_rpm",
        tab="correction",
        group="批量参数",
        attr="_corr_rpm",
        widget="spin",
        label="RPM 限制:",
        tr_label=True,
        min=0,
        max=120,
        default=30,
        suffix=" RPM",
        tr_suffix=True,
        tooltip="每分钟最大请求数，0 表示不限制",
        tr_tooltip=True,
    ),
    dict(
        key="seg_time_gap",
        tab="correction",
        group="批量参数",
        attr="_seg_time_gap",
        widget="double_spin",
        label="上下文时间间隔:",
        tr_label=True,
        min=0.0,
        max=60.0,
        default=3.0,
        suffix=" 秒",
        tr_suffix=True,
        tooltip="上下文窗口中，跳过时间间隔超过此值的行",
        tr_tooltip=True,
    ),
    dict(
        key="api_key",
        tab="correction",
        group="API 连接",
        attr="_corr_api_key",
        widget="line",
        label="API Key:",
        tr_label=True,
        placeholder="sk-xxx（可选）",
        tr_placeholder=True,
        echo="password",
        source="corr",
        sync=False,
    ),
    dict(
        key="base_url",
        tab="correction",
        group="API 连接",
        attr="_corr_api_url",
        widget="line",
        label="Base URL:",
        tr_label=True,
        placeholder="http://127.0.0.1:8080",
        tr_placeholder=True,
        default="http://127.0.0.1:8080",
        source="corr",
        sync=False,
    ),
    dict(
        key="model",
        tab="correction",
        group="API 连接",
        attr="_corr_api_model",
        widget="corr_model_row",
        label="模型:",
        tr_label=True,
        source="corr",
        sync=False,
    ),
    dict(
        key="timeout",
        tab="correction",
        group="API 连接",
        attr="_corr_api_timeout",
        widget="spin",
        label="超时:",
        tr_label=True,
        min=1,
        max=300,
        default=30,
        suffix=" 秒",
        tr_suffix=True,
        source="corr",
        sync=False,
    ),
    dict(
        key="retry_on_failure",
        tab="correction",
        group="API 连接",
        attr="_corr_api_retry",
        widget="spin",
        label="重试次数:",
        tr_label=True,
        min=0,
        max=10,
        default=2,
        source="corr",
        sync=False,
    ),
]

# ── 引擎字段：可见性矩阵 + 值填充/收集声明（_on_engine_changed / get_engine_config）──
#   visible_when(is_local, is_paddle) -> bool  字段可见性
#   engine_get(widget, eng_cfg)                引擎切换时回填
#   engine_out=(cfg_key, getter(widget))       收集到 get_engine_config()
_VER_MAP = {0: None, 1: "PP-OCRv5_mobile", 2: "PP-OCRv4"}


def _fill_paddle_version(widget, cfg: dict):
    """按 ocr_version 文本回填模型版本下拉框。"""
    ver = cfg.get("ocr_version") or ""
    if "v4" in ver:
        widget.setCurrentIndex(2)
    elif "mobile" in ver:
        widget.setCurrentIndex(1)
    else:
        widget.setCurrentIndex(0)


def _paddle_version_out(widget):
    """将模型版本下拉框索引映射为 ocr_version 配置值。"""
    return _VER_MAP.get(widget.currentIndex())


_ENGINE_FIELDS: list[dict] = [
    dict(
        attr="_eng_api_key",
        widget="line",
        label="API Key:",
        tr_label=True,
        placeholder="sk-xxx",
        tr_placeholder=True,
        echo="password",
        visible_when=lambda is_local, is_paddle: not is_local,
        engine_get=lambda w, cfg: w.setText(cfg.get("api_key", "")),
        engine_out=("api_key", lambda w: w.text()),
    ),
    dict(
        attr="_eng_base_url",
        widget="line",
        label="Base URL:",
        tr_label=True,
        placeholder="https://api.openai.com/v1",
        tr_placeholder=True,
        visible_when=lambda is_local, is_paddle: not is_local,
        engine_get=lambda w, cfg: w.setText(cfg.get("base_url", "")),
        engine_out=("base_url", lambda w: w.text()),
    ),
    dict(
        attr="_eng_model",
        row_attr="_eng_model_row",
        widget="combo_edit",
        label="模型:",
        tr_label=True,
        placeholder="gpt-4o",
        tr_placeholder=True,
        visible_when=lambda is_local, is_paddle: not is_local,
        engine_get=lambda w, cfg: w.setEditText(cfg.get("model", "")),
        engine_out=("model", lambda w: w.currentText()),
    ),
    dict(
        attr="_eng_timeout",
        widget="spin",
        label="超时:",
        tr_label=True,
        min=1,
        max=300,
        default=30,
        suffix=" 秒",
        tr_suffix=True,
        visible_when=lambda is_local, is_paddle: not is_local,
        engine_get=lambda w, cfg: w.setValue(cfg.get("timeout", 30)),
        engine_out=("timeout", lambda w: w.value()),
    ),
    dict(
        attr="_eng_gpu",
        widget="check",
        label="",
        text="启用 GPU 加速",
        tr_text=True,
        visible_when=lambda is_local, is_paddle: is_local,
        engine_get=lambda w, cfg: w.setChecked(cfg.get("device") == "gpu" or cfg.get("use_gpu", False)),
        engine_out=("device", lambda w: "gpu" if w.isChecked() else "cpu"),
    ),
    dict(
        attr="_eng_paddle_version",
        widget="combo",
        label="模型版本:",
        tr_label=True,
        options=["PP-OCRv5_server (高精度/慢)", "PP-OCRv5_mobile (平衡)", "PP-OCRv4 (快速)"],
        visible_when=lambda is_local, is_paddle: is_paddle,
        engine_get=_fill_paddle_version,
        engine_out=("ocr_version", _paddle_version_out),
    ),
    dict(
        attr="_eng_angle",
        widget="check",
        label="",
        text="启用角度检测",
        tr_text=True,
        default=True,
        visible_when=lambda is_local, is_paddle: is_paddle,
        engine_get=lambda w, cfg: w.setChecked(cfg.get("use_angle_cls", True)),
        engine_out=("use_angle_cls", lambda w: w.isChecked()),
    ),
    dict(
        attr="_eng_save_preset",
        widget="button",
        label="",
        text="💾 保存为 API 预设",
        tr_text=True,
        tooltip="将当前 API 配置保存为预设，供纠错等功能使用",
        visible_when=lambda is_local, is_paddle: not is_local,
    ),
]

# ── Tab / 分组布局声明 ──
_TABS = (
    ("basic", "⚙ 基础"),
    ("asr", "🎙 语音识别"),
    ("ocr", "🔤 OCR 处理"),
    ("correction", "✏ AI 纠错"),
    ("sort", "📊 结果输出"),
)

# kind: group（form 布局）/ vbox（无标签垂直布局）/ panel（专用构建方法）
# tr=True 时组标题构建期翻译；collapsed=True 默认折叠；spacing 表单行间距
_TAB_GROUPS: dict[str, list[dict]] = {
    "basic": [
        dict(kind="group", title="处理模式", tr=True),
        dict(kind="panel", name="engine"),
        dict(kind="group", title="输出控制", tr=True),
    ],
    "asr": [
        dict(kind="group", title="字幕模式", tr=True),
        dict(kind="group", title="流式参数（哨兵去重）", bind="_s_group"),
        dict(kind="group", title="常规参数（固定间隔）", bind="_r_group"),
        dict(kind="group", title="ASR 语音识别引擎", tr=True),
        dict(kind="group", title="解码参数", collapsed=True, spacing=6),
        dict(kind="group", title="VAD (语音活动检测)", collapsed=True, spacing=6),
    ],
    "ocr": [
        dict(kind="group", title="后处理参数", tr=True),
        dict(kind="panel", name="filter"),
    ],
    "correction": [
        dict(kind="vbox", title="纠错模式", tr=True, spacing=6),
        dict(kind="group", title="提示词配置", collapsed=True),
        dict(kind="group", title="批量参数", tr=True),
        dict(kind="group", title="API 连接", tr=True, collapsed=True, spacing=6),
    ],
    "sort": [
        dict(kind="panel", name="sort"),
    ],
}

_PANEL_BUILDERS = {
    "engine": "_build_engine_panel",
    "filter": "_build_filter_panel",
    "sort": "_build_sort_panel",
}


class SettingsDialog(QDialog):
    """参数设置对话框，集中管理处理参数 + 纠错 API 配置。"""

    def __init__(
        self,
        config_panel,
        correction_config: dict = None,
        parent=None,
        filter_keywords: list[str] | None = None,
        engine_manager=None,
        current_engine: str = "",
    ):
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

    # ── 通用字段读写 ──

    @staticmethod
    def _t(spec: dict, key: str) -> str:
        """按 tr_<key> 标志返回构建期翻译后的显示文本。"""
        text = spec[key]
        return _(text) if spec.get("tr_" + key) else text

    def _set_field_value(self, spec: dict, widget, value):
        """按控件类型设置值（等价 safe_set_widget 行为）。"""
        wtype = spec["widget"]
        try:
            if wtype in ("combo", "preset_combo"):
                widget.setCurrentText(str(value))
            elif wtype == "combo_edit":
                widget.setEditText(str(value))
            elif wtype == "combo_data":
                pass  # data 绑定控件由 on_load 钩子处理
            elif wtype == "check":
                widget.setChecked(bool(value))
            elif wtype == "spin":
                widget.setValue(int(value))
            elif wtype == "double_spin":
                widget.setValue(float(value))
            elif wtype == "line":
                widget.setText(str(value))
            elif wtype == "text":
                widget.setPlainText(str(value))
        except RuntimeError:
            pass

    def _field_value(self, spec: dict, widget):
        """按控件类型收集当前值。"""
        wtype = spec["widget"]
        if wtype == "check":
            return widget.isChecked()
        if wtype == "combo":
            return self._rev_map(widget.currentText(), *spec["options"])
        if wtype == "combo_data":
            return widget.currentData() or ""
        if wtype in ("combo_edit", "preset_combo"):
            return widget.currentText()
        if wtype == "spin":
            return widget.value()
        if wtype == "double_spin":
            return widget.value()
        if wtype == "line":
            return widget.text()
        if wtype == "text":
            return widget.toPlainText()
        return None

    def _load_initial_values(self):
        """从 ConfigPanel 的公共属性读取所有参数初始值（描述表驱动）。"""
        mp = self._cp.get_mode_params()
        for spec in _FIELDS:
            widget = getattr(self, spec.get("attr"), None)
            if widget is None or "key" not in spec or spec.get("load") is False:
                continue
            source = self._corr_cfg if spec.get("source") == "corr" else mp
            value = source.get(spec["key"], spec.get("default"))
            if spec.get("load_map"):
                value = spec["load_map"](value)
            self._set_field_value(spec, widget, value)
            if spec.get("on_load"):
                getattr(self, spec["on_load"])(value)

        # ── 过滤器 ──
        self._filter_items.clear()
        self._filter_list.clear()
        self._filter_original = list(self._initial_filter_keywords)
        for kw in self._initial_filter_keywords:
            self._filter_items.append(kw)
            self._filter_list.addItem(kw)

        # ── 排序 ──
        self._sort_items.clear()
        self._sort_list.clear()
        for prefix, name, suffix in self._cp.get_sort_rules():
            self._sort_items.append((prefix, name, suffix))
            self._add_sort_row(name, prefix, suffix)

    @staticmethod
    def _rev_map(translated: str, *orig_values: str) -> str:
        """将翻译后的文本映射回内部中文值。"""
        for orig in orig_values:
            if translated == _(orig) or translated == orig:
                return orig
        return translated

    def _sync_values_to_cp(self):
        """将对话框中的值通过 ConfigPanel 公共 API 写回（描述表驱动）。"""
        params = {}
        for spec in _FIELDS:
            if "key" not in spec or spec.get("sync") is False or spec.get("source") == "corr":
                continue
            widget = getattr(self, spec["attr"], None)
            if widget is None:
                continue
            getter = spec.get("sync_get")
            value = getter(widget) if getter else self._field_value(spec, widget)
            params[spec["key"]] = value

        # ── 排序 ──
        self._collect_sort_items()
        params["region_order"] = "\n".join(
            f"{prefix}：{name}：{suffix}"
            if prefix and suffix
            else f"{prefix}：{name}"
            if prefix
            else f"{name}：{suffix}"
            if suffix
            else name
            for prefix, name, suffix in self._sort_items
            if name
        )

        # 通过公共 API 写入 ConfigPanel
        cp = self._cp
        cp.apply_mode_params(params)

        # ── 过滤器差异同步 ──
        original = getattr(self, "_filter_original", [])
        current = list(self._filter_items)
        for kw in set(original) - set(current):
            cp.filter_remove_requested.emit(kw)
        for kw in set(current) - set(original):
            cp.filter_add_requested.emit(kw)

        # ── 排序规则同步 ──
        cp.set_sort_rules(list(self._sort_items))

    # ── Tab 构建 ──
    def _build_tabs(self):
        for tab_name, title in _TABS:
            self._tabs.addTab(self._wrap_scroll(self._build_tab(tab_name)), title)

    def _build_tab(self, tab_name: str) -> QWidget:
        """按 _TAB_GROUPS 声明的分组顺序构建一个 tab。"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)
        for item in _TAB_GROUPS[tab_name]:
            if item["kind"] == "panel":
                getattr(self, _PANEL_BUILDERS[item["name"]])(layout)
                continue
            title = self._t(item, "title")
            fields = [s for s in _FIELDS if s["tab"] == tab_name and s.get("group") == item["title"]]
            group = self._build_group(item, title, fields)
            if item.get("bind"):
                setattr(self, item["bind"], group)
            layout.addWidget(group)
        layout.addStretch()
        return tab

    def _build_group(self, item: dict, title: str, fields: list) -> CollapsibleGroup:
        """构建一个 CollapsibleGroup：form 布局（带标签行）或 vbox 布局。"""
        group = CollapsibleGroup(title, collapsed=bool(item.get("collapsed")))
        spacing = item.get("spacing", 8)
        if item["kind"] == "vbox":
            fl = QVBoxLayout()
            fl.setSpacing(spacing)
            for spec in fields:
                fl.addWidget(self._build_field(spec))
        else:
            fl = QFormLayout()
            fl.setSpacing(spacing)
            for spec in fields:
                fl.addRow(self._t(spec, "label"), self._build_field(spec))
        group.addLayout(fl)
        return group

    def _build_field(self, spec: dict):
        """根据字段描述构建控件，并绑定到 self.<attr>。"""
        wtype = spec["widget"]
        if wtype == "combo":
            w = QComboBox()
            if spec.get("tr_options"):
                w.addItems([_(o) for o in spec["options"]])
            else:
                w.addItems(list(spec["options"]))
            if spec.get("init_text"):
                w.setCurrentText(spec["init_text"])
            if spec.get("on_change"):
                w.currentTextChanged.connect(getattr(self, spec["on_change"]))
        elif wtype == "preset_combo":
            from core.api_preset_manager import APIPresetManager

            mgr = APIPresetManager()
            w = QComboBox()
            w.addItems(mgr.get_names())
            default_name = mgr.get_default_name()
            if default_name:
                w.setCurrentText(default_name)
            w.currentTextChanged.connect(self._on_preset_changed)
        elif wtype == "combo_edit":
            w = QComboBox()
            w.setEditable(True)
            w.setInsertPolicy(QComboBox.NoInsert)
            w.lineEdit().setPlaceholderText(self._t(spec, "placeholder"))
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        elif wtype == "combo_data":
            w = QComboBox()
        elif wtype == "spin":
            w = QSpinBox()
            w.setRange(spec["min"], spec["max"])
            w.setValue(spec.get("default", 0))
            if "step" in spec:
                w.setSingleStep(spec["step"])
            if "suffix" in spec:
                w.setSuffix(self._t(spec, "suffix"))
        elif wtype == "double_spin":
            w = QDoubleSpinBox()
            w.setRange(spec["min"], spec["max"])
            if "step" in spec:
                w.setSingleStep(spec["step"])
            if "decimals" in spec:
                w.setDecimals(spec["decimals"])
            w.setValue(spec.get("default", 0.0))
            if "suffix" in spec:
                w.setSuffix(self._t(spec, "suffix"))
        elif wtype == "line":
            w = QLineEdit()
            if spec.get("placeholder"):
                w.setPlaceholderText(self._t(spec, "placeholder"))
            if spec.get("echo") == "password":
                w.setEchoMode(QLineEdit.Password)
            if "default" in spec:
                w.setText(str(spec["default"]))
        elif wtype == "check":
            w = QCheckBox(self._t(spec, "text"))
            w.setChecked(spec.get("default", False))
        elif wtype == "text":
            w = QTextEdit()
            if spec.get("placeholder"):
                w.setPlaceholderText(self._t(spec, "placeholder"))
            if "max_height" in spec:
                w.setMaximumHeight(spec["max_height"])
            if "min_height" in spec:
                w.setMinimumHeight(spec["min_height"])
        elif wtype == "button":
            w = QPushButton(self._t(spec, "text"))
            w.clicked.connect(getattr(self, spec["on_click"]))
        elif wtype == "btn_asr_refresh":
            w = QPushButton("🔄 刷新模型列表")
            w.clicked.connect(self._refresh_asr_models)
        elif wtype == "corr_model_row":
            w = self._build_corr_model_row(spec)
        else:
            raise ValueError(f"未知字段类型: {wtype}")
        if spec.get("tooltip") and wtype not in ("button", "corr_model_row"):
            w.setToolTip(self._t(spec, "tooltip"))
        if spec.get("attr") and not hasattr(self, spec["attr"]):
            setattr(self, spec["attr"], w)
        return w

    def _build_corr_model_row(self, spec: dict) -> QWidget:
        """纠错 API 模型行：可编辑下拉 + 状态标签 + 获取模型按钮。"""
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        self._corr_api_model = QComboBox()
        self._corr_api_model.setEditable(True)
        self._corr_api_model.setInsertPolicy(QComboBox.NoInsert)
        self._corr_api_model.lineEdit().setPlaceholderText(_("gpt-4o / gemma 等"))
        self._corr_api_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(self._corr_api_model, 1)
        self._corr_model_status = QLabel("")
        self._corr_model_status.setMinimumWidth(100)
        btn_fetch = QPushButton(_("📋 获取模型"))
        btn_fetch.setToolTip("从 Base URL 获取可用模型列表")
        btn_fetch.clicked.connect(self._on_fetch_corr_models)
        row.addWidget(self._corr_model_status)
        row.addWidget(btn_fetch)
        self._corr_api_model_row = w
        return w

    def _wrap_scroll(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    # ── Tab 1: 引擎面板（专用构建，联动 _ENGINE_FIELDS）──
    def _build_engine_panel(self, layout: QVBoxLayout):
        """OCR 引擎组：引擎选择 + 引擎配置字段。"""
        group = CollapsibleGroup(_("OCR 引擎"))
        ef = QFormLayout()
        ef.setSpacing(8)
        self._engine_combo = QComboBox()
        if self._engine_mgr:
            self._engine_combo.addItems(self._engine_mgr.get_engine_names())
            if self._current_engine:
                self._engine_combo.setCurrentText(self._current_engine)
        self._engine_combo.currentTextChanged.connect(self._on_engine_changed)
        ef.addRow(_("引擎:"), self._engine_combo)
        for spec in _ENGINE_FIELDS:
            ef.addRow(self._t(spec, "label"), self._build_engine_field(spec))
        group.addLayout(ef)
        layout.addWidget(group)
        # 初始化引擎字段可见性
        self._on_engine_changed(self._engine_combo.currentText())

    def _build_engine_field(self, spec: dict):
        """构建引擎配置字段，并绑定到 self.<attr>（combo_edit 时绑定容器行）。"""
        wtype = spec["widget"]
        if wtype == "line":
            w = QLineEdit()
            if spec.get("placeholder"):
                w.setPlaceholderText(self._t(spec, "placeholder"))
            if spec.get("echo") == "password":
                w.setEchoMode(QLineEdit.Password)
        elif wtype == "spin":
            w = QSpinBox()
            w.setRange(spec["min"], spec["max"])
            w.setValue(spec.get("default", 0))
            if "suffix" in spec:
                w.setSuffix(self._t(spec, "suffix"))
        elif wtype == "check":
            w = QCheckBox(self._t(spec, "text"))
            w.setChecked(spec.get("default", False))
        elif wtype == "combo":
            w = QComboBox()
            w.addItems(spec["options"])
        elif wtype == "combo_edit":
            # 模型行：可编辑下拉 + 状态标签 + 获取模型按钮（整体作为容器行）
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.NoInsert)
            combo.lineEdit().setPlaceholderText(self._t(spec, "placeholder"))
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row.addWidget(combo, 1)
            self._eng_model_status = QLabel("")
            self._eng_model_status.setMinimumWidth(80)
            btn_fetch = QPushButton(_("📋 获取模型"))
            btn_fetch.clicked.connect(self._on_fetch_eng_models)
            row.addWidget(self._eng_model_status)
            row.addWidget(btn_fetch)
            setattr(self, spec["attr"], combo)
            setattr(self, spec["row_attr"], w)
            return w
        elif wtype == "button":
            w = QPushButton(self._t(spec, "text"))
            if spec.get("tooltip"):
                w.setToolTip(spec["tooltip"])
            w.clicked.connect(self._on_save_eng_preset)
        else:
            raise ValueError(f"未知引擎字段类型: {wtype}")
        if spec.get("tooltip") and wtype != "button":
            w.setToolTip(self._t(spec, "tooltip"))
        setattr(self, spec["attr"], w)
        return w

    def _on_engine_changed(self, name: str):
        """引擎切换时更新字段可见性和值（声明式遍历 _ENGINE_FIELDS）。"""
        if not self._engine_mgr or not name:
            return
        eng_cfg = self._engine_mgr._config.get("engines", {}).get(name, {})
        cfg = eng_cfg.get("config", {})
        is_local = eng_cfg.get("type") == "local"
        is_paddle = name == "paddleocr"
        for spec in _ENGINE_FIELDS:
            widget = getattr(self, spec["attr"])
            target = getattr(self, spec.get("row_attr", spec["attr"]))
            target.setVisible(spec["visible_when"](is_local, is_paddle))
            if spec.get("engine_get"):
                spec["engine_get"](widget, cfg)

    def _on_fetch_eng_models(self):
        """从当前引擎 Base URL 获取可用模型列表。"""
        base_url = self._eng_base_url.text().strip()
        if not base_url:
            self._eng_model_status.setText(_("⚠ 请输入 URL"))
            return
        self._eng_model_status.setText(_("⏳ 获取中..."))
        import threading

        from PySide6.QtCore import QObject as _QObject

        class _Bridge(_QObject):
            done = Signal(object)
            err = Signal(str)

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
        mgr.add_preset(
            name,
            {
                "api_key": self._eng_api_key.text(),
                "base_url": self._eng_base_url.text(),
                "model": self._eng_model.currentText(),
                "timeout": self._eng_timeout.value(),
            },
        )
        self._eng_model_status.setText(f"✅ 已保存: {name}")

    def get_engine_config(self) -> tuple[str, dict]:
        """返回 (engine_name, config_dict) 供主窗口保存。"""
        name = self._engine_combo.currentText()
        cfg = {}
        for spec in _ENGINE_FIELDS:
            out = spec.get("engine_out")
            if out:
                cfg[out[0]] = out[1](getattr(self, spec["attr"]))
        return name, cfg

    # ── Tab 2: 字幕模式联动 ──
    def _on_subtitle_mode_changed(self, mode: str):
        is_streaming = mode == _("流式字幕（去重）") or "流式" in mode
        self._s_group.setVisible(is_streaming)
        self._r_group.setVisible(not is_streaming)

    # faster-whisper 标准模型大小（可自动下载）
    _STANDARD_ASR_MODELS = [
        "tiny",
        "tiny.en",
        "base",
        "base.en",
        "small",
        "small.en",
        "medium",
        "medium.en",
        "large-v1",
        "large-v2",
        "large-v3",
        "distil-small.en",
        "distil-medium.en",
        "distil-large-v2",
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

    def _load_asr_model_value(self, value: str):
        """ASR 模型字段加载钩子：刷新列表后按 data 值选中。"""
        self._refresh_asr_models()
        if value:
            self._select_combo_by_data(self._asr_model, value)

    @staticmethod
    def _select_combo_by_data(combo: QComboBox, data_value: str):
        """通过 item data 值设置 QComboBox 选中项（而非显示文本）。"""
        for i in range(combo.count()):
            if combo.itemData(i) == data_value:
                combo.setCurrentIndex(i)
                return

    # ── Tab 3: 关键词过滤面板（专用构建）──
    def _build_filter_panel(self, layout: QVBoxLayout):
        group = CollapsibleGroup(_("关键词过滤"))
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
        group.addLayout(fl)
        layout.addWidget(group)

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
        if (
            QMessageBox.question(
                self, "确认清空", "确定要清空所有过滤关键词吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            == QMessageBox.Yes
        ):
            self._filter_items.clear()
            self._filter_list.clear()

    # ── Tab 5: 排序规则面板（专用构建，拖放列表）──
    def _build_sort_panel(self, layout: QVBoxLayout):
        group = CollapsibleGroup(_("排序规则"))
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
        group.addLayout(sl)
        layout.addWidget(group)

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

    # ── API 连接（纠错预设联动）──
    def _on_extract_env_clicked(self):
        self._cp.extract_env_clicked.emit()

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
            APIPresetManager().update_preset(
                preset_name,
                {
                    "api_key": self._corr_api_key.text(),
                    "base_url": self._corr_api_url.text(),
                    "model": self._corr_api_model.currentText(),
                    "timeout": self._corr_api_timeout.value(),
                },
            )

    def get_corr_api_config(self) -> dict:
        """获取 API 连接配置（纯读取）。

        stream_mode/json_mode 一并返回：重建 AICorrector 时保持
        与 UI 勾选一致（设置同步 P9 修复，配合 AICorrector.__init__ 读配置）。
        """
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
            "stream_mode": self._corr_stream.isChecked(),
            "json_mode": self._corr_json.isChecked(),
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
            done = Signal(object)
            err = Signal(str)

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
        """确认时：同步数据到 ConfigPanel 并触发应用。

        顺序说明（设置同步 P1 修复）：set_polish_enabled 必须在
        _sync_values_to_cp 之前调用——后者 emit mode_changed 时携带的
        是全量 _params 快照，先写入 corr_polish 才能进入保存链路。
        """
        # 模型非空校验：空模型会导致所有纠错/润色调用失败（llm_client 模型名称未设置）
        if not self._corr_api_model.currentText().strip():
            QMessageBox.warning(
                self,
                _("模型名称未设置"),
                _("请先填写 API 模型名称（如 deepseek-chat / gpt-4o），\n或选择包含模型的 API 预设。"),
            )
            self._tabs.setCurrentIndex(3)  # 跳到 AI 纠错 tab
            return
        self._cp.set_polish_enabled(self._corr_polish.isChecked())
        self._sync_values_to_cp()
        self._sync_preset()
        self.accept()
