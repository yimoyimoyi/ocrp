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


def _sent_prompt(mock_llm) -> str:
    """取出 mock ask_llm 收到的 prompt 参数。"""
    call = mock_llm.call_args
    return call.kwargs.get("prompt") or call[1].get("prompt") or ""


class TestPolishPayloadInvariant:
    """回归：润色提示词必须真的包含待润色正文。

    历史缺陷：域对象 / 建文件模板里的 polish_prompt 是被截断的
    "你是一个专业的字幕润色专家。请对翻译/纠错后的字幕进行润色..."，不含任何
    占位符 —— polish() 的两次替换都是空操作，模型收到一条**没有正文**的指令，
    返回内容与输入无关，润色结果被静默污染（而 enable_polish 默认场景下配置
    文件里写的正是这个值）。
    """

    BROKEN = "你是一个专业的字幕润色专家。请对翻译/纠错后的字幕进行润色..."

    @patch("core.ai_correction.ask_llm")
    def test_placeholder_free_prompt_still_carries_text(self, mock_llm):
        mock_llm.return_value = "润色后"
        c = _make_corrector(enable_polish=True, polish_prompt=self.BROKEN)
        c.polish("OCR 原始文本", "待润色的纠错文本")
        assert "待润色的纠错文本" in _sent_prompt(mock_llm)

    @patch("core.ai_correction.ask_llm")
    def test_placeholder_free_prompt_falls_back_to_default(self, mock_llm):
        from core.prompts import DEFAULT_POLISH_PROMPT

        mock_llm.return_value = "润色后"
        c = _make_corrector(enable_polish=True, polish_prompt=self.BROKEN)
        assert c._polish_prompt == DEFAULT_POLISH_PROMPT

    @patch("core.ai_correction.ask_llm")
    def test_custom_placeholder_prompt_used_verbatim(self, mock_llm):
        mock_llm.return_value = "润色后"
        c = _make_corrector(enable_polish=True, polish_prompt="请润色：{待校对文本}")
        c.polish("原始", "纠错文本")
        assert "请润色：纠错文本" in _sent_prompt(mock_llm)

    @patch("core.ai_correction.ask_llm")
    def test_raw_result_placeholder_alone_is_not_enough(self, mock_llm):
        """只用 {原始结果} 的提示词同样拿不到"待润色正文"（原文 ≠ 纠错后文本）。"""
        mock_llm.return_value = "润色后"
        c = _make_corrector(enable_polish=True, polish_prompt="请润色：{原始结果}")
        c.polish("OCR 原始文本", "待润色的纠错文本")
        assert "待润色的纠错文本" in _sent_prompt(mock_llm)

    @patch("core.ai_correction.ask_llm")
    def test_polish_system_prompt_is_shared_constant(self, mock_llm):
        from core.prompts import DEFAULT_POLISH_SYSTEM_PROMPT

        mock_llm.return_value = "润色后"
        c = _make_corrector(enable_polish=True)
        c.polish("原始", "纠错文本")
        assert mock_llm.call_args.kwargs.get("system_prompt") == DEFAULT_POLISH_SYSTEM_PROMPT


