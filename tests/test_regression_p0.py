"""P0 批次修复（T1-T7）回归测试。

覆盖：
- T4 logger: _ColoredFormatter format 后 record.levelname 还原（不污染文件日志）
- T5 原子写: atomic_write_json（临时文件 + fsync + os.replace + 权限保留 + 清理）
- T7 LLM: _is_local_url 判定 / 空 key 空 model 早期分支 / 损坏缓存容错
- T6 removeRows: remove_rows_by_predicate + _ResultsModel.removeRows 防御实现
- T1 引擎释放: close() 幂等 + manager release_all/reload_config 先释放
- T2 并发 OCR: _infer_lock 存在 + _paddle_available=False 快速返回 +
  SharedMemoryManager 加锁往返 + read_json_response 按 request_id 匹配
"""

import json
import logging
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.asr_engine import ASREngineManager, WhisperXEngine
from core.config_manager import atomic_write_json
from core.llm_utils import llm_client
from core.logger import _LEVEL_COLORS, _ColoredFormatter
from core.ocr_engine import OCREngineManager, PaddleOCREngine
from core.subprocess_utils import QtSubprocessManager, SharedMemoryManager

# ══════════════════════════════════════════════════════════════════
# T4 logger：_ColoredFormatter 不污染 record.levelname
# ══════════════════════════════════════════════════════════════════


