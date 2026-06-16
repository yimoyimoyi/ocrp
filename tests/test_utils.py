"""核心工具函数单元测试。"""

from core.utils import format_time, get_similarity


class TestGetSimilarity:
    """测试字符串相似度计算。"""

    def test_identical_strings(self):
        assert get_similarity("hello", "hello") == 1.0

    def test_completely_different(self):
        assert get_similarity("abc", "xyz") < 0.5

    def test_empty_strings(self):
        assert get_similarity("", "") == 0.0
        assert get_similarity("hello", "") == 0.0
        assert get_similarity("", "hello") == 0.0

    def test_similar_strings(self):
        sim = get_similarity("hello world", "hello wrold")
        assert 0.8 < sim < 1.0

    def test_chinese_strings(self):
        sim = get_similarity("你好世界", "你好世界！")
        assert sim > 0.8


class TestFormatTime:
    """测试时间格式化。"""

    def test_zero(self):
        assert format_time(0) == "00:00:00,000"

    def test_simple_seconds(self):
        assert format_time(61.5) == "00:01:01,500"

    def test_hours(self):
        assert format_time(3661.123) == "01:01:01,123"

    def test_negative(self):
        assert format_time(-5) == "00:00:00,000"

    def test_fractional(self):
        assert format_time(0.001) == "00:00:00,001"
