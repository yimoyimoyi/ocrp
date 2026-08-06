"""P2 批次修复（P2-1 SRT / P2-2 性能 / P2-3 check_dll / P2-4 死代码 / P2-6 JSON 收口）回归测试。

覆盖：
- P2-1 SRT 导出：end<=start 钳制 _MIN_SRT_DURATION、多行文本换行规范化（\r\n→\n、连续空行压缩）、
  sort_results_by_order 默认时长统一引用 DEFAULT_SRT_DURATION
- P2-2 性能：post_sim_dedup 长度预筛不改变阈值语义（数学上界证明 + 朴素实现等价性）、
  sorted_insert bisect_right 等值时间戳语义（后插者排后、稳定有序）
- P2-3 check_dll：主脚本子进程 exit 0、threading 别名 / from threading import Thread 识别、
  Lambda target 检测、Attribute target 保守跳过
- P2-4 死代码：load_key / _fmt_time / _build_context_block / _submit_correction /
  _remove_correction_worker / _AudioExtractWorker / _audio_timer 已删除；_imread_unicode 已恢复；
  correct_batch / BatchCorrectionWorker 无 context_window 参数；配置键无 corr_context_window 残留
- P2-6 JSON 收口：load_json_with_comments 行/块注释、字符串内 // 与 :// 不误删、
  api_preset_manager 加载带注释的 api_presets.json 不失败

WorkflowManager / ResultTableWidget 相关测试使用 offscreen 平台（无需真实显示）。
"""

import ast
import importlib.util
import inspect
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core.ai_correction import AICorrector
from core.config_manager import MODE_PARAMS_DEFAULTS, load_json_with_comments
from core.result_processor import (
    _MIN_SRT_DURATION,
    _normalize_srt_text,
    export_results,
    polish_results,
    sort_results_by_order,
)
from core.utils import DEFAULT_SRT_DURATION, get_similarity
from core.workers import BatchCorrectionWorker

ROOT = Path(__file__).resolve().parent.parent


def _srt_result(time_sec: float = 5.0, end_sec=0.0, raw: str = "测试文本", time: str = "00:00:05"):
    """构造 export_results 所需的 SRT 输入项。"""
    return {
        "time_sec": time_sec,
        "end_sec": end_sec,
        "time": time,
        "region": "R1",
        "engine": "eng",
        "speaker": "NONE",
        "content": raw,
        "raw": raw,
    }


def _export_srt_text(results: list, tmp_path, srt_mode: str = "corrected") -> str:
    out = tmp_path / "out.srt"
    export_results(results, str(out), fmt="srt", srt_mode=srt_mode)
    return out.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# P2-1 SRT 导出
# ══════════════════════════════════════════════════════════════════


class TestSrtEndClamp:
    """P2-1 回归：end <= start 时钳制为 start + _MIN_SRT_DURATION。"""

    def test_end_zero_clamped(self, tmp_path):
        text = _export_srt_text([_srt_result(5.0, 0.0)], tmp_path)
        assert "00:00:05,000 --> 00:00:06,000" in text

    def test_end_before_start_clamped(self, tmp_path):
        text = _export_srt_text([_srt_result(5.0, 2.0)], tmp_path)
        assert "00:00:05,000 --> 00:00:06,000" in text

    def test_end_missing_clamped(self, tmp_path):
        text = _export_srt_text([_srt_result(5.0, None)], tmp_path)
        assert "00:00:05,000 --> 00:00:06,000" in text

    def test_valid_end_preserved(self, tmp_path):
        text = _export_srt_text([_srt_result(5.0, 7.5)], tmp_path)
        assert "00:00:05,000 --> 00:00:07,500" in text

    def test_clamp_duration_constant_is_one_second(self):
        # 钳制时长常量本身是 1.0s（若改默认值，测试需同步）
        assert _MIN_SRT_DURATION == 1.0


