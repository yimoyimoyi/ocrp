"""BatchProcessWorker 设置同步修复回归测试。

覆盖：
- R6：构造签名移除死参数 corrector；视频处理补齐 _subtitle_mode/_r_* 等参数
- W1：批量会话使用传入的 mode_params 快照（dict 副本）
"""

import inspect
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.workers import BatchProcessWorker

_MODE_PARAMS = {
    "subtitle_mode": "流式字幕（去重）",
    "sentinel_enabled": True,
    "s_drop_ratio": 0.5,
    "s_min_text_len": 2,
    "s_buffer_size": 8,
    "s_sim_threshold": 0.85,
    "s_ocr_version": "PP-OCRv4 (最快)",
    "r_dedup": True,
    "r_sim_threshold": 0.9,
    "r_buffer_size": 5,
    "r_min_text_len": 2,
    "r_interval": 2.0,
    "frame_interval": 0.1,
}


class TestBatchWorkerConstructor:
    """构造签名与 _mode_params 快照。"""

    def test_signature_has_no_corrector_param(self):
        sig = inspect.signature(BatchProcessWorker.__init__)
        assert "corrector" not in sig.parameters

    def test_construct_without_corrector_ok(self):
        worker = BatchProcessWorker(
            engine_manager=None,
            file_list=["a.mp4"],
            regions=[],
            mode_params=_MODE_PARAMS,
            output_dir="output",
        )
        assert not hasattr(worker, "_corrector")
        worker.stop()  # 清理 stop flag，防止资源残留

    def test_mode_params_is_snapshot_copy(self):
        params = dict(_MODE_PARAMS)
        worker = BatchProcessWorker(
            engine_manager=None,
            file_list=[],
            regions=[],
            mode_params=params,
            output_dir="output",
        )
        assert worker._mode_params == params
        assert worker._mode_params is not params
        # 修改外部 dict 不影响 worker 快照
        params["frame_interval"] = 9.9
        assert worker._mode_params["frame_interval"] == 0.1


class TestBatchVideoParams:
    """视频处理分支把 mode_params 完整写入 FrameProcessor（R6 修复）。"""

    @staticmethod
    def _process_video(mode_params: dict):
        worker = BatchProcessWorker(
            engine_manager=MagicMock(),
            file_list=[],
            regions=[{"name": "全帧", "engine": "paddleocr", "enabled": True}],
            mode_params=mode_params,
            output_dir="output",
        )
        with patch("core.frame_processor.FrameProcessor") as MockFP:
            MockFP.return_value.process_video.return_value = []
            results = worker._process_one_file("C:/tmp/test.mp4")
        return results, MockFP.return_value

    def test_all_params_written_to_frame_processor(self):
        _, fp = self._process_video(_MODE_PARAMS)
        # 修复前缺失的参数（此前批量始终走默认流式）
        assert fp._subtitle_mode == "流式字幕（去重）"
        assert fp._s_ocr_version == "PP-OCRv4 (最快)"
        assert fp._r_dedup is True
        assert fp._r_sim_threshold == 0.9
        assert fp._r_buffer_size == 5
        assert fp._r_min_text_len == 2
        assert fp._r_interval == 2.0
        # 原有参数不回退
        assert fp._sentinel_enabled is True
        assert fp._s_sim_threshold == 0.85
        assert fp._frame_interval == 0.1

    def test_missing_keys_use_defaults(self):
        # 部分参数：缺失键走 mp.get 默认值（空 dict 时 `if mp:` 守卫跳过赋值，
        # 由 FrameProcessor 自身默认值接管，不属于本修复范围）
        _, fp = self._process_video({"sentinel_enabled": False})
        assert fp._subtitle_mode == "stream"
        assert fp._sentinel_enabled is False
        assert fp._s_ocr_version == ""
        assert fp._r_dedup is True
        assert fp._r_interval == 2.0
        assert fp._frame_interval == 0.1

    def test_custom_values_override(self):
        custom = dict(_MODE_PARAMS, subtitle_mode="固定间隔", r_interval=5.0, r_dedup=False)
        _, fp = self._process_video(custom)
        assert fp._subtitle_mode == "固定间隔"
        assert fp._r_interval == 5.0
        assert fp._r_dedup is False
