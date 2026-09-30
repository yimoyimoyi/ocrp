"""LLM 网关缓存与响应处理回归测试。

覆盖三类缺陷：
  1. 内存缓存没有 TTL / 容量上限（文件缓存的 7 天 TTL 被内存层绕过，且长时间
     批量纠错会无界增长）
  2. finish_reason == "length"（max_tokens 截断）的结果被写入缓存，之后 7 天内
     每次请求都拿到同一份残缺内容
  3. resp_type="json" 时 json_repair 对非 JSON 输入**静默**返回 [] / '' / ['ID:1']
     等异类值，被当作合法结构 → "[]" 被写回字幕行，或误判解析成功而放弃
     [ID:n] 文本回退。该失败此前被伪造为 {"raw": ...} 字典。
"""

from types import SimpleNamespace

import pytest

from core.llm_utils import llm_client


@pytest.fixture(autouse=True)
def _clean_memory_cache():
    llm_client.clear_memory_cache()
    yield
    llm_client.clear_memory_cache()


# ── 极简 OpenAI 客户端替身 ──────────────────────────────────────


class _FakeCompletions:
    def __init__(self, content, finish_reason):
        self._content = content
        self._finish_reason = finish_reason
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        choice = SimpleNamespace(
            message=SimpleNamespace(content=self._content),
            finish_reason=self._finish_reason,
        )
        return SimpleNamespace(choices=[choice])


class _FakeClient:
    def __init__(self, content, finish_reason="stop"):
        self.completions = _FakeCompletions(content, finish_reason)
        self.chat = SimpleNamespace(completions=self.completions)


def _call(content, resp_type=None, finish_reason="stop", cache_key="", valid_def=None):
    client = _FakeClient(content, finish_reason)
    result = llm_client._call_normal(client, {}, resp_type, valid_def, cache_key, "test", llm_client.logger)
    return result, client.completions.calls


# ── 1. 内存缓存 TTL / 容量 ─────────────────────────────────────


class TestMemoryCacheTTLAndCapacity:
    def test_expired_entry_is_dropped(self, monkeypatch):
        llm_client._memory_cache_put("k", "v")
        assert llm_client._memory_cache_get("k") == "v"
        monkeypatch.setattr(llm_client, "CACHE_TTL_SECONDS", -1)
        assert llm_client._memory_cache_get("k") is None
        # 过期条目应被真正移除，而非留在字典里
        assert "k" not in llm_client._memory_cache

    def test_fresh_entry_survives_ttl(self):
        llm_client._memory_cache_put("k", "v")
        assert llm_client._memory_cache_get("k") == "v"

    def test_capacity_is_bounded_with_lru_eviction(self, monkeypatch):
        monkeypatch.setattr(llm_client, "MEMORY_CACHE_MAX_ENTRIES", 3)
        for i in range(5):
            llm_client._memory_cache_put(f"k{i}", i)
        assert len(llm_client._memory_cache) == 3
        assert "k0" not in llm_client._memory_cache
        assert "k1" not in llm_client._memory_cache
        assert "k4" in llm_client._memory_cache

    def test_recently_read_entry_is_kept(self, monkeypatch):
        monkeypatch.setattr(llm_client, "MEMORY_CACHE_MAX_ENTRIES", 2)
        llm_client._memory_cache_put("a", 1)
        llm_client._memory_cache_put("b", 2)
        # 读 a 使其成为最近使用，随后插入 c 应淘汰 b
        assert llm_client._memory_cache_get("a") == 1
        llm_client._memory_cache_put("c", 3)
        assert "a" in llm_client._memory_cache
        assert "b" not in llm_client._memory_cache

    def test_clear_memory_cache(self):
        llm_client._memory_cache_put("k", "v")
        llm_client.clear_memory_cache()
        assert llm_client._memory_cache_get("k") is None


# ── 2. 截断响应不入缓存 ────────────────────────────────────────


