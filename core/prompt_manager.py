"""提示词模板管理器 —— 加载、查询、增删改、导入导出 prompt_templates.json。"""

import json
import os
from pathlib import Path

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"

from core.config_manager import _load_json_with_comments
from core.logger import get_logger

logger = get_logger(__name__)


class PromptTemplateManager:
    """管理提示词模板的加载、查询、增删改和导入导出。

    模板数据存储在 config/prompt_templates.json 中，结构：
    {
        "templates": [
            {
                "name": "模板名称",
                "description": "模板描述",
                "prompt": "提示词内容",
                "applicable_regions": ["any"]
            }
        ]
    }

    每个模板支持以下字段：
        - name (str): 唯一名称标识
        - description (str): 模板用途描述
        - prompt (str): 实际提示词文本
        - applicable_regions (list[str]): 适用的区域类型，"any" 表示通用
        - category (str, 可选): 分类标签（如 "ocr", "correction", "summary"）
    """

    # ── 内置模板分类常量 ──
    CATEGORY_OCR = "ocr"
    CATEGORY_CORRECTION = "correction"
    CATEGORY_SUMMARY = "summary"
    CATEGORY_TRANSLATION = "translation"

    def __init__(self):
        self._templates: list[dict] = []
        self.reload()

    def reload(self):
        """重新加载模板配置。"""
        path = CONFIG_DIR / "prompt_templates.json"
        if path.exists():
            try:
                data = _load_json_with_comments(path)
                from core.config_schema import validate_config
                from core.config_schemas import PROMPT_TEMPLATES_SCHEMA
                validate_config(data, PROMPT_TEMPLATES_SCHEMA, "prompt_templates.json")
                self._templates = data.get("templates", [])
                logger.info("已加载 %d 个提示词模板", len(self._templates))
            except Exception as e:
                logger.error("加载提示词模板失败: %s", e)
                self._templates = []
        else:
            logger.warning("提示词模板文件不存在: %s", path)
            self._templates = []

    def get_all_templates(self) -> list[dict]:
        """获取全部模板。"""
        return list(self._templates)

    def get_template_names(self) -> list[str]:
        """获取全部模板名称。"""
        return [t.get("name", "未命名") for t in self._templates]

    def get_templates_by_category(self, category: str) -> list[dict]:
        """按分类获取模板列表。"""
        return [dict(t) for t in self._templates if t.get("category") == category]

    def get_template_by_name(self, name: str) -> dict | None:
        """按名称查找模板。"""
        for t in self._templates:
            if t.get("name") == name:
                return dict(t)
        return None

    def get_template_by_index(self, index: int) -> dict | None:
        """按索引查找模板。"""
        if 0 <= index < len(self._templates):
            return dict(self._templates[index])
        return None

    def get_prompt_by_name(self, name: str) -> str:
        """按名称获取提示词文本。"""
        t = self.get_template_by_name(name)
        return t.get("prompt", "") if t else ""

    def find_templates_for_region(self, region_type: str) -> list[dict]:
        """查找适用于指定区域类型的模板。"""
        result = []
        for t in self._templates:
            regions = t.get("applicable_regions", [])
            if not regions or "any" in regions or region_type in regions:
                result.append(dict(t))
        return result

    def add_template(self, template: dict) -> bool:
        """添加新模板。重名时覆盖并记录日志。"""
        name = template.get("name", "").strip()
        if not name:
            logger.warning("添加模板失败: 名称为空")
            return False
        # 检查重名
        for i, t in enumerate(self._templates):
            if t.get("name") == name:
                self._templates[i] = dict(template)
                self._save()
                logger.info("已覆盖同名模板: %s", name)
                return True
        self._templates.append(dict(template))
        self._save()
        logger.info("已添加模板: %s", name)
        return True

    def remove_template(self, name: str) -> bool:
        """按名称删除模板。"""
        for i, t in enumerate(self._templates):
            if t.get("name") == name:
                self._templates.pop(i)
                self._save()
                logger.info("已删除模板: %s", name)
                return True
        logger.warning("删除模板失败: 未找到 %s", name)
        return False

    def import_templates(self, filepath: str) -> int:
        """从 JSON 文件导入模板。返回导入数量。"""
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            imported = data.get("templates", []) if isinstance(data, dict) else data
            if not isinstance(imported, list):
                logger.warning("导入失败: JSON 结构不正确")
                return 0
            count = 0
            for t in imported:
                if self.add_template(t):
                    count += 1
            logger.info("从 %s 导入 %d 个模板", filepath, count)
            return count
        except Exception as e:
            logger.error("导入模板失败: %s", e)
            return 0

    def export_templates(self, filepath: str) -> bool:
        """导出全部模板到 JSON 文件。"""
        try:
            data = {"templates": self._templates}
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("已导出 %d 个模板到 %s", len(self._templates), filepath)
            return True
        except Exception as e:
            logger.error("导出模板失败: %s", e)
            return False

    def _save(self):
        """保存当前模板到配置文件。"""
        path = CONFIG_DIR / "prompt_templates.json"
        try:
            data = {"templates": self._templates}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("保存模板配置失败: %s", e)
