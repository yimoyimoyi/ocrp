"""R11 批次修复回归测试。

覆盖：
- B1 ASR 参数变更误判：_on_mode_changed 以 MODE_PARAMS_DEFAULTS 归一化后逐键比较，
  旧配置缺新增 asr 键（如 asr_temperature）不得触发 ASR 引擎重建
- B2 mode_params 单源化：_save_mode_params 从 ConfigPanel 读全量，过滤 defaults 之外的垃圾键
- B3 保存链路：corr 同步差异触发、_sync_correction_config 含 corr_translate → translate_mode 分支
- B4 translate_mode 持久化：AICorrector.__init__ 从 config 读取 translate_mode
- S1 条件化校验：_on_accept 仅当 corr_enabled 时校验模型名；_set_field_value 支持 corr_model_row 回填
- S2 summary_prompt 键名：_FIELDS 中 key=="summary_prompt" 且 source=="corr"
- P3 api_key/model spec 默认空串
- P4 _load_initial_values 空连接字段回填预设
- B4 决策：_sync_preset 已删除
- A2 scan_local_asr_models 单层扫描 + build_asr_model_items 共享函数
- UX：corr_preset 分组归位 API 连接、批量参数默认折叠、quick tooltip 引导、dirty 追踪
- 显示设置：_apply_theme_from_dialog 预览不写盘，_save_theme_from_dialog 拆分

MainWindow 相关测试用 object.__new__ 绕过 __init__（避免拉起完整引擎依赖），
SettingsDialog 测试使用 offscreen 平台。
"""

import inspect
import os
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.ai_correction import AICorrector
from core.asr_engine import STANDARD_ASR_MODELS, build_asr_model_items, scan_local_asr_models
from core.config_manager import MODE_PARAMS_DEFAULTS
from ui.config_panel import ConfigPanel
from ui.settings_dialog import _FIELDS, _TAB_GROUPS, SettingsDialog

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def app():
    _app = QApplication.instance() or QApplication([])
    yield _app


# ══════════════════════════════════════════════════════════════════
# 工具：绕过 __init__ 的 MainWindow 测试替身
# ══════════════════════════════════════════════════════════════════


class _FakeCorrector:
    """AICorrector 的最小替身：只承载 _on_mode_changed 会写入的属性。"""

    def __init__(self):
        self.polish_enabled = False
        self.use_template = False
        self.translate_mode = False
        self.stream_mode = False
        self.json_mode = False
        self.presets_applied: list[str] = []

    def apply_preset(self, name: str):
        self.presets_applied.append(name)


def _make_window():
    """构造一个未执行 __init__ 的 MainWindow 实例（仅测方法逻辑，不拉起 Qt 引擎）。"""
    from ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._config_panel = ConfigPanel()
    win._corrector = _FakeCorrector()
    win._last_mode_params = {}
    # 防御性兜底：无 mode_save_timer 时 _schedule_mode_save 会创建 QTimer(self)——
    # 对未初始化实例非法，统一打桩
    return win


# ══════════════════════════════════════════════════════════════════
# B1 ASR 参数变更误判修复
# ══════════════════════════════════════════════════════════════════