class TestEnvContextInjection:
    """回归：环境信息注入判据曾是"提示词里有没有'环境'两字"。

    只要提示词正文/用户文案里出现该词，环境上下文就被**永久跳过**；反之占位符
    ``{环境上下文}``/``{环境描述}`` 在 ``_should_skip_env_extraction`` 里被当作
    "用户自管环境"，但当时 ``_resolve_placeholders`` 并不替换它们，于是提示词里
    留下字面占位符且环境信息永久缺失。
    """

    @patch("core.ai_correction.ask_llm")
    def test_env_appended_when_prompt_mentions_word_without_placeholder(self, mock_llm):
        mock_llm.return_value = "润色后"
        # 提示词含"环境"二字但**没有**占位符
        c = _make_corrector(enable_polish=True, polish_prompt="请润色：{待校对文本}\n注意环境描写。")
        c._env_context = "科幻 / 紧张 / 太空站事故"
        c.polish("原始", "纠错文本")
        assert "科幻 / 紧张 / 太空站事故" in _sent_prompt(mock_llm)

    @patch("core.ai_correction.ask_llm")
    def test_env_not_duplicated_when_placeholder_present(self, mock_llm):
        mock_llm.return_value = "润色后"
        c = _make_corrector(enable_polish=True, polish_prompt="环境：{环境信息}\n待润色：{待校对文本}")
        c._env_context = "游戏对话"
        c.polish("原始", "纠错文本")
        assert _sent_prompt(mock_llm).count("游戏对话") == 1

    def test_env_placeholder_aliases_are_resolved(self):
        """``{环境上下文}``/``{环境描述}`` 是 ``{环境信息}`` 的同义写法。"""
        from core.ai_correction import AICorrector

        for placeholder in ("{环境信息}", "{环境上下文}", "{环境描述}"):
            out = AICorrector._resolve_placeholders(f"参考：{placeholder}", env_context="ENV")
            assert out == "参考：ENV", placeholder
            assert "{" not in out

    def test_skip_env_extraction_matches_resolvable_placeholders(self):
        """跳过判据里的占位符集合必须都能被 _resolve_placeholders 真正替换。"""
        from core.ai_correction import AICorrector

        c = _make_corrector(summary_prompt="请参考{环境描述}进行总结。")
        assert c.should_skip_env_extraction() is True
        # 该占位符确实可由 _resolve_placeholders 填充
        assert AICorrector._resolve_placeholders("{环境描述}", env_context="ENV") == "ENV"

    def test_public_api_exposes_env_state(self):
        c = _make_corrector()
        c._env_context = "上下文"
        assert c.env_context == "上下文"
        assert c.should_skip_env_extraction() is True
        assert c.extract_env is False


class TestRetrySemantics:
    """外层 ``max_retries`` 与 ``ask_llm`` 内部 ``@except_handler(retry=3)`` 不得相乘。

    若两者叠加，一次 API 故障会变成 ``(3+1)×(max_retries+1)`` 次请求（默认
    ``retry_on_failure=2`` 时 12 次），批量纠错几乎无法结束。
    """

    @patch("core.ai_correction.ask_llm")
    def test_api_failure_does_not_multiply_retries(self, mock_llm):
        mock_llm.return_value = None  # 内部重试已耗尽
        c = _make_corrector()
        result = c.correct_batch([(0, "原文")], max_retries=5)
        assert mock_llm.call_count == 1, "ask_llm 已耗尽重试，外层不应再叠加"
        assert result == {0: "原文"}

    @patch("core.ai_correction.ask_llm")
    def test_parse_failure_retries_outer_loop(self, mock_llm):
        """拿到响应但解析不出来时，应由外层重试换一次采样。"""
        mock_llm.return_value = "完全无法解析的内容"
        c = _make_corrector()
        result = c.correct_batch([(0, "原文")], max_retries=2)
        assert mock_llm.call_count == 3  # max_retries + 1
        assert result == {0: "原文"}  # 兜底填原文

    @patch("core.ai_correction.ask_llm")
    def test_cancel_event_short_circuits_before_any_request(self, mock_llm):
        import threading

        cancel = threading.Event()
        cancel.set()
        c = _make_corrector()
        result = c.correct_batch([(0, "原文")], max_retries=3, cancel_event=cancel)
        assert mock_llm.call_count == 0, "已取消时不应发起任何请求"
        assert result == {0: "原文"}


