# -*- coding: utf-8 -*-
"""结果导出器单元测试 —— TXT / JSON / CSV / SRT。"""

import json


def _sample_results():
    """返回一组测试用 OCR 结果。"""
    return [
        {"time_sec": 0.0, "time": "00:00", "region": "字幕", "engine": "paddleocr",
         "raw": "你好世界", "corrected": "", "confidence": 0.95, "end_sec": 2.0,
         "speaker": "NONE", "content": "你好世界"},
        {"time_sec": 2.0, "time": "00:02", "region": "字幕", "engine": "paddleocr",
         "raw": "第二行文本", "corrected": "", "confidence": 0.88, "end_sec": 4.0,
         "speaker": "NONE", "content": "第二行文本"},
    ]


class TestExportTXT:
    """测试 TXT 纯文本导出。"""

    def test_export_txt_basic(self, tmp_path):
        from core.result_processor import _export_txt

        output = tmp_path / "test.txt"
        _export_txt(_sample_results(), str(output), False, {}, False)
        content = output.read_text(encoding="utf-8")
        assert "你好世界" in content
        assert "第二行文本" in content

    def test_export_txt_with_corrected(self, tmp_path):
        from core.result_processor import _export_txt

        output = tmp_path / "test.txt"
        corrected = {0: "Hello World", 1: "Second Line"}
        _export_txt(_sample_results(), str(output), True, corrected, False)
        content = output.read_text(encoding="utf-8")
        assert "Hello World" in content
        assert "Second Line" in content

    def test_export_txt_keep_original(self, tmp_path):
        from core.result_processor import _export_txt

        output = tmp_path / "test.txt"
        corrected = {0: "Hello World"}
        _export_txt(_sample_results(), str(output), True, corrected, True)
        content = output.read_text(encoding="utf-8")
        # keep_original=True: always show original, not corrected
        assert "你好世界" in content


class TestExportJSON:
    """测试 JSON 格式导出。"""

    def test_export_json(self, tmp_path):
        from core.result_processor import _export_json

        output = tmp_path / "test.json"
        _export_json(_sample_results(), str(output), False, {}, False)
        data = json.loads(output.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert data[0]["raw"] == "你好世界"

    def test_export_json_with_corrected(self, tmp_path):
        from core.result_processor import _export_json

        output = tmp_path / "test.json"
        corrected = {0: "Hello World"}
        _export_json(_sample_results(), str(output), True, corrected, False)
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data[0]["corrected"] == "Hello World"


class TestExportCSV:
    """测试 CSV 格式导出。"""

    def test_export_csv(self, tmp_path):
        from core.result_processor import _export_csv

        output = tmp_path / "test.csv"
        _export_csv(_sample_results(), str(output), False, {}, False)
        content = output.read_text(encoding="utf-8")
        assert "你好世界" in content
        assert "第二行文本" in content


class TestExportSRT:
    """测试 SRT 字幕导出。"""

    def test_export_srt(self, tmp_path):
        from core.result_processor import _export_srt

        output = tmp_path / "test.srt"
        _export_srt(_sample_results(), str(output), False, {}, False, "original")
        content = output.read_text(encoding="utf-8")
        assert "1\n" in content
        assert "你好世界" in content
        assert "00:00:00,000" in content

    def test_export_srt_with_corrected(self, tmp_path):
        from core.result_processor import _export_srt

        output = tmp_path / "test.srt"
        corrected = {0: "Hello World"}
        _export_srt(_sample_results(), str(output), True, corrected, False, "corrected")
        content = output.read_text(encoding="utf-8")
        assert "Hello World" in content

    def test_export_srt_empty(self, tmp_path):
        from core.result_processor import _export_srt

        output = tmp_path / "test.srt"
        _export_srt([], str(output), False, {}, False, "original")
        assert not output.exists() or output.read_text(encoding="utf-8") == ""


class TestExportResults:
    """测试 export_results 顶层调度函数。"""

    def test_export_txt_format(self, tmp_path):
        from core.result_processor import export_results

        output = tmp_path / "test.txt"
        export_results(_sample_results(), str(output), fmt="txt")
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert len(content) > 0

    def test_export_json_format(self, tmp_path):
        from core.result_processor import export_results

        output = tmp_path / "test.json"
        export_results(_sample_results(), str(output), fmt="json")
        assert output.exists()
        data = json.loads(output.read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_export_csv_format(self, tmp_path):
        from core.result_processor import export_results

        output = tmp_path / "test.csv"
        export_results(_sample_results(), str(output), fmt="csv")
        assert output.exists()

    def test_export_srt_format(self, tmp_path):
        from core.result_processor import export_results

        output = tmp_path / "test.srt"
        export_results(_sample_results(), str(output), fmt="srt")
        assert output.exists()
