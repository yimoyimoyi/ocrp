"""配置管理器 —— 支持 JSON 注释，持久化用户设置。"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Union

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent
CONFIG_DIR = BASE_DIR / "config"

DEFAULT_SETTINGS = {
    "theme": "dark",
    "ui_scale": 1.0,
    "font_size": 12,
    "last_engine": "paddleocr",
    "last_directory": "",
    "window_geometry": {"x": 100, "y": 100, "width": 1280, "height": 800},
    "splitter_sizes": [400, 500],
    "recent_videos": [],
    "ai_correction_enabled": False,
    "hw_accel": False,
    "mode_params": {},
    "language": "",
}


def _load_json_with_comments(filepath: Union[str, Path]) -> Any:
    """读取 JSON 文件，自动去除 // 行注释和 /* */ 块注释后解析。

    使用状态机正确处理字符串内的 // 和 /*（含转义引号）。
    """
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    # 第一步：去除块注释（状态机，正确处理字符串内的 /*）
    out: list[str] = []
    in_string = False
    in_block_comment = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_block_comment:
            if ch == '*' and i + 1 < len(text) and text[i + 1] == '/':
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_string:
            if ch == '\\':
                out.append(ch)
                if i + 1 < len(text):
                    out.append(text[i + 1])
                    i += 2
                else:
                    i += 1
            elif ch == '"':
                in_string = False
                out.append(ch)
                i += 1
            else:
                out.append(ch)
                i += 1
        else:
            if ch == '"':
                in_string = True
                out.append(ch)
                i += 1
            elif ch == '/' and i + 1 < len(text) and text[i + 1] == '*':
                in_block_comment = True
                i += 2
            else:
                out.append(ch)
                i += 1
    text = ''.join(out)
    # 第二步：去除行注释（状态机，正确处理字符串内的 //）
    lines: list[str] = []
    for line in text.split('\n'):
        in_string = False
        j = 0
        while j < len(line):
            ch = line[j]
            if in_string:
                if ch == '\\':
                    j += 2
                elif ch == '"':
                    in_string = False
                    j += 1
                else:
                    j += 1
            elif ch == '"':
                in_string = True
                j += 1
            elif ch == '/' and j + 1 < len(line) and line[j + 1] == '/':
                line = line[:j]
                break
            else:
                j += 1
        lines.append(line)
    return json.loads('\n'.join(lines))


def load_key(key: str) -> Any:
    """支持点号分隔的多级键访问 settings.json。

    示例: load_key("api.key") → settings["api"]["key"]
          load_key("mode_params.corr_enabled") → settings["mode_params"]["corr_enabled"]

    注意：该函数每次调用都会重新读取文件，适合低频调用场景
    （UI 初始化、预设管理器、WorkflowManager 初始化）。
    高频调用应使用 ConfigManager 实例的 get() 方法。
    """
    keys = key.split(".")
    data = _load_json_with_comments(CONFIG_DIR / "settings.json")
    value = data
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            raise KeyError(f"配置键 '{key}' 不存在（中断于 '{k}'）")
    return value


# mode_params 默认值，新增或改名时在此维护
MODE_PARAMS_DEFAULTS = {
    "frame_interval": 0.1,
    "process_mode": "OCR + ASR（完整流程）",
    "sentinel_enabled": True,
    "subtitle_mode": "流式字幕（去重）",
    "s_drop_ratio": 0.5,
    "s_buffer_size": 8,
    "s_sim_threshold": 0.85,
    "s_min_text_len": 2,
    "s_filter_keywords": "",
    "s_ocr_version": "PP-OCRv4 (最快)",
    "r_dedup": True,
    "r_sim_threshold": 0.9,
    "r_buffer_size": 5,
    "r_min_text_len": 2,
    "r_filter_keywords": "",
    "r_interval": 2.0,
    "subtitle_duration": 3.0,
    "region_order": "",
    "post_sim_dedup": True,
    "post_conf_threshold": 0.6,
    "post_sim_threshold": 0.9,
    "post_min_text_len": 2,
    "corr_enabled": False,
    "corr_batch_size": 5,
    "corr_context_window": 3,
    "corr_retry": 2,
    "corr_prompt": "",
    "corr_extract_env": False,
    "corr_system_prompt": "",
    "corr_output_format": "",
    "corr_preset": "",
    "asr_model_size": "large-v3",
    "asr_model_path": "",
    "asr_language": "zh",
    "asr_vad": False,
    "asr_word_ts": True,
    "asr_region_name": "语音",
    "asr_beam_size": 5,
    "asr_initial_prompt": "",
    "asr_condition_prev": True,
    "asr_no_speech_thresh": 0.6,
    "asr_comp_ratio_thresh": 2.4,
    "asr_temperature": "0.0,0.2,0.4,0.6,0.8,1.0",
    "asr_hotwords": "",
    "asr_vad_min_silence": 500,
    "asr_vad_threshold": 0.5,
}

# 旧名 → 新名 映射（用于配置文件自动迁移）
_MODE_PARAMS_RENAME_MAP = {
    "drop_ratio": "s_drop_ratio",
    "buffer_max_size": "s_buffer_size",
    "min_text_length": "s_min_text_len",
    "similarity_threshold": "s_sim_threshold",
    "sentinel_filter_keywords": "s_filter_keywords",
    "regular_interval": "r_interval",
}

# ── 各配置文件的默认模板（首次运行时自动生成）──
_CONFIG_TEMPLATES: dict[str, dict] = {
    "ocr_engines.json": {
        "engines": {
            "paddleocr": {"type": "local", "enabled": True, "config": {
                "lang": "ch", "use_angle_cls": False, "use_gpu": False,
                "show_log": False, "fast_mode": True, "rec_batch_num": 6,
                "api_key": "", "base_url": "", "model": "", "timeout": 30,
                "device": "gpu", "ocr_version": "PP-OCRv4",
            }},
            "openai_vision": {"type": "api", "enabled": True, "config": {
                "api_key": "sk-xxx", "base_url": "https://api.deepseek.com/v1",
                "model": "gpt-4o", "prompt_template": "请识别图片中的文字，只返回文字内容",
                "timeout": 30, "retry": 2, "device": "cpu", "ocr_version": None, "use_angle_cls": True,
            }},
            "ollama_vision": {"type": "api", "enabled": True, "config": {
                "base_url": "http://localhost:11434", "model": "llama3.2-vision:11b",
                "prompt_template": "请识别图片中的文字，只返回文字内容", "timeout": 60, "retry": 2,
            }},
            "llamacpp": {"type": "api", "enabled": True, "config": {
                "base_url": "http://127.0.0.1:8080", "api_key": "not-needed", "model": "",
                "prompt_template": "请识别图片中的文字，只返回文字内容", "timeout": 60, "retry": 2,
            }},
        },
        "default_engine": "llamacpp",
    },
    "asr_engines.json": {
        "engine": "whisperx", "enabled": False, "model_size": "large-v3",
        "language": "zh", "vad_enabled": False, "vad_min_silence_ms": 500,
        "vad_threshold": 0.5, "word_timestamps": True, "asr_region_name": "语音",
        "model_dir": "", "beam_size": 5, "initial_prompt": "",
        "condition_on_previous_text": True, "no_speech_threshold": 0.6,
        "compression_ratio_threshold": 2.4, "temperature": "0.0,0.2,0.4,0.6,0.8,1.0", "hotwords": "",
    },
    "ai_correction.json": {
        "enabled": False, "engine": "llamacpp",
        "correction_prompt": "你是一个文本校对专家。请根据上下文纠正OCR识别结果中的明显错误，保留原格式。",
        "retry_on_failure": 2, "api_key": "", "base_url": "http://127.0.0.1:8080",
        "model": "", "timeout": 30, "batch_size": 10, "context_window": 3, "retry": 2,
        "summary_prompt": "", "correction_system_prompt": "", "output_format": "",
        "prompts": {"default": ""}, "stream_mode": True, "json_mode": True,
        "seg_time_gap": 3.0,
        "enable_polish": False,
        "polish_prompt": "你是一个专业的字幕润色专家。请对翻译/纠错后的字幕进行润色...",
    },
    "api_presets.json": {
        "presets": {},
        "default": "",
    },
    "prompt_templates.json": {
        "templates": [],
    },
    "filters.json": {
        "keywords": [],
    },
}


def ensure_config_files() -> None:
    """检查并自动生成缺失的配置文件（从默认模板）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for filename, template in _CONFIG_TEMPLATES.items():
        path = CONFIG_DIR / filename
        if not path.exists():
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(template, f, ensure_ascii=False, indent=2)
                print(f"[config] 已创建默认配置: {filename}", file=sys.stderr)
            except OSError as e:
                print(f"[config] 创建配置失败 {filename}: {e}", file=sys.stderr)


