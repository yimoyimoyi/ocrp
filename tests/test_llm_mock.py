"""LLM 模拟调用测试 —— 使用 mock 验证 AICorrector 核心流程。"""

from unittest.mock import patch

from core.ai_correction import AICorrector, _clean_content

# ── 辅助工具 ──

def _make_corrector(**overrides) -> AICorrector:
    """构造最小可用的 AICorrector 实例（mock 配置）。"""
    config = {
        "enabled": True,
        "engine": "openai_vision",
        "api_key": "test-key",
        "base_url": "http://localhost:8080",
        "model": "test-model",
        "timeout": 10,
        "correction_prompt": "请纠正OCR错误。",
        "correction_system_prompt": "你是字幕校对助手。",
        "output_format": "",
        "seg_time_gap": 3.0,
        "enable_polish": False,
        "polish_prompt": "润色：{原始结果} / {待校对文本}",
        "summary_prompt": "总结领域和氛围。",
        "extract_environment": False,
        "retry_on_failure": 0,
    }
    config.update(overrides)
    return AICorrector(config=config)


# ── 单条纠错 ──

class TestCorrectSingle:
    """测试 AICorrector.correct() 单条纠错。"""

    @patch("core.ai_correction.ask_llm")
    def test_basic_correction(self, mock_llm):
        mock_llm.return_value = "你好世界"
        c = _make_corrector()
        result = c.correct("你好世界")
        assert result == "你好世界"
        mock_llm.assert_called_once()

    @patch("core.ai_correction.ask_llm")
    def test_correction_returns_different_text(self, mock_llm):
        mock_llm.return_value = "修正后的文本"
        c = _make_corrector()
        result = c.correct("错误的文本")
        assert result == "修正后的文本"

    @patch("core.ai_correction.ask_llm")
    def test_correction_returns_none_passthrough(self, mock_llm):
        mock_llm.return_value = None
        c = _make_corrector()
        result = c.correct("原始文本")
        # ask_llm 返回 None 时，correct() 也返回 None
        assert result is None

    @patch("core.ai_correction.ask_llm")
    def test_empty_input_returns_early(self, mock_llm):
        c = _make_corrector()
        result = c.correct("")
        assert result == ""
        mock_llm.assert_not_called()

    @patch("core.ai_correction.ask_llm")
    def test_whitespace_input_returns_early(self, mock_llm):
        c = _make_corrector()
        result = c.correct("   ")
        assert result == "   "
        mock_llm.assert_not_called()


# ── 翻译模式 ──

class TestTranslateMode:
    """测试翻译模式下的纠错。"""

    @patch("core.ai_correction.ask_llm")
    def test_translate_mode_prompt_contains_translation(self, mock_llm):
        mock_llm.return_value = "翻译结果"
        c = _make_corrector()
        c._translate_mode = True
        result = c.correct("Hello World")
        assert result == "翻译结果"
        # 验证 prompt 包含翻译指令
        call_args = mock_llm.call_args
        prompt = call_args.kwargs.get("prompt", "") or call_args[1].get("prompt", "")
        assert "翻译" in prompt or "中文" in prompt

    @patch("core.ai_correction.ask_llm")
    def test_translate_mode_with_context(self, mock_llm):
        mock_llm.return_value = "你好"
        c = _make_corrector()
        c._translate_mode = True
        result = c.correct("Hello", context_texts=["前文", "后文"])
        assert result == "你好"
        call_args = mock_llm.call_args
        prompt = call_args.kwargs.get("prompt", "") or call_args[1].get("prompt", "")
        assert "前文" in prompt


# ── 批量纠错 ──

class TestCorrectBatch:
    """测试 AICorrector.correct_batch() 批量纠错。"""

    @patch("core.ai_correction.ask_llm")
    def test_batch_basic(self, mock_llm):
        mock_llm.return_value = "[ID:0] 你好\n[ID:1] 世界"
        c = _make_corrector()
        c.correct_batch([(0, "你好"), (1, "世界")])
        # 纠错结果与原文相同时不返回（跳过相同项）
        # 但如果 ask_llm 返回了不同内容就会被收录
        assert mock_llm.called

    @patch("core.ai_correction.ask_llm")
    def test_batch_with_corrections(self, mock_llm):
        mock_llm.return_value = "[ID:0] 修正一\n[ID:1] 修正二"
        c = _make_corrector()
        result = c.correct_batch([(10, "错误一"), (11, "错误二")])
        # row_idx 应映射回原始行号
        assert 10 in result or 11 in result

    @patch("core.ai_correction.ask_llm")
    def test_batch_empty_input(self, mock_llm):
        c = _make_corrector()
        result = c.correct_batch([])
        assert result == {}
        mock_llm.assert_not_called()

    @patch("core.ai_correction.ask_llm")
    def test_batch_api_failure_fills_original(self, mock_llm):
        mock_llm.return_value = None
        c = _make_corrector()
        result = c.correct_batch([(0, "原文")], max_retries=0)
        # API 失败时用原文填充
        assert 0 in result
        assert result[0] == "原文"

    @patch("core.ai_correction.ask_llm")
    def test_batch_malformed_response_fills_original(self, mock_llm):
        mock_llm.return_value = "这不是有效的ID格式"
        c = _make_corrector()
        result = c.correct_batch([(0, "原文")], max_retries=0)
        assert 0 in result


