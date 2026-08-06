"""配置管理器单元测试。"""


class TestLoadJsonWithComments:
    """测试 JSON 注释解析。"""

    def test_line_comment(self, tmp_path):
        from core.config_manager import load_json_with_comments

        p = tmp_path / "test.json"
        p.write_text('{"key": "value" // comment\n}', encoding="utf-8")
        result = load_json_with_comments(p)
        assert result == {"key": "value"}

    def test_block_comment(self, tmp_path):
        from core.config_manager import load_json_with_comments

        p = tmp_path / "test.json"
        p.write_text('{"key": /* inline */ "value"}', encoding="utf-8")
        result = load_json_with_comments(p)
        assert result == {"key": "value"}

    def test_multiline_block_comment(self, tmp_path):
        from core.config_manager import load_json_with_comments

        p = tmp_path / "test.json"
        p.write_text('{\n/* multi\nline */\n"key": "value"\n}', encoding="utf-8")
        result = load_json_with_comments(p)
        assert result == {"key": "value"}

    def test_no_comments(self, tmp_path):
        from core.config_manager import load_json_with_comments

        p = tmp_path / "test.json"
        p.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")
        result = load_json_with_comments(p)
        assert result == {"a": 1, "b": [2, 3]}


class TestConfigManager:
    """测试 ConfigManager 基本功能。"""

    def test_default_settings(self):
        from core.config_manager import DEFAULT_SETTINGS

        assert "theme" in DEFAULT_SETTINGS
        assert "last_engine" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["theme"] in ("dark", "light")

    def test_mode_params_defaults(self):
        from core.config_manager import MODE_PARAMS_DEFAULTS

        assert "frame_interval" in MODE_PARAMS_DEFAULTS
        assert MODE_PARAMS_DEFAULTS["frame_interval"] > 0


# 设置同步修复回归：MODE_PARAMS_DEFAULTS 补齐 13 键 + _migrate_mode_params 空/非 dict 补键
_NEW_DEFAULT_KEYS = [
    "corr_translate",
    "corr_stream",
    "corr_json",
    "corr_concurrency",
    "corr_rpm",
    "seg_time_gap",
    "corr_polish",
    "corr_use_template",
    "corr_summary_prompt",
    "asr_enabled",
    "srt_export_mode",
    "post_conf_enabled",
    "asr_model_dir",
]


class TestModeParamsMigration:
    """测试 _migrate_mode_params 的默认值补齐与旧键迁移。"""

    @staticmethod
    def _migrate(cfg: dict) -> dict:
        """用无副作用实例（不触发 __init__ 的磁盘读写）执行迁移。"""
        from core.config_manager import ConfigManager

        cm = object.__new__(ConfigManager)
        cm._migrate_mode_params(cfg)
        return cfg

    def test_defaults_contain_13_new_keys(self):
        from core.config_manager import MODE_PARAMS_DEFAULTS

        for key in _NEW_DEFAULT_KEYS:
            assert key in MODE_PARAMS_DEFAULTS, f"缺少默认键: {key}"
        # 抽查典型默认值
        assert MODE_PARAMS_DEFAULTS["corr_retry"] == 2
        assert MODE_PARAMS_DEFAULTS["corr_concurrency"] == 4
        assert MODE_PARAMS_DEFAULTS["asr_model_dir"] == "models/asr"

    def test_migrate_empty_mode_params_fills_all_defaults(self):
        from core.config_manager import MODE_PARAMS_DEFAULTS

        cfg = self._migrate({"mode_params": {}})
        mp = cfg["mode_params"]
        # 空 dict 首启也必须补齐全部默认键（统一事实源）
        assert set(MODE_PARAMS_DEFAULTS) <= set(mp)
        for key, default in MODE_PARAMS_DEFAULTS.items():
            assert mp[key] == default, f"{key} 默认值不一致"

    def test_migrate_missing_mode_params_fills_defaults(self):
        """mode_params 键完全缺失时，新补 dict 并补齐默认值。"""
        from core.config_manager import MODE_PARAMS_DEFAULTS

        cfg = self._migrate({})
        mp = cfg["mode_params"]
        assert set(MODE_PARAMS_DEFAULTS) <= set(mp)

    def test_migrate_non_dict_mode_params_resets(self):
        """mode_params 非 dict（损坏配置）时重置为 dict 并补齐默认值。"""
        from core.config_manager import MODE_PARAMS_DEFAULTS

        for bad in (None, ["x"], "corrupt", 42):
            cfg = self._migrate({"mode_params": bad})
            mp = cfg["mode_params"]
            assert isinstance(mp, dict), f"{bad!r} 应被重置为 dict"
            assert set(MODE_PARAMS_DEFAULTS) <= set(mp)

    def test_migrate_keeps_existing_values(self):
        """已有用户配置值不被默认值覆盖；旧键名仍被重命名。"""
        cfg = self._migrate(
            {
                "mode_params": {
                    "corr_retry": 5,  # 用户已有值
                    "drop_ratio": 0.7,  # 旧键名 → s_drop_ratio
                }
            }
        )
        mp = cfg["mode_params"]
        assert mp["corr_retry"] == 5
        assert "drop_ratio" not in mp
        assert mp["s_drop_ratio"] == 0.7
