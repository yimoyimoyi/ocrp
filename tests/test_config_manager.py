# -*- coding: utf-8 -*-
"""配置管理器单元测试。"""

import json
import pytest
from pathlib import Path


class TestLoadJsonWithComments:
    """测试 JSON 注释解析。"""

    def test_line_comment(self, tmp_path):
        from config_manager import _load_json_with_comments

        p = tmp_path / "test.json"
        p.write_text('{"key": "value" // comment\n}', encoding="utf-8")
        result = _load_json_with_comments(p)
        assert result == {"key": "value"}

    def test_block_comment(self, tmp_path):
        from config_manager import _load_json_with_comments

        p = tmp_path / "test.json"
        p.write_text('{"key": /* inline */ "value"}', encoding="utf-8")
        result = _load_json_with_comments(p)
        assert result == {"key": "value"}

    def test_multiline_block_comment(self, tmp_path):
        from config_manager import _load_json_with_comments

        p = tmp_path / "test.json"
        p.write_text('{\n/* multi\nline */\n"key": "value"\n}', encoding="utf-8")
        result = _load_json_with_comments(p)
        assert result == {"key": "value"}

    def test_no_comments(self, tmp_path):
        from config_manager import _load_json_with_comments

        p = tmp_path / "test.json"
        p.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")
        result = _load_json_with_comments(p)
        assert result == {"a": 1, "b": [2, 3]}


class TestConfigManager:
    """测试 ConfigManager 基本功能。"""

    def test_default_settings(self):
        from config_manager import DEFAULT_SETTINGS

        assert "theme" in DEFAULT_SETTINGS
        assert "last_engine" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["theme"] in ("dark", "light")

    def test_mode_params_defaults(self):
        from config_manager import MODE_PARAMS_DEFAULTS

        assert "frame_interval" in MODE_PARAMS_DEFAULTS
        assert MODE_PARAMS_DEFAULTS["frame_interval"] > 0
