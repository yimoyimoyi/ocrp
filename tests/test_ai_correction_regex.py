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
