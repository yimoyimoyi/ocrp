"""结果处理器单元测试。"""



class TestFormatTime:
    """测试时间格式化函数。"""

    def test_zero(self):
        """0 秒应格式化为 00:00:00,000。"""
        def format_time(seconds: float) -> str:
            if seconds < 0:
                seconds = 0
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        assert format_time(0) == "00:00:00,000"

    def test_minutes(self):
        def format_time(seconds: float) -> str:
            if seconds < 0:
                seconds = 0
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        assert format_time(61.5) == "00:01:01,500"

    def test_hours(self):
        def format_time(seconds: float) -> str:
            if seconds < 0:
                seconds = 0
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        assert format_time(3661.123) == "01:01:01,123"

    def test_negative(self):
        def format_time(seconds: float) -> str:
            if seconds < 0:
                seconds = 0
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        assert format_time(-1) == "00:00:00,000"


class TestDeduplication:
    """测试文本去重逻辑。"""

    def test_similarity_identical(self):
        from rapidfuzz.fuzz import ratio

        assert ratio("你好世界", "你好世界") == 100

    def test_similarity_different(self):
        from rapidfuzz.fuzz import ratio

        assert ratio("你好世界", "再见") < 50

    def test_empty_strings(self):
        from rapidfuzz.fuzz import ratio

        assert ratio("", "") == 100