class TestAsrCompareNormalized:
    """B1 回归：_on_mode_changed 的 ASR 变更判定必须按默认值归一化。"""

    @staticmethod
    def _restore_mode_params(win, saved: dict):
        """复刻 _restore_mode_params 的归一化：_last_mode_params = defaults + saved。"""
        base = dict(MODE_PARAMS_DEFAULTS)
        base.update(saved)
        win._last_mode_params = base
        win._config_panel.mode_changed.connect(win._on_mode_changed)

    def test_missing_asr_key_not_misjudged(self):
        """旧配置缺 asr_temperature 等新键：apply_mode_params 后不得误判 ASR 变更。"""
        from ui.main_window import MainWindow

        win = _make_window()
        self._restore_mode_params(win, {"asr_language": "ja"})  # saved 缺 asr_temperature
        with (
            mock.patch.object(MainWindow, "_schedule_mode_save"),
            mock.patch.object(MainWindow, "_schedule_asr_restart"),
            mock.patch.object(MainWindow, "_restart_ocr_engine"),
        ):
            # 与 _restore_mode_params 相同的恢复路径：apply_mode_params 触发 mode_changed
            win._config_panel.apply_mode_params({"asr_language": "ja"})
            assert win._asr_params_changed is False

    def test_missing_multiple_asr_keys_not_misjudged(self):
        """缺多个 asr 键（temperature/hotwords/vad）也不误判。"""
        from ui.main_window import MainWindow

        win = _make_window()
        saved = {"asr_model_size": "base", "asr_language": "en"}
        self._restore_mode_params(win, saved)
        with (
            mock.patch.object(MainWindow, "_schedule_mode_save"),
            mock.patch.object(MainWindow, "_schedule_asr_restart"),
            mock.patch.object(MainWindow, "_restart_ocr_engine"),
        ):
            win._config_panel.apply_mode_params(saved)
            assert win._asr_params_changed is False

    def test_real_asr_change_still_detected(self):
        """真正修改 asr 参数（如语言）必须被判定为变更（防止过度修复）。"""
        from ui.main_window import MainWindow

        win = _make_window()
        self._restore_mode_params(win, {"asr_language": "zh"})
        with (
            mock.patch.object(MainWindow, "_schedule_mode_save"),
            mock.patch.object(MainWindow, "_schedule_asr_restart"),
            mock.patch.object(MainWindow, "_restart_ocr_engine"),
        ):
            win._config_panel.apply_mode_params({"asr_language": "ja"})
            assert win._asr_params_changed is True

    def test_corr_translate_synced_on_mode_changed(self):
        """B4 联动：corr_translate 出现在 mode_params 中时同步到 corrector 实例。"""
        from ui.main_window import MainWindow

        win = _make_window()
        self._restore_mode_params(win, {})
        with (
            mock.patch.object(MainWindow, "_schedule_mode_save"),
            mock.patch.object(MainWindow, "_schedule_asr_restart"),
            mock.patch.object(MainWindow, "_restart_ocr_engine"),
        ):
            win._config_panel.apply_mode_params({"corr_translate": True})
            assert win._corrector.translate_mode is True


# ══════════════════════════════════════════════════════════════════
# S2 summary_prompt 键名修复
# ══════════════════════════════════════════════════════════════════


class TestSummaryPromptKey:
    """S2 回归：_FIELDS 中环境提示词 spec 键名必须为 summary_prompt（文件真实键）。"""

    def test_spec_exists_with_correct_key_and_source(self):
        specs = [s for s in _FIELDS if s.get("key") == "summary_prompt"]
        assert len(specs) == 1
        spec = specs[0]
        assert spec["source"] == "corr"  # 从 ai_correction.json 读取，而非 mode_params
        assert spec["attr"] == "_corr_summary_prompt"
        assert spec["widget"] == "text"
        assert spec.get("default") == ""

    def test_no_legacy_key_name_left(self):
        """旧键名 corr_summary_prompt 不得再出现在 _FIELDS（防止键名回归）。"""
        assert all(s.get("key") != "corr_summary_prompt" for s in _FIELDS)


# ══════════════════════════════════════════════════════════════════
# S1 条件化校验 + _set_field_value corr_model_row 分支
# ══════════════════════════════════════════════════════════════════


@pytest.fixture()
def dialog(app):
    return SettingsDialog(ConfigPanel(), correction_config={}, filter_keywords=[], engine_manager=None)


