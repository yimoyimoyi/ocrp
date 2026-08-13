"""配置域对象定义 —— 每个域对应一个配置文件（方案 A）。

域对象持有的键名 = 文件键名（单一命名），UI 直接读写域对象，
不再经 mode_params 的 asr_*/corr_* 镜像（docs/设置冗杂问题分析.md R1/R2）。
"""

import os
from copy import deepcopy

from core.config_manager import BASE_DIR, ConfigManager
from core.config_schemas import AI_CORRECTION_SCHEMA, ASR_ENGINES_SCHEMA, OCR_ENGINES_SCHEMA
from core.settings.base import ConfigObject

# ── OCR 版本规范 token（R4：存储值=token，显示文案由 UI 单独映射）──
OCR_VERSION_TOKENS = ("跟随全局", "PP-OCRv4", "PP-OCRv5_mobile", "PP-OCRv5_server")


class AsrConfig(ConfigObject):
    """asr_engines.json 域 —— UI 直接读写文件键，不再经 mode_params 镜像。"""

    FILENAME = "asr_engines.json"
    SCHEMA = ASR_ENGINES_SCHEMA
    DEFAULTS = {
        "engine": "whisperx",
        "model_size": "large-v3",
        "language": "zh",
        "device": "cuda",
        "compute_type": "float16",
        "batch_size": 16,
        "vad_enabled": False,
        "vad_min_silence_ms": 500,
        "vad_threshold": 0.5,
        "word_timestamps": True,
        "asr_region_name": "语音",
        "model_dir": "",
        "beam_size": 5,
        "initial_prompt": "",
        "condition_on_previous_text": True,
        "no_speech_threshold": 0.6,
        "compression_ratio_threshold": 2.4,
        "temperature": "0.0,0.2,0.4,0.6,0.8,1.0",
        "hotwords": "",
        "hf_endpoint": "",
    }
    # mode_params 键 → 文件键（迁移与读透合并共用，双向往返）
    KEY_MAP = {
        "asr_model_size": "model_size",
        "asr_model_dir": "model_dir",
        "asr_language": "language",
        "asr_vad": "vad_enabled",
        "asr_vad_min_silence": "vad_min_silence_ms",
        "asr_vad_threshold": "vad_threshold",
        "asr_word_ts": "word_timestamps",
        "asr_beam_size": "beam_size",
        "asr_initial_prompt": "initial_prompt",
        "asr_condition_prev": "condition_on_previous_text",
        "asr_no_speech_thresh": "no_speech_threshold",
        "asr_comp_ratio_thresh": "compression_ratio_threshold",
        "asr_temperature": "temperature",
        "asr_hotwords": "hotwords",
        "asr_region_name": "asr_region_name",
    }

    def apply_model_selection(self, path_or_name: str) -> None:
        """模型选择（UI 值：绝对路径或标准名）→ 文件键 model_dir/model_size。

        吸收原 main_window._sync_asr_config 的路径推导逻辑（单侧收敛）。
        """
        if not path_or_name:
            return
        if os.path.isabs(path_or_name) or os.sep in path_or_name:
            # 本地模型完整路径 → 提取目录名作为 model_size，父目录作为 model_dir
            self._data["model_size"] = os.path.basename(path_or_name)
            parent = os.path.dirname(path_or_name)
            if parent:
                self._data["model_dir"] = parent
        else:
            # 标准模型名称（如 large-v3）→ 直接作为 model_size
            self._data["model_size"] = path_or_name

    def resolve_model_path(self, saved_selection: str = "") -> str:
        """反推 UI 选择状态：saved 优先，否则从 model_dir/model_size 派生。

        吸收原 main_window._restore_business_params 的路径反推逻辑。
        """
        if saved_selection:
            return saved_selection
        ms = self._data.get("model_size", "")
        if not ms:
            return ""
        md = self._data.get("model_dir", "")
        md_abs = md if os.path.isabs(md) else os.path.join(str(BASE_DIR), md)
        local_dir = os.path.join(md_abs, ms) if md_abs else ""
        return local_dir if os.path.isdir(local_dir) else ms


