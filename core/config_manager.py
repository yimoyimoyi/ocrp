# -*- coding: utf-8 -*-
"""配置管理器 —— 支持 JSON 注释，持久化用户设置。"""

import re
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
}


def _load_json_with_comments(filepath: Union[str, Path]) -> Any:
    """读取 JSON 文件，自动去除 // 行注释和 /* */ 块注释后解析"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    lines = []
    for line in text.split('\n'):
        idx = line.find('//')
        if idx >= 0:
            before = line[:idx]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                line = before
        lines.append(line)
    return json.loads('\n'.join(lines))


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
    "post_keep_longest": False,
    "post_sim_dedup": True,
    "post_sim_threshold": 0.9,
    "post_min_text_len": 2,
    "ocr_retry": 2,
    "ocr_timeout": 60,
    "corr_enabled": False,
    "corr_batch_size": 5,
    "corr_context_window": 3,
    "corr_retry": 2,
    "corr_prompt": "",
    "corr_extract_env": False,
    "corr_summary_prompt": "",
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

class ConfigManager:
    """管理应用配置的读写，自动合并默认值。"""

    def __init__(self):
        self.settings_path = CONFIG_DIR / "settings.json"
        self.settings = self._load_settings()

    def _load_settings(self) -> dict:
        if self.settings_path.exists():
            try:
                cfg = _load_json_with_comments(self.settings_path)
                self._migrate_mode_params(cfg)
                return self._merge_defaults(cfg)
            except Exception as e:
                print(f"加载设置失败: {e}", file=sys.stderr)
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

    def _merge_defaults(self, cfg: dict) -> dict:
        result = dict(DEFAULT_SETTINGS)
        for key, value in cfg.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value
        return result

    def get(self, key: str, default=None):
        return self.settings.get(key, default)

    def set(self, key: str, value):
        self.settings[key] = value

    def get_theme(self) -> str:
        return self.settings.get("theme", "dark")

    def get_scale(self) -> float:
        return self.settings.get("ui_scale", 1.0)

    def get_font_size(self) -> int:
        return self.settings.get("font_size", 12)

    def get_last_engine(self) -> str:
        return self.settings.get("last_engine", "paddleocr")

    def get_last_directory(self) -> str:
        return self.settings.get("last_directory", "")

    def get_window_geometry(self) -> dict:
        return self.settings.get("window_geometry", {})

    def get_splitter_sizes(self) -> list:
        return self.settings.get("splitter_sizes", [300, 400])

    def get_hw_accel(self) -> bool:
        return self.settings.get("hw_accel", False)

    def get_recent_videos(self) -> list:
        return self.settings.get("recent_videos", [])

    def add_recent_video(self, path: str, max_entries: int = 10):
        recent = self.settings.get("recent_videos", [])
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        self.settings["recent_videos"] = recent[:max_entries]
        self.save_settings()
