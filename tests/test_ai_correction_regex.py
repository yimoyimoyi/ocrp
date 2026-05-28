"""AI 纠错正则和解析容错测试。"""

import re

# 从 ai_correction.py 导入正则模式，保持与生产代码同步
from core.ai_correction import _MD_FENCE, ID_PATTERN, ID_TAG, TIME_MARKER

# 宽松模式回退：匹配 "数字. 文本" 或 "数字) 文本"
_LOOSE = re.compile(r'^(\d+)\s*[.)\:：]\s*(.+)', re.MULTILINE)


def _parse_batch_result(text: str) -> dict:
    """从 AI 返回文本中解析 [ID:idx] 标记的内容（从 AICorrector._parse_batch_result 提取）。"""
    cleaned = _MD_FENCE.sub('', text).strip()
    result = {}
    for match in ID_PATTERN.finditer(cleaned):
        try:
            idx = int(match.group(1))
            content = match.group(2).strip()
            if content:
                result[idx] = content
        except (ValueError, IndexError):
            continue
    # 回退：宽松模式
    if not result:
        for match in _LOOSE.finditer(cleaned):
            try:
                idx = int(match.group(1))
                content = match.group(2).strip()
                if content:
                    result[idx] = content
            except (ValueError, IndexError):
                continue
    return result


def _clean_content(text: str) -> str:
    """去除 AI 可能附带的时间标记、[ID:n] 标记和 markdown 代码块。"""
    text = _MD_FENCE.sub('', text)
    text = TIME_MARKER.sub('', text)
    text = ID_TAG.sub('', text)
    return text.strip()


class TestIDPattern:
    """测试 [ID:n] 正则匹配容错。"""

    def test_standard_format(self):
        text = "[ID:0] 你好世界\n[ID:1] 测试文本"
        result = _parse_batch_result(text)
        assert result == {0: "你好世界", 1: "测试文本"}

    def test_fullwidth_colon(self):
        text = "[ID：0] 全角冒号测试"
        result = _parse_batch_result(text)
        assert 0 in result
        assert "全角冒号" in result[0]

    def test_extra_spaces(self):
        text = "[ID : 0] 带空格"
        result = _parse_batch_result(text)
        assert 0 in result

    def test_lowercase_id(self):
        text = "[id:0] 小写测试"
        result = _parse_batch_result(text)
        assert 0 in result

    def test_markdown_fence_removal(self):
        text = "```text\n[ID:0] 代码块内\n[ID:1] 第二行\n```"
        result = _parse_batch_result(text)
        assert 0 in result
        assert 1 in result

    def test_loose_fallback_numbered_list(self):
        text = "0. 第一行\n1. 第二行"
        result = _parse_batch_result(text)
        assert 0 in result
        assert 1 in result

    def test_loose_fallback_paren(self):
        text = "0) 第一行\n1) 第二行"
        result = _parse_batch_result(text)
        assert 0 in result

    def test_empty_content_skipped(self):
        text = "[ID:0] \n[ID:1] 有效内容"
        result = _parse_batch_result(text)
        assert 0 not in result
        assert 1 in result

    def test_no_match_returns_empty(self):
        assert _parse_batch_result("随便一段文字") == {}
        assert _parse_batch_result("") == {}


class TestCleanContent:
    """测试 _clean_content 清洗逻辑。"""

    def test_remove_time_marker(self):
        text = "[00:01:23.456 -> 00:01:25.789] 测试文本"
        result = _clean_content(text)
        assert "测试文本" in result
        assert "00:01" not in result

    def test_remove_id_tag(self):
        text = "[ID:3] 测试文本"
        result = _clean_content(text)
        assert result == "测试文本"

    def test_remove_markdown_fence(self):
        text = "```json\n测试文本\n```"
        result = _clean_content(text)
        assert result == "测试文本"

    def test_combined_clean(self):
        text = "```text\n[ID:0] [00:01:00 -> 00:01:05] 实际内容\n```"
        result = _clean_content(text)
        assert result == "实际内容"

    def test_empty_input(self):
        assert _clean_content("") == ""
        assert _clean_content("   ") == ""