def _make_record(level: int, msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord("orcp.test", level, __file__, 1, msg, (), None)


class TestLoggerColorFormatter:
    """T4 回归：format 后 record.levelname 必须还原为原值（否则文件 handler 读到 ANSI）。"""

    def test_levelname_restored_after_format(self):
        fmt = _ColoredFormatter("%(levelname)s: %(message)s")
        record = _make_record(logging.INFO)
        fmt.format(record)
        assert record.levelname == "INFO"

    def test_levelname_restored_for_error_too(self):
        fmt = _ColoredFormatter("%(levelname)s: %(message)s")
        record = _make_record(logging.ERROR)
        fmt.format(record)
        assert record.levelname == "ERROR"

    def test_output_contains_ansi_color(self):
        fmt = _ColoredFormatter("%(levelname)s: %(message)s")
        out = fmt.format(_make_record(logging.INFO))
        assert "\033[" in out
        # INFO 级别只对 levelname 着色（整行不着色）
        assert out == f"{_LEVEL_COLORS[logging.INFO]}INFO\033[0m: hello"

    def test_error_line_fully_colored(self):
        fmt = _ColoredFormatter("%(levelname)s: %(message)s")
        out = fmt.format(_make_record(logging.ERROR, "boom"))
        assert out.startswith("\033[91m") and out.endswith("\033[0m")

    def test_same_record_plain_formatter_has_no_ansi(self):
        """同一 record 先经彩色 formatter 再经普通 formatter → 文件日志无 ANSI。"""
        fmt = _ColoredFormatter("%(levelname)s: %(message)s")
        plain = logging.Formatter("%(levelname)s: %(message)s")
        record = _make_record(logging.INFO)
        fmt.format(record)
        assert "\033[" not in plain.format(record)


# ══════════════════════════════════════════════════════════════════
# T5 原子写：atomic_write_json
# ══════════════════════════════════════════════════════════════════


class TestAtomicWriteJson:
    """T5 回归：临时文件 + 原子替换 + 清理 + 嵌套目录。"""

    def test_write_new_file_nested_dirs(self, tmp_path):
        p = tmp_path / "a" / "b" / "cfg.json"
        atomic_write_json(p, {"key": 1, "中文": "值"})
        assert json.loads(p.read_text(encoding="utf-8")) == {"key": 1, "中文": "值"}

    def test_overwrite_existing_file(self, tmp_path):
        p = tmp_path / "cfg.json"
        atomic_write_json(p, {"v": 1})
        atomic_write_json(p, {"v": 2, "list": [1, 2, 3]})
        assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2, "list": [1, 2, 3]}

    def test_rebuild_corrupt_file(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text("{corrupt!!!", encoding="utf-8")
        atomic_write_json(p, {"ok": True})
        assert json.loads(p.read_text(encoding="utf-8")) == {"ok": True}

    def test_no_tmp_file_leftover(self, tmp_path):
        p = tmp_path / "cfg.json"
        atomic_write_json(p, {"a": 1})
        atomic_write_json(p, {"a": 2})
        assert not p.with_name(p.name + ".tmp").exists()
        assert list(tmp_path.iterdir()) == [p]

    def test_preserves_existing_permissions(self, tmp_path):
        """Linux 下权限保留（API key 文件常为 0600）；Windows 无 POSIX 权限，跳过。"""
        p = tmp_path / "cfg.json"
        atomic_write_json(p, {"k": "v"})
        if os.name == "nt":
            pytest.skip("Windows 无 POSIX 权限位")
        os.chmod(p, 0o600)
        atomic_write_json(p, {"k": "v2"})
        assert (p.stat().st_mode & 0o777) == 0o600

    def test_config_manager_save_settings_uses_atomic_write(self, tmp_path):
        """ConfigManager._save_settings 迁移到 atomic_write_json 后行为不变。"""
        from core.config_manager import ConfigManager

        cm = object.__new__(ConfigManager)
        cm.settings_path = tmp_path / "settings.json"
        cm._save_settings({"theme": "dark"})
        assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == {"theme": "dark"}
        assert not (tmp_path / "settings.json.tmp").exists()


# ══════════════════════════════════════════════════════════════════
# T7 LLM：_is_local_url / 空 key 空 model 早期分支 / 损坏缓存容错
# ══════════════════════════════════════════════════════════════════


class TestLocalUrlDetection:
    """T7 回归：本地无鉴权端点判定。"""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434",
            "http://localhost:11434/v1",
            "http://127.0.0.1:8080",
            "https://127.0.0.1:8443/v1",
            "http://[::1]:8080/v1",
            "http://0.0.0.0:8080",
        ],
    )
    def test_local_hosts(self, url):
        assert llm_client._is_local_url(url) is True, url

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "https://api.deepseek.com/v1",
            "http://192.168.1.5:8080",
            "https://ark.cn-beijing.volces.com/api/v3",
            "http://localhost.evil.com:8080",  # 域名含 localhost 但并非本地——不得放行
        ],
    )
    def test_cloud_urls(self, url):
        assert llm_client._is_local_url(url) is False, url


