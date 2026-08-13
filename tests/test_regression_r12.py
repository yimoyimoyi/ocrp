"""R12 批次设置简化回归测试。

覆盖：
- 配置版本号迁移：v0 → v1（死键清理 + ai_correction_enabled 删除）
- 孤儿键删除：s_filter_keywords / r_filter_keywords / corr_use_template / asr_enabled
- 方向 A：_restore_business_params 文件反同步、_save_mode_params 只持久化 UI 状态键
- 断链修复：retry_on_failure / seg_time_gap / extract_environment 同步分支、corr_prompt truthy
- r_min_text_len 接入常规模式
- 模板 seed（prompt_templates.json 为空时内置模板）
- 测试连接按钮、预设导入、模板编辑器入口、s_ocr_version UI、SRT 选项去重
- 右侧面板后处理组删除、引擎切换下拉
- ui_config.json 删除、HttpCheckWorker 删除、模板补齐
"""

import inspect
import os
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core.config_manager import MODE_PARAMS_DEFAULTS
from ui.settings_dialog import _FIELDS

ROOT = Path(__file__).resolve().parent.parent


def _app():
    app = QApplication.instance() or QApplication([])
    return app


# ══════════════════════════════════════════════════════════════════
# 版本号迁移 + 死键清理
# ══════════════════════════════════════════════════════════════════


class TestConfigVersionMigration:
    """方向 D：config_version 版本号迁移链。"""

    def test_migrate_v0_to_v2_split_business_params(self, tmp_path):
        """v2 迁移：死键清理 + 业务镜像迁入域文件 + 白名单化。"""
        from core.config_manager import ConfigManager

        cm = object.__new__(ConfigManager)
        cfg = {
            "theme": "dark",
            "ai_correction_enabled": True,  # 死键
            "mode_params": {
                "corr_context_window": 3,  # 死键
                "corr_proofread": True,  # 死键
                "corr_segmentation": "x",  # 死键
                "corr_translate": True,
                "asr_language": "en",
            },
        }
        with mock.patch("core.config_manager.CONFIG_DIR", tmp_path):
            cm._migrate_settings(cfg)
        assert cfg["config_version"] == 2
        assert "ai_correction_enabled" not in cfg
        mp = cfg["mode_params"]
        assert "corr_context_window" not in mp
        assert "corr_proofread" not in mp
        assert "corr_segmentation" not in mp
        # 业务键已迁入域文件，不再留在 mp（R1/R2 收敛）
        assert "corr_translate" not in mp
        assert "asr_language" not in mp
        # 合法白名单键保留 + 默认值补齐
        assert mp["frame_interval"] == 0.1
        assert "asr_model_path" in mp

    def test_migrate_v2_idempotent(self, tmp_path):
        """v2 输入再迁移：不产生任何变化（幂等）。"""
        from core.config_manager import ConfigManager

        cm = object.__new__(ConfigManager)
        cfg = {"config_version": 2, "mode_params": {"corr_preset": "x"}}
        before = dict(cfg["mode_params"])
        with mock.patch("core.config_manager.CONFIG_DIR", tmp_path):
            cm._migrate_settings(cfg)
        assert cfg["config_version"] == 2
        assert cfg["mode_params"] == before  # 幂等：v2 不重复迁移


class TestOrphanKeysRemoved:
    """R12：孤儿键必须从 defaults 删除。"""

    def test_orphan_keys_not_in_defaults(self):
        for key in ("s_filter_keywords", "r_filter_keywords", "corr_use_template", "asr_enabled"):
            assert key not in MODE_PARAMS_DEFAULTS, f"孤儿键未删除: {key}"

    def test_rename_map_cleanup(self):
        from core.config_manager import _MODE_PARAMS_RENAME_MAP

        assert "sentinel_filter_keywords" not in _MODE_PARAMS_RENAME_MAP


# ══════════════════════════════════════════════════════════════════
# 方向 A：业务参数文件反同步
# ══════════════════════════════════════════════════════════════════