class TestSrtTextNormalization:
    """P2-1 回归：_normalize_srt_text 换行规范化，块结构完好。"""

    def test_crlf_and_consecutive_blank_lines_collapsed(self, tmp_path):
        raw = "第一行\r\n\r\n\r\n第二行\r\n第三行"
        text = _export_srt_text([_srt_result(1.0, 4.0, raw=raw)], tmp_path)
        # 无 \r 残留；块内三行文本仅以单个 \n 连接
        assert "\r" not in text
        assert "第一行\n第二行\n第三行" in text
        # 块结构完好：整份输出仅一个空行（块尾分隔符），无独立空行提前截断
        assert text.count("\n\n") == 1

    def test_normalize_unit_cases(self):
        assert _normalize_srt_text("a\r\nb") == "a\nb"
        assert _normalize_srt_text("a\rb") == "a\nb"
        assert _normalize_srt_text("a\n\n\nb") == "a\nb"
        assert _normalize_srt_text("  a  \n\n") == "a"

    def test_single_line_untouched(self, tmp_path):
        text = _export_srt_text([_srt_result(1.0, 2.0, raw="单行字幕")], tmp_path)
        assert "单行字幕" in text
        assert text.count("\n\n") == 1


class TestSortByOrderDefaultDuration:
    """P2-1 回归：sort_results_by_order 的 end_sec 默认引用 DEFAULT_SRT_DURATION（原 ts+3.0 字面量）。"""

    def test_default_end_sec_matches_constant(self):
        results = [{"time_sec": 1.0, "region": "R1", "content": "你好", "time": "00:00:01"}]
        out = sort_results_by_order(results, "R1")
        assert len(out) == 1
        assert out[0]["end_sec"] == 1.0 + DEFAULT_SRT_DURATION

    def test_existing_end_sec_preserved(self):
        results = [{"time_sec": 1.0, "end_sec": 9.5, "region": "R1", "content": "你好", "time": "00:00:01"}]
        out = sort_results_by_order(results, "R1")
        assert out[0]["end_sec"] == 9.5


# ══════════════════════════════════════════════════════════════════
# P2-2 post_sim_dedup 长度预筛（不改变阈值语义）
# ══════════════════════════════════════════════════════════════════


def _polish_raw(items):
    """polish_results 输入构造：(time_sec, time_str, region, engine, raw_text)。"""
    return [(float(i), f"{i:02d}:00", "R", "eng", text) for i, text in enumerate(items)]


def _naive_global_dedup(items: list[str], threshold: float) -> list[str]:
    """post_sim_dedup 的朴素参考实现（无长度预筛，全量相似度比较）。"""
    merged: list[str] = []
    for cur in items:
        is_dup = False
        for j, exist in enumerate(merged):
            if get_similarity(exist, cur) > threshold:
                if len(cur) > len(exist):
                    merged[j] = cur
                is_dup = True
                break
        if not is_dup:
            merged.append(cur)
    return merged


class TestLengthPrescreen:
    """P2-2 回归：长度预筛 f = t/(2-t) 严格不改变阈值结果。"""

    def test_prescreen_bound_math(self):
        """数学上界：长度比超 f 的文本对，ratio 必 ≤ 阈值（预筛跳过是安全的）。"""
        t = 0.9
        f = t / (2.0 - t)
        for la in range(1, 40):
            for lb in range(1, 40):
                max_ratio = 2 * min(la, lb) / (la + lb)
                if la < lb * f or la > lb / f:
                    # 预筛会跳过 ⇒ 实际 ratio 不可能超过阈值
                    assert max_ratio <= t, (la, lb, max_ratio)
                else:
                    # 界内 ⇒ 相似度理论上可能达标（预筛不得跳过）
                    assert max_ratio >= t, (la, lb, max_ratio)

    def test_dedup_keeps_longest_within_bound(self):
        """界内相似对（8 vs 9，ratio≈0.94 > 0.9）：去重且保留更长者。"""
        out = polish_results(_polish_raw(["A" * 8, "A" * 9]), post_sim_threshold=0.9)
        assert len(out) == 1
        assert out[0]["content"] == "A" * 9

    def test_out_of_bound_not_merged(self):
        """长度差 25%（8 vs 10）极相似对：不被误并（ratio 上界 0.889 < 0.9）。"""
        out = polish_results(_polish_raw(["A" * 8, "A" * 10]), post_sim_threshold=0.9)
        assert len(out) == 2
        assert [r["content"] for r in out] == ["A" * 8, "A" * 10]

    def test_different_text_not_merged(self):
        out = polish_results(_polish_raw(["你好世界", "完全无关内容"]), post_sim_threshold=0.9)
        assert len(out) == 2

    def test_prescreen_equivalent_to_naive(self):
        """与朴素全量比较输出完全一致（预筛不改变阈值语义）。

        全部不同 speaker 前缀 → 第一阶段（连续同 speaker 合并）零干扰，
        第二阶段结果可直接对照朴素全局去重参考实现。
        """
        prefixes = ["甲：", "乙：", "丙：", "丁：", "戊：", "己：", "庚："]
        bodies = [
            "你好世界",
            "你好世界啊",
            "你好世界",
            "完全不同的内容",
            "A" * 8,
            "A" * 10,
            "再见再见",
        ]
        texts = [p + b for p, b in zip(prefixes, bodies, strict=True)]
        out = polish_results(_polish_raw(texts), post_sim_threshold=0.9)
        assert [r["content"] for r in out] == _naive_global_dedup(texts, 0.9)