class TestTruncatedResponseNotCached:
    def test_length_finish_reason_skips_cache(self, monkeypatch):
        saved = []
        monkeypatch.setattr(llm_client, "_save_cache", lambda k, r, t: saved.append(k))
        result, _ = _call("[ID:0] 部分内容", finish_reason="length", cache_key="ck")
        assert result == "[ID:0] 部分内容"
        assert saved == [], "截断结果不得写入缓存"

    def test_normal_finish_reason_is_cached(self, monkeypatch):
        saved = []
        monkeypatch.setattr(llm_client, "_save_cache", lambda k, r, t: saved.append(k))
        result, _ = _call("完整内容", finish_reason="stop", cache_key="ck")
        assert result == "完整内容"
        assert saved == ["ck"]

    def test_stream_truncation_is_handled(self):
        """流式路径也应识别 finish_reason=length（结果本身不缓存）。"""
        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="abc"), finish_reason=None)]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=""), finish_reason="length")]),
        ]
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kw: iter(chunks))))
        out = llm_client._call_stream(client, {}, None, "test", llm_client.logger)
        assert out == "abc"


# ── 3. JSON 响应类型校验 ───────────────────────────────────────


class TestJsonResponseTypeValidation:
    def test_broken_json_returns_text_not_list(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        result, _ = _call("这不是 JSON {{{", resp_type="json", cache_key="ck")
        assert not isinstance(result, dict)
        assert result == "这不是 JSON {{{"

    def test_id_lines_are_not_mangled_into_json_list(self, monkeypatch):
        """json_repair 会把 '[ID:0] a\\n[ID:1] b' 静默解析成 ['ID:1']。"""
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        raw = "[ID:0] 甲\n[ID:1] 乙"
        result, _ = _call(raw, resp_type="json", cache_key="ck")
        assert result == raw, "必须原样返回，交给 [ID:n] 文本回退链"

    def test_prose_is_not_returned_as_raw_dict(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        result, _ = _call("随便的一段散文内容", resp_type="json", cache_key="ck")
        assert "raw" not in str(result)
        assert result == "随便的一段散文内容"

    def test_valid_json_object_still_returns_dict(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        result, _ = _call('{"results": [{"id": 0, "text": "x"}]}', resp_type="json", cache_key="ck")
        assert result == {"results": [{"id": 0, "text": "x"}]}

    def test_repairable_json_object_is_repaired(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        result, _ = _call('{"results": [{"id": 0, "text": "x"}]', resp_type="json", cache_key="ck")
        assert isinstance(result, dict)
        assert result["results"][0]["text"] == "x"


# ── meta-response 线上拦截 ────────────────────────────────────


class TestMetaResponseRejectedLive:
    def test_meta_response_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        result, _ = _call("好的，请提供需要校对的文本内容。", resp_type=None, cache_key="ck")
        assert result is None

    def test_meta_response_not_cached(self, monkeypatch):
        saved = []
        monkeypatch.setattr(llm_client, "_save_cache", lambda k, r, t: saved.append(k))
        _call("请提供更多信息", resp_type=None, cache_key="ck")
        assert saved == []

    def test_normal_short_line_is_kept(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        result, _ = _call("你好，世界。", resp_type=None, cache_key="ck")
        assert result == "你好，世界。"

    def test_id_prefixed_dialogue_is_kept(self, monkeypatch):
        """批量响应带 [ID:n] 前缀，即使正文形似 meta 也不应被丢弃。"""
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        result, _ = _call("[ID:0] 请提供更多信息", resp_type=None, cache_key="ck")
        assert result == "[ID:0] 请提供更多信息"


# ── valid_def 校验钩子 ─────────────────────────────────────────


class TestValidDefHook:
    def test_valid_def_success_passes_through(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        result, _ = _call('{"ok": true}', resp_type="json", valid_def=lambda _r: {"status": "success"})
        assert result == {"ok": True}

    def test_valid_def_failure_raises_for_retry(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_save_cache", lambda *a: None)
        with pytest.raises(ValueError, match="响应校验失败"):
            _call('{"ok": true}', resp_type="json", valid_def=lambda _r: {"status": "error", "message": "缺 results"})

    def test_valid_def_failure_does_not_cache(self, monkeypatch):
        saved = []
        monkeypatch.setattr(llm_client, "_save_cache", lambda k, r, t: saved.append(k))
        with pytest.raises(ValueError):
            _call('{"ok": true}', resp_type="json", cache_key="ck", valid_def=lambda _r: {"status": "error"})
        assert saved == []


# ── 截断自愈：ask_llm 层的预算升级与 truncated_out ─────────────


def _patch_ask_llm_client(monkeypatch, responses):
    """替换 ask_llm 内部的 OpenAI 客户端。

    ``responses``: [(content, finish_reason), ...] —— 按调用顺序依次返回，
    越界后重复最后一项。返回 (max_tokens 记录, 已写缓存键记录)。
    """
    from types import SimpleNamespace

    budgets: list[int] = []

    class _Completions:
        def create(self, **kwargs):
            idx = min(len(budgets), len(responses) - 1)
            content, finish_reason = responses[idx]
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
    saved: list[str] = []
    monkeypatch.setattr(llm_client, "_save_cache", lambda k, r, t: saved.append(k))
    return budgets, saved


def _ask(**kwargs):
    params = dict(
        prompt="p",
        api_key="k",
        base_url="http://localhost:8080",
        model="m",
        use_rate_limiter=False,
    )
    params.update(kwargs)
    return params


class TestTruncationEscalation:
    """回归：截断此前只记日志，半截内容被当作正常结果交给调用方。"""

    def test_escalates_budget_once_on_truncation(self, monkeypatch):
        budgets, _saved = _patch_ask_llm_client(
            monkeypatch,
            [("半截内容", "length"), ("完整内容", "stop")],
        )
        flag: list[bool] = []
        result = llm_client.ask_llm(**_ask(max_tokens=2048, truncated_out=flag))

        assert result == "完整内容"
        assert budgets == [2048, 4096], f"应以更大预算重试一次，实际 {budgets}"
        assert flag == [False], "最终结果完整 → 不报告截断"

    def test_reports_truncation_when_still_cut_at_ceiling(self, monkeypatch):
        ceiling = llm_client.TRUNCATION_ESCALATION_CEILING
        budgets, _saved = _patch_ask_llm_client(monkeypatch, [("半截", "length")])
        flag: list[bool] = []
        result = llm_client.ask_llm(**_ask(max_tokens=ceiling, truncated_out=flag))

        assert result == "半截"
        assert budgets == [ceiling], "已到上限 → 不再重试"
        assert flag == [True], "调用方必须被告知仍被截断"

    def test_escalation_can_be_disabled(self, monkeypatch):
        budgets, _saved = _patch_ask_llm_client(monkeypatch, [("半截", "length")])
        flag: list[bool] = []
        llm_client.ask_llm(**_ask(max_tokens=2048, escalate_on_truncation=False, truncated_out=flag))

        assert budgets == [2048], f"关闭后不应重试，实际 {budgets}"
        assert flag == [True]

    def test_no_report_when_not_truncated(self, monkeypatch):
        budgets, _saved = _patch_ask_llm_client(monkeypatch, [("完整", "stop")])
        flag: list[bool] = []
        assert llm_client.ask_llm(**_ask(truncated_out=flag)) == "完整"
        assert budgets == [llm_client.DEFAULT_MAX_TOKENS]
        assert flag == [False]

    def test_truncated_result_is_not_cached(self, monkeypatch):
        _budgets, saved = _patch_ask_llm_client(monkeypatch, [("半截", "length")])
        llm_client.ask_llm(**_ask(max_tokens=2048, escalate_on_truncation=False))
        assert saved == [], "截断结果不得进入缓存"

    def test_escalated_attempt_uses_its_own_cache_key(self, monkeypatch):
        """不同预算必须命中不同缓存键，否则会把 4096 的结果写到 2048 的键下。"""
        _budgets, saved = _patch_ask_llm_client(
            monkeypatch,
            [("半截内容", "length"), ("完整内容", "stop")],
        )
        llm_client.ask_llm(**_ask(max_tokens=2048))

        def key_for(budget: int) -> str:
            return llm_client._get_cache_key("m", 0.1, "p", "", budget, None, "http://localhost:8080")

        assert saved == [key_for(4096)], f"只应写入成功那次的键，实际 {saved}"
        assert key_for(2048) not in saved, "被截断的 2048 结果不得写入缓存"

    def test_stream_truncation_is_reported(self, monkeypatch):
        """流式路径同样要能上报截断（用户当前就开着 stream_mode）。"""
        from types import SimpleNamespace

        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="abc"), finish_reason=None)]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=""), finish_reason="length")]),
        ]
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kw: iter(chunks))))
        monkeypatch.setattr(llm_client, "OpenAI", lambda **_kw: client)

        flag: list[bool] = []
        result = llm_client.ask_llm(**_ask(stream=True, escalate_on_truncation=False, truncated_out=flag))
        assert result == "abc"
        assert flag == [True]