class TestAcceptValidationConditional:
    """S1 回归：_on_accept 仅当 corr_enabled 勾选时才强制校验模型名。"""

    def test_disabled_with_empty_model_accepts(self, dialog):
        """未启用 AI 纠错时模型为空也必须能保存（纯 OCR 用户不被拦截）。"""
        dialog._corr_enabled.setChecked(False)
        dialog._corr_api_model.setEditText("")
        with (
            mock.patch.object(ui_sd().QMessageBox, "warning") as warn,
            mock.patch.object(SettingsDialog, "accept") as accept,
        ):
            dialog._on_accept()
            warn.assert_not_called()
            accept.assert_called_once()

    def test_enabled_with_empty_model_warns(self, dialog):
        """启用纠错且模型为空：弹警告且不 accept。"""
        dialog._corr_enabled.setChecked(True)
        dialog._corr_api_model.setEditText("")
        with (
            mock.patch.object(ui_sd().QMessageBox, "warning") as warn,
            mock.patch.object(SettingsDialog, "accept") as accept,
        ):
            dialog._on_accept()
            warn.assert_called_once()
            accept.assert_not_called()

    def test_enabled_with_model_accepts(self, dialog):
        """启用纠错且模型非空：正常保存。"""
        dialog._corr_enabled.setChecked(True)
        dialog._corr_api_model.setEditText("deepseek-chat")
        with (
            mock.patch.object(ui_sd().QMessageBox, "warning") as warn,
            mock.patch.object(SettingsDialog, "accept") as accept,
        ):
            dialog._on_accept()
            warn.assert_not_called()
            accept.assert_called_once()


def ui_sd():
    """惰性引用 ui.settings_dialog 模块（避免与局部 import 命名冲突）。"""
    import ui.settings_dialog as sd

    return sd


class TestModelFieldLoad:
    """S1 回归：_set_field_value 支持 corr_model_row 回填，且对脏值（None/错类型）不抛异常。"""

    @staticmethod
    def _model_spec():
        specs = [s for s in _FIELDS if s.get("widget") == "corr_model_row"]
        assert len(specs) == 1
        return specs[0]

    def test_model_name_backfilled(self, dialog):
        """配置中的模型名必须回显到可编辑下拉（此前永不回填）。"""
        spec = self._model_spec()
        dialog._set_field_value(spec, dialog._corr_api_model, "gpt-4o")
        assert dialog._corr_api_model.currentText() == "gpt-4o"

    def test_empty_model_clears(self, dialog):
        spec = self._model_spec()
        dialog._corr_api_model.setEditText("old")
        dialog._set_field_value(spec, dialog._corr_api_model, "")
        assert dialog._corr_api_model.currentText() == ""

    def test_none_value_does_not_raise(self, dialog):
        """None 模型值 → 空串，不抛异常（R11 防御）。"""
        spec = self._model_spec()
        dialog._set_field_value(spec, dialog._corr_api_model, None)
        assert dialog._corr_api_model.currentText() == ""

    def test_wrong_type_swallowed(self, dialog):
        """spin 字段收到 None：int(None) 抛 TypeError，必须被捕获不崩（R11 防御）。"""
        spin_spec = next(s for s in _FIELDS if s.get("key") == "corr_batch_size")
        dialog._set_field_value(spin_spec, dialog._corr_batch, None)  # 不应抛异常


# ══════════════════════════════════════════════════════════════════
# B4 translate_mode 持久化
# ══════════════════════════════════════════════════════════════════


class TestTranslateModePersisted:
    """B4 回归：AICorrector 从配置读取 translate_mode（不再硬编码 False）。"""

    def test_init_reads_translate_mode_true(self):
        corr = AICorrector(config={"translate_mode": True, "api_key": "", "model": "m"})
        assert corr.translate_mode is True

    def test_init_default_false_when_key_missing(self):
        corr = AICorrector(config={"api_key": "", "model": "m"})
        assert corr.translate_mode is False

    def test_init_truthy_value_coerced_to_bool(self):
        corr = AICorrector(config={"translate_mode": 1, "api_key": "", "model": "m"})
        assert corr.translate_mode is True


# ══════════════════════════════════════════════════════════════════
# B2 单源化：_save_mode_params 过滤垃圾键
# ══════════════════════════════════════════════════════════════════