# ══════════════════════════════════════════════════════════════════
# P2-2 sorted_insert bisect_right 语义
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def app():
    _app = QApplication.instance() or QApplication([])
    yield _app


@pytest.fixture()
def table(app):
    from ui.result_table import ResultTableWidget

    return ResultTableWidget()


def _insert(table, time_sec: float, raw: str):
    table.add_result("00:00", "R1", "eng", raw, 0.9, time_sec=time_sec, sorted_insert=True)


class TestSortedInsertBisect:
    """P2-2 回归：bisect_right 语义与原循环一致——等值时间戳后插者排后。"""

    def test_equal_time_sec_second_inserts_after(self, table):
        _insert(table, 5.0, "first")
        _insert(table, 5.0, "second")
        assert [r["raw"] for r in table._results] == ["first", "second"]

    def test_equal_time_sec_three_inserts_stable(self, table):
        for raw in ["a", "b", "c"]:
            _insert(table, 5.0, raw)
        assert [r["raw"] for r in table._results] == ["a", "b", "c"]

    def test_out_of_order_inserts_keep_sorted_and_stable(self, table):
        for t, raw in [(3.0, "c"), (1.0, "a"), (3.0, "d"), (2.0, "b"), (1.0, "e")]:
            _insert(table, t, raw)
        times = [r["time_sec"] for r in table._results]
        raws = [r["raw"] for r in table._results]
        assert times == sorted(times) == [1.0, 1.0, 2.0, 3.0, 3.0]
        # 等值组内保持插入顺序（稳定）
        assert raws.index("a") < raws.index("e")
        assert raws.index("c") < raws.index("d")


# ══════════════════════════════════════════════════════════════════
# P2-3 check_dll_regressions
# ══════════════════════════════════════════════════════════════════


def _load_check_dll_module():
    path = ROOT / "scripts" / "check_dll_regressions.py"
    spec = importlib.util.spec_from_file_location("check_dll_regressions", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestCheckDllMain:
    """P2-3 回归：主入口以子进程运行必须 exit 0（torch 顺序 / UI 线程检查 / bat ASCII）。"""

    def test_main_script_exit_zero(self):
        script = ROOT / "scripts" / "check_dll_regressions.py"
        proc = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=120
        )
        assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


