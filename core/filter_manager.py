# -*- coding: utf-8 -*-
"""关键词过滤器管理器 —— 包含指定关键词的结果将被自动过滤。"""

import os
import json
from pathlib import Path
from typing import List

from core.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"
FILTERS_PATH = CONFIG_DIR / "filters.json"


class FilterManager:
    """管理过滤关键词的增删查。"""

    def __init__(self):
        self._keywords: List[str] = []
        self._garbage_patterns: List[str] = []
        self.reload()

    def reload(self):
        """重新加载过滤器配置。"""
        if FILTERS_PATH.exists():
            try:
                with open(FILTERS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                from core.config_schema import validate_config
                from core.config_schemas import FILTERS_SCHEMA
                validate_config(data, FILTERS_SCHEMA, "filters.json")
                self._keywords = data.get("keywords", [])
                self._garbage_patterns = data.get("garbage_patterns", [])
            except Exception as e:
                logger.warning("加载过滤配置失败: %s", e)
                self._keywords = []
                self._garbage_patterns = []
        else:
            self._keywords = []
            self._garbage_patterns = []

    def get_garbage_patterns(self) -> List[str]:
        """获取垃圾文本过滤模式列表。"""
        return list(self._garbage_patterns)

    def get_keywords(self) -> List[str]:
        """获取所有过滤关键词。"""
        return list(self._keywords)

    def add_keyword(self, keyword: str) -> bool:
        """添加过滤关键词。"""
        kw = keyword.strip()
        if not kw or kw in self._keywords:
            return False
        self._keywords.append(kw)
        self._save()
        return True

    def remove_keyword(self, keyword: str) -> bool:
        """按文本删除过滤关键词。"""
        kw = keyword.strip()
        if kw in self._keywords:
            self._keywords.remove(kw)
            self._save()
            return True
        return False

    def matches(self, text: str) -> bool:
        """检查文本是否匹配任一过滤关键词。"""
        if not text or not self._keywords:
            return False
        return any(kw in text for kw in self._keywords)

    def _save(self):
        """保存关键词到配置文件。"""
        try:
            FILTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(FILTERS_PATH, "w", encoding="utf-8") as f:
                json.dump({"keywords": self._keywords, "garbage_patterns": self._garbage_patterns}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存过滤配置失败: %s", e)
