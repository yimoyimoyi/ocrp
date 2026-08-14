"""视频预览控件（VideoPreviewWidget）与打轴交互单元测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from ui.video_preview import VideoPreviewWidget, _ClickableSlider


@pytest.fixture(scope="module")
def app():
    _app = QApplication.instance() or QApplication([])
    yield _app


@pytest.fixture()
def preview(app):
    return VideoPreviewWidget()


class TestClickableSlider:
    def test_click_to_seek(self, app):
        slider = _ClickableSlider(Qt.Horizontal)
        slider.resize(200, 20)
        slider.setRange(0, 1000)
        # 模拟点击中间位置 (x=100)
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPoint(100, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        slider.mousePressEvent(ev)
        # 应跳转到约 500
        assert 480 <= slider.value() <= 520


class TestVideoPreviewInteraction:
    def test_subtitle_overlay(self, preview):
        preview.set_subtitle_overlay("当前字幕测试文本")
        assert preview._subtitle_overlay_text == "当前字幕测试文本"
        preview.set_subtitle_overlay("")
        assert preview._subtitle_overlay_text == ""

    def test_step_frame(self, preview):
        preview._video_duration = 60.0
        preview._current_position = 10.0
        preview._video_fps = 25.0
        preview._step_frame(1)  # 前进 1 帧 (0.04s)
        assert abs(preview._current_position - 10.04) < 0.001
        preview._step_frame(-1)  # 后退 1 帧
        assert abs(preview._current_position - 10.0) < 0.001

    def test_position_changed_signal(self, preview):
        emitted = []
        preview.position_changed.connect(emitted.append)
        preview.seek_to(5.5)
        assert 5.5 in emitted


class TestRoiInteraction:
    """测试 ROI 选区 8 点控制、微调移动、缩放与边缘吸附。"""

    def test_edge_snapping(self, preview):
        import numpy as np

        # 模拟 1920x1080 图像
        dummy = np.zeros((1080, 1920, 3), dtype=np.uint8)
        preview._display_frame(dummy)

        # 边缘 3px 以内自动吸附到 0
        r = {"x": 3, "y": 4, "w": 100, "h": 50}
        preview._apply_edge_snapping(r, snap_threshold=6)
        assert r["x"] == 0
        assert r["y"] == 0
        assert r["w"] == 103
        assert r["h"] == 54

    def test_keyboard_micro_step_move(self, preview):
        import numpy as np
        from PySide6.QtGui import QKeyEvent

        dummy = np.zeros((1080, 1920, 3), dtype=np.uint8)
        preview._display_frame(dummy)

        preview.add_region(100, 100, 200, 80)
        preview.select_region(0)

        # Ctrl + Right 移动 1px
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Right, Qt.KeyboardModifier.ControlModifier)
        preview.keyPressEvent(ev)
        assert preview._regions[0]["x"] == 101

        # Ctrl + Down 移动 1px
        ev_down = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Down, Qt.KeyboardModifier.ControlModifier)
        preview.keyPressEvent(ev_down)
        assert preview._regions[0]["y"] == 101

    def test_keyboard_micro_step_resize(self, preview):
        import numpy as np
        from PySide6.QtGui import QKeyEvent

        dummy = np.zeros((1080, 1920, 3), dtype=np.uint8)
        preview._display_frame(dummy)

        preview.add_region(100, 100, 200, 80)
        preview.select_region(0)

        # Alt + Right 扩大宽度 1px
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Right, Qt.KeyboardModifier.AltModifier)
        preview.keyPressEvent(ev)
        assert preview._regions[0]["w"] == 201

    def test_keyboard_delete_region(self, preview):
        from PySide6.QtGui import QKeyEvent

        preview.add_region(10, 10, 50, 50)
        preview.select_region(0)
        assert len(preview._regions) == 1

        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key_Delete, Qt.KeyboardModifier.NoModifier)
        preview.keyPressEvent(ev)
        assert len(preview._regions) == 0
