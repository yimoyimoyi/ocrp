"""过滤器管理器单元测试。"""

import json


class TestFilterManager:
    """测试 FilterManager 关键词管理。"""

    def test_init_loads_keywords(self, tmp_path):

        # Write temp config
        data = {"keywords": ["test1", "test2"]}
        p = tmp_path / "filters.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        # Patch path and create
        import core.filter_manager as fm
        old_path = fm.FILTERS_PATH
        fm.FILTERS_PATH = p
        try:
            mgr = fm.FilterManager()
            assert "test1" in mgr.get_keywords()
            assert "test2" in mgr.get_keywords()
        finally:
            fm.FILTERS_PATH = old_path

    def test_add_keyword(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = []
        assert mgr.add_keyword("hello")
        assert "hello" in mgr.get_keywords()

    def test_add_duplicate_keyword(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = ["hello"]
        assert not mgr.add_keyword("hello")

    def test_add_empty_keyword(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = []
        assert not mgr.add_keyword("")
        assert not mgr.add_keyword("   ")

    def test_remove_keyword(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = ["hello", "world"]
        assert mgr.remove_keyword("hello")
        assert "hello" not in mgr.get_keywords()
        assert "world" in mgr.get_keywords()

    def test_remove_nonexistent_keyword(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = ["hello"]
        assert not mgr.remove_keyword("nope")

    def test_matches(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = ["bad", "skip"]
        assert mgr.matches("this is bad text")
        assert mgr.matches("skip me")
        assert not mgr.matches("clean text")

    def test_matches_empty(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = []
        assert not mgr.matches("anything")

    def test_matches_case_insensitive(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = ["Hello", "WORLD"]
        assert mgr.matches("say HELLO to the world")
        assert mgr.matches("World peace")
        assert mgr.matches("helloworld")
        assert not mgr.matches("nothing here")

    def test_get_keywords_returns_copy(self):
        from core.filter_manager import FilterManager

        mgr = FilterManager()
        mgr._keywords = ["test"]
        kw = mgr.get_keywords()
        kw.append("extra")
        assert "extra" not in mgr.get_keywords()