class TestCancelEvent:
    """worker.stop() 必须能中断在飞调用（此前只能置标志，等重试全部走完）。"""

    @patch("core.ai_correction.ask_llm")
    def test_cancel_event_forwarded_to_gateway(self, mock_llm):
        import threading

        mock_llm.return_value = "[ID:0] 修正"
        cancel = threading.Event()
        c = _make_corrector()
        c.correct_batch([(0, "原文")], max_retries=0, cancel_event=cancel)
        assert mock_llm.call_args.kwargs.get("cancel_event") is cancel

    @patch("core.ai_correction.ask_llm")
    def test_polish_returns_original_when_cancelled(self, mock_llm):
        import threading

        cancel = threading.Event()
        cancel.set()
        c = _make_corrector(enable_polish=True)
        assert c.polish("原始", "纠错文本", cancel_event=cancel) == "纠错文本"
        mock_llm.assert_not_called()

    @patch("core.ai_correction.ask_llm")
    def test_polish_forwards_cancel_event(self, mock_llm):
        import threading

        mock_llm.return_value = "润色后"
        cancel = threading.Event()
        c = _make_corrector(enable_polish=True)
        c.polish("原始", "纠错文本", cancel_event=cancel)
        assert mock_llm.call_args.kwargs.get("cancel_event") is cancel

    @patch("core.ai_correction.ask_llm")
    def test_correct_forwards_cancel_event(self, mock_llm):
        import threading

        mock_llm.return_value = "修正后"
        cancel = threading.Event()
        c = _make_corrector()
        c.correct("原文", cancel_event=cancel)
        assert mock_llm.call_args.kwargs.get("cancel_event") is cancel


class TestDefaultCorrectionPromptDetection:
    """回归：默认提示词判定改为精确比较（此前是前 20 字符子串启发式）。"""

    def test_default_prompt_detected(self):
        from core.prompts import DEFAULT_CORRECTION_PROMPT

        c = _make_corrector(correction_prompt=DEFAULT_CORRECTION_PROMPT)
        assert c.is_default_correction_prompt() is True

    def test_custom_prompt_starting_with_same_words_not_treated_as_default(self):
        """旧启发式只看前 20 字符：以"文本校对"开头的自定义提示词会被误丢弃。"""
        c = _make_corrector(correction_prompt="文本校对要求：只修正错别字，保留所有标点符号和英文缩写。")
        assert c.is_default_correction_prompt() is False

    def test_whitespace_variation_still_counts_as_default(self):
        from core.prompts import DEFAULT_CORRECTION_PROMPT

        c = _make_corrector(correction_prompt=f"  {DEFAULT_CORRECTION_PROMPT}\n")
        assert c.is_default_correction_prompt() is True

    @patch("core.ai_correction.ask_llm")
    def test_custom_prompt_is_injected_as_hint(self, mock_llm):
        mock_llm.return_value = "修正后"
        c = _make_corrector(correction_prompt="文本校对要求：保留英文缩写。")
        c.correct_batch([(0, "原文")], max_retries=0)
        assert "保留英文缩写" in _sent_prompt(mock_llm)

    @patch("core.ai_correction.ask_llm")
    def test_default_prompt_not_injected_as_hint(self, mock_llm):
        from core.prompts import DEFAULT_CORRECTION_PROMPT

        mock_llm.return_value = "修正后"
        c = _make_corrector(correction_prompt=DEFAULT_CORRECTION_PROMPT)
        c.correct_batch([(0, "原文")], max_retries=0)
        assert "用户额外参考" not in _sent_prompt(mock_llm)


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

    def test_no_skip_when_summary_contains_domain_word(self):
        """宽泛词（领域/氛围）不再是跳过判据——默认 summary_prompt 恰含这些词，
        否则自动提取永远被跳过（实测回归修复）。"""
        c = _make_corrector(summary_prompt="请总结领域类型。")
        assert c._should_skip_env_extraction() is False

    def test_skip_when_summary_contains_placeholder(self):
        c = _make_corrector(summary_prompt="请参考{环境描述}进行总结。")
        assert c._should_skip_env_extraction() is True

    def test_no_skip_when_polish_contains_env_word(self):
        """不带花括号的环境词不触发跳过（需明确占位符）。"""
        c = _make_corrector(polish_prompt="请参考环境描述进行润色。")
        assert c._should_skip_env_extraction() is False

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
            tpl,
            raw_text="a",
            context="b",
            env_context="c",
            timestamp="t",
            region="r",
            engine="e",
            language="l",
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


