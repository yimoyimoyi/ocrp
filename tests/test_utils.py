"""核心工具函数单元测试。"""

from core.utils import (
    SUBTITLE_MODE_REGULAR,
    SUBTITLE_MODE_STREAM,
    format_time,
    get_similarity,
    normalize_subtitle_mode,
)


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


class TestNormalizeSubtitleMode:
    """字幕模式归一（P22：唯一规范 token，消除三套词汇表）。"""

    def test_canonical_tokens_pass_through(self):
        assert normalize_subtitle_mode("stream") == SUBTITLE_MODE_STREAM
        assert normalize_subtitle_mode("regular") == SUBTITLE_MODE_REGULAR

    def test_legacy_chinese_labels(self):
        assert normalize_subtitle_mode("流式字幕（去重）") == SUBTITLE_MODE_STREAM
        assert normalize_subtitle_mode("常规字幕（固定间隔）") == SUBTITLE_MODE_REGULAR
        assert normalize_subtitle_mode("流式") == SUBTITLE_MODE_STREAM
        assert normalize_subtitle_mode("常规") == SUBTITLE_MODE_REGULAR
        assert normalize_subtitle_mode("固定间隔") == SUBTITLE_MODE_REGULAR

    def test_translated_labels_are_locale_independent(self):
        # 英文/日文界面标签也必须映射到同一 token
        assert normalize_subtitle_mode("Streaming (Dedup)") == SUBTITLE_MODE_STREAM
        assert normalize_subtitle_mode("Regular (Fixed Interval)") == SUBTITLE_MODE_REGULAR
        assert normalize_subtitle_mode("ストリーミング（重複除去）") == SUBTITLE_MODE_STREAM
        assert normalize_subtitle_mode("通常（固定間隔）") == SUBTITLE_MODE_REGULAR

    def test_unknown_and_empty_fall_back_to_stream(self):
        # 未知值回落默认（历史默认流式），保证下游只需比较 token
        assert normalize_subtitle_mode("") == SUBTITLE_MODE_STREAM
        assert normalize_subtitle_mode(None) == SUBTITLE_MODE_STREAM
        assert normalize_subtitle_mode("完全未知的模式") == SUBTITLE_MODE_STREAM

    def test_regular_is_not_matched_by_substring_collision(self):
        # 回归：旧实现用 `"regular" in mode`，任何含该子串的值都会误判
        assert normalize_subtitle_mode("stream") != SUBTITLE_MODE_REGULAR
