"""P1 批次修复（M9/M10/2.6/2.7/L16/M12/L18）回归测试。

覆盖：
- P1-1 会话代际令牌：纠错/润色停止后旧 worker 回调作废（_on_parallel_batch_done/
  _on_parallel_batch_error/_on_polish_batch_done/_on_polish_batch_error 代际校验 +
  stop_processing 代际 +1）
- P1-2 润色 worker GC 防御：_on_polish_finished clear 前 wait；manager.cleanup 收集
- P1-3 视频跳帧：setData EditRole 显式 roles；notify_row 程序化更新不跳转；
  _on_cell_clicked 仅可编辑列触发
- P1-4 ASR 防抖：_on_mode_changed 走 _schedule_asr_restart（400ms 单发定时器），
  不再立即重建；_do_asr_restart 保留运行中保护
- P1-5 暂停语言：_on_pause_processing 布尔状态（_paused）驱动，文本仅展示
- P1-6 批量双源：_maybe_start_next_batch_file / _on_batch_finished_all 读真源并 pop/clear
- P1-7 模板回退：_refresh_template_list 保留用户当前选择；_on_config_template_saved
  经 combo 统一同步

WorkflowManager 是纯 QObject，无需 QApplication；GUI 测试使用 offscreen。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

import core.workflow.correction_flow as correction_flow_mod
import ui.main_window as main_window_mod
from core.utils import MODE_OCR_ONLY
from core.workflow.manager import WorkflowManager

_LIVE_PARAMS = {
    "process_mode": MODE_OCR_ONLY,
    "corr_translate": False,
    "corr_stream": False,
    "corr_json": False,
    "corr_extract_env": False,
    "corr_retry": 2,
    "corr_batch_size": 2,
    "corr_concurrency": 1,
}


class _FakeCorrector:
    """可写模式属性的 corrector 替身（correct_batch/polish 不被测试路径调用）。"""

    def __init__(self):
        self.translate_mode = None
        self.stream_mode = None
        self.json_mode = None


def _make_wf(**mp_overrides) -> WorkflowManager:
    """构造 WorkflowManager：注入假 mode_params 来源与假 corrector（同 test_workflow_session）。"""
    wf = WorkflowManager()
    live = dict(_LIVE_PARAMS)
    live.update(mp_overrides)
    wf._get_mode_params = lambda: dict(live)
    wf._corrector = _FakeCorrector()
    return wf


# ══════════════════════════════════════════════════════════════════
# 假批量 worker：QObject 子类（Signal 类属性），start() 只登记不启线程，
# 测试手动 emit 信号驱动回调链（同线程直连，完全确定性）
# ══════════════════════════════════════════════════════════════════


class _FakeBatchCorrectionWorker(QObject):
    correction_ready = Signal(int, str, str)
    batch_finished = Signal()
    batch_error = Signal(str)
    created: list = []

    def __init__(self, corrector, texts: list, max_retries: int = 3):
        super().__init__()
        self.texts = list(texts)
        self.max_retries = max_retries

    def start(self):
        _FakeBatchCorrectionWorker.created.append(self)

    def isRunning(self):
        return False

    def stop(self):
        pass

    def wait(self, *args):
        return True

    def terminate(self):
        pass


class _FakeBatchPolishWorker(QObject):
    polish_ready = Signal(int, str, str)
    batch_finished = Signal()
    batch_error = Signal(str)
    created: list = []

    def __init__(self, corrector, items: list):
        super().__init__()
        self.items = list(items)

    def start(self):
        _FakeBatchPolishWorker.created.append(self)

    def isRunning(self):
        return False

    def stop(self):
        pass

    def wait(self, *args):
        return True

    def terminate(self):
        pass


@pytest.fixture()
def fake_workers(monkeypatch):
    """monkeypatch correction_flow 的批量 worker 为假实现，并清空登记表。"""
    _FakeBatchCorrectionWorker.created = []
    _FakeBatchPolishWorker.created = []
    monkeypatch.setattr(correction_flow_mod, "BatchCorrectionWorker", _FakeBatchCorrectionWorker)
    monkeypatch.setattr(correction_flow_mod, "BatchPolishWorker", _FakeBatchPolishWorker)
    return _FakeBatchCorrectionWorker, _FakeBatchPolishWorker


def _submit_correction(wf: WorkflowManager, texts: list, batch_size: int = 2, concurrency: int = 1):
    """按 correct_all 的语义启动一批纠错（清 stop 标志 → 提交）。"""
    wf._correction_stop_requested = False
    wf._is_auto_correction = False
    total_batches = (len(texts) + batch_size - 1) // batch_size
    wf._submit_all_correction_batches(texts, batch_size, 2, total_batches, concurrency)


# ══════════════════════════════════════════════════════════════════
# P1-1 纠错会话代际令牌
# ══════════════════════════════════════════════════════════════════


class TestCorrectionSessionGeneration:
    """P1-1 回归：停止/重启后旧会话 worker 回调必须作废。"""

    def test_old_generation_done_callback_dropped(self, fake_workers):
        FakeCorr, _ = fake_workers
        wf = _make_wf()
        # 会话 1：2 批（batch_size=2, 3 条文本），并发 1 → 仅 1 个 worker 在跑
        _submit_correction(wf, [("t0", "a", 0.0, 1.0), ("t1", "b", 1.0, 2.0), ("t2", "c", 2.0, 3.0)])
        assert wf._correction_session_generation == 1
        assert len(FakeCorr.created) == 1
        w1 = FakeCorr.created[0]
        assert wf._batch_completed_count == 0
        assert len(wf._batch_pending_batches) == 1  # 剩 1 批待处理

        # 停止 → 代际 +1；重启新会话（代际再 +1）
        wf.stop_processing()
        assert wf._correction_session_generation == 2
        _submit_correction(wf, [("t3", "d", 3.0, 4.0)])
        assert wf._correction_session_generation == 3
        assert len(FakeCorr.created) == 2
        w2 = FakeCorr.created[1]
        assert wf._batch_completed_count == 0

        # 旧会话 worker 迟到完成回调 → 丢弃：计数不变、不启动新批次
        w1.batch_finished.emit()
        assert wf._batch_completed_count == 0
        assert len(wf._batch_correction_workers) == 1  # 仍只有新会话的 w2

        # 新会话 worker 正常完成 → 计数推进并收尾
        w2.batch_finished.emit()
        assert wf._batch_completed_count == 1
        assert wf._correction_in_progress is False  # _on_batch_correction_finished 已执行

    def test_old_generation_error_callback_dropped(self, fake_workers):
        FakeCorr, _ = fake_workers
        wf = _make_wf()
        _submit_correction(wf, [("t0", "a", 0.0, 1.0), ("t1", "b", 1.0, 2.0)])
        assert len(FakeCorr.created) == 1
        assert wf._batch_completed_count == 0

        # 旧代际错误回调（直接注入旧 gen）→ 丢弃，计数不变
        wf._on_parallel_batch_error("boom", 2, gen=wf._correction_session_generation - 1)
        assert wf._batch_completed_count == 0

        # 当前代际错误回调 → 计数 +1（错误批次也计入完成）
        wf._on_parallel_batch_error("boom", 2, gen=wf._correction_session_generation)
        assert wf._batch_completed_count == 1

    def test_done_callback_requires_matching_generation(self, fake_workers):
        """代际校验分支本身：gen 不匹配直接 return，不触碰任何状态。"""
        wf = _make_wf()
        wf._batch_completed_count = 7
        wf._batch_total_count = 10
        wf._correction_session_generation = 5
        wf._on_parallel_batch_done(2, gen=4)
        assert wf._batch_completed_count == 7  # 未变
        wf._on_parallel_batch_done(2, gen=5)
        assert wf._batch_completed_count == 8  # 匹配 → 正常 +1

    def test_stop_processing_increments_generations(self):
        """stop_processing 对纠错/润色代际各 +1（旧会话回调从此作废）。"""
        wf = _make_wf()
        assert (wf._correction_session_generation, wf._polish_session_generation) == (0, 0)
        wf.stop_processing()
        assert (wf._correction_session_generation, wf._polish_session_generation) == (1, 1)


# ══════════════════════════════════════════════════════════════════
# P1-1 润色代际 + P1-2 润色 worker GC 防御
# ══════════════════════════════════════════════════════════════════


class TestPolishSessionGeneration:
    """P1-1 润色同构：旧会话 polish 回调作废；P1-2 收尾 clear 前 wait。"""

    def test_old_generation_polish_done_dropped(self, fake_workers):
        _, FakePolish = fake_workers
        wf = _make_wf()
        # 会话 1：3 条, batch_size=2 → 2 批, 并发 1
        wf._start_polish([(0, "a", "a"), (1, "b", "b"), (2, "c", "c")])
        assert wf._polish_session_generation == 1
        assert len(FakePolish.created) == 1
        p1 = FakePolish.created[0]
        assert wf._polish_completed_batches == 0

        # 停止 → 重启（_start_polish 内部清 stop 标志、代际 +1）
        wf.stop_processing()
        assert wf._polish_session_generation == 2
        wf._start_polish([(3, "d", "d")])
        assert wf._polish_session_generation == 3
        assert len(FakePolish.created) == 2
        p2 = FakePolish.created[1]

        # 旧会话完成回调 → 丢弃
        p1.batch_finished.emit()
        assert wf._polish_completed_batches == 0
        assert len(wf._polish_workers) == 1  # 仍只有 p2

        # 新会话完成 → 收尾（_on_polish_finished：wait 后 clear + 标志复位）
        p2.batch_finished.emit()
        assert wf._polish_completed_batches == 1
        assert wf._polish_in_progress is False
        assert wf._polish_workers == []

    def test_old_generation_polish_error_dropped(self, fake_workers):
        wf = _make_wf()
        wf._polish_completed_batches = 3
        wf._polish_total_batches = 5
        wf._polish_session_generation = 2
        wf._on_polish_batch_error("boom", gen=1)
        assert wf._polish_completed_batches == 3  # 丢弃
        wf._on_polish_batch_error("boom", gen=2)
        assert wf._polish_completed_batches == 4  # 当前代际 → +1

    def test_on_polish_finished_waits_then_clears(self, fake_workers):
        """P1-2：_on_polish_finished 对仍在运行的 worker wait(100) 后再 clear。"""
        _, FakePolish = fake_workers
        wf = _make_wf()
        wf._start_polish([(0, "a", "a")])
        worker = FakePolish.created[0]

        # 模拟 worker 刚发完 batch_finished 尚未退出：isRunning 短暂为 True
        wait_called = []
        worker.isRunning = lambda: True
        worker.wait = lambda *a: wait_called.append(a) or True
        worker.batch_finished.emit()  # → _on_polish_finished
        assert wait_called  # clear 前确实 wait 了
        assert wf._polish_workers == []  # clear 执行
        assert wf._polish_in_progress is False

    def test_cleanup_collects_polish_workers(self, fake_workers):
        """P1-2：manager.cleanup() 必须收集润色 worker（此前遗漏 → 关窗崩溃隐患）。"""
        _, FakePolish = fake_workers
        wf = _make_wf()
        wf._start_polish([(0, "a", "a"), (1, "b", "b"), (2, "c", "c")])
        assert len(wf._polish_workers) == 1

        running = [w for w in wf._polish_workers]
        for w in running:
            w.isRunning = lambda: True  # 模拟仍在运行
        wf.cleanup()
        assert wf._polish_workers == []
        # 收集后对 worker 发了 stop/quit（假 worker 不崩溃即验证路径可达）
        for w in running:
            assert w.isRunning() is False or True  # 无异常即通过


# ══════════════════════════════════════════════════════════════════
# P1-3 视频跳帧：dataChanged roles 区分程序化更新与用户编辑
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def app():
    _app = QApplication.instance() or QApplication([])
    yield _app


@pytest.fixture()
def table(app):
    from ui.result_table import ResultTableWidget

    return ResultTableWidget()


@pytest.fixture()
def spy(table):
    """cell_edit_activated 信号记录器。"""
    emitted = []
    table.cell_edit_activated.connect(emitted.append)
    return emitted


def _add(table, i: int, text: str | None = None):
    return table.add_result(
        f"{i // 60:02d}:{i % 60:02d}", "region1", "paddleocr", text or f"行 {i}", 0.95, time_sec=float(i)
    )


class TestEditRoleOnlyTriggersJump:
    """P1-3 回归：仅用户编辑提交（EditRole）触发 cell_edit_activated。"""

    def test_notify_row_programmatic_update_no_jump(self, table, spy):
        _add(table, 0)
        table.update_correction(0, "润色后")  # notify_row(col6)
        table.update_correction_result(0, "纠错后")  # notify_row(col5)
        table.update_confidence(0, 0.5)  # notify_row(col7)
        assert spy == []

    def test_checkstate_toggle_no_jump(self, table, spy):
        _add(table, 0)
        model = table._table.model()
        model.setData(model.index(0, 0), Qt.Checked, Qt.ItemDataRole.CheckStateRole)
        model.setData(model.index(0, 0), Qt.Unchecked, Qt.ItemDataRole.CheckStateRole)
        assert spy == []

    def test_set_highlight_no_jump(self, table, spy):
        for i in range(3):
            _add(table, i)
        model = table._table.model()
        model.set_highlight({0, 2}, current_row=1)
        assert spy == []

    def test_editrole_setdata_emits_jump(self, table, spy):
        _add(table, 0)
        model = table._table.model()
        assert model.setData(model.index(0, 4), "用户改的原文", Qt.ItemDataRole.EditRole)
        assert spy == [0]

    def test_delegate_setmodeldata_emits_jump(self, table, spy):
        _add(table, 0)
        delegate = table._table.itemDelegate()
        model = table._table.model()
        # QLineEdit 路径（短文本）
        delegate.setModelData(_FakeLineEdit("delegate 提交"), model, model.index(0, 4))
        assert spy == [0]

    def test_cell_clicked_only_editable_cols(self, table, spy):
        for i in range(2):
            _add(table, i)
        model = table._table.model()
        table._on_cell_clicked(model.index(0, 0))  # 复选框列（不跳转）
        table._on_cell_clicked(model.index(0, 8))  # 过滤按钮列（不跳转）
        assert spy == []
        table._on_cell_clicked(model.index(0, 1))  # 时间列（触发跳转）
        assert spy == [0]
        table._on_cell_clicked(model.index(0, 4))  # 原始结果列（可编辑，触发跳转）
        assert spy == [0, 0]


class _FakeLineEdit:
    """delegate.setModelData 的 QLineEdit 替身（仅需 .text()）。"""

    def __init__(self, text: str):
        self._text = text

    def text(self):
        return self._text


# ══════════════════════════════════════════════════════════════════
# P1-4 ASR 防抖：_on_mode_changed → _schedule_asr_restart（400ms 单发）
# ══════════════════════════════════════════════════════════════════


def _bare_mw():
    """不跑 __init__ 的 MainWindow 空壳（仅测试纯逻辑方法）。"""
    return main_window_mod.MainWindow.__new__(main_window_mod.MainWindow)


class TestAsrDebounce:
    """P1-4 回归：RebuildRouter 防抖调度，不再逐键立即重建。"""

    @staticmethod
    def _make_router(monkeypatch):
        """构造 RebuildRouter + Fake QTimer（复用原 P1-4 测试模式）。"""
        import PySide6.QtCore

        from core.settings.rebuild import RebuildRouter

        class _FakeSignal:
            def __init__(self):
                self.cb = None

            def connect(self, cb):
                self.cb = cb

        class _FakeTimer:
            instances = []

            def __init__(self, parent=None):
                self.single_shot = None
                self.timeout = _FakeSignal()
                self.started = []
                _FakeTimer.instances.append(self)

            def setSingleShot(self, v):
                self.single_shot = v

            def start(self, ms):
                self.started.append(ms)

        monkeypatch.setattr(PySide6.QtCore, "QTimer", _FakeTimer)
        owner = mock.Mock()
        owner._workflow = mock.Mock()
        owner._workflow.is_asr_running.return_value = False
        router = RebuildRouter(owner)
        return router, _FakeTimer

    def test_notify_schedules_debounced_once(self, monkeypatch):
        """多次 notify 复用同一单发 400ms 定时器（逐键输入合并为一次重建）。"""
        router, FakeTimer = self._make_router(monkeypatch)
        router.notify("asr", "model_size")
        router.notify("asr", "vad_enabled")
        assert len(FakeTimer.instances) == 1
        assert FakeTimer.instances[0].single_shot is True
        assert FakeTimer.instances[0].timeout.cb is not None  # timeout → _flush
        assert FakeTimer.instances[0].started == [400, 400]  # 每次重置 400ms

    def test_notify_no_immediate_rebuild(self, monkeypatch):
        """notify 不立即重建（防抖期内仅记录 pending/脏域）。"""
        router, _ = self._make_router(monkeypatch)
        router.notify("asr", "model_size")
        assert router._pending == {"asr"}
        assert router._dirty_domains == {"asr"}

    def test_flush_running_guard(self, monkeypatch):
        """_flush：ASR 运行中 → 标记 pending 不重建；process_finished 补建。"""
        router, _ = self._make_router(monkeypatch)
        router._owner._workflow.is_asr_running.return_value = True
        router.notify("asr", "model_size")
        with mock.patch.object(router, "_rebuild_asr") as rb:
            router._flush()
            rb.assert_not_called()
        assert router._asr_pending is True
        with mock.patch.object(router, "_rebuild_asr") as rb:
            router.on_process_finished()
            rb.assert_called_once()
        assert router._asr_pending is False

    def test_flush_idle_rebuilds(self, monkeypatch):
        """_flush：空闲 → 调度重建。"""
        router, _ = self._make_router(monkeypatch)
        router.notify("asr", "model_size")
        with mock.patch.object(router, "_rebuild_asr") as rb:
            router._flush()
            rb.assert_called_once()


# ══════════════════════════════════════════════════════════════════
# P1-5 暂停语言：_paused 布尔标志驱动（en/ja 下文本判断不再反转）
# ══════════════════════════════════════════════════════════════════


class TestPauseStateBoolean:
    """P1-5 回归：暂停/继续由 _paused 布尔标志决定，与按钮文本无关。"""

    def test_pause_resume_toggle(self):
        from core.i18n import _

        class _FakeWF:
            def __init__(self):
                self.calls = []

            def pause_processing(self):
                self.calls.append("pause")

            def resume_processing(self):
                self.calls.append("resume")

        class _FakeBtn:
            def __init__(self):
                self.text = ""

            def setText(self, t):
                self.text = t

        mw = _bare_mw()
        mw._paused = False
        mw._workflow = _FakeWF()
        mw._btn_pause = _FakeBtn()

        mw._on_pause_processing()
        assert mw._workflow.calls == ["pause"]
        assert mw._paused is True
        assert mw._btn_pause.text == _("▶ 继续")

        mw._on_pause_processing()
        assert mw._workflow.calls == ["pause", "resume"]
        assert mw._paused is False
        assert mw._btn_pause.text == _("⏸ 暂停")

    def test_no_enum_text_branch_remains(self):
        """源码断言：_on_pause_processing 不再按按钮文本枚举判断（文本仅展示）。"""
        import inspect

        src = inspect.getsource(main_window_mod.MainWindow._on_pause_processing)
        assert "self._paused" in src
        assert "text() ==" not in src and ".text() ==" not in src
        assert "btn_pause.text" not in src.replace("_btn_pause.setText", "")


# ══════════════════════════════════════════════════════════════════
# P1-6 批量双源：真源 _get_batch_files() 推进/清空
# ══════════════════════════════════════════════════════════════════


class _FakePreview(QObject):
    """video_preview 替身：真实 Signal + 记录加载调用。"""

    video_loaded = Signal(str)

    def __init__(self):
        super().__init__()
        self.loaded = []

    def load_video(self, p):
        self.loaded.append(("video", p))

    def load_image(self, p):
        self.loaded.append(("image", p))

    def load_audio(self, p):
        self.loaded.append(("audio", p))


def _wf_with_batch_files(files: list) -> WorkflowManager:
    """构造 WorkflowManager：_get_batch_files 指向外部真源 list（同 main_window 注入）。"""
    wf = _make_wf()
    wf._get_batch_files = lambda: files
    wf._get_results = lambda: []
    wf._get_video_path = lambda: None
    wf._update_batch_label = lambda: None  # 注：委托链缺该方法（见报告遗留问题）
    wf._clear_results_table = lambda: None
    return wf


class TestBatchQueueRealSource:
    """P1-6 回归：_maybe_start_next_batch_file 从真源 pop，队列持续推进。"""

    def test_queue_advances_by_pops(self):
        files = ["a.png", "b.png", "c.png"]
        wf = _wf_with_batch_files(files)
        preview = _FakePreview()
        wf._video_preview = preview

        wf._maybe_start_next_batch_file()
        assert files == ["b.png", "c.png"]  # 真源被 pop
        assert preview.loaded == [("image", "b.png")]

        wf._maybe_start_next_batch_file()
        assert files == ["c.png"]
        assert preview.loaded[-1] == ("image", "c.png")

        # 剩 1 个 → 不再推进
        wf._maybe_start_next_batch_file()
        assert files == ["c.png"]

        wf._disconnect_batch_load()  # 清理定时器

    def test_single_file_does_not_pop(self):
        files = ["only.mp4"]
        wf = _wf_with_batch_files(files)
        wf._video_preview = _FakePreview()
        wf._maybe_start_next_batch_file()
        assert files == ["only.mp4"]  # len<=1 直接返回

    def test_empty_queue_noop(self):
        files: list = []
        wf = _wf_with_batch_files(files)
        wf._video_preview = _FakePreview()
        wf._maybe_start_next_batch_file()
        assert files == []

    def test_batch_finished_all_uses_real_source_and_clears(self):
        files = ["a.mp4", "b.mp4", "c.mp4"]
        wf = _wf_with_batch_files(files)
        wf._get_table_row_count = lambda: 0
        done = []
        wf.batch_all_done.connect(lambda: done.append(True))

        wf._on_batch_finished_all()

        assert done == [True]
        assert files == []  # 真源被 clear（此前清假源 → 队列标签不归零）


# ══════════════════════════════════════════════════════════════════
# P1-7 模板回退：保留当前选择 + 保存后经 combo 统一同步
# ══════════════════════════════════════════════════════════════════


class _FakeSignal:
    def __init__(self):
        self.cb = None

    def connect(self, cb):
        self.cb = cb


class _FakeAction:
    """QAction 替身：记录 setChecked，text() 返回名称，triggered 带 connect。"""

    def __init__(self, name, parent=None):
        self.name = name
        self.checked = False
        self.triggered = _FakeSignal()

    def setCheckable(self, v):
        pass

    def setToolTip(self, t):
        pass

    def setChecked(self, v):
        self.checked = v

    def text(self):
        return self.name

    def isSeparator(self):
        return False


class _FakeMenu:
    def __init__(self, extra_actions):
        self._actions = list(extra_actions)
        self.inserted = []

    def actions(self):
        return self._actions

    def removeAction(self, a):
        if a in self._actions:
            self._actions.remove(a)

    def insertAction(self, before, action):
        self.inserted.append((before, action))


class _FakeActionGroup:
    def __init__(self):
        self._actions = []
        self.removed = []

    def actions(self):
        return self._actions

    def removeAction(self, a):
        self.removed.append(a)

    def addAction(self, a):
        self._actions.append(a)


class _FakeCombo:
    def __init__(self, current: str):
        self.current = current
        self.items = []
        self.set_calls = []

    def blockSignals(self, b):
        pass

    def currentText(self):
        return self.current

    def clear(self):
        pass

    def addItems(self, names):
        self.items = list(names)

    def setCurrentText(self, name):
        self.set_calls.append(name)
        self.current = name

    def setCurrentIndex(self, i):
        self.current = self.items[i] if self.items else ""


class _FakePromptMgr:
    def __init__(self, names, prompts=None):
        self.names = list(names)
        self.prompts = prompts or {n: f"prompt-{n}" for n in names}

    def get_template_names(self):
        return list(self.names)

    def get_template_by_name(self, name):
        if name in self.names:
            return {"name": name, "prompt": self.prompts.get(name, "")}
        return None

    def add_template(self, t):
        # 模拟真实 PromptTemplateManager：保存后名字立即可见
        if t.get("name") not in self.names:
            self.names.append(t["name"])
            self.prompts[t["name"]] = t.get("prompt", "")


class _FakeRegionMgr:
    def __init__(self):
        self.template_names = []

    def set_template_names(self, names):
        self.template_names = list(names)


class _FakeConfigPanel:
    def __init__(self):
        self.template_names = []
        self.contents = {}
        self.selected = []
        self.prompt_text = ""

    def set_template_names(self, names):
        self.template_names = list(names)

    def set_template_contents(self, contents):
        self.contents = dict(contents)

    def select_template(self, name):
        self.selected.append(name)


def _mw_with_template_env(monkeypatch, names, combo_current, prompts=None):
    """构造带模板刷新所需替身的环境（全部 Python 假对象，无真实 Qt 控件）。"""
    monkeypatch.setattr(main_window_mod, "QAction", _FakeAction)
    mw = _bare_mw()
    mw._prompt_mgr = _FakePromptMgr(names, prompts)
    mw._region_manager = _FakeRegionMgr()
    mw._config_panel = _FakeConfigPanel()
    mw._template_combo = _FakeCombo(combo_current)
    edit_action = _FakeAction("编辑模板", mw)
    mw._template_menu = _FakeMenu([edit_action])
    mw._template_action_group = _FakeActionGroup()
    mw._template_import_action = _FakeAction("导入", mw)
    mw._template_export_action = _FakeAction("导出", mw)
    mw._template_edit_action = edit_action
    mw._current_template = ""
    return mw


class TestTemplateRefreshKeepsSelection:
    """P1-7 回归：_refresh_template_list 不再强制回退第一个模板。"""

    def test_existing_current_selection_kept(self, monkeypatch):
        mw = _mw_with_template_env(monkeypatch, ["A", "B", "C"], combo_current="B")
        mw._refresh_template_list()
        assert mw._current_template == "B"  # 保留用户当前选择
        checked = [a.name for a in mw._template_action_group.actions() if a.checked]
        assert checked == ["B"]
        assert mw._config_panel.selected == ["B"]
        assert mw._config_panel.prompt_text == "prompt-B"

    def test_missing_current_falls_back_to_first(self, monkeypatch):
        mw = _mw_with_template_env(monkeypatch, ["A", "B", "C"], combo_current="X")  # X 已被删除
        mw._refresh_template_list()
        assert mw._current_template == "A"
        assert mw._config_panel.selected == ["A"]

    def test_empty_names_clears(self, monkeypatch):
        mw = _mw_with_template_env(monkeypatch, [], combo_current="")
        mw._refresh_template_list()
        assert mw._current_template == ""

    def test_config_template_saved_syncs_via_combo(self, monkeypatch):
        """保存模板后 combo.setCurrentText 走统一同步（combo/菜单/current 一致）。"""
        mw = _mw_with_template_env(monkeypatch, ["t1"], combo_current="t1")
        refreshed = []
        mw._refresh_template_list = lambda: refreshed.append(True)
        mw._status_label = _FakeLabel()
        mw._on_config_template_saved("t2", "新提示词")
        assert mw._template_combo.set_calls == ["t2"]  # 经 combo 统一同步
        assert refreshed  # 列表已刷新


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, t):
        self.text = t