# ── 构造时从 config 读取流式/JSON 模式 ──


class TestConfigModes:
    """测试 stream_mode/json_mode 从配置读取（设置同步 P9 修复）。"""

    def test_stream_json_mode_read_from_config(self):
        c = _make_corrector(stream_mode=True, json_mode=True)
        assert c._stream_mode is True
        assert c._json_mode is True

    def test_stream_json_mode_default_false(self):
        c = _make_corrector()
        assert c._stream_mode is False
        assert c._json_mode is False

    def test_stream_mode_only(self):
        c = _make_corrector(stream_mode=True)
        assert c._stream_mode is True
        assert c._json_mode is False

    def test_truthy_config_values_coerced_to_bool(self):
        c = _make_corrector(stream_mode=1, json_mode="true")
        assert c._stream_mode is True
        assert c._json_mode is True


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


# ── 输出截断（用户报告：流程截断 + 润色不携带原文） ──


def _patch_gateway_client(monkeypatch, content, finish_reason="stop"):
    """把网关内部的 OpenAI 客户端换成受控替身，返回 max_tokens 记录列表。

    必须替换 ``OpenAI`` 而不是 ``ask_llm``：要覆盖的正是"截断如何一路传到消费方"。
    """
    from types import SimpleNamespace

    from core.llm_utils import llm_client

    budgets: list[int] = []

    class _Completions:
        def create(self, **kwargs):
            budgets.append(kwargs.get("max_tokens"))
            choice = SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
            return SimpleNamespace(choices=[choice])

    class _Client:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(llm_client, "OpenAI", _Client)
    monkeypatch.setattr(llm_client, "_load_cache", lambda *a: None)
    monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
    return budgets


