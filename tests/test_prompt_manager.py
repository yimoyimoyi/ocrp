# -*- coding: utf-8 -*-
"""提示词模板管理器单元测试。"""

import json
from pathlib import Path


class TestPromptTemplateManager:
    """测试 PromptTemplateManager CRUD 操作。"""

    def test_reload_from_file(self, tmp_path):
        """测试从模板文件重新加载。"""
        from core.prompt_manager import PromptTemplateManager
        import core.prompt_manager as pm

        data = {
            "templates": [
                {"name": "测试模板", "category": "ocr", "description": "d",
                 "prompt": "识别文字", "applicable_regions": []},
            ]
        }
        p = tmp_path / "prompt_templates.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        mgr = PromptTemplateManager()
        old_dir = pm.CONFIG_DIR
        pm.CONFIG_DIR = tmp_path
        try:
            mgr.reload()
            names = [t["name"] for t in mgr.get_all_templates()]
            assert "测试模板" in names
        finally:
            pm.CONFIG_DIR = old_dir

    def test_get_templates_by_category(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = [
            {"name": "t1", "category": "ocr"},
            {"name": "t2", "category": "ocr"},
            {"name": "t3", "category": "correction"},
        ]
        ocr = mgr.get_templates_by_category("ocr")
        assert len(ocr) == 2
        assert all(t["category"] == "ocr" for t in ocr)

    def test_get_template_by_name(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = [{"name": "test", "category": "ocr"}]
        t = mgr.get_template_by_name("test")
        assert t is not None
        assert t["name"] == "test"

    def test_get_template_not_found(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = []
        assert mgr.get_template_by_name("nope") is None

    def test_add_template(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = []
        tpl = {"name": "new", "category": "ocr", "description": "d", "prompt": "p", "applicable_regions": []}
        assert mgr.add_template(tpl)
        names = [t["name"] for t in mgr.get_all_templates()]
        assert "new" in names

    def test_add_duplicate_template_overwrites(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = [{"name": "existing", "category": "ocr", "prompt": "old"}]
        tpl = {"name": "existing", "category": "ocr", "prompt": "new"}
        assert mgr.add_template(tpl)
        updated = mgr.get_template_by_name("existing")
        assert updated["prompt"] == "new"

    def test_add_empty_name(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = []
        assert not mgr.add_template({"name": "", "category": "ocr"})

    def test_remove_template(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = [{"name": "to_remove", "category": "ocr"}]
        assert mgr.remove_template("to_remove")
        assert len(mgr.get_all_templates()) == 0

    def test_get_template_names(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = [
            {"name": "a", "category": "ocr"},
            {"name": "b", "category": "correction"},
        ]
        names = mgr.get_template_names()
        assert "a" in names
        assert "b" in names

    def test_find_templates_for_region(self):
        from core.prompt_manager import PromptTemplateManager

        mgr = PromptTemplateManager()
        mgr._templates = [
            {"name": "general", "applicable_regions": []},
            {"name": "any", "applicable_regions": ["any"]},
            {"name": "specific", "applicable_regions": ["speech"]},
        ]
        results = mgr.find_templates_for_region("speech")
        names = [t["name"] for t in results]
        assert "general" in names
        assert "any" in names
        assert "specific" in names
