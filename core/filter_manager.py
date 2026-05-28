"""关键词过滤器管理器 —— 包含指定关键词的结果将被自动过滤。"""

import json
import os
from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = BASE_DIR / "config"
FILTERS_PATH = CONFIG_DIR / "filters.json"


class FilterManager:
    """管理过滤关键词的增删查。"""

    def __init__(self):
        self._keywords: list[str] = []
        self.reload()

    def reload(self):
        """重新加载过滤器配置。"""
        if FILTERS_PATH.exists():
            try:
                with open(FILTERS_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                from core.config_schema import validate_config
                from core.config_schemas import FILTERS_SCHEMA
                validate_config(data, FILTERS_SCHEMA, "filters.json")
                self._keywords = data.get("keywords", [])
            except Exception as e:
                logger.warning("加载过滤配置失败: %s", e)
                self._keywords = []
        else:
            self._keywords = []

    def get_keywords(self) -> list[str]:
        """获取所有过滤关键词。"""
        return list(self._keywords)

    def add_keyword(self, keyword: str) -> bool:
        """添加过滤关键词。"""
        kw = keyword.strip()
        if not kw or kw in self._keywords:
            return False
        self._keywords.append(kw)
        ok = self._save()
        logger.info("添加过滤关键词 '%s' (保存:%s)", kw[:40], "成功" if ok else "失败")
        return ok

    def remove_keyword(self, keyword: str) -> bool:
        """按文本删除过滤关键词。"""
        kw = keyword.strip()
        if kw in self._keywords:
            self._keywords.remove(kw)
            ok = self._save()
            logger.info("删除过滤关键词 '%s' (保存:%s)", kw[:40], "成功" if ok else "失败")
            return ok
        logger.info("删除过滤关键词 '%s' 失败: 不在列表中 (列表:%s)", kw[:40], self._keywords[:5])
        return False

    def matches(self, text: str) -> bool:
        """检查文本是否匹配任一过滤关键词（大小写不敏感）。"""
        if not text or not self._keywords:
            return False
        low = text.lower()
        return any(kw.lower() in low for kw in self._keywords)

    def _save(self) -> bool:
        """保存关键词到配置文件。返回是否成功。"""
        try:
            FILTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(FILTERS_PATH, "w", encoding="utf-8") as f:
                json.dump({"keywords": self._keywords}, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.warning("保存过滤配置失败: %s", e)
            return False