class ConfigManager:
    """管理应用配置的读写，自动合并默认值。"""

    def __init__(self):
        self.settings_path = CONFIG_DIR / "settings.json"
        self.settings = self._load_settings()

    def _load_settings(self) -> dict:
        if self.settings_path.exists():
            try:
                cfg = _load_json_with_comments(self.settings_path)
                if not isinstance(cfg, dict):
                    raise ValueError("settings.json 不是有效的对象")
                self._migrate_mode_params(cfg)
                merged = self._merge_defaults(cfg)
                # 如果加载的配置与默认值有差异（缺键或多余键），重写文件
                if set(cfg.keys()) != set(merged.keys()):
                    self._save_settings(merged)
                return merged
            except Exception as e:
                print(f"设置文件损坏，已恢复默认: {e}", file=sys.stderr)
                self._save_settings(DEFAULT_SETTINGS)
                return dict(DEFAULT_SETTINGS)
        else:
            self._save_settings(DEFAULT_SETTINGS)
            return dict(DEFAULT_SETTINGS)

    def _migrate_mode_params(self, cfg: dict):
        """迁移 mode_params 中的旧键名 → 新键名，补充缺失默认值。"""
        mp = cfg.get("mode_params", {})
        if not mp:
            return
        # 重命名旧键
        for old_key, new_key in _MODE_PARAMS_RENAME_MAP.items():
            if old_key in mp and new_key not in mp:
                mp[new_key] = mp.pop(old_key)
            elif old_key in mp:
                del mp[old_key]
        # 补充缺失的默认值（不删除已有键）
        for key, default in MODE_PARAMS_DEFAULTS.items():
            if key not in mp:
                mp[key] = default

    def _save_settings(self, cfg: dict):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def save_settings(self):
        self._save_settings(self.settings)

    def reload(self):
        """从文件重新加载配置。"""
        self.settings = self._load_settings()

    def _merge_defaults(self, cfg: dict) -> dict:
        result = dict(DEFAULT_SETTINGS)
        for key, value in cfg.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key].update(value)  # type: ignore[attr-defined]
            else:
                result[key] = value
        return result

    def get(self, key: str, default=None):
        return self.settings.get(key, default)

    def set(self, key: str, value):
        self.settings[key] = value

    def get_theme(self) -> str:
        return str(self.settings.get("theme", "dark"))

    def get_scale(self) -> float:
        return float(self.settings.get("ui_scale", 1.0))

    def get_font_size(self) -> int:
        return int(self.settings.get("font_size", 12))

    def get_last_engine(self) -> str:
        return str(self.settings.get("last_engine", "paddleocr"))

    def get_last_directory(self) -> str:
        return str(self.settings.get("last_directory", ""))

    def get_window_geometry(self) -> dict:
        d = self.settings.get("window_geometry", {})
        return dict(d) if isinstance(d, dict) else {}

    def get_splitter_sizes(self) -> list:
        v = self.settings.get("splitter_sizes", [300, 400])
        return list(v) if isinstance(v, list) else [300, 400]

    def get_hw_accel(self) -> bool:
        return bool(self.settings.get("hw_accel", False))

    def get_language(self) -> str:
        return str(self.settings.get("language", ""))

    def set_language(self, lang: str):
        self.settings["language"] = lang
        self.save_settings()

    def get_recent_videos(self) -> list:
        v = self.settings.get("recent_videos", [])
        return list(v) if isinstance(v, list) else []

    def add_recent_video(self, path: str, max_entries: int = 10):
        recent = self.settings.get("recent_videos", [])
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        self.settings["recent_videos"] = recent[:max_entries]
        self.save_settings()
