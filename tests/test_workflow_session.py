"""WorkflowManager 会话配置快照与 ASR 运行中保护测试。

覆盖设置同步修复：
- W1：start_processing / start_batch 时快照 _session_mp，自动纠错使用快照值
- R5：is_asr_running() 供 MainWindow 在 ASR 运行中延迟重建引擎

WorkflowManager 是纯 QObject，无需 QApplication 即可实例化。
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.utils import MODE_OCR_ONLY
from core.workflow.manager import WorkflowManager

_LIVE_PARAMS = {
    "process_mode": MODE_OCR_ONLY,
    "corr_translate": False,
    "corr_stream": False,
    "corr_json": False,
    "corr_extract_env": False,
    "corr_retry": 2,
    "corr_batch_size": 5,
    "corr_concurrency": 4,
}


class _FakeCorrector:
    """记录 _sync_corrector_modes 写入值的替身。"""

    def __init__(self):
        self.translate_mode = None
        self.stream_mode = None
        self.json_mode = None


def _make_wf(**mp_overrides) -> tuple[WorkflowManager, dict, _FakeCorrector]:
    """构造 WorkflowManager：注入假 mode_params 来源与假 corrector。"""
    wf = WorkflowManager()
    live = dict(_LIVE_PARAMS)
    live.update(mp_overrides)
    wf._get_mode_params = lambda: dict(live)
    wf._corrector = _FakeCorrector()
    return wf, live, wf._corrector


class TestIsAsrRunning:
    """is_asr_running() 运行中保护判定。"""

    def test_false_without_worker(self):
        wf, _, _ = _make_wf()
        assert wf.is_asr_running() is False

    def test_true_with_running_worker(self):
        wf, _, _ = _make_wf()
        worker = MagicMock()
        worker.isRunning.return_value = True
        wf._audio_worker = worker
        assert wf.is_asr_running() is True

    def test_false_with_idle_worker(self):
        wf, _, _ = _make_wf()
        worker = MagicMock()
        worker.isRunning.return_value = False
        wf._audio_worker = worker
        assert wf.is_asr_running() is False


class TestStartProcessingSnapshot:
    """start_processing 时快照 _session_mp。"""

    def test_sets_session_snapshot(self):
        wf, live, _ = _make_wf(corr_translate=True)
        wf._get_video_path = lambda: "C:/tmp/test.mp4"
        wf._get_is_audio_file = lambda: False
        wf._get_is_image = lambda: False
        wf._get_regions = lambda: [{"name": "全帧", "engine": "paddleocr", "enabled": True}]
        wf._get_current_engine = lambda: "paddleocr"
        wf._get_custom_prompt = lambda: ""
        wf._get_config_prompt = lambda: ""
        wf._get_current_template = lambda: ""
        wf._set_regions = lambda r: None
        wf._ocr_flow._do_ocr_pass = lambda vp: None  # 拦截真实处理

        wf.start_processing()

        assert wf._session_mp == live
        # 快照是副本：后续修改来源 dict 不影响已开始的会话
        assert wf._session_mp is not live
        live["corr_translate"] = False
        assert wf._session_mp["corr_translate"] is True

    def test_no_video_does_not_snapshot(self):
        wf, _, _ = _make_wf()
        wf._get_video_path = lambda: None
        wf.start_processing()
        assert wf._session_mp == {}


class TestBatchCorrectionSnapshot:
    """_start_batch_correction 的会话快照语义（is_auto 区分）。"""

    def test_auto_uses_session_snapshot(self):
        wf, _, corrector = _make_wf(corr_translate=False)  # 实时值 translate=False
        wf._session_mp = dict(_LIVE_PARAMS, corr_translate=True)  # 快照 translate=True
        wf._start_batch_correction([], is_auto=True)
        # 自动纠错（处理会话触发）必须用快照值
        assert corrector.translate_mode is True

    def test_auto_without_snapshot_uses_live(self):
        wf, _, corrector = _make_wf(corr_translate=True)
        wf._session_mp = {}  # 无快照（如直接调用纠错入口）
        wf._start_batch_correction([], is_auto=True)
        assert corrector.translate_mode is True

    def test_manual_uses_live_values(self):
        wf, _, corrector = _make_wf(corr_translate=False)  # 实时值 translate=False
        wf._session_mp = dict(_LIVE_PARAMS, corr_translate=True)  # 快照 translate=True
        wf._start_batch_correction([], is_auto=False)
        # 手动纠错（用户点击）使用点击前的实时参数
        assert corrector.translate_mode is False

    def test_syncs_all_modes(self):
        wf, _, corrector = _make_wf()
        wf._session_mp = dict(_LIVE_PARAMS, corr_translate=True, corr_stream=True, corr_json=True)
        wf._start_batch_correction([], is_auto=True)
        assert (corrector.translate_mode, corrector.stream_mode, corrector.json_mode) == (True, True, True)