class TestSaveModeParamsFiltersJunkKeys:
    """B2 回归：_save_mode_params 只持久化 MODE_PARAMS_DEFAULTS 内的键。"""

    def test_junk_keys_filtered(self):

        win = _make_window()
        # 注入垃圾键（历史死键）+ 运行时通道键
        win._config_panel.apply_mode_params(
            {
                "corr_context_window": 3,
                "corr_proofread": True,
                "corr_segmentation": "x",
                "corr_summary_prompt": "领域：测试",
                "asr_temperature": "0.0,0.2",
            }
        )
        win._config_mgr = mock.Mock()
        win._asr_params_changed = False
        win._corr_params_changed = False
        win._save_mode_params()

        set_calls = [c for c in win._config_mgr.set.call_args_list if c.args and c.args[0] == "mode_params"]
        assert set_calls, "mode_params 未被写入 ConfigManager"
        params = set_calls[-1].args[1]
        assert "corr_context_window" not in params
        assert "corr_proofread" not in params
        assert "corr_segmentation" not in params
        assert "corr_summary_prompt" not in params  # 环境提示词仅作运行时通道，不持久化
        assert set(params) <= set(MODE_PARAMS_DEFAULTS)
        # R12 方向 A：业务参数（asr_*/corr_*）不再写 settings.json（文件为事实源）
        assert "asr_temperature" not in params
        assert "asr_language" not in params
        assert "frame_interval" in params  # UI 状态键保留
        win._config_mgr.save_settings.assert_called_once()

    def test_no_junk_from_clean_state(self):
        """全默认状态保存：恰好是 UI 状态键（非 asr_*/corr_*，除 corr_preset）。"""

        win = _make_window()
        win._config_mgr = mock.Mock()
        win._asr_params_changed = False
        win._corr_params_changed = False
        win._save_mode_params()
        set_calls = [c for c in win._config_mgr.set.call_args_list if c.args and c.args[0] == "mode_params"]
        params = set_calls[-1].args[1]
        expected = {
            k for k in MODE_PARAMS_DEFAULTS
            if k != "corr_summary_prompt"
            and (k == "corr_preset" or not (k.startswith("asr_") or k.startswith("corr_")))
        }
        assert set(params) == expected

    def test_corr_changed_flag_gates_sync(self):
        """差异触发：_corr_params_changed=False 时不调用 _sync_correction_config。"""
        from ui.main_window import MainWindow

        win = _make_window()
        win._config_mgr = mock.Mock()
        win._asr_params_changed = False
        win._corr_params_changed = False
        with mock.patch.object(MainWindow, "_sync_correction_config") as sync:
            win._save_mode_params()
            sync.assert_not_called()
            win._config_mgr.save_settings.assert_called_once()  # 数据本身仍写盘


class TestSyncCorrectionConfigTranslateBranch:
    """B3 回归：_sync_correction_config 必须包含 corr_translate → translate_mode 分支。"""

    def test_translate_branch_present(self):
        from ui.main_window import MainWindow

        src = inspect.getsource(MainWindow._sync_correction_config)
        assert '"translate_mode"' in src
        assert '"corr_translate"' in src
        assert src.index('"corr_translate"') < src.index('"translate_mode"')


# ══════════════════════════════════════════════════════════════════
# A2 单层扫描 + 模型条目共享构建
# ══════════════════════════════════════════════════════════════════


