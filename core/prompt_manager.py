# -*- coding: utf-8 -*-
"""提示词模板管理器 —— 加载、查询、增删改、导入导出 prompt_templates.json。"""

import os
import json
from pathlib import Path
from typing import List, Dict, Optional

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"

from config_manager import _load_json_with_comments


class PromptTemplateManager:
    """管理提示词模板的加载、查询、增删改和导入导出。"""

    def __init__(self):
        self._templates: List[dict] = []
        self.reload()

    def reload(self):
        """重新加载模板配置。"""
        path = CONFIG_DIR / "prompt_templates.json"
        if path.exists():
            try:
                data = _load_json_with_comments(path)
                self._templates = data.get("templates", [])
            except Exception:
                self._templates = []
        else:
            self._templates = []

    def get_all_templates(self) -> List[dict]:
        """获取全部模板。"""
        return list(self._templates)

    def get_template_names(self) -> List[str]:
        """获取全部模板名称。"""
        return [t.get("name", "未命名") for t in self._templates]

    def get_template_by_name(self, name: str) -> Optional[dict]:
        """按名称查找模板。"""
        for t in self._templates:
            if t.get("name") == name:
                return dict(t)
        return None

    def get_template_by_index(self, index: int) -> Optional[dict]:
        """按索引查找模板。"""
        if 0 <= index < len(self._templates):
            return dict(self._templates[index])
        return None

    def get_prompt_by_name(self, name: str) -> str:
        """按名称获取提示词文本。"""
        t = self.get_template_by_name(name)
        return t.get("prompt", "") if t else ""

    def find_templates_for_region(self, region_type: str) -> List[dict]:
        """查找适用于指定区域类型的模板。"""
        result = []
        for t in self._templates:
            regions = t.get("applicable_regions", [])
            if "any" in regions or region_type in regions:
                result.append(dict(t))
        return result

    def add_template(self, template: dict) -> bool:
        """添加新模板。"""
        name = template.get("name", "").strip()
        if not name:
            return False
        # 检查重名，重名则覆盖
        for i, t in enumerate(self._templates):
            if t.get("name") == name:
                self._templates[i] = dict(template)
                self._save()
                return True
        self._templates.append(dict(template))
        self._save()
        return True

    def remove_template(self, name: str) -> bool:
        """按名称删除模板。"""
        for i, t in enumerate(self._templates):
            if t.get("name") == name:
                self._templates.pop(i)
                self._save()
                return True
        return False

    def import_templates(self, filepath: str) -> int:
        """从 JSON 文件导入模板。返回导入数量。"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            imported = data.get("templates", []) if isinstance(data, dict) else data
            if not isinstance(imported, list):
                return 0
            count = 0
            for t in imported:
                if self.add_template(t):
                    count += 1
            return count
        except Exception:
            return 0

    def export_templates(self, filepath: str) -> bool:
        """导出全部模板到 JSON 文件。"""
        try:
            data = {"templates": self._templates}
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def _save(self):
        """保存当前模板到配置文件。"""
        path = CONFIG_DIR / "prompt_templates.json"
        try:
            data = {"templates": self._templates}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