class TestCheckDllThreadDetection:
    """P2-3 回归：threading 别名 / Thread 导入 / Lambda / Attribute target 处理。"""

    def test_collect_thread_constructors_aliases(self):
        tree = ast.parse(
            "import threading\n"
            "import threading as th\n"
            "from threading import Thread\n"
            "from threading import Thread as T\n"
        )
        mod = _load_check_dll_module()
        aliases, ctors = mod._collect_thread_constructors(tree)
        assert "threading" in aliases and "th" in aliases
        assert "Thread" in ctors and "T" in ctors

    def test_qtimer_in_thread_function_target_fails(self, tmp_path, monkeypatch):
        mod = _load_check_dll_module()
        f = tmp_path / "bad_fn.py"
        f.write_text(
            "import threading as th\n"
            "from PySide6.QtCore import QTimer\n"
            "def worker():\n"
            "    QTimer.singleShot(0, lambda: None)\n"
            "th.Thread(target=worker).start()\n",
            encoding="utf-8",
        )
        fails: list[str] = []
        monkeypatch.setattr(mod, "fail", fails.append)
        mod.check_no_qtimer_in_thread(f)
        assert len(fails) == 1  # 别名 + 函数 target 内 QTimer.singleShot → FAIL
        assert "worker" in fails[0]

    def test_qtimer_in_thread_lambda_target_fails(self, tmp_path, monkeypatch):
        mod = _load_check_dll_module()
        f = tmp_path / "bad_lambda.py"
        f.write_text(
            "import threading\n"
            "from PySide6.QtCore import QTimer\n"
            "threading.Thread(target=lambda: QTimer.singleShot(0, f)).start()\n",
            encoding="utf-8",
        )
        fails: list[str] = []
        monkeypatch.setattr(mod, "fail", fails.append)
        mod.check_no_qtimer_in_thread(f)
        assert len(fails) == 1  # 内联 Lambda target 也被静态扫描

    def test_qtimer_from_thread_import_fails(self, tmp_path, monkeypatch):
        mod = _load_check_dll_module()
        f = tmp_path / "bad_from.py"
        f.write_text(
            "from threading import Thread\n"
            "from PySide6.QtCore import QTimer\n"
            "def worker():\n"
            "    QTimer.singleShot(0, f)\n"
            "Thread(target=worker).start()\n",
            encoding="utf-8",
        )
        fails: list[str] = []
        monkeypatch.setattr(mod, "fail", fails.append)
        mod.check_no_qtimer_in_thread(f)
        assert len(fails) == 1

    def test_attribute_target_skipped_conservatively(self, tmp_path, monkeypatch):
        """Attribute target 保守跳过（无法静态解析），不得误报。"""
        mod = _load_check_dll_module()
        f = tmp_path / "attr_target.py"
        f.write_text(
            "import threading as th\nfrom PySide6.QtCore import QTimer\nth.Thread(target=self._run).start()\n",
            encoding="utf-8",
        )
        fails: list[str] = []
        monkeypatch.setattr(mod, "fail", fails.append)
        mod.check_no_qtimer_in_thread(f)
        assert fails == []

    def test_thread_without_qtimer_ok(self, tmp_path, monkeypatch):
        mod = _load_check_dll_module()
        f = tmp_path / "good.py"
        f.write_text(
            "import threading\ndef worker():\n    pass\nthreading.Thread(target=worker).start()\n",
            encoding="utf-8",
        )
        fails: list[str] = []
        monkeypatch.setattr(mod, "fail", fails.append)
        mod.check_no_qtimer_in_thread(f)
        assert fails == []


# ══════════════════════════════════════════════════════════════════
# P2-4 死代码删除
# ══════════════════════════════════════════════════════════════════


class TestDeadCodeRemoved:
    """P2-4 回归：已删符号不再存在；误删的 _imread_unicode 已恢复。"""

    def test_load_key_removed(self):
        import core.config_manager as cm

        assert not hasattr(cm, "load_key")
        assert not hasattr(cm.ConfigManager, "load_key")
        with pytest.raises(ImportError):
            from core.config_manager import load_key  # noqa: F401

    def test_correction_dead_helpers_removed(self):
        import core.ai_correction as ac

        assert not hasattr(ac, "_fmt_time")
        assert not hasattr(ac, "_build_context_block")

    def test_correction_flow_dead_methods_removed(self):
        import core.workflow.correction_flow as cf

        assert not hasattr(cf.CorrectionFlow, "_submit_correction")
        assert not hasattr(cf.CorrectionFlow, "_remove_correction_worker")
        assert not hasattr(cf.CorrectionFlow, "_on_correction_failed")

    def test_audio_extract_worker_removed(self):
        import ui.video_preview as vp

        assert not hasattr(vp, "_AudioExtractWorker")
        assert not hasattr(vp.VideoPreviewWidget, "_audio_timer")
        src = inspect.getsource(vp.VideoPreviewWidget)
        assert "_audio_timer" not in src
        assert "_extract_full_audio_async" not in src
        assert "_on_audio_extracted" not in src

    def test_imread_unicode_restored(self):
        """误删后已恢复：Unicode 安全图片读取函数必须存在。"""
        import ui.video_preview as vp

        assert callable(vp._imread_unicode)
        assert inspect.isfunction(vp._imread_unicode)

    def test_correct_batch_signature_no_context_params(self):
        sig = inspect.signature(AICorrector.correct_batch)
        assert "context_window" not in sig.parameters
        assert "context_block" not in sig.parameters

    def test_batch_worker_signature_no_context_window(self):
        sig = inspect.signature(BatchCorrectionWorker.__init__)
        assert "context_window" not in sig.parameters

    def test_mode_params_defaults_no_context_keys(self):
        assert "corr_context_window" not in MODE_PARAMS_DEFAULTS
        assert "context_window" not in MODE_PARAMS_DEFAULTS

    def test_settings_dialog_no_context_window_field(self):
        import ui.settings_dialog as sd

        assert "context_window" not in inspect.getsource(sd)


