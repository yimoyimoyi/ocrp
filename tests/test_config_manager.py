"""配置管理器单元测试。"""

import json
from unittest import mock


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


# R12：已删除的孤儿键（防止未来误加回 defaults）
_REMOVED_KEYS = [
    "s_filter_keywords",
    "r_filter_keywords",
    "corr_use_template",
    "asr_enabled",
]


class TestModeParamsMigration:
    """测试 v2 迁移：白名单裁剪 + 业务镜像迁入域对象文件（方案 A）。"""

    @staticmethod
    def _migrate(cfg: dict, tmp_path) -> dict:
        """执行完整迁移链（mock CONFIG_DIR 避免污染真实 config/）。"""
        from core.config_manager import ConfigManager

        cm = object.__new__(ConfigManager)
        with mock.patch("core.config_manager.CONFIG_DIR", tmp_path):
            cm._migrate_settings(cfg)
        return cfg

    def test_defaults_is_ui_state_whitelist(self):
        """MODE_PARAMS_DEFAULTS == UI 状态白名单（业务镜像键已移除）。"""

        from core.config_manager import MODE_PARAMS_DEFAULTS
        from core.settings import UiStateConfig

        assert set(MODE_PARAMS_DEFAULTS) == UiStateConfig.UI_STATE_MODE_KEYS
        # 业务镜像键必须已从 defaults 移除（R1/R2 收敛）
        assert not any(k.startswith("asr_") and k != "asr_model_path" for k in MODE_PARAMS_DEFAULTS)
        assert not any(
            k.startswith("corr_") and k not in ("corr_preset", "corr_concurrency", "corr_rpm", "corr_summary_prompt")
            for k in MODE_PARAMS_DEFAULTS
        )
        assert "seg_time_gap" not in MODE_PARAMS_DEFAULTS
        # 抽查典型默认值
        assert MODE_PARAMS_DEFAULTS["corr_concurrency"] == 4
        assert MODE_PARAMS_DEFAULTS["corr_rpm"] == 30
        assert MODE_PARAMS_DEFAULTS["asr_model_path"] == ""
        # R12：孤儿键必须已删除
        for key in _REMOVED_KEYS:
            assert key not in MODE_PARAMS_DEFAULTS, f"孤儿键未删除: {key}"

    def test_migrate_empty_mode_params_fills_whitelist(self, tmp_path):
        from core.config_manager import MODE_PARAMS_DEFAULTS

        cfg = self._migrate({"mode_params": {}}, tmp_path)
        mp = cfg["mode_params"]
        # 空 dict 首启补齐全部白名单默认键
        assert set(MODE_PARAMS_DEFAULTS) <= set(mp)
        for key, default in MODE_PARAMS_DEFAULTS.items():
            assert mp[key] == default, f"{key} 默认值不一致"
        assert cfg["config_version"] == 2

    def test_migrate_missing_mode_params_fills_defaults(self, tmp_path):
        """mode_params 键完全缺失时，新补 dict 并补齐白名单默认值。"""
        from core.config_manager import MODE_PARAMS_DEFAULTS

        cfg = self._migrate({}, tmp_path)
        mp = cfg["mode_params"]
        assert set(MODE_PARAMS_DEFAULTS) <= set(mp)

    def test_migrate_non_dict_mode_params_resets(self, tmp_path):
        """mode_params 非 dict（损坏配置）时重置为 dict 并补齐白名单默认值。"""
        from core.config_manager import MODE_PARAMS_DEFAULTS

        for bad in (None, ["x"], "corrupt", 42):
            cfg = self._migrate({"mode_params": bad}, tmp_path)
            mp = cfg["mode_params"]
            assert isinstance(mp, dict), f"{bad!r} 应被重置为 dict"
            assert set(MODE_PARAMS_DEFAULTS) <= set(mp)

    def test_migrate_keeps_existing_values(self, tmp_path):
        """已有用户配置值不被默认值覆盖；旧键名仍被重命名；业务键迁入域文件。"""

        cfg = self._migrate(
            {
                "mode_params": {
                    "corr_retry": 5,  # 用户已有值 → 迁入 ai_correction.json
                    "drop_ratio": 0.7,  # 旧键名 → s_drop_ratio
                }
            },
            tmp_path,
        )
        mp = cfg["mode_params"]
        assert "drop_ratio" not in mp
        assert mp["s_drop_ratio"] == 0.7
        # corr_retry 已迁入域文件（文件缺键时回填）
        corr = json.loads((tmp_path / "ai_correction.json").read_text(encoding="utf-8"))
        assert corr["retry_on_failure"] == 5
        assert "corr_retry" not in mp

    def test_migrate_business_params_split_to_files(self, tmp_path):
        """业务镜像键 → 域文件；文件已有值优先（不被 mp 覆盖）。"""

        (tmp_path / "ai_correction.json").write_text(
            json.dumps({"engine": "llamacpp", "translate_mode": True, "enabled": False}),
            encoding="utf-8",
        )
        cfg = self._migrate({"mode_params": {"corr_translate": False, "asr_language": "en"}}, tmp_path)
        corr = json.loads((tmp_path / "ai_correction.json").read_text(encoding="utf-8"))
        assert corr["translate_mode"] is True  # 文件已有值优先（未被 mp False 覆盖）
        mp = cfg["mode_params"]
        assert "corr_translate" not in mp
        assert "asr_language" not in mp
        asr = json.loads((tmp_path / "asr_engines.json").read_text(encoding="utf-8"))
        assert asr["language"] == "en"  # 文件缺键 → mp 回填

    def test_migrate_v2_idempotent(self, tmp_path):
        """v2 再跑不产生任何变化（迁移幂等）。"""
        cfg = {"config_version": 1, "mode_params": {"corr_translate": True, "frame_interval": 0.3}}
        first = self._migrate(dict(cfg), tmp_path)
        corr_before = (tmp_path / "ai_correction.json").read_text(encoding="utf-8")
        mp_before = dict(first["mode_params"])
        second = self._migrate(dict(first), tmp_path)
        assert second["mode_params"] == mp_before
        assert (tmp_path / "ai_correction.json").read_text(encoding="utf-8") == corr_before
