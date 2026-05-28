"""API 预设管理器 —— 管理多个 API 连接配置预设。

支持 OCR API 引擎（openai_vision / ollama_vision / llamacpp）和
AI 纠错器共用同一套预设，存储在 config/api_presets.json。
"""

import json
import os
from pathlib import Path
from typing import Optional

from core.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRESETS_PATH = BASE_DIR / "config" / "api_presets.json"


def _load_presets() -> dict:
    if PRESETS_PATH.exists():
        try:
            with open(PRESETS_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            from core.config_schema import validate_config
            from core.config_schemas import API_PRESETS_SCHEMA
            ok, errors = validate_config(cfg, API_PRESETS_SCHEMA, "api_presets.json")
            if not ok:
                logger.warning("API 预设配置校验失败: %s", "; ".join(errors[:3]))
            return cfg
        except Exception as e:
            logger.warning("加载 API 预设配置失败: %s", e)
    return {"presets": {}, "default_preset": ""}


def _save_presets(data: dict):
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PRESETS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class APIPresetManager:
    """API 预设管理器 —— 单例模式。"""

    _instance: Optional["APIPresetManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = _load_presets()
        return cls._instance

    def reload(self):
        self._data = _load_presets()

    def save(self):
        _save_presets(self._data)

    # ── 查询 ──
    def get_names(self) -> list[str]:
        return list(self._data.get("presets", {}).keys())

    def get_default_name(self) -> str:
        return self._data.get("default_preset", "")

    def set_default(self, name: str):
        self._data["default_preset"] = name
        self.save()

    def get_preset(self, name: str) -> dict | None:
        return self._data.get("presets", {}).get(name)

    def get_effective_config(self, name: str = "") -> dict:
        """获取预设配置，未指定则使用默认预设。"""
        preset = self.get_preset(name or self.get_default_name())
        if preset:
            return dict(preset)
        # 回退到第一个预设
        names = self.get_names()
        if names:
            p = self.get_preset(names[0])
            return dict(p) if p else {}
        return {}

    # ── 增删改 ──
    def add_preset(self, name: str, config: dict) -> bool:
        if not name.strip():
            return False
        self._data.setdefault("presets", {})[name] = {
            "api_key": config.get("api_key", ""),
            "base_url": config.get("base_url", "http://127.0.0.1:8080"),
            "model": config.get("model", ""),
            "timeout": config.get("timeout", 30),
        }
        if not self._data.get("default_preset"):
            self._data["default_preset"] = name
        self.save()
        return True

    def update_preset(self, name: str, config: dict) -> bool:
        if name not in self._data.get("presets", {}):
            return False
        self._data["presets"][name].update({
            "api_key": config.get("api_key", ""),
            "base_url": config.get("base_url", "http://127.0.0.1:8080"),
            "model": config.get("model", ""),
            "timeout": config.get("timeout", 30),
        })
        self.save()
        return True

    def delete_preset(self, name: str) -> bool:
        if name not in self._data.get("presets", {}):
            return False
        del self._data["presets"][name]
        if self._data.get("default_preset") == name:
            names = self.get_names()
            self._data["default_preset"] = names[0] if names else ""
        self.save()
        return True

    def get_all_presets(self) -> dict[str, dict]:
        return dict(self._data.get("presets", {}))