# ── 润色 ──

class TestPolish:
    """测试 AICorrector.polish() 润色功能。"""

    @patch("core.ai_correction.ask_llm")
    def test_polish_disabled_passthrough(self, mock_llm):
        c = _make_corrector(enable_polish=False)
        result = c.polish("原始", "纠错文本")
        assert result == "纠错文本"
        mock_llm.assert_not_called()

    @patch("core.ai_correction.ask_llm")
    def test_polish_enabled(self, mock_llm):
        mock_llm.return_value = "润色后文本"
        c = _make_corrector(enable_polish=True)
        result = c.polish("原始", "纠错文本")
        assert result == "润色后文本"

    @patch("core.ai_correction.ask_llm")
    def test_polish_api_failure_returns_corrected(self, mock_llm):
        mock_llm.return_value = None
        c = _make_corrector(enable_polish=True)
        result = c.polish("原始", "纠错文本")
        # API 失败时返回纠错文本作为 fallback
        assert result == "纠错文本"

    @patch("core.ai_correction.ask_llm")
    def test_polish_empty_inputs(self, mock_llm):
        c = _make_corrector(enable_polish=True)
        result = c.polish("", "纠错文本")
        assert result == "纠错文本"
        mock_llm.assert_not_called()


# ── 环境提取跳过逻辑 ──

class TestShouldSkipEnvExtraction:
    """测试 _should_skip_env_extraction() 条件判断。"""

    def test_skip_when_extract_env_enabled(self):
        c = _make_corrector(extract_environment=True)
        assert c._should_skip_env_extraction() is True

    def test_skip_when_env_context_exists(self):
        c = _make_corrector()
        c._env_context = "已有的环境上下文"
        assert c._should_skip_env_extraction() is True

    def test_skip_when_prompt_contains_env_placeholder(self):
        c = _make_corrector(correction_prompt="请参考{环境信息}进行纠正。")
        assert c._should_skip_env_extraction() is True

    def test_skip_when_summary_contains_domain(self):
        c = _make_corrector(summary_prompt="请总结领域类型。")
        assert c._should_skip_env_extraction() is True

    def test_skip_when_polish_contains_env(self):
        c = _make_corrector(polish_prompt="请参考环境描述进行润色。")
        assert c._should_skip_env_extraction() is True

    def test_no_skip_when_all_empty(self):
        c = _make_corrector(
            correction_prompt="纠正错误。",
            summary_prompt="总结。",
            polish_prompt="润色。",
            extract_environment=False,
        )
        c._env_context = ""
        assert c._should_skip_env_extraction() is False


# ── 占位符替换 ──

class TestResolvePlaceholders:
    """测试 AICorrector._resolve_placeholders() 占位符替换。"""

    def test_replace_raw_text(self):
        result = AICorrector._resolve_placeholders("原始：{原始结果}", raw_text="你好")
        assert result == "原始：你好"

    def test_replace_context(self):
        result = AICorrector._resolve_placeholders("上下文：{上下文}", context="前文")
        assert result == "上下文：前文"

    def test_replace_env(self):
        result = AICorrector._resolve_placeholders("环境：{环境信息}", env_context="游戏对话")
        assert result == "环境：游戏对话"

    def test_replace_timestamp(self):
        result = AICorrector._resolve_placeholders("时间：{时间戳}", timestamp="00:01:23")
        assert result == "时间：00:01:23"

    def test_replace_multiple(self):
        tpl = "{原始结果} | {上下文} | {环境信息}"
        result = AICorrector._resolve_placeholders(tpl, raw_text="A", context="B", env_context="C")
        assert result == "A | B | C"

    def test_replace_all_placeholders(self):
        tpl = "{原始结果}{上下文}{环境信息}{时间戳}{区域}{引擎}{语言}"
        result = AICorrector._resolve_placeholders(
            tpl, raw_text="a", context="b", env_context="c",
            timestamp="t", region="r", engine="e", language="l",
        )
        assert result == "abctrel"