class CorrectionConfig(ConfigObject):
    """ai_correction.json 域。"""

    FILENAME = "ai_correction.json"
    SCHEMA = AI_CORRECTION_SCHEMA
    # R3 死键清理：retry（真键 retry_on_failure）/ segmentation_* / enable_proofread / prompts / use_template 均无消费者
    DEAD_KEYS = (
        "retry",
        "prompts",
        "enable_sentence_segmentation",
        "segmentation_mode",
        "sentence_segmentation_prompt",
        "sentence_segmentation_system_prompt",
        "segmentation_prompts",
        "enable_proofread",
        "use_template",
    )
    DEFAULTS = {
        "enabled": False,
        "engine": "llamacpp",
        "correction_prompt": "你是一个文本校对专家。请根据上下文纠正OCR识别结果中的明显错误，保留原格式。",
        "retry_on_failure": 2,
        "api_key": "",
        "base_url": "http://127.0.0.1:8080",
        "model": "",
        "timeout": 30,
        "batch_size": 5,
        "context_window": 4,
        "summary_prompt": "",
        "correction_system_prompt": "",
        "output_format": "",
        "stream_mode": True,
        "json_mode": True,
        # R3 三方对齐：模板此前缺这两个键（_sync_correction_config 在写，模板没有）
        "translate_mode": False,
        "extract_environment": False,
        "seg_time_gap": 3.0,
        "enable_polish": False,
        "polish_prompt": "你是一个专业的字幕润色专家。请对翻译/纠错后的字幕进行润色...",
    }
    KEY_MAP = {
        "corr_enabled": "enabled",
        "corr_batch_size": "batch_size",
        "corr_retry": "retry_on_failure",
        "corr_prompt": "correction_prompt",
        "corr_system_prompt": "correction_system_prompt",
        "corr_output_format": "output_format",
        "corr_stream": "stream_mode",
        "corr_json": "json_mode",
        "corr_translate": "translate_mode",
        "corr_extract_env": "extract_environment",
        "corr_polish": "enable_polish",
        "seg_time_gap": "seg_time_gap",
    }

    def as_engine_config(self) -> dict:
        """API 连接配置子集（AICorrector 消费）。"""
        return {
            "api_key": self._data.get("api_key", ""),
            "base_url": self._data.get("base_url", "http://127.0.0.1:8080"),
            "model": self._data.get("model", ""),
            "timeout": self._data.get("timeout", 30),
        }