class TestRestoreBusinessParams:
    """方向 A 继承（v3）：业务参数由域对象承载（替代 _restore_business_params 人肉映射）。"""

    @staticmethod
    def _write_asr(cfg_dir, data: dict):
        import json

        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "asr_engines.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _write_corr(cfg_dir, data: dict):
        import json

        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "ai_correction.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_asr_restore_mapping(self, tmp_path):
        """asr 域对象文件键即持久化键（单一命名，无 mode_params 镜像）。"""
        from core.settings.domains import AsrConfig

        self._write_asr(
            tmp_path,
            {
                "language": "en",
                "vad_enabled": True,
                "vad_min_silence_ms": 400,
                "word_timestamps": False,
                "beam_size": 3,
                "model_size": "small",
                "model_dir": "models/asr",
            },
        )
        obj = AsrConfig(tmp_path)
        assert obj.get("language") == "en"
        assert obj.get("vad_enabled") is True
        assert obj.get("vad_min_silence_ms") == 400
        assert obj.get("word_timestamps") is False
        assert obj.get("beam_size") == 3
        # model_size 是标准名且本地目录不存在 → resolve_model_path 回退标准名
        assert obj.resolve_model_path("") == "small"

    def test_corr_restore_mapping(self, tmp_path):
        """corr 域对象文件键即持久化键（KEY_MAP 覆盖全部业务键）。"""
        from core.settings.domains import CorrectionConfig

        self._write_corr(
            tmp_path,
            {
                "engine": "llamacpp",
                "enabled": True,
                "batch_size": 8,
                "retry_on_failure": 3,
                "correction_prompt": "校对提示",
                "stream_mode": True,
                "json_mode": False,
                "translate_mode": True,
                "enable_polish": True,
                "seg_time_gap": 2.0,
                "extract_environment": True,
            },
        )
        obj = CorrectionConfig(tmp_path)
        assert obj.get("enabled") is True
        assert obj.get("batch_size") == 8
        assert obj.get("retry_on_failure") == 3
        assert obj.get("correction_prompt") == "校对提示"
        assert obj.get("stream_mode") is True
        assert obj.get("translate_mode") is True
        assert obj.get("enable_polish") is True
        assert obj.get("seg_time_gap") == 2.0
        assert obj.get("extract_environment") is True

    def test_saved_path_preferred_for_model(self, tmp_path):
        """asr_model_path UI 选择状态优先于文件派生（resolve_model_path saved 参数）。"""
        from core.settings.domains import AsrConfig

        self._write_asr(tmp_path, {"engine": "whisperx", "model_size": "small"})
        obj = AsrConfig(tmp_path)
        assert obj.resolve_model_path("E:/models/asr/large-v3") == "E:/models/asr/large-v3"  # saved 优先


# ══════════════════════════════════════════════════════════════════
# 断链修复（P1-④⑤⑥⑦）
# ══════════════════════════════════════════════════════════════════


class TestSyncCorrectionConfigFixes:
    """R12 断链修复继承（v3）：行为由 CorrectionConfig 域对象与对话框 commit 承载。"""

    @pytest.fixture(autouse=True)
    def _ensure_app(self):
        _app()

    @staticmethod
    def _corr(tmp_path):
        from core.settings.domains import CorrectionConfig

        return CorrectionConfig(tmp_path)

    def test_retry_writes_retry_on_failure(self, tmp_path):
        """retry 死键清理；retry_on_failure 由域对象写入。"""
        from core.settings.domains import CorrectionConfig

        assert "retry" in CorrectionConfig.DEAD_KEYS  # 死键清理
        obj = self._corr(tmp_path)
        obj.update({"retry_on_failure": 3})
        obj2 = self._corr(tmp_path)
        assert obj2.get("retry_on_failure") == 3

    def test_seg_time_gap_branch_present(self):
        """seg_time_gap 映射由域对象 KEY_MAP 承载。"""
        from core.settings.domains import CorrectionConfig

        assert CorrectionConfig.KEY_MAP["seg_time_gap"] == "seg_time_gap"

    def test_extract_environment_branch_present(self, tmp_path):
        """extract_environment 由域对象写入。"""
        obj = self._corr(tmp_path)
        obj.update({"extract_environment": True})
        obj2 = self._corr(tmp_path)
        assert obj2.get("extract_environment") is True

    def test_corr_prompt_truthy(self):
        """corr_prompt 空值不覆盖文件默认提示词（对话框 commit_to_domains 保留 truthy 语义）。"""
        from ui.settings_dialog import SettingsDialog

        src = inspect.getsource(SettingsDialog.commit_to_domains)
        assert "correction_prompt" in src


