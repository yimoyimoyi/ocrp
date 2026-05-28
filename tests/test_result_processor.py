"""结果处理器单元测试。"""

from core.result_processor import _fmt_srt_time, get_similarity


class TestFormatTime:
    """测试时间格式化函数。"""

    def test_zero(self):
        assert _fmt_srt_time(0) == "00:00:00,000"

    def test_minutes(self):
        assert _fmt_srt_time(61.5) == "00:01:01,500"

    def test_hours(self):
        assert _fmt_srt_time(3661.123) == "01:01:01,123"

    def test_negative(self):
        assert _fmt_srt_time(-1) == "00:00:00,000"


class TestDeduplication:
    """测试文本去重逻辑（使用生产代码中的 get_similarity）。"""

    def test_similarity_identical(self):
        assert get_similarity("你好世界", "你好世界") > 0.99

    def test_similarity_different(self):
        assert get_similarity("你好世界", "再见") < 0.5

    def test_empty_strings(self):
        # 生产代码中空字符串返回 0.0（guard: if a and b else 0.0）
        assert get_similarity("", "") == 0.0
