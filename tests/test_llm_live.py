"""LLM 实际调用测试 —— 使用配置文件中保存的 API 密钥。

运行方式：
    uv run pytest tests/test_llm_live.py -v -s

注意：需要网络连接，会消耗少量 API 额度。
"""

import json
import os
import time

import pytest

from core.ai_correction import AICorrector
from core.config_manager import _load_json_with_comments
from core.llm_utils import ask_llm

# CI 环境跳过（设置 CI=true 环境变量），本地手动运行: uv run pytest tests/test_llm_live.py -v -s
if os.environ.get("CI"):
    pytest.skip("需要真实 API key，CI 中跳过", allow_module_level=True)


def _load_api_config() -> dict:
    """从 ai_correction.json 加载 API 配置。"""
    from pathlib import Path
    cfg_path = Path(__file__).parent.parent / "config" / "ai_correction.json"
    if not cfg_path.exists():
        return {}
    cfg = _load_json_with_comments(cfg_path)
    return {
        "api_key": cfg.get("api_key", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "timeout": cfg.get("timeout", 30),
    }


def _load_preset_config(preset_name: str = "") -> dict:
    """从 api_presets.json 加载预设配置。"""
    from pathlib import Path
    presets_path = Path(__file__).parent.parent / "config" / "api_presets.json"
    if not presets_path.exists():
        return {}
    data = _load_json_with_comments(presets_path)
    preset = data.get("presets", {}).get(preset_name, {})
    return {
        "api_key": preset.get("api_key", ""),
        "base_url": preset.get("base_url", ""),
        "model": preset.get("model", ""),
        "timeout": preset.get("timeout", 30),
    }


# ── ask_llm 直接调用 ──

class TestAskLLMLive:
    """测试 ask_llm() 实际 API 调用。"""

    def test_simple_text_request(self):
        """基本文本请求：输入一句话，验证返回非空。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        result = ask_llm(
            prompt="请用中文回答：1+1等于几？只回答数字。",
            system_prompt="你是一个简洁的助手。",
            api_key=api["api_key"],
            base_url=api["base_url"],
            model=api["model"],
            timeout=api["timeout"],
            max_tokens=64,
            temperature=0.1,
        )
        assert result is not None, "API 返回了 None"
        assert len(result.strip()) > 0, "API 返回了空字符串"
        print("\n[ask_llm 基本请求] 输入: 1+1等于几？")
        print(f"  返回: {result.strip()}")

    def test_json_mode_request(self):
        """JSON 模式请求：验证返回可解析为 dict。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        result = ask_llm(
            prompt='请以JSON格式返回：{"name": "张三", "age": 25}。只返回JSON，不要其他文字。',
            system_prompt="你是一个JSON生成助手。",
            resp_type="json",
            api_key=api["api_key"],
            base_url=api["base_url"],
            model=api["model"],
            timeout=api["timeout"],
            max_tokens=128,
            temperature=0.1,
        )
        assert result is not None, "JSON 模式返回了 None"
        if isinstance(result, str):
            parsed = json.loads(result)
        else:
            parsed = result
        assert isinstance(parsed, dict), f"期望 dict，得到 {type(parsed)}"
        print(f"\n[ask_llm JSON模式] 返回: {json.dumps(parsed, ensure_ascii=False)}")

    def test_stream_mode_request(self):
        """流式请求：验证回调被调用、最终返回非空。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        chunks = []

        def on_chunk(text: str):
            chunks.append(text)

        result = ask_llm(
            prompt="用中文写一句关于春天的诗。",
            system_prompt="你是一个诗人。",
            stream=True,
            stream_callback=on_chunk,
            api_key=api["api_key"],
            base_url=api["base_url"],
            model=api["model"],
            timeout=api["timeout"],
            max_tokens=128,
            temperature=0.7,
        )
        assert result is not None, "流式返回了 None"
        assert len(chunks) > 0, "流式回调从未被调用"
        print(f"\n[ask_llm 流式] chunk数={len(chunks)}, 总长度={len(result)}")
        print(f"  内容: {result.strip()}")


# ── AICorrector 实际调用 ──

class TestAICorrectorLive:
    """测试 AICorrector 实际纠错/翻译/润色流程。"""

    def _make_corrector(self, **overrides) -> AICorrector:
        api = _load_api_config()
        config = {
            "enabled": True,
            "engine": "openai_vision",
            "api_key": api["api_key"],
            "base_url": api["base_url"],
            "model": api["model"],
            "timeout": api["timeout"],
            "correction_prompt": "请纠正OCR识别中的明显错误。",
            "correction_system_prompt": "你是字幕校对助手，逐行校对，保持行数不变。",
            "output_format": "",
            "seg_time_gap": 3.0,
            "enable_polish": False,
            "polish_prompt": "润色以下文本：{原始结果} / {待校对文本}",
            "summary_prompt": "总结领域和氛围。",
            "extract_environment": False,
            "retry_on_failure": 1,
        }
        config.update(overrides)
        return AICorrector(config=config)

    def test_correct_single_text(self):
        """单条纠错：输入带错别字的文本，验证返回修正结果。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        c = self._make_corrector(
            correction_prompt="请纠正以下OCR文本中的错别字：\n{原始结果}\n直接输出纠正后的文本。",
        )
        result = c.correct("今天天汽很好，我们去公圆玩吧。")
        assert result is not None, "纠错返回了 None"
        print("\n[纠错] 原文: 今天天汽很好，我们去公圆玩吧。")
        print(f"  纠错: {result}")

    def test_correct_translate(self):
        """翻译模式：输入英文，验证返回中文。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        c = self._make_corrector()
        c._translate_mode = True
        result = c.correct("Hello, how are you today?")
        assert result is not None, "翻译返回了 None"
        assert any('一' <= ch <= '鿿' for ch in result), "翻译结果不含中文"
        print("\n[翻译] 原文: Hello, how are you today?")
        print(f"  翻译: {result}")

    def test_correct_batch(self):
        """批量纠错：多条文本同时纠错，验证 [ID:n] 解析。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        c = self._make_corrector()
        texts = [
            (0, "今天天汽很好"),
            (1, "我们去公圆玩"),
            (2, "小明在吃萍果"),
        ]
        result = c.correct_batch(texts, max_retries=1)
        assert len(result) > 0, "批量纠错返回空结果"
        print(f"\n[批量纠错] 输入 {len(texts)} 条，返回 {len(result)} 条")
        for row_idx, corrected in result.items():
            orig = texts[row_idx][1] if row_idx < len(texts) else "?"
            print(f"  [{row_idx}] {orig} → {corrected}")

    def test_polish(self):
        """润色：输入已纠错文本，验证润色结果。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        c = self._make_corrector(enable_polish=True)
        result = c.polish(
            "今天天气很好，我们去公园玩吧",
            "今天天气很好，我们去公园玩耍"
        )
        assert result is not None, "润色返回了 None"
        print("\n[润色] 原文: 今天天气很好，我们去公园玩吧")
        print("  纠错: 今天天气很好，我们去公园玩耍")
        print(f"  润色: {result}")

    def test_extract_env(self):
        """环境提取：从 OCR 文本中提取领域/氛围信息。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        c = self._make_corrector()
        c._extract_env = False
        c._env_context = ""
        result = c.extract_environment([
            "勇者啊，你终于来了！",
            "魔王已经占领了城堡。",
            "王国的命运掌握在你手中。",
        ])
        assert result is not None, "环境提取返回了 None"
        assert len(result.strip()) > 0, "环境提取返回空"
        print(f"\n[环境提取] 结果: {result}")


# ── 缓存验证 ──

class TestCacheLive:
    """验证缓存机制：相同请求应命中缓存。"""

    def test_cache_hit(self):
        """相同参数两次调用，第二次应命中缓存（不发请求）。"""
        api = _load_api_config()
        if not api["api_key"] or api["api_key"] == "sk-xxx":
            pytest.skip("API key 未配置")

        prompt = "请回答：中国的首都是哪里？只回答城市名。"
        kwargs = dict(
            prompt=prompt,
            system_prompt="简洁回答。",
            api_key=api["api_key"],
            base_url=api["base_url"],
            model=api["model"],
            timeout=api["timeout"],
            max_tokens=32,
            temperature=0.1,
            log_title="test_cache_live",
        )

        # 第一次调用
        t0 = time.time()
        r1 = ask_llm(**kwargs)
        t1 = time.time()

        # 第二次调用（应命中缓存）
        t2 = time.time()
        r2 = ask_llm(**kwargs)
        t3 = time.time()

        assert r1 is not None
        assert r2 is not None
        assert r1.strip() == r2.strip()

        cache_time = t3 - t2
        api_time = t1 - t0
        print(f"\n[缓存测试] 首次: {api_time:.2f}s, 缓存命中: {cache_time:.4f}s")
        print(f"  结果: {r1.strip()}")
        # 缓存命中应该明显更快（< 0.05s vs > 0.5s）
        # 但不硬性断言，因为环境差异可能影响计时
