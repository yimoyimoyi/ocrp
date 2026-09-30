"""MainWindow 集成冒烟测试 —— 三种语言下完整构建 / 切主题 / 关闭。

不进入事件循环（进程内即可完成），但会真实执行 ``setup()``：域注册中心、
引擎管理器、WorkflowManager、全部视图构建器、``_retranslate_ui`` 的 hasattr
探针、以及 ``closeEvent`` 的同步清理路径。这是"拆分/重构后整体仍能站起来"的
最低成本保障 —— 单元测试各自通过但组装后崩溃的情况只有这里能抓到。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

LANGS = ("zh_CN", "en_US", "ja_JP")


@pytest.fixture(scope="module")
def app():
    _app = QApplication.instance() or QApplication([])
    yield _app


@pytest.fixture()
def restore_language():
    import core.i18n as i18n_mod

    saved = i18n_mod._translation
    saved_lang = i18n_mod.LanguageManager._current_lang
    yield
    i18n_mod._translation = saved
    i18n_mod.LanguageManager._current_lang = saved_lang


def _build_window():
    from ui.main_window import MainWindow

    win = MainWindow()
    win.setup()
    return win


@pytest.mark.parametrize("lang", LANGS)
def test_window_builds_and_closes_in_every_locale(app, restore_language, lang):
    from core.i18n import setup_i18n

    setup_i18n(lang)
    win = _build_window()
    app.processEvents()

    # 关键控件都建起来了（视图构建器委托链没有断）
    for attr in (
        "_status_label",
        "_progress_bar",
        "_btn_start",
        "_btn_stop",
        "_btn_pause",
        "_btn_correction",
        "_btn_correction_all",
        "_btn_polish",
        "_btn_polish_all",
        "_result_table",
        "_video_preview",
        "_config_panel",
    ):
        assert getattr(win, attr, None) is not None, f"{lang}: 缺少控件 {attr}"

    win.close()
    app.processEvents()


@pytest.mark.parametrize("theme", ["light_blue"])
def test_theme_switch_recolours_status_label(app, restore_language, theme):
    """真实主题切换后状态标签必须按新主题取色。

    这条覆盖的是"调控件调色板"路径在真实 QSS 主题下失效的问题：qt-material 只注入
    QSS，控件物化后的 QPalette 不随主题刷新，因此必须依赖已登记的主题名
    （见 ui/theme_tokens 的说明与 TestThemeRegistryTakesPrecedence）。
    只跑一个主题：apply_theme 会重刷整棵控件树，是这里最贵的一步。
    """
    from ui.theme_tokens import STATUS_COLORS_LIGHT, current_theme

    win = _build_window()
    win._apply_theme(theme)
    assert current_theme() == theme, "apply_theme 必须登记主题名"
    win._status_label.setText("▸ 处理中")
    app.processEvents()
    assert STATUS_COLORS_LIGHT["▸"] in win._status_label.text(), win._status_label.text()

    win.close()
    app.processEvents()


def test_close_event_runs_cleanup_without_thread(app, restore_language, monkeypatch):
    """closeEvent 必须同步完成 worker 清理（不再派生裸线程）。"""
    import threading

    win = _build_window()
    called = []
    monkeypatch.setattr(win._workflow, "cleanup", lambda *a, **kw: called.append(True))

    started: list = []
    real_thread = threading.Thread

    def _record(*args, **kwargs):
        started.append(kwargs.get("target"))
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(threading, "Thread", _record)

    win.close()
    app.processEvents()
    assert called, "closeEvent 必须调用 workflow.cleanup()"

    # 只关心 closeEvent 自己派生的清理线程 —— 引擎 warm_up 等其它子系统合法地
    # 使用后台线程，不能一概而论。
    from_close = [
        t for t in started if t is not None and getattr(t, "__qualname__", "").startswith("MainWindow.closeEvent")
    ]
    assert from_close == [], f"closeEvent 不得再派生裸线程执行清理: {from_close}"


class _NoopThread:
    def start(self):
        pass


class _FakeAudioPlayer:
    def __init__(self):
        self.positions: list[int] = []

    def setPosition(self, ms: int):
        self.positions.append(ms)


class _FakePreview(QObject):
    """最小播放器替身：复刻真实 VideoPreviewWidget 的关键副作用。

    真实 ``stop_playback(reset_position=True)`` 会 ``seek_to(0.0)``，而 ``seek_to``
    会 emit ``position_changed``；``MainWindow._on_video_position_changed`` 据此驱动
    结果表 ``sync_play_position`` → 高亮/滚动到对应行。这正是"跳回初始行"的传递路径。
    """

    position_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.is_image = False
        self._audio_player = _FakeAudioPlayer()
        self._player = None
        self._ffmpeg = None
        self._current_position = 0.0
        self.slider_values: list[float] = []
        self.seek_targets: list[float] = []
        self.stop_calls: list[bool] = []

    def stop_playback(self, reset_position: bool = True):
        self.stop_calls.append(reset_position)
        if reset_position:
            self._current_position = 0.0
            self.slider_values.append(0.0)
            self.position_changed.emit(0.0)

    def _on_stop_playback(self):
        self.stop_playback(reset_position=True)

    def seek_to(self, ts: float):
        self._current_position = ts
        self.seek_targets.append(ts)
        self.slider_values.append(ts)
        self.position_changed.emit(ts)

    def _set_slider(self, ts: float):
        self.slider_values.append(ts)

    def _update_preview_label(self):
        pass

    def set_subtitle_overlay(self, _text: str):
        pass


def _window_with_results(rows: int = 5):
    """构建窗口并塞入若干条带时间轴的结果（0s / 10s / 20s / ...）。

    第 0 行从 0 秒开始 —— 这正是用户遇到的现象前提：``seek_to(0.0)`` 会命中第 0 行。
    """
    win = _build_window()
    table = win._result_table
    for i in range(rows):
        table.add_result(
            time_str=f"00:00:{10 * i:02d}",
            region="字幕",
            engine="test",
            raw_text=f"第{i}行文本",
            time_sec=float(10 * i),
            end_sec=float(10 * i + 5),
        )
    fake = _FakePreview()
    fake.position_changed.connect(win._on_video_position_changed)
    win._video_preview = fake
    return win, table, fake


@pytest.fixture(scope="module")
def jump_window(app):
    """跳转类用例共享的窗口。

    单次 ``setup()`` 约 5s（域注册中心 + 引擎管理器 + 视图树），跳转断言本身是只读的，
    没有理由每个用例重建一次 —— 否则本文件的耗时会被放大数倍。
    """
    win, table, fake = _window_with_results()
    yield win, table, fake
    win.close()
    app.processEvents()


@pytest.fixture()
def jump_ctx(jump_window):
    """每个跳转用例开始前把可观测状态清干净（窗口复用）。"""
    win, table, fake = jump_window
    fake.seek_targets.clear()
    fake.slider_values.clear()
    fake.stop_calls.clear()
    fake._audio_player.positions.clear()
    fake.is_image = False
    fake._current_position = 0.0
    table._model.set_playing_row(-1)
    return win, table, fake


@pytest.mark.parametrize("is_audio", [False, True])
def test_jump_to_row_lands_on_clicked_row_not_initial_row(jump_ctx, monkeypatch, is_audio):
    """回归（用户报告）：双击结果会跳转到结果列表的**初始行**。

    传递路径：``_on_result_cell_edit`` 先调 ``_on_stop_playback()``，它内部
    ``seek_to(0.0)`` 会 emit ``position_changed(0.0)`` → 表格高亮/滚动到覆盖
    0.0 秒的行（通常是第 0 行）。随后必须再以目标行的真实时间戳驱动一次，
    否则表格就停留在初始行 —— 音频分支此前正是如此（手工设置位置但不再 emit）。
    """
    win, table, fake = jump_ctx
    monkeypatch.setattr(win, "_is_audio_file", lambda: is_audio)

    target_row = 3
    win._on_result_cell_edit(target_row)

    assert table._model._playing_row == target_row, (
        f"应高亮第 {target_row} 行，实际停在 {table._model._playing_row} 行"
        f"（is_audio={is_audio}, seek_targets={fake.seek_targets}）"
    )
    assert 30.0 in fake.seek_targets, f"应向播放器请求 30.0s，实际 {fake.seek_targets}"
    assert fake.stop_calls == [False], "跳转时必须停止播放但不得回到起点"
    if is_audio:
        assert fake._audio_player.positions[-1] == 30000, "音频应 setPosition(30000ms)"


@pytest.mark.parametrize("is_audio", [False, True])
def test_single_click_also_lands_on_clicked_row(jump_ctx, monkeypatch, is_audio):
    """同一路径也服务单击跳转，行为必须一致。"""
    win, table, _fake = jump_ctx
    monkeypatch.setattr(win, "_is_audio_file", lambda: is_audio)

    win._on_result_cell_edit(1)
    assert table._model._playing_row == 1


def test_stop_playback_still_rewinds_to_start(jump_ctx):
    """对照：真正的"停止播放"仍应回到起点并高亮初始行（不要为修双击而破坏它）。"""
    _win, table, fake = jump_ctx
    fake._on_stop_playback()
    assert fake.stop_calls == [True]
    assert table._model._playing_row == 0


def test_double_click_time_column_lands_on_clicked_row(jump_ctx, monkeypatch):
    """用户手势回归：双击"时间"列 → 结果表必须落在被双击的那一行。

    双击由 ``QTableView.clicked``（单击）+ ``doubleClicked`` 共同触发；时间列不可编辑，
    因此跳转完全来自 ``_on_cell_clicked → cell_edit_activated(row)``。
    """
    from ui.result_table import COL_TIME

    win, table, _fake = jump_ctx
    monkeypatch.setattr(win, "_is_audio_file", lambda: True)

    emitted: list[int] = []
    table.cell_edit_activated.connect(emitted.append)

    # 直接调用真实槽函数（不依赖真实鼠标事件）
    table._on_cell_clicked(table._model.index(4, COL_TIME))
    table._on_cell_double_clicked(table._model.index(4, COL_TIME))

    assert emitted == [4], f"应只跳转第 4 行，实际 {emitted}"
    assert table._model._playing_row == 4, f"应停在 4 行，实际 {table._model._playing_row}"


def test_edit_commit_lands_on_edited_row(jump_ctx, monkeypatch):
    """编辑提交（double-click 可编辑列 → 编辑 → 提交）也必须落在被编辑行。

    该路径经 ``setData(..., EditRole) → dataChanged[EditRole] →
    _on_model_data_changed → cell_edit_activated(row)``，与单击共用同一跳转实现，
    因此曾经同样受"先 seek_to(0.0) 再跳到目标"缺陷影响。
    """
    from PySide6.QtCore import Qt

    from ui.result_table import COL_CORRECTED

    win, table, _fake = jump_ctx
    monkeypatch.setattr(win, "_is_audio_file", lambda: True)

    ok = table._model.setData(table._model.index(3, COL_CORRECTED), "改后的文本", Qt.ItemDataRole.EditRole)
    assert ok is True
    assert table._results[3]["corrected"] == "改后的文本"
    assert table._model._playing_row == 3, f"应停在 3 行，实际 {table._model._playing_row}"


def test_image_preview_disables_jump(jump_ctx):
    """图片预览不应触发任何跳转（保持原有守卫）。"""
    win, _table, fake = jump_ctx
    fake.is_image = True
    win._on_result_cell_edit(2)
    assert fake.seek_targets == []
    assert fake.stop_calls == []