class TestScanLocalAsrModelsSingleLevel:
    """A2 回归：扫描只识别模型目录根部的 model.bin（单层，启动性能修复）。"""

    def test_only_direct_child_model_bin_recognized(self, tmp_path):
        target = tmp_path / "models" / "asr"
        (target / "m1").mkdir(parents=True)
        (target / "m1" / "model.bin").write_bytes(b"x")
        (target / "m2").mkdir()  # 无 model.bin
        # 嵌套更深结构：m3/nested/model.bin、m4/sub/model.bin —— 均不得被识别
        (target / "m3" / "nested").mkdir(parents=True)
        (target / "m3" / "nested" / "model.bin").write_bytes(b"x")
        (target / "m4" / "sub").mkdir(parents=True)
        (target / "m4" / "sub" / "model.bin").write_bytes(b"x")

        res = scan_local_asr_models(str(target))
        names = sorted(Path(p).name for p in res)
        assert names == ["m1"]
        assert all(Path(p).is_dir() for p in res)

    def test_top_level_model_bin_prepended(self, tmp_path):
        """目录自身含 model.bin 时排在最前（insert(0)）。"""
        (tmp_path / "model.bin").write_bytes(b"x")
        (tmp_path / "m1").mkdir()
        (tmp_path / "m1" / "model.bin").write_bytes(b"x")
        res = scan_local_asr_models(str(tmp_path))
        assert Path(res[0]) == tmp_path
        assert Path(res[1]).name == "m1"

    def test_missing_dir_returns_empty(self, tmp_path):
        assert scan_local_asr_models(str(tmp_path / "nonexistent")) == []

    def test_unreadable_dir_returns_empty(self, tmp_path):
        """目录无法读取时返回空列表而不是抛异常。"""
        target = tmp_path / "models"
        target.mkdir()
        with mock.patch("os.scandir", side_effect=PermissionError("denied")):
            assert scan_local_asr_models(str(target)) == []


class TestBuildAsrModelItems:
    """A2 回归：本地模型 + 标准模型条目生成，重复项跳过。"""

    def test_local_and_standard_items(self):
        local = [str(Path("C:/models/large-v3")), str(Path("C:/models/my_local"))]
        items = build_asr_model_items(local)
        displays = [d for d, _ in items]
        datas = [d for _, d in items]
        assert displays[:2] == ["📁 large-v3", "📁 my_local"]
        assert datas[:2] == local  # 本地条目 data 为完整路径
        # 标准模型全部在场（data 为标准名），本地已有的 large-v3 被跳过
        expected_standard = [s for s in STANDARD_ASR_MODELS if s != "large-v3"]
        assert datas[2:] == expected_standard
        assert "⬇ tiny（在线下载）" in displays
        assert "⬇ large-v3（在线下载）" not in displays

    def test_empty_local_returns_all_standard(self):
        items = build_asr_model_items([])
        assert len(items) == len(STANDARD_ASR_MODELS)
        assert [d for _, d in items] == STANDARD_ASR_MODELS

    def test_no_duplicate_datas(self):
        local = [str(Path("D:/m/base")), str(Path("D:/m/small"))]
        items = build_asr_model_items(local)
        datas = [d for _, d in items]
        assert len(datas) == len(set(datas))


# ══════════════════════════════════════════════════════════════════
# B4 决策：_sync_preset 已删除；P3/P4/UX 结构断言
# ══════════════════════════════════════════════════════════════════


class TestSyncPresetRemoved:
    """B4 决策回归：SettingsDialog 不得再存在 _sync_preset（预设不再自动回写）。"""

    def test_method_absent(self):
        assert not hasattr(SettingsDialog, "_sync_preset")
        src = inspect.getsource(SettingsDialog)
        assert "def _sync_preset" not in src
        assert "def _on_accept" in src

    def test_on_accept_does_not_write_presets(self):
        src = inspect.getsource(SettingsDialog._on_accept)
        assert "def _sync_preset" not in src
        assert "self._sync_preset(" not in src  # 注释提及不算，实际调用必须不存在
        assert "add_preset" not in src  # 保存预设由「💾 保存为 API 预设」按钮显式完成


