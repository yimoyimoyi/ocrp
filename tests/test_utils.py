"""工具函数单元测试。"""


class TestFindFfmpeg:
    """测试 FFmpeg 路径查找。"""

    def test_find_ffmpeg_returns_string(self):
        from core.utils import find_ffmpeg

        path = find_ffmpeg("ffmpeg")
        assert isinstance(path, str)
        assert "ffmpeg" in path

    def test_find_ffprobe_returns_string(self):
        from core.utils import find_ffmpeg

        path = find_ffmpeg("ffprobe")
        assert isinstance(path, str)
        assert "ffprobe" in path


class TestConstants:
    """测试模块常量。"""

    def test_engine_constants(self):
        from core.utils import ENGINE_PADDLEOCR, ENGINE_WHISPERX

        assert ENGINE_PADDLEOCR == "paddleocr"
        assert ENGINE_WHISPERX == "whisperx"

    def test_mode_constants(self):
        from core.utils import MODE_ASR_ONLY, MODE_OCR_ASR_FULL, MODE_OCR_ONLY

        assert "OCR" in MODE_OCR_ONLY
        assert "ASR" in MODE_ASR_ONLY
        assert "完整流程" in MODE_OCR_ASR_FULL

    def test_default_constants(self):
        from core.utils import DEFAULT_OCR_TEMPLATE, DEFAULT_SRT_DURATION

        assert DEFAULT_OCR_TEMPLATE == "通用OCR"
        assert DEFAULT_SRT_DURATION == 3.0


class TestPopulateModelCombo:
    """测试模型下拉框填充。"""

    def test_populate_empty_models(self):
        from core.utils import populate_model_combo

        class MockCombo:
            def currentText(self): return ""
            def blockSignals(self, v): pass
            def clear(self): pass
            def addItems(self, items): self._items = items
            def setEditText(self, t): pass

        combo = MockCombo()
        combo._items = []
        populate_model_combo(combo, [])
        assert combo._items == []

    def test_populate_with_models(self):
        from core.utils import populate_model_combo

        class MockCombo:
            def currentText(self): return ""
            def blockSignals(self, v): pass
            def clear(self): self._items = []
            def addItems(self, items): self._items = items
            def setEditText(self, t): pass

        combo = MockCombo()
        populate_model_combo(combo, ["gpt-4o", "gpt-3.5"])
        assert combo._items == ["gpt-4o", "gpt-3.5"]
