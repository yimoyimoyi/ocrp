"""默认提示词单一事实源测试（P23）。

回归背景：``correction_prompt`` / ``polish_prompt`` 的默认值曾在三个位置各存一份
（core/ai_correction.py 运行时回落、core/config_manager.py 建文件模板、
core/settings/domains.py 域对象 DEFAULTS），并且已经漂移 —— polish_prompt 的
域对象副本是被截断且无 ``{待校对文本}`` 占位符的句子，而建文件时写入的正是它，
于是实际生效的润色提示词永远不含正文。
"""

from core.config_manager import _CONFIG_TEMPLATES
from core.prompts import (
    DEFAULT_CORRECTION_PROMPT,
    DEFAULT_CORRECTION_SYSTEM_PROMPT,
    DEFAULT_POLISH_PROMPT,
    DEFAULT_POLISH_SYSTEM_PROMPT,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_TRANSLATE_SYSTEM_PROMPT,
    POLISH_TEXT_PLACEHOLDER,
    is_placeholder_free_polish_prompt,
)
from core.settings.domains import CorrectionConfig


class TestSingleSourceOfTruth:
    """三处默认值必须全部指向 core/prompts.py 的常量。"""

    def test_domain_defaults_match_shared_constants(self):
        assert CorrectionConfig.DEFAULTS["correction_prompt"] == DEFAULT_CORRECTION_PROMPT
        assert CorrectionConfig.DEFAULTS["polish_prompt"] == DEFAULT_POLISH_PROMPT

    def test_config_file_template_matches_shared_constants(self):
        template = _CONFIG_TEMPLATES["ai_correction.json"]
        assert template["correction_prompt"] == DEFAULT_CORRECTION_PROMPT
        assert template["polish_prompt"] == DEFAULT_POLISH_PROMPT

    def test_domain_and_template_agree_with_each_other(self):
        template = _CONFIG_TEMPLATES["ai_correction.json"]
        for key in ("correction_prompt", "polish_prompt"):
            assert template[key] == CorrectionConfig.DEFAULTS[key], f"{key} 默认值漂移"

    def test_ai_correction_fallbacks_are_the_shared_constants(self):
        """AICorrector 在配置缺键时的回落值必须与共享常量一致。"""
        from core.ai_correction import AICorrector

        # 注意：不能用空 dict —— AICorrector 内部 `config or load_...()` 会回落到真实配置文件
        corr = AICorrector(config={"enabled": False})
        assert corr._prompt_template == DEFAULT_CORRECTION_PROMPT
        assert corr._correction_system_prompt == DEFAULT_CORRECTION_SYSTEM_PROMPT
        assert corr._summary_prompt == DEFAULT_SUMMARY_PROMPT
        assert corr._polish_prompt == DEFAULT_POLISH_PROMPT


class TestPromptPayloadPlaceholders:
    """提示词必须含有能注入正文的占位符，否则模型收到的指令没有内容。"""

    def test_polish_default_prompt_contains_text_placeholder(self):
        assert POLISH_TEXT_PLACEHOLDER in DEFAULT_POLISH_PROMPT

    def test_domain_polish_default_contains_text_placeholder(self):
        assert POLISH_TEXT_PLACEHOLDER in CorrectionConfig.DEFAULTS["polish_prompt"]

    def test_template_polish_default_contains_text_placeholder(self):
        assert POLISH_TEXT_PLACEHOLDER in _CONFIG_TEMPLATES["ai_correction.json"]["polish_prompt"]

    def test_placeholder_free_detection(self):
        assert is_placeholder_free_polish_prompt("请对翻译/纠错后的字幕进行润色...") is True
        assert is_placeholder_free_polish_prompt("") is True
        assert is_placeholder_free_polish_prompt(None) is True
        assert is_placeholder_free_polish_prompt("请润色：{待校对文本}") is False
        assert is_placeholder_free_polish_prompt("请润色：{原始结果}") is False

    def test_translate_system_prompt_mentions_line_count_invariant(self):
        """逐行翻译必须保持行数一致，否则 ID 映射会错位。"""
        assert "行数" in DEFAULT_TRANSLATE_SYSTEM_PROMPT

    def test_polish_system_prompt_is_non_empty(self):
        assert DEFAULT_POLISH_SYSTEM_PROMPT.strip()