# ══════════════════════════════════════════════════════════════════
# P2-6 JSON 注释解析收口
# ══════════════════════════════════════════════════════════════════


class TestLoadJsonWithComments:
    """P2-6 回归：统一解析器处理行/块注释，字符串内 // 与 :// 不误删。"""

    def test_line_comments_removed(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(
            '{\n  // 行注释\n  "a": 1,\n  "b": "x // 保留" // 尾注释\n}\n',
            encoding="utf-8",
        )
        assert load_json_with_comments(f) == {"a": 1, "b": "x // 保留"}

    def test_block_comments_and_url_string_kept(self, tmp_path):
        f = tmp_path / "b.json"
        f.write_text(
            '{\n  /* 块注释 */\n  "url": "http://example.com/api",\n  "c": 2\n}\n',
            encoding="utf-8",
        )
        data = load_json_with_comments(f)
        assert data["url"] == "http://example.com/api"  # :// 与 // 在字符串内不被误删
        assert data["c"] == 2

    def test_escaped_quote_in_string(self, tmp_path):
        f = tmp_path / "e.json"
        f.write_text('{\n  "t": "say \\"hi\\" // keep"\n}\n', encoding="utf-8")
        assert load_json_with_comments(f)["t"] == 'say "hi" // keep'

    def test_private_old_name_removed(self):
        import core.config_manager as cm

        assert not hasattr(cm, "_load_json_with_comments")


class TestApiPresetManagerComments:
    """P2-6 回归：api_preset_manager 加载带注释的 api_presets.json 不失败。"""

    def test_loads_commented_presets(self, monkeypatch, tmp_path):
        import core.api_preset_manager as apm

        f = tmp_path / "api_presets.json"
        f.write_text(
            "{\n"
            "  // 预设注释\n"
            '  "presets": {\n'
            '    "local": {\n'
            '      "api_key": "sk-xxx", // 密钥\n'
            '      "base_url": "http://127.0.0.1:8080",\n'
            '      "model": "llama3",\n'
            '      "timeout": 30\n'
            "    }\n"
            "  },\n"
            '  "default_preset": "local"\n'
            "}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(apm, "PRESETS_PATH", f)
        # 单例重建（__new__ 缓存实例，monkeypatch 后强制重载）
        apm.APIPresetManager._instance = None
        try:
            mgr = apm.APIPresetManager()
            assert mgr.get_default_name() == "local"
            preset = mgr.get_preset("local")
            assert preset is not None
            assert preset["model"] == "llama3"
            assert preset["base_url"] == "http://127.0.0.1:8080"
        finally:
            apm.APIPresetManager._instance = None  # 恢复，避免污染其他测试

    def test_corrupt_json_falls_back_to_default(self, monkeypatch, tmp_path):
        import core.api_preset_manager as apm

        f = tmp_path / "api_presets.json"
        f.write_text("{ 不是合法 JSON", encoding="utf-8")
        monkeypatch.setattr(apm, "PRESETS_PATH", f)
        apm.APIPresetManager._instance = None
        try:
            mgr = apm.APIPresetManager()
            assert mgr.get_names() == []
            assert mgr.get_default_name() == ""
        finally:
            apm.APIPresetManager._instance = None