class TestTimeMarkerRegex:
    """测试时间标记正则。"""

    def test_standard_srt_time(self):
        text = "[00:01:23,456 -> 00:01:25,789]"
        result = TIME_MARKER.sub("", text).strip()
        assert result == ""

    def test_arrow_variant(self):
        text = "[00:01:23 → 00:01:25]"
        result = TIME_MARKER.sub("", text).strip()
        assert result == ""

    def test_paren_format(self):
        text = "(00:01:23 -> 00:01:25)"
        result = TIME_MARKER.sub("", text).strip()
        assert result == ""

    def test_simple_timestamp(self):
        text = "[00:01:23]"
        result = TIME_MARKER.sub("", text).strip()
        assert result == ""

    def test_time_with_text(self):
        text = "[00:01:23 -> 00:01:25] 实际文本"
        result = TIME_MARKER.sub("", text).strip()
        assert "实际文本" in result


class TestSegmentationTimeGap:
    """分句时间轴约束测试。"""

    @staticmethod
    def _make_corrector(seg_time_gap: float = 3.0):
        from core.ai_correction import AICorrector
        config = {"enabled": False, "engine": "llamacpp", "api_key": "test",
                  "base_url": "http://localhost", "model": "test", "timeout": 30}
        c = AICorrector(config=config)
        c._seg_time_gap = seg_time_gap
        return c

    @staticmethod
    def _make_texts(*items: tuple[int, str, float, float]):
        """构造 texts 列表: (row_idx, raw_text, time_start, time_end)"""
        return [list(item) for item in items]

    def test_gap_within_threshold_allows_merge(self):
        """时间间隔 ≤ 阈值时，短碎片词正常合并。"""
        c = self._make_corrector(seg_time_gap=3.0)
        texts = self._make_texts(
            (0, "ok", 0.0, 1.0),
            (1, "lets go", 1.5, 3.0),
        )
        text_map, range_map = c.segment_sentences(texts)
        # gap = 1.5 - 1.0 = 0.5 ≤ 3.0，碎片词应合并
        assert text_map[0] == "ok lets go"
        assert range_map["ok lets go"] == (0, 1)

    def test_gap_exceeds_threshold_forces_split(self):
        """时间间隔 > 阈值时强制拆分，即使碎片词也不合并。"""
        c = self._make_corrector(seg_time_gap=3.0)
        texts = self._make_texts(
            (0, "hello", 0.0, 1.0),
            (1, "world", 5.0, 6.0),
        )
        text_map, range_map = c.segment_sentences(texts)
        # gap = 5.0 - 1.0 = 4.0 > 3.0，强制拆分
        assert text_map[0] == "hello"
        assert text_map[1] == "world"

    def test_gap_equals_threshold_allows_merge(self):
        """时间间隔 == 阈值时允许合并（仅 > 阈值才拆分）。"""
        c = self._make_corrector(seg_time_gap=3.0)
        texts = self._make_texts(
            (0, "a", 0.0, 1.0),
            (1, "b", 4.0, 5.0),
        )
        text_map, range_map = c.segment_sentences(texts)
        # gap = 4.0 - 1.0 = 3.0 == threshold，不拆分
        assert text_map[0] == "a b"

    def test_mixed_gaps_respect_threshold(self):
        """混合场景：gaps [0.2, 3.5, 0.2]，阈值 2.0s → 第 2 个 gap 超时强制拆分。"""
        c = self._make_corrector(seg_time_gap=2.0)
        texts = self._make_texts(
            (0, "line one", 0.0, 1.0),
            (1, "line two", 1.2, 2.5),   # gap 0.2 → 可合并
            (2, "line three", 6.0, 7.0), # gap 3.5 → 强制拆分
            (3, "line four", 7.2, 8.5),  # gap 0.2 → 可合并（碎片词）
        )
        text_map, range_map = c.segment_sentences(texts)
        # 0,1 合并；2,3 合并（gap 0.2s + 碎片词）
        assert text_map[0] == "line one line two"
        assert text_map[2] == "line three line four"

    def test_custom_threshold_configured(self):
        """自定义阈值生效：设 1.0s，gap 1.5s > 1.0s 应拆分。"""
        c = self._make_corrector(seg_time_gap=1.0)
        texts = self._make_texts(
            (0, "first", 0.0, 0.5),
            (1, "second", 2.0, 3.0),
        )
        text_map, range_map = c.segment_sentences(texts)
        # gap = 2.0 - 0.5 = 1.5 > 1.0，拆分
        assert text_map[0] == "first"
        assert text_map[1] == "second"

    def test_long_sentence_within_gap_merges(self):
        """长句在阈值内且满足短词条件时合并。"""
        c = self._make_corrector(seg_time_gap=3.0)
        texts = self._make_texts(
            (0, "ok, lets start", 0.0, 1.0),
            (1, "the game", 1.2, 2.5),
        )
        text_map, range_map = c.segment_sentences(texts)
        assert text_map[0] == "ok, lets start the game"