class TestAskLlmEmptyConfig:
    """T7 回归：空 key / 空 model 的早期分支（不构造客户端、不发网络请求）。"""

    @staticmethod
    def _fake_client_factory(captured: dict, resp_content: str):
        """构造 OpenAI 客户端替身：记录构造参数，create() 返回固定内容。

        注意：类体内赋值 `content = resp_content` 时，函数参数名必须与
        类属性名不同（类体同名遮蔽会导致 NameError）。
        """

        class _FakeMsg:
            content = resp_content

        class _FakeChoice:
            message = _FakeMsg()

        class _FakeResp:
            choices = [_FakeChoice()]

        class _FakeCompletions:
            @staticmethod
            def create(**params):
                captured["params"] = params
                return _FakeResp()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            def __init__(self, api_key, base_url):
                captured["api_key"] = api_key
                captured["base_url"] = base_url
                self.chat = _FakeChat()

        return _FakeClient

    def test_cloud_empty_key_returns_none_without_client(self, monkeypatch):
        monkeypatch.setattr(llm_client, "OpenAI", lambda **kw: pytest.fail("云端空 key 不应构造客户端"))
        result = llm_client.ask_llm(
            "hi", api_key="", base_url="https://api.example.com/v1", model="m", no_cache=True, use_rate_limiter=False
        )
        assert result is None

    def test_cloud_empty_model_returns_none_without_client(self, monkeypatch):
        monkeypatch.setattr(llm_client, "OpenAI", lambda **kw: pytest.fail("云端空 model 不应构造客户端"))
        result = llm_client.ask_llm(
            "hi", api_key="key", base_url="https://api.example.com/v1", model="", no_cache=True, use_rate_limiter=False
        )
        assert result is None

    def test_local_empty_key_passes_not_needed(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(llm_client, "OpenAI", self._fake_client_factory(captured, "你好世界"))
        result = llm_client.ask_llm(
            "hi", api_key="", base_url="http://127.0.0.1:8080", model="m", no_cache=True, use_rate_limiter=False
        )
        assert result == "你好世界"
        assert captured["api_key"] == "not-needed"

    def test_local_empty_model_uses_default(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(llm_client, "OpenAI", self._fake_client_factory(captured, "ok"))
        result = llm_client.ask_llm(
            "hi", api_key="k", base_url="http://localhost:11434", model="", no_cache=True, use_rate_limiter=False
        )
        assert result == "ok"
        assert captured["params"]["model"] == "default"


class TestCacheCorruptionRecovery:
    """T7 回归：损坏缓存文件（顶层非 list / 非法 JSON）不抛异常、能自愈。"""

    @pytest.fixture()
    def llm_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "llm_log"
        d.mkdir()
        monkeypatch.setattr(llm_client, "LLM_LOG_DIR", d)
        return d

    def test_load_top_level_dict_returns_none(self, llm_dir):
        (llm_dir / "t.json").write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        assert llm_client._load_cache_from_file("k1", "t") is None

    def test_load_invalid_json_returns_none(self, llm_dir):
        (llm_dir / "t.json").write_text("not-json-at-all", encoding="utf-8")
        assert llm_client._load_cache_from_file("k1", "t") is None

    def test_load_mixed_list_skips_non_dict_entries(self, llm_dir):
        good = {"cache_key": "k2", "response": "好", "cached_at": time.time()}
        (llm_dir / "t.json").write_text(json.dumps([42, "x", None, good]), encoding="utf-8")
        assert llm_client._load_cache_from_file("k2", "t") == "好"
        assert llm_client._load_cache_from_file("k1", "t") is None

    def test_save_on_corrupt_top_level_dict_appends_cleanly(self, llm_dir):
        (llm_dir / "t.json").write_text(json.dumps({"corrupt": True}), encoding="utf-8")
        llm_client._save_cache("ck", "响应A", "t")
        entries = json.loads((llm_dir / "t.json").read_text(encoding="utf-8"))
        assert isinstance(entries, list)
        assert entries[-1]["cache_key"] == "ck"
        assert entries[-1]["response"] == "响应A"
        # 自愈后可再次命中
        assert llm_client._load_cache_from_file("ck", "t") == "响应A"

    def test_save_on_invalid_json_resets(self, llm_dir):
        (llm_dir / "t.json").write_text("!!!", encoding="utf-8")
        llm_client._save_cache("ck", "B", "t")
        entries = json.loads((llm_dir / "t.json").read_text(encoding="utf-8"))
        assert isinstance(entries, list)
        assert entries[-1]["response"] == "B"

    def test_save_appends_to_healthy_cache(self, llm_dir):
        llm_client._save_cache("ck1", "A", "t")
        llm_client._save_cache("ck2", "B", "t")
        entries = json.loads((llm_dir / "t.json").read_text(encoding="utf-8"))
        assert len(entries) == 2
        assert [e["response"] for e in entries] == ["A", "B"]


# ══════════════════════════════════════════════════════════════════
# GUI fixtures（T6 / T2 read_json_response）
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def app():
    _app = QApplication.instance() or QApplication([])
    yield _app


@pytest.fixture()
def table(app):
    from ui.result_table import ResultTableWidget

    return ResultTableWidget()


def _add(table, i: int, engine: str = "paddleocr", conf: float = 0.95, text: str | None = None):
    return table.add_result(
        f"{i // 60:02d}:{i % 60:02d}",
        "region1",
        engine,
        text or f"测试文本行 {i}",
        conf,
        time_sec=float(i),
    )


def _check(table, row: int, checked: bool = True):
    model = table._table.model()
    model.setData(model.index(row, 0), Qt.Checked if checked else Qt.Unchecked, Qt.ItemDataRole.CheckStateRole)


# ══════════════════════════════════════════════════════════════════
# T6 removeRows：remove_rows_by_predicate + _ResultsModel.removeRows
# ══════════════════════════════════════════════════════════════════


class TestRemoveRowsByPredicate:
    """T6 回归：统一 reset 删除，行号/模型/选中状态三者一致。"""

    def test_remove_all(self, table):
        for i in range(5):
            _add(table, i)
        deleted = table.remove_rows_by_predicate(lambda r: True)
        assert deleted == 5
        assert table._table.model().rowCount() == 0
        assert table._results == []

    def test_remove_partial_by_confidence(self, table):
        for i in range(5):
            _add(table, i, conf=0.9 if i % 2 == 0 else 0.3)
        deleted = table.remove_rows_by_predicate(lambda r: (r.get("confidence", 1.0) or 0.0) < 0.5)
        assert deleted == 2
        assert table._table.model().rowCount() == 3
        assert len(table._results) == 3
        assert all((r.get("confidence", 1.0) or 0.0) >= 0.5 for r in table._results)
        # 删除后数据顺序保持
        assert [r["raw"] for r in table._results] == ["测试文本行 0", "测试文本行 2", "测试文本行 4"]

    def test_remove_none_keeps_rows(self, table):
        for i in range(3):
            _add(table, i)
        assert table.remove_rows_by_predicate(lambda r: False) == 0
        assert table._table.model().rowCount() == 3

    def test_checked_rows_cleared_after_remove(self, table):
        """reset 删除后不允许残留选中（此前直接 del _results 导致 checked 行号错位）。"""
        for i in range(5):
            _add(table, i, conf=0.9 if i % 2 == 0 else 0.3)
        _check(table, 0)  # 将被删除
        _check(table, 1)  # 将被保留
        table.remove_rows_by_predicate(lambda r: (r.get("confidence", 1.0) or 0.0) < 0.5)
        assert table.get_selected_rows() == set()
        assert table._table.model()._checked_rows == set()


class TestModelRemoveRows:
    """T6 回归：_ResultsModel.removeRows 防御实现（begin/endRemoveRows + 行索引修正）。"""

    def test_remove_single_row(self, table):
        for i in range(3):
            _add(table, i)
        model = table._table.model()
        assert model.removeRows(1, 1)
        assert model.rowCount() == 2
        assert [r["raw"] for r in table._results] == ["测试文本行 0", "测试文本行 2"]

    def test_remove_middle_range(self, table):
        for i in range(4):
            _add(table, i)
        model = table._table.model()
        assert model.removeRows(1, 2)
        assert model.rowCount() == 2
        assert [r["raw"] for r in table._results] == ["测试文本行 0", "测试文本行 3"]

    def test_invalid_range_returns_false(self, table):
        for i in range(2):
            _add(table, i)
        model = table._table.model()
        assert model.removeRows(0, 5) is False
        assert model.removeRows(-1, 1) is False
        assert model.removeRows(1, 2) is False
        assert model.rowCount() == 2

    def test_checked_rows_index_shifted(self, table):
        """删除后选中行号前移，不指向错误行。"""
        for i in range(4):
            _add(table, i)
        model = table._table.model()
        _check(table, 0)
        _check(table, 2)
        assert model.removeRows(1, 1)  # 删除行1 → 原行2 变行1
        assert model._checked_rows == {0, 1}

    def test_remove_current_row_cleared(self, table):
        for i in range(3):
            _add(table, i)
        model = table._table.model()
        model._current_row = 2
        assert model.removeRows(1, 1)
        assert model._current_row == 1  # 前移
        model._current_row = 0
        assert model.removeRows(0, 1)
        assert model._current_row == -1  # 被删除


# ══════════════════════════════════════════════════════════════════
# T1 引擎释放契约：close() 幂等 + manager 统一走 close
# ══════════════════════════════════════════════════════════════════


class TestCloseIdempotent:
    """T1 回归：close() 可重复调用，不抛异常、不产生副作用。"""

    def test_paddle_engine_close_twice(self):
        eng = PaddleOCREngine({"config": {}})
        eng.close()
        eng.close()  # 幂等
        assert eng._paddle_available is True  # 允许按新配置重新初始化
        assert eng._ocr is None

    def test_paddle_engine_subprocess_mode_close_without_start(self):
        """use_subprocess=True 但从未启动：close 必须安全（_subproc 为 None）。"""
        eng = PaddleOCREngine({"config": {"use_subprocess": True}})
        eng.close()
        eng.close()

    def test_whisperx_engine_close_twice(self):
        eng = WhisperXEngine({})
        eng.close()
        eng.close()

    def test_ocr_manager_release_empty_dict(self):
        mgr = OCREngineManager.__new__(OCREngineManager)
        mgr._engines = {}
        mgr.release_all_engines()
        mgr.release_engine("paddleocr")  # 不存在 → 静默

    def test_ocr_manager_release_calls_close(self):
        mgr = OCREngineManager.__new__(OCREngineManager)
        mgr._engines = {"paddleocr": PaddleOCREngine({"config": {}})}
        mgr.release_all_engines()
        assert not mgr.has_engine

    def test_asr_manager_release_empty_and_with_engine(self):
        mgr = ASREngineManager.__new__(ASREngineManager)
        mgr._engines = {}
        mgr._config = {"engine": "whisperx"}
        mgr._default_name = "whisperx"
        mgr.release_all_engines()
        mgr._engines = {"whisperx": WhisperXEngine({})}
        mgr.release_engine("whisperx")
        assert not mgr.has_engine


class TestReloadConfigReleasesEngines:
    """T1 回归：reload_config 先释放旧引擎（子进程/共享内存），再加载新配置。"""

    def test_ocr_reload_clears_engines(self):
        mgr = OCREngineManager.__new__(OCREngineManager)
        mgr._engines = {}
        mgr._config = {
            "engines": {"paddleocr": {"type": "local", "enabled": True, "config": {}}},
            "default_engine": "paddleocr",
        }
        mgr._default_name = "paddleocr"
        mgr._current_name = "paddleocr"
        mgr._hw_accel_enabled = False
        assert mgr.get_engine("paddleocr", warm_up=False) is not None
        assert mgr.has_engine
        mgr.reload_config()
        assert not mgr.has_engine

    def test_asr_reload_clears_engines(self):
        mgr = ASREngineManager.__new__(ASREngineManager)
        mgr._engines = {"whisperx": WhisperXEngine({})}
        mgr._config = {"engine": "whisperx"}
        mgr._default_name = "whisperx"
        mgr.reload_config()
        assert not mgr.has_engine


# ══════════════════════════════════════════════════════════════════
# T2 并发 OCR：_infer_lock / 不可用时快速返回 / 共享内存加锁
# ══════════════════════════════════════════════════════════════════


class TestInferLock:
    """T2 回归：PaddleOCREngine 识别互斥锁与不可用快速返回。"""

    def test_infer_lock_is_lock(self):
        eng = PaddleOCREngine({"config": {}})
        # threading.Lock 是工厂函数而非类型，用 type(threading.Lock()) 取类型
        assert isinstance(eng._infer_lock, type(threading.Lock()))
        assert eng._infer_lock.locked() is False
        assert isinstance(eng._req_id, int)

    def test_recognize_fast_return_when_paddle_unavailable(self):
        """_paddle_available=False 时 recognize 不触发加载、快速返回空串。"""
        eng = PaddleOCREngine({"config": {}})
        eng._paddle_available = False
        img = np.zeros((8, 8, 3), dtype=np.uint8)
        assert eng.recognize(img) == ""


class TestSharedMemoryManager:
    """T2 回归：加锁写/读往返、close 幂等、缺块清晰报错。"""

    @staticmethod
    def _name(tag: str) -> str:
        return f"orcp_test_{tag}_{os.getpid()}"

    def test_write_read_roundtrip(self):
        mgr = SharedMemoryManager(self._name("rt"), capacity=1024 * 1024)
        try:
            img = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)
            n = mgr.write_array(img)
            assert n == 12
            out = SharedMemoryManager.read_array_from(mgr.name, 2, 2, 3)
            assert np.array_equal(out, img)
        finally:
            mgr.close()

    def test_close_idempotent_and_recreate_same_name(self):
        name = self._name("recreate")
        mgr = SharedMemoryManager(name, capacity=1024 * 1024)
        mgr.close()
        mgr.close()  # 幂等
        # unlink 后同名块可重新创建（无残留）
        mgr2 = SharedMemoryManager(name, capacity=1024 * 1024)
        mgr2.close()

    def test_read_missing_block_raises_runtime_error(self):
        with pytest.raises(RuntimeError):
            SharedMemoryManager.read_array_from("orcp_test_missing_block_xyz", 1, 1, 3)

    def test_expand_capacity_locked(self):
        """写入超过初始容量时自动扩展（加锁事务），往返数据一致。"""
        mgr = SharedMemoryManager(self._name("expand"), capacity=1024)  # 1KB 初始
        try:
            big = np.full((64, 64, 3), 7, dtype=np.uint8)  # 12KB
            mgr.write_array(big)
            out = SharedMemoryManager.read_array_from(mgr.name, 64, 64, 3)
            assert np.array_equal(out, big)
        finally:
            mgr.close()


# ══════════════════════════════════════════════════════════════════
# T2 read_json_response：按 request_id 匹配（无需真实子进程）
# ══════════════════════════════════════════════════════════════════


class TestReadJsonResponseRequestId:
    """T2 回归：响应队列按 id 匹配，丢弃不匹配响应防错配。

    不启动真实子进程——直接注入 _response_queue，命中分支在首个循环
    迭代即返回，无需事件循环驱动。
    """

    def test_matches_by_request_id(self, app):
        mgr = QtSubprocessManager()
        mgr._response_queue = [
            {"id": 1, "status": "result", "text": "A"},
            {"id": 2, "status": "result", "text": "B"},
        ]
        resp = mgr.read_json_response(timeout=0.2, request_id=2)
        assert resp == {"id": 2, "status": "result", "text": "B"}
        # 不匹配的响应保留在队列中
        assert mgr._response_queue == [{"id": 1, "status": "result", "text": "A"}]

    def test_unmatched_id_returns_none_keeps_queue(self, app):
        mgr = QtSubprocessManager()
        mgr._response_queue = [{"id": 7, "status": "error", "message": "x"}]
        assert mgr.read_json_response(timeout=0.2, request_id=9) is None
        assert mgr._response_queue == [{"id": 7, "status": "error", "message": "x"}]

    def test_no_request_id_pops_first(self, app):
        mgr = QtSubprocessManager()
        mgr._response_queue = [{"id": 1, "status": "result", "text": "A"}, {"id": 2, "status": "result", "text": "B"}]
        assert mgr.read_json_response(timeout=0.2) == {"id": 1, "status": "result", "text": "A"}
        assert len(mgr._response_queue) == 1