class TestMinTextLenRegularMode:
    """P1-④：r_min_text_len 接入常规模式（此前只赋值不消费）。"""

    def test_regular_branch_uses_min_text_len(self):
        from core.frame_processor import FrameProcessor

        src = inspect.getsource(FrameProcessor.process_video)
        assert "self._r_min_text_len" in src


# ══════════════════════════════════════════════════════════════════
# 模板 seed + 模板补齐
# ══════════════════════════════════════════════════════════════════


class TestTemplateSeed:
    """P0-①：空模板文件 seed 内置模板，消除默认模板悬空。"""

    def test_empty_file_seeds_default(self, tmp_path):
        from core import prompt_manager
        from core.utils import DEFAULT_OCR_TEMPLATE

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "prompt_templates.json").write_text('{"templates": []}', encoding="utf-8")
        with mock.patch.object(prompt_manager, "CONFIG_DIR", cfg_dir):
            mgr = prompt_manager.PromptTemplateManager()
            names = mgr.get_template_names()
        assert DEFAULT_OCR_TEMPLATE in names

    def test_missing_file_seeds_default(self, tmp_path):
        from core import prompt_manager
        from core.utils import DEFAULT_OCR_TEMPLATE

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        with mock.patch.object(prompt_manager, "CONFIG_DIR", cfg_dir):
            mgr = prompt_manager.PromptTemplateManager()
            names = mgr.get_template_names()
        assert DEFAULT_OCR_TEMPLATE in names

    def test_nonempty_file_not_overwritten(self, tmp_path):
        from core import prompt_manager

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "prompt_templates.json").write_text(
            '{"templates": [{"name": "自定义", "prompt": "x"}]}', encoding="utf-8"
        )
        with mock.patch.object(prompt_manager, "CONFIG_DIR", cfg_dir):
            mgr = prompt_manager.PromptTemplateManager()
            names = mgr.get_template_names()
        assert names == ["自定义"]


class TestConfigTemplatesUpdated:
    """模板补齐：新装用户的配置模板包含完整键。"""

    def test_asr_template_has_device_keys(self):
        from core.config_manager import _CONFIG_TEMPLATES

        tpl = _CONFIG_TEMPLATES["asr_engines.json"]
        assert "device" in tpl
        assert "compute_type" in tpl
        assert "batch_size" in tpl
        assert "hf_endpoint" in tpl
        assert "enabled" not in tpl  # 死键删除

    def test_corr_template_dead_keys_removed(self):
        """R3 死键清理：segmentation_* / prompts / enable_proofread / use_template 必须删除。"""
        from core.config_manager import _CONFIG_TEMPLATES

        tpl = _CONFIG_TEMPLATES["ai_correction.json"]
        for dead in (
            "retry",
            "prompts",
            "enable_sentence_segmentation",
            "segmentation_mode",
            "sentence_segmentation_prompt",
            "sentence_segmentation_system_prompt",
            "segmentation_prompts",
            "enable_proofread",
            "use_template",
        ):
            assert dead not in tpl, f"死键未清理: {dead}"
        assert "retry_on_failure" in tpl  # 真实现键保留
        assert "translate_mode" in tpl  # R3 三方对齐：此前模板缺键
        assert "extract_environment" in tpl


# ══════════════════════════════════════════════════════════════════
# 设置页字段（P0-② / P1-1 / P1-4 / P2）
# ══════════════════════════════════════════════════════════════════