class OcrEnginesConfig(ConfigObject):
    """ocr_engines.json 域 —— 嵌套结构（engines 字典），整体包裹。"""

    FILENAME = "ocr_engines.json"
    SCHEMA = OCR_ENGINES_SCHEMA
    # retry 死键（R3）：作用于每个引擎 config（_scrub 覆盖为嵌套清理）
    DEAD_KEYS = ("retry",)
    DEFAULTS = {
        "engines": {
            "paddleocr": {
                "type": "local",
                "enabled": True,
                "config": {
                    "lang": "ch",
                    "use_angle_cls": False,
                    "show_log": False,
                    "fast_mode": True,
                    "rec_batch_num": 6,
                    "api_key": "",
                    "base_url": "",
                    "model": "",
                    "timeout": 30,
                    "device": "cpu",  # R5：唯一计算设备开关（use_gpu 已收敛删除）
                    "ocr_version": "PP-OCRv4",
                },
            },
            "openai_vision": {
                "type": "api",
                "enabled": True,
                "config": {
                    "api_key": "sk-xxx",
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "gpt-4o",
                    "prompt_template": "请识别图片中的文字，只返回文字内容",
                    "timeout": 30,
                    "device": "cpu",
                    "ocr_version": None,
                    "use_angle_cls": True,
                },
            },
            "ollama_vision": {
                "type": "api",
                "enabled": True,
                "config": {
                    "base_url": "http://localhost:11434",
                    "model": "llama3.2-vision:11b",
                    "prompt_template": "请识别图片中的文字，只返回文字内容",
                    "timeout": 60,
                },
            },
            "llamacpp": {
                "type": "api",
                "enabled": True,
                "config": {
                    "base_url": "http://127.0.0.1:8080",
                    "api_key": "not-needed",
                    "model": "",
                    "prompt_template": "请识别图片中的文字，只返回文字内容",
                    "timeout": 60,
                },
            },
        },
        "default_engine": "llamacpp",
    }

    def _scrub(self, data: dict) -> None:
        """嵌套治理：每个引擎 config 清理死键（R3）+ use_gpu 收敛（R5）。

        R5：use_gpu 与 device 双键并存（历史矛盾状态 use_gpu:false + device:gpu）。
        保留 device 为唯一计算设备开关；旧文件缺 device 时由 use_gpu 派生一次后删除。
        """
        for eng in data.get("engines", {}).values():
            cfg = eng.get("config")
            if not isinstance(cfg, dict):
                continue
            for key in self.DEAD_KEYS:
                cfg.pop(key, None)
            if "use_gpu" in cfg:
                if "device" not in cfg:
                    cfg["device"] = "gpu" if cfg.get("use_gpu") else "cpu"
                cfg.pop("use_gpu", None)

    def get_engine_names(self) -> list[str]:
        return list(self._data.get("engines", {}).keys())

    def get_engine_config(self, name: str) -> dict:
        return deepcopy(self._data.get("engines", {}).get(name, {}))

    def set_engine_config(self, name: str, patch: dict) -> None:
        """合并写入单个引擎 config（原子写 + 变更信号）。"""
        engines = self._data.setdefault("engines", {})
        eng = engines.setdefault(name, {})
        cfg = eng.setdefault("config", {})
        cfg.update(patch)
        self.commit()
        self.changed.emit(name)

    def get_current_engine(self) -> str:
        return self._data.get("default_engine", "")

    def set_current_engine(self, name: str) -> None:
        self._data["default_engine"] = name
        self.commit()
        self.changed.emit("default_engine")


class UiStateConfig:
    """settings.json 域 —— 薄适配器，委托 ConfigManager（已具备原子写/迁移/默认值）。"""

    # mode_params 残余白名单（C2 修复：前缀+例外 → 显式白名单）。
    # asr_model_path 为 UI 选择状态键（此前不持久化，重构后转入白名单）。
    UI_STATE_MODE_KEYS: frozenset[str] = frozenset(
        {
            "frame_interval",
            "process_mode",
            "sentinel_enabled",
            "subtitle_mode",
            "s_drop_ratio",
            "s_buffer_size",
            "s_sim_threshold",
            "s_min_text_len",
            "s_ocr_version",
            "r_dedup",
            "r_sim_threshold",
            "r_buffer_size",
            "r_min_text_len",
            "r_interval",
            "subtitle_duration",
            "region_order",
            "post_sim_dedup",
            "post_conf_enabled",
            "post_conf_threshold",
            "post_sim_threshold",
            "post_min_text_len",
            "srt_export_mode",
            "corr_preset",
            "corr_concurrency",
            "corr_rpm",
            "corr_summary_prompt",
            "asr_model_path",
        }
    )

    def __init__(self, manager: ConfigManager):
        self._mgr = manager

    # ── 委托 ConfigManager ──
    def get(self, key: str, default=None):
        return self._mgr.settings.get(key, default)

    def set(self, key: str, value) -> None:
        self._mgr.set(key, value)

    def save(self) -> None:
        self._mgr.save_settings()

    def get_mode_params(self) -> dict:
        return self._mgr.get("mode_params", {})

    def set_mode_params(self, params: dict) -> None:
        self._mgr.set("mode_params", params)