class TestSettingsFieldStructure:
    """P3/P4/UX 结构回归：spec 默认值、分组归位、分组折叠状态。"""

    def test_api_key_and_model_default_empty(self):
        api_key = next(s for s in _FIELDS if s.get("key") == "api_key")
        model = next(s for s in _FIELDS if s.get("key") == "model")
        assert api_key.get("default") == ""
        assert model.get("default") == ""

    def test_corr_preset_group_back_to_api_connection(self):
        preset = next(s for s in _FIELDS if s.get("key") == "corr_preset")
        assert preset["group"] == "API 连接"

    def test_batch_params_collapsed_api_connection_expanded(self):
        groups = {g["title"]: g for g in _TAB_GROUPS["correction"]}
        assert groups["批量参数"]["collapsed"] is True
        assert "collapsed" not in groups["API 连接"] or groups["API 连接"].get("collapsed") is False

    def test_quick_fields_count(self):
        """quick 字段应 ≥13 个（R11 承诺 13 个 + 实际实现），防止 tooltip 引导缺失回归。"""
        quick = [s for s in _FIELDS if s.get("quick")]
        assert len(quick) >= 13

    def test_quick_tooltip_guidance_mechanism(self):
        """quick 字段 tooltip 追加「主窗口可快捷调整」引导（源码级断言）。"""
        import ui.settings_dialog as sd

        src = inspect.getsource(sd.SettingsDialog._build_field)
        assert "主窗口工具栏/右侧面板可快捷调整" in src or "可快捷调整" in src
        assert "spec.get(\"quick\")" in src


# ══════════════════════════════════════════════════════════════════
# UX：dirty 追踪（reject 确认）
# ══════════════════════════════════════════════════════════════════


class TestDirtyTrackingReject:
    """R11 UX 回归：reject 仅在 dirty 时确认；未修改时直接关闭；不影响 accept 路径。"""

    def test_clean_reject_no_prompt(self, dialog):
        with mock.patch.object(QMessageBox, "question") as question:
            dialog.reject()
            question.assert_not_called()

    def test_dirty_reject_prompts(self, dialog):
        dialog._mark_dirty()
        with mock.patch.object(
            QMessageBox, "question", return_value=QMessageBox.Yes
        ) as question:
            dialog.reject()
            question.assert_called_once()

    def test_dirty_reject_no_keeps_open(self, dialog):
        dialog._mark_dirty()
        with mock.patch.object(
            QMessageBox, "question", return_value=QMessageBox.No
        ) as question:
            dialog.reject()
            question.assert_called_once()

    def test_initial_state_not_dirty(self, dialog):
        """初始回填不得误报 dirty（信号在 _load_initial_values 之后才接线）。"""
        assert dialog._dirty is False

    def test_accept_path_not_blocked_by_reject(self, dialog):
        """确定按钮走 _on_accept → accept()，不受 reject 确认逻辑影响。"""
        dialog._corr_enabled.setChecked(False)
        with (
            mock.patch.object(ui_sd().QMessageBox, "warning"),
            mock.patch.object(SettingsDialog, "accept") as accept,
            mock.patch.object(QMessageBox, "question") as question,
        ):
            dialog._on_accept()
            accept.assert_called_once()
            question.assert_not_called()


# ══════════════════════════════════════════════════════════════════
# 显示设置：预览不写盘（拆分 _save_theme_from_dialog）
# ══════════════════════════════════════════════════════════════════


class TestDisplaySaveSplit:
    """R11 回归：主题预览不写盘；持久化只在确定后由 _save_theme_from_dialog 执行。"""

    def test_save_theme_from_dialog_exists(self):
        from ui.main_window import MainWindow

        assert hasattr(MainWindow, "_save_theme_from_dialog")
        src = inspect.getsource(MainWindow._save_theme_from_dialog)
        assert "config_mgr.set" in src
        assert "save_settings" in src

    def test_apply_theme_from_dialog_preview_only(self):
        from ui.main_window import MainWindow

        src = inspect.getsource(MainWindow._apply_theme_from_dialog)
        assert "config_mgr" not in src
        assert "save_settings" not in src
        assert "set(\"theme\"" not in src
        assert "_apply_theme(" in src

    def test_display_dialog_wiring(self):
        from ui.main_window import MainWindow

        src = inspect.getsource(MainWindow._open_display_settings)
        assert "theme_applied.connect(self._apply_theme_from_dialog)" in src
        assert "_save_theme_from_dialog(" in src