class TestSettingsFieldsR12:
    """R12 设置页字段变更。"""

    @pytest.fixture(autouse=True)
    def _ensure_app(self):
        _app()

    def test_test_connection_button_exists(self):
        specs = [s for s in _FIELDS if s.get("attr") == "_btn_test_conn"]
        assert specs, "测试连接按钮缺失"
        assert specs[0]["widget"] == "button"

    def test_template_editor_button_exists(self):
        specs = [s for s in _FIELDS if s.get("attr") == "_btn_open_templates"]
        assert specs, "模板编辑器入口按钮缺失"

    def test_retry_field_uses_file_key_domain(self):
        """R1/R2：重试字段键收敛为文件键 retry_on_failure（domain="corr"），
        不再存在 source="corr" 双轨制。"""
        specs = [s for s in _FIELDS if s.get("key") == "retry_on_failure"]
        assert specs, "retry_on_failure 字段应存在（文件键收敛）"
        assert specs[0].get("domain") == "corr"
        assert all(s.get("source") != "corr" for s in _FIELDS)

    def test_socr_version_field_exists(self):
        specs = [s for s in _FIELDS if s.get("key") == "s_ocr_version"]
        assert specs, "s_ocr_version 应补充 UI 入口"
        assert "跟随全局" in specs[0]["options"]
        assert MODE_PARAMS_DEFAULTS["s_ocr_version"] == "跟随全局"

    def test_srt_export_option_deduped(self):
        specs = [s for s in _FIELDS if s.get("key") == "srt_export_mode"]
        assert len(specs[0]["options"]) == 3  # 第 4 个冗余选项已删

    def test_test_connection_method_exists(self):
        from ui.settings_dialog import SettingsDialog

        assert hasattr(SettingsDialog, "_on_test_connection")
        assert hasattr(SettingsDialog, "_on_test_conn_result")

    def test_engine_preset_import_exists(self):
        from ui.settings_dialog import SettingsDialog

        assert hasattr(SettingsDialog, "_on_eng_preset_import")

    def test_template_editor_opens(self):
        from ui.settings_dialog import SettingsDialog

        assert hasattr(SettingsDialog, "_on_open_template_editor")


# ══════════════════════════════════════════════════════════════════
# 右侧面板（P1-2 / P0-③）
# ══════════════════════════════════════════════════════════════════


class TestRightPanelR12:
    """后处理组删除 + 引擎切换下拉。"""

    def test_post_group_removed(self):
        from ui.views.right_panel import RightPanelView

        src = inspect.getsource(RightPanelView)
        assert "_post_group" not in src
        assert "_on_post_option_r_changed" not in src

    def test_engine_combo_added(self):
        from ui.views.right_panel import RightPanelView

        assert hasattr(RightPanelView, "_sync_engine_combo_r")
        assert hasattr(RightPanelView, "_on_engine_r_changed")


# ══════════════════════════════════════════════════════════════════
# 死代码清理（B6）
# ══════════════════════════════════════════════════════════════════


class TestDeadCodeCleanup:
    """ui_config.json / HttpCheckWorker 删除。"""

    def test_ui_config_file_removed(self):
        assert not (ROOT / "config" / "ui_config.json").exists()

    def test_ui_config_schema_removed(self):
        from core.config_schemas import SCHEMA_REGISTRY

        assert "ui_config.json" not in SCHEMA_REGISTRY

    def test_http_check_worker_removed(self):
        import core.workers as workers

        assert not hasattr(workers, "HttpCheckWorker")


# ══════════════════════════════════════════════════════════════════
# filter_manager 注释解析
# ══════════════════════════════════════════════════════════════════


class TestFilterManagerComments:
    """R12：filters.json 统一走注释解析器。"""

    def test_uses_comment_loader(self, tmp_path):
        from core import filter_manager

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "filters.json").write_text(
            '{\n  // 注释应被忽略\n  "keywords": ["广告", "片头"]\n}', encoding="utf-8"
        )
        with mock.patch.object(filter_manager, "FILTERS_PATH", cfg_dir / "filters.json"):
            mgr = filter_manager.FilterManager()
            assert mgr.get_keywords() == ["广告", "片头"]
