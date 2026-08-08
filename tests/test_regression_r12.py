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

    def test_migrate_v0_to_v1_removes_dead_keys(self):
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
            },
        }
        cm._migrate_settings(cfg)
        assert cfg["config_version"] == 1
        assert "ai_correction_enabled" not in cfg
        mp = cfg["mode_params"]
        assert "corr_context_window" not in mp
        assert "corr_proofread" not in mp
        assert "corr_segmentation" not in mp
        # 合法键保留 + 默认值补齐
        assert mp["corr_translate"] is True
        assert "asr_language" in mp
        assert "frame_interval" in mp

    def test_migrate_idempotent(self):
        from core.config_manager import ConfigManager

        cm = object.__new__(ConfigManager)
        cfg = {"config_version": 1, "mode_params": {"corr_translate": True}}
        before = dict(cfg["mode_params"])
        cm._migrate_settings(cfg)
        assert cfg["config_version"] == 1
        assert cfg["mode_params"] == before  # 幂等：v1 不重复迁移


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
    """方向 A：asr_*/corr_* 从专用文件反同步到 mode_params。"""

    @staticmethod
    def _new_win():
        # PySide6 下 object.__new__(QMainWindow 子类) 不安全，需走 MainWindow.__new__
        from ui.main_window import MainWindow

        return MainWindow.__new__(MainWindow)

    def test_asr_restore_mapping(self):

        win = self._new_win()
        with mock.patch("core.asr_engine.load_asr_config", return_value={
            "language": "en",
            "vad_enabled": True,
            "vad_min_silence_ms": 400,
            "word_timestamps": False,
            "beam_size": 3,
            "model_size": "small",
            "model_dir": "models/asr",
        }), mock.patch("core.ai_correction.load_correction_config", return_value={}):
            params = win._restore_business_params({})
        assert params["asr_language"] == "en"
        assert params["asr_vad"] is True
        assert params["asr_vad_min_silence"] == 400
        assert params["asr_word_ts"] is False
        assert params["asr_beam_size"] == 3
        # model_size 是标准名且本地目录不存在 → asr_model_path 用标准名
        assert params["asr_model_path"] == "small"

    def test_corr_restore_mapping(self):

        win = self._new_win()
        with mock.patch("core.asr_engine.load_asr_config", return_value={}), mock.patch(
            "core.ai_correction.load_correction_config",
            return_value={
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
        ):
            params = win._restore_business_params({})
        assert params["corr_enabled"] is True
        assert params["corr_batch_size"] == 8
        assert params["corr_retry"] == 3
        assert params["corr_prompt"] == "校对提示"
        assert params["corr_stream"] is True
        assert params["corr_translate"] is True
        assert params["corr_polish"] is True
        assert params["seg_time_gap"] == 2.0
        assert params["corr_extract_env"] is True

    def test_saved_path_preferred_for_model(self):

        win = self._new_win()
        saved = {"asr_model_path": "E:/models/asr/large-v3"}
        with mock.patch("core.asr_engine.load_asr_config", return_value={"model_size": "small"}), mock.patch(
            "core.ai_correction.load_correction_config", return_value={}
        ):
            params = win._restore_business_params(saved)
        assert params["asr_model_path"] == "E:/models/asr/large-v3"  # UI 选择状态优先


# ══════════════════════════════════════════════════════════════════
# 断链修复（P1-④⑤⑥⑦）
# ══════════════════════════════════════════════════════════════════


class TestSyncCorrectionConfigFixes:
    """R12：_sync_correction_config 断链修复分支。"""

    @pytest.fixture(autouse=True)
    def _ensure_app(self):
        _app()

    def test_retry_writes_retry_on_failure(self):
        from ui.main_window import MainWindow

        src = inspect.getsource(MainWindow._sync_correction_config)
        assert 'cfg["retry_on_failure"] = params["corr_retry"]' in src
        assert 'cfg["retry"]' not in src.replace('cfg["retry_on_failure"]', "")

    def test_seg_time_gap_branch_present(self):
        from ui.main_window import MainWindow

        src = inspect.getsource(MainWindow._sync_correction_config)
        assert 'cfg["seg_time_gap"] = params["seg_time_gap"]' in src

    def test_extract_environment_branch_present(self):
        from ui.main_window import MainWindow

        src = inspect.getsource(MainWindow._sync_correction_config)
        assert 'cfg["extract_environment"] = params["corr_extract_env"]' in src

    def test_corr_prompt_truthy(self):
        from ui.main_window import MainWindow

        src = inspect.getsource(MainWindow._sync_correction_config)
        assert "if params.get(\"corr_prompt\"):" in src  # 空值不覆盖文件默认提示词


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

    def test_corr_template_has_segmentation_keys(self):
        from core.config_manager import _CONFIG_TEMPLATES

        tpl = _CONFIG_TEMPLATES["ai_correction.json"]
        assert "enable_sentence_segmentation" in tpl
        assert "segmentation_mode" in tpl
        assert "segmentation_prompts" in tpl
        assert "enable_proofread" in tpl
        assert "context_window" in tpl
        assert "use_template" in tpl
        assert "retry" not in tpl  # 死键删除


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

    def test_retry_field_removed(self):
        specs = [s for s in _FIELDS if s.get("key") == "retry_on_failure"]
        assert not specs, "retry_on_failure 字段应已删除（统一 corr_retry）"

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
