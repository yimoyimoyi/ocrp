"""设置对话框字段描述表与配置域的一致性测试。

``_FIELDS`` 是设置对话框的声明式单一来源，``KEY_MAP`` / ``DEFAULTS`` /
``UI_STATE_MODE_KEYS`` 是配置的声明式来源。两张表一旦漂移就会出现
"界面能改但没人读"或"配置有键但界面改不了"的静默缺陷。
"""

import pytest

from core.config_manager import MODE_PARAMS_DEFAULTS
from core.settings.domains import AsrConfig, CorrectionConfig, UiStateConfig
from ui.settings_dialog import _FIELDS


def _specs(domain: str) -> list[dict]:
    return [s for s in _FIELDS if s.get("domain", "ui") == domain and "key" in s]


class TestFieldKeyDomains:
    def test_ui_field_keys_are_configurable(self):
        """ui 域字段键必须能落进 mode_params（白名单或面板默认值）。"""
        allowed = set(UiStateConfig.UI_STATE_MODE_KEYS) | set(MODE_PARAMS_DEFAULTS)
        stray = [s["key"] for s in _specs("ui") if s["key"] not in allowed]
        assert stray == [], f"ui 字段键不可持久化: {stray}"

    def test_asr_field_keys_exist_in_domain_defaults(self):
        stray = [s["key"] for s in _specs("asr") if s["key"] not in AsrConfig.DEFAULTS]
        assert stray == [], f"asr 字段键不在 AsrConfig.DEFAULTS: {stray}"

    def test_corr_field_keys_exist_in_domain_defaults(self):
        stray = [s["key"] for s in _specs("corr") if s["key"] not in CorrectionConfig.DEFAULTS]
        assert stray == [], f"corr 字段键不在 CorrectionConfig.DEFAULTS: {stray}"

    def test_domain_is_known(self):
        known = {"ui", "asr", "corr"}
        unknown = sorted({s.get("domain", "ui") for s in _FIELDS} - known)
        assert unknown == [], f"未知 domain: {unknown}"


class TestCorrectionFieldsCoverage:
    """纠错域中"有配置无界面"的键必须显式登记为有意为之。"""

    #: 无需设置对话框字段的键 —— 由引擎面板/域逻辑承载，或功能已移除
    INTENTIONALLY_HEADLESS = {
        "engine",  # 由「AI 纠错引擎」下拉面板（_ENGINE_FIELDS）承载
        # 分句/上下文窗口功能已在早前批次移除（context_window 已删、segmentation_*
        # 进 DEAD_KEYS），seg_time_gap 无任何消费者。保留配置键以兼容既有
        # ai_correction.json（KEY_MAP/DEFAULTS 契约由 test_regression_r12 锁定），
        # 但**不再**提供设置字段 —— 否则界面在宣传一个不存在的功能
        # （原字段 tooltip 写的是"上下文窗口中，跳过时间间隔超过此值的行"）。
        "seg_time_gap",
    }

    def test_polish_prompt_is_editable(self):
        """回归：enable_polish 有开关但 polish_prompt 曾有键无界面，
        用户无法自定义润色提示词（而纠错/摘要/系统提示词都可编辑）。"""
        keys = {s["key"] for s in _specs("corr")}
        assert "polish_prompt" in keys, "polish_prompt 缺少设置对话框字段"

    def test_no_other_unexposed_correction_keys(self):
        exposed = {s["key"] for s in _specs("corr")}
        missing = sorted(set(CorrectionConfig.DEFAULTS) - exposed - self.INTENTIONALLY_HEADLESS)
        assert missing == [], f"纠错域存在无界面键: {missing}"


class TestFieldSpecShape:
    def test_every_field_has_attr_and_widget(self):
        for spec in _FIELDS:
            assert "attr" in spec, f"字段缺 attr: {spec.get('key')}"
            assert "widget" in spec, f"字段缺 widget: {spec.get('key')}"

    def test_attrs_are_unique(self):
        attrs = [s["attr"] for s in _FIELDS if "attr" in s]
        dupes = sorted({a for a in attrs if attrs.count(a) > 1})
        assert dupes == [], f"字段 attr 重复: {dupes}"

    def test_keys_are_unique_per_domain(self):
        for domain in ("ui", "asr", "corr"):
            keys = [s["key"] for s in _specs(domain)]
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            assert dupes == [], f"{domain} 域字段键重复: {dupes}"

    def test_load_map_and_on_change_reference_existing_methods(self):
        """on_load/on_change 指向的方法必须存在（字符串反射，改错了只在运行时炸）。"""
        import ui.settings_dialog as sd

        for spec in _FIELDS:
            for hook in ("on_load", "on_change"):
                name = spec.get(hook)
                if name:
                    assert hasattr(sd.SettingsDialog, name), f"{spec.get('key')}.{hook}={name} 不存在"

    @pytest.mark.parametrize("widget", ["combo", "spin", "double_spin", "check", "line", "text"])
    def test_supported_widget_types_are_handled(self, widget):
        """_field_value 必须能收集所有用到的控件类型（返回 None 会静默写坏配置）。"""
        import inspect

        import ui.settings_dialog as sd

        source = inspect.getsource(sd.SettingsDialog._field_value)
        assert f'"{widget}"' in source, f"_field_value 未处理控件类型: {widget}"