class TestOutputTruncation:
    """回归（用户报告）：流程截断。

    纠错/润色调用此前**完全不传 max_tokens**（网关默认 2048），且截断
    （``finish_reason == "length"``）只记日志 —— 半截内容被当作正常结果：
      * 润色把 480 字输入变成 43 字输出（内容 91% 丢失）
      * 批量纠错把半句（``[ID:1] 半句被截``）写进字幕行
    修复后：预算按内容长度给足 → 仍截断则翻倍重试一次 → 仍截断则**回退**而非采纳。
    """

    def test_budget_is_sized_from_content_not_left_at_default(self, monkeypatch):
        from core.llm_utils.llm_client import DEFAULT_MAX_TOKENS

        budgets = _patch_gateway_client(monkeypatch, "润色后的完整文本")
        c = _make_corrector(enable_polish=True)
        # 约 3000 字 → 1.6× ≈ 4800 token，远超默认 2048
        c.polish("原始文本", "这是一段较长的纠错后文本，" * 220)
        assert budgets, "应当发起调用"
        assert budgets[0] > DEFAULT_MAX_TOKENS, f"长输入应给足预算，实际 {budgets[0]}"

    def test_estimate_scales_and_is_clamped(self):
        from core.llm_utils.llm_client import DEFAULT_MAX_TOKENS, TRUNCATION_ESCALATION_CEILING

        # 下限必须等于网关默认值 —— 否则短行预算反而从 2048 降到更低（比修复前更糟）
        assert AICorrector._estimate_max_tokens("短") == DEFAULT_MAX_TOKENS
        assert AICorrector._estimate_max_tokens("") == DEFAULT_MAX_TOKENS
        assert AICorrector._estimate_max_tokens("字" * 100_000) == TRUNCATION_ESCALATION_CEILING
        # 中等长度应落在两者之间
        mid = AICorrector._estimate_max_tokens("字" * 1500)
        assert DEFAULT_MAX_TOKENS <= mid <= TRUNCATION_ESCALATION_CEILING

    def test_polish_rejects_truncated_output(self, monkeypatch):
        """截断的润色结果必须被丢弃，保留未润色的纠错文本。"""
        original_corrected = "完整的纠错后文本。" * 10
        budgets = _patch_gateway_client(monkeypatch, "半句被截", finish_reason="length")
        c = _make_corrector(enable_polish=True)

        out = c.polish("原始", original_corrected)
        assert out == original_corrected, "不得用半截内容替换正文"
        assert len(budgets) == 2, f"截断后应以更大预算自愈重试一次，实际 {budgets}"
        assert budgets[1] > budgets[0]

    def test_polish_accepts_complete_output(self, monkeypatch):
        _patch_gateway_client(monkeypatch, "润色后的完整文本")
        c = _make_corrector(enable_polish=True)
        assert c.polish("原始", "纠错后") == "润色后的完整文本"

    def test_correct_rejects_truncated_output(self, monkeypatch):
        _patch_gateway_client(monkeypatch, "半句被截", finish_reason="length")
        c = _make_corrector()
        assert c.correct("原文") is None, "截断的单行结果不得作为纠错结果"

    def test_correct_accepts_complete_output(self, monkeypatch):
        _patch_gateway_client(monkeypatch, "修正后的文本")
        c = _make_corrector()
        assert c.correct("原文") == "修正后的文本"

    def test_batch_drops_last_line_when_truncated(self, monkeypatch):
        """最后一条解析结果可能是半句，必须丢弃；缺失行由调用方回退原文。"""
        _patch_gateway_client(monkeypatch, "[ID:0] 完整的一行\n[ID:1] 半句被截", finish_reason="length")
        c = _make_corrector()

        res = c.correct_batch([(0, "原文0"), (1, "原文1")], max_retries=0)
        assert res.get(0) == "完整的一行"
        assert 1 not in res, f"半句不得写入，实际 {res}"
        # 未返回的行由 worker 回退原文（corrected_map.get(row, raw)）
        assert res.get(1, "原文1") == "原文1"

    def test_batch_keeps_all_lines_when_complete(self, monkeypatch):
        _patch_gateway_client(monkeypatch, "[ID:0] 完整一行\n[ID:1] 完整二行")
        c = _make_corrector()
        res = c.correct_batch([(0, "原文0"), (1, "原文1")], max_retries=0)
        assert res == {0: "完整一行", 1: "完整二行"}

    def test_last_truncated_flag_resets_between_calls(self, monkeypatch):
        """标记不得跨调用残留（否则下一次正常结果会被误判为截断而丢弃）。"""
        _patch_gateway_client(monkeypatch, "修正后的文本")
        c = _make_corrector()
        c.correct("原文")
        assert c._last_truncated is False
        assert c.correct("原文") == "修正后的文本"

    @patch("core.ai_correction.ask_llm")
    def test_polish_prompt_carries_original_and_corrected_text(self, mock_llm):
        """回归（用户报告）：润色不携带原文。

        历史配置里的 polish_prompt 是被截断且**无任何占位符**的句子，两次替换都是
        空操作 —— 模型收到的指令里既没有原文也没有待润色正文。
        """
        from core.prompts import DEFAULT_POLISH_PROMPT

        mock_llm.return_value = "润色后"
        c = _make_corrector(enable_polish=True, polish_prompt=DEFAULT_POLISH_PROMPT)
        original = "OCR 识别出来的原始文本，可能有错别字"
        corrected = "OCR 识别出来的原始文本，可能有错别字。"

        c.polish(original, corrected)
        prompt = _sent_prompt(mock_llm)
        assert original in prompt, "润色 prompt 必须携带 OCR 原文"
        assert corrected in prompt, "润色 prompt 必须携带待润色正文"
        assert "{原始结果}" not in prompt and "{待校对文本}" not in prompt, "占位符必须已替换"