# ── 清理标记 ──

class TestCleanContent:
    """测试 _clean_content() 标记清理。"""

    def test_clean_id_tag(self):
        assert _clean_content("[ID:0] 你好") == "你好"

    def test_clean_time_marker(self):
        assert _clean_content("[00:01:23 -> 00:01:25] 文本") == "文本"

    def test_clean_markdown_fence(self):
        assert _clean_content("```\n文本\n```") == "文本"

    def test_clean_combined(self):
        assert _clean_content("[ID:0] [00:01:23] 你好世界") == "你好世界"


# ── 缓存 key 包含 max_tokens ──

class TestCacheKey:
    """测试缓存 key 计算包含所有关键参数。"""

    def test_cache_key_includes_max_tokens(self):
        from core.llm_utils.llm_client import _get_cache_key

        key1 = _get_cache_key("model", 0.1, "prompt", "sys", 512)
        key2 = _get_cache_key("model", 0.1, "prompt", "sys", 2048)
        assert key1 != key2

    def test_cache_key_same_params(self):
        from core.llm_utils.llm_client import _get_cache_key

        key1 = _get_cache_key("model", 0.1, "prompt", "sys", 1024)
        key2 = _get_cache_key("model", 0.1, "prompt", "sys", 1024)
        assert key1 == key2

    def test_cache_key_includes_resp_type(self):
        from core.llm_utils.llm_client import _get_cache_key

        key_text = _get_cache_key("model", 0.1, "prompt", "sys", 1024, resp_type=None)
        key_json = _get_cache_key("model", 0.1, "prompt", "sys", 1024, resp_type="json")
        assert key_text != key_json

    def test_cache_key_includes_base_url(self):
        from core.llm_utils.llm_client import _get_cache_key

        key_a = _get_cache_key("model", 0.1, "prompt", "sys", 1024, base_url="http://a.com")
        key_b = _get_cache_key("model", 0.1, "prompt", "sys", 1024, base_url="http://b.com")
        assert key_a != key_b


class TestMetaResponseFilter:
    """测试 meta-response 检测。"""

    def test_meta_response_detected(self):
        from core.llm_utils.llm_client import _is_meta_response

        assert _is_meta_response("好的，请提供需要校对的文本内容。") is True
        assert _is_meta_response("请提供更多信息") is True
        assert _is_meta_response("请问您想问什么？") is True

    def test_normal_response_not_filtered(self):
        from core.llm_utils.llm_client import _is_meta_response

        assert _is_meta_response("今天天气很好") is False
        assert _is_meta_response("[ID:0] 修正后的文本") is False
        assert _is_meta_response("") is False

    def test_long_response_not_filtered(self):
        from core.llm_utils.llm_client import _is_meta_response

        long_text = "好的，请提供需要校对的文本内容。" * 10
        assert _is_meta_response(long_text) is False


# ── reload_config 保留运行时状态 ──

class TestReloadConfig:
    """测试 reload_config() 保留运行时状态。"""

    def test_reload_preserves_translate_mode(self):
        c = _make_corrector()
        c._translate_mode = True
        c._stream_mode = True
        c.reload_config()
        assert c._translate_mode is True
        assert c._stream_mode is True

    def test_reload_preserves_env_context(self):
        c = _make_corrector()
        c._env_context = "测试环境"
        c.reload_config()
        assert c._env_context == "测试环境"

    def test_reload_preserves_seg_time_gap(self):
        c = _make_corrector(seg_time_gap=5.0)
        c._seg_time_gap = 5.0
        c.reload_config()
        assert c._seg_time_gap == 5.0


# ── 系统 prompt 构建 ──

class TestBuildSystemPrompt:
    """测试 _build_system_prompt() 构建逻辑。"""

    def test_normal_mode(self):
        c = _make_corrector()
        sp = c._build_system_prompt()
        assert "校对" in sp

    def test_translate_mode(self):
        c = _make_corrector()
        c._translate_mode = True
        sp = c._build_system_prompt()
        assert "翻译" in sp

    def test_with_env_context(self):
        c = _make_corrector()
        sp = c._build_system_prompt(env_context="游戏对话场景")
        assert "游戏对话场景" in sp

    def test_json_mode(self):
        c = _make_corrector()
        c._json_mode = True
        sp = c._build_system_prompt()
        assert "JSON" in sp
