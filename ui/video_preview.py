# -*- coding: utf-8 -*-
"""视频预览组件 —— 支持拖放视频帧、图片、鼠标拖动画矩形 ROI 区域。
QLabel 子类手动绘制 pixmap（保持宽高比居中）+ ROI 叠加层。
"""

import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy, QSlider, QPushButton,
    QApplication,
)
from PyQt5.QtCore import Qt, QPoint, pyqtSignal, QRect, QRectF
from PyQt5.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor, QFont,
    QDragEnterEvent, QDropEvent, QMouseEvent, QResizeEvent,
    QKeyEvent,
)
from pathlib import Path
from typing import List, Dict, Optional, Tuple


def _imread_unicode(path: str) -> Optional[np.ndarray]:
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size > 0 else None
    except Exception:
        return None


class _PreviewLabel(QLabel):
    """预览标签 —— 手动居中绘制 pixmap（保持宽高比），并在之上绘制 ROI 矩形。

    关键：所有可变状态通过实例引用（非类变量），避免 PyQt5 中
    monkey-patching paintEvent 的不稳定性。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setScaledContents(False)  # 手动控制缩放，保持宽高比
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

        # 实例引用（由 VideoPreviewWidget 设置）
        self._pixmap_ref = lambda: None       # () -> Optional[QPixmap]
        self._regions_ref = lambda: []        # () -> List[dict]
        self._selected_ref = lambda: -1       # () -> int
        self._color_pool_ref = lambda: []     # () -> List[QColor]
        self._drawing_ref = lambda: False     # () -> bool
        self._start_point_ref = lambda: QPoint()
        self._end_point_ref = lambda: QPoint()
        self._placeholder_text = "拖放视频/图片文件到此处\n或 Ctrl+V 粘贴文件路径"

    def set_refs(self, pixmap_fn, regions_fn, selected_fn, color_pool_fn,
                 drawing_fn, start_fn, end_fn):
        """设置对 VideoPreviewWidget 状态的引用。"""
        self._pixmap_ref = pixmap_fn
        self._regions_ref = regions_fn
        self._selected_ref = selected_fn
        self._color_pool_ref = color_pool_fn
        self._drawing_ref = drawing_fn
        self._start_point_ref = start_fn
        self._end_point_ref = end_fn

    def paintEvent(self, event):
        """手动绘制：背景 → 居中 pixmap → ROI 矩形 → 拖拽预览。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        pix = self._pixmap_ref()
        lw, lh = self.width(), self.height()

        # 背景
        painter.fillRect(0, 0, lw, lh, QColor(30, 30, 30))

        if pix is None or pix.isNull():
            # 占位文字
            painter.setPen(QColor(136, 136, 136))
            painter.setFont(QFont("Microsoft YaHei", 14))
            painter.drawText(QRect(0, 0, lw, lh), Qt.AlignCenter,
                             self._placeholder_text)
            painter.end()
            return

        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            painter.end()
            return

        # 计算居中、保持宽高比的绘制区域
        scale = min(lw / pw, lh / ph)
        img_w, img_h = int(pw * scale), int(ph * scale)
        ox, oy = (lw - img_w) // 2, (lh - img_h) // 2

        # 绘制 pixmap
        target = QRectF(ox, oy, img_w, img_h)
        painter.drawPixmap(target, pix, QRectF(0, 0, pw, ph))

        # 绘制 ROI 矩形
        regions = self._regions_ref()
        selected = self._selected_ref()
        color_pool = self._color_pool_ref()

        for i, r in enumerate(regions):
            # 帧坐标 → 标签坐标
            fx, fy, fw, fh = r["x"], r["y"], r["w"], r["h"]
            rx = int(fx * img_w / pw) + ox
            ry = int(fy * img_h / ph) + oy
            rw = int(fw * img_w / pw)
            rh = int(fh * img_h / ph)

            color = r.get("color", color_pool[i % len(color_pool)] if color_pool else QColor(0, 200, 100))
            pen = QPen(color, 2)
            if i == selected:
                pen.setWidth(3)
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(rx, ry, rw, rh)

            # 区域名标签
            name = r.get("name", "")
            if name:
                painter.setPen(QColor(255, 255, 255))
                painter.setFont(QFont("Microsoft YaHei", 10))
                painter.drawText(rx + 2, ry - 4 if ry > 12 else ry + rh + 14, name)

        # 拖拽中的矩形预览
        if self._drawing_ref():
            sp = self._start_point_ref()
            ep = self._end_point_ref()
            painter.setPen(QPen(QColor(0, 200, 100), 1, Qt.DashLine))
            x = min(sp.x(), ep.x())
            y = min(sp.y(), ep.y())
            w = abs(ep.x() - sp.x())
            h = abs(ep.y() - sp.y())
            painter.drawRect(x, y, w, h)

        painter.end()


class VideoPreviewWidget(QWidget):
    video_loaded = pyqtSignal(str)
    frame_captured = pyqtSignal(object)
    regions_changed = pyqtSignal(list)
    files_dropped = pyqtSignal(list)  # 拖放多个文件到队列

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)  # 允许接收键盘事件（Ctrl+V 粘贴）
        self.setMinimumSize(320, 240)
        self._video_path: Optional[str] = None
        self._ffmpeg: object = None
        self._hw_accel: bool = False
        self._current_frame: Optional[np.ndarray] = None
        self._display_pixmap: Optional[QPixmap] = None
        self._is_image: bool = False
        self._regions: List[dict] = []
        self._region_counter = 0
        self._selected_region_index: int = -1
        self._default_engine = "paddleocr"
        self._default_prompt = ""
        self._default_template = "通用OCR"
        self._drawing = False
        self._start_point = QPoint()
        self._end_point = QPoint()
        self._moving_region_index = -1
        self._resizing_region_index = -1
        self._resize_handle = ""
        self._drag_offset = QPoint()
        self._color_pool = [
            QColor(0, 200, 100), QColor(200, 100, 0), QColor(100, 100, 255),
            QColor(255, 200, 0), QColor(200, 0, 150), QColor(0, 200, 200)
        ]
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 使用子类 _PreviewLabel，手动控制 pixmap 绘制 + ROI 叠加
        self._label = _PreviewLabel()
        self._label.setObjectName("videoLabel")
        # 建立引用桥接（避免 monkey-patching）
        self._label.set_refs(
            lambda: self._display_pixmap,
            lambda: self._regions,
            lambda: self._selected_region_index,
            lambda: self._color_pool,
            lambda: self._drawing,
            lambda: self._start_point,
            lambda: self._end_point,
        )
        self._label.mousePressEvent = self._on_mouse_press
        self._label.mouseMoveEvent = self._on_mouse_move
        self._label.mouseReleaseEvent = self._on_mouse_release
        layout.addWidget(self._label)

        # 时间选择器
        tr = QHBoxLayout()
        tr.setSpacing(4)
        tr.addWidget(QLabel("开始:"))
        self._time_start_label = QLabel("00:00")
        tr.addWidget(self._time_start_label)
        self._timeline_start = QSlider(Qt.Horizontal)
        self._timeline_start.setRange(0, 0)
        self._timeline_start.sliderReleased.connect(self._on_timeline_seek_start)
        tr.addWidget(self._timeline_start, 1)
        tr.addWidget(QLabel("结束:"))
        self._time_end_label = QLabel("00:00")
        tr.addWidget(self._time_end_label)
        self._timeline_end = QSlider(Qt.Horizontal)
        self._timeline_end.setRange(0, 0)
        self._timeline_end.sliderReleased.connect(self._on_timeline_seek_end)
        tr.addWidget(self._timeline_end, 1)
        self._time_range_widget = QWidget()
        self._time_range_widget.setLayout(tr)
        self._time_range_widget.hide()
        layout.addWidget(self._time_range_widget)

        # 播放栏（共用：视频/音频）
        play_bar = QHBoxLayout()
        play_bar.setSpacing(4)
        self._preview_time_label = QLabel("00:00 / 00:00")
        play_bar.addWidget(self._preview_time_label)
        self._btn_play = QPushButton("▶")
        self._btn_play.setToolTip("播放/暂停")
        self._btn_play.setMaximumWidth(36)
        self._btn_play.clicked.connect(self._on_play_pause)
        play_bar.addWidget(self._btn_play)
        self._btn_stop_play = QPushButton("⏹")
        self._btn_stop_play.setToolTip("停止")
        self._btn_stop_play.setMaximumWidth(36)
        self._btn_stop_play.clicked.connect(self._on_stop_playback)
        play_bar.addWidget(self._btn_stop_play)
        self._preview_slider = QSlider(Qt.Horizontal)
        self._preview_slider.setRange(0, 0)
        self._preview_slider.setTracking(True)
        self._preview_slider.sliderPressed.connect(self._on_preview_slider_press)
        self._preview_slider.sliderMoved.connect(self._on_preview_slider_move)
        self._preview_slider.sliderReleased.connect(self._on_preview_slider_release)
        play_bar.addWidget(self._preview_slider, 1)
        self._play_bar_widget = QWidget()
        self._play_bar_widget.setLayout(play_bar)
        self._play_bar_widget.hide()
        layout.addWidget(self._play_bar_widget)

        # 播放定时器
        from PyQt5.QtCore import QTimer
        self._play_timer = QTimer()
        self._play_timer.setInterval(500)  # 极低频检查 ffplay 是否结束（0.5次/秒），不更新预览条
        self._play_timer.timeout.connect(self._on_play_tick)
        self._is_playing = False
        self._play_start_real: float = 0.0
        self._play_start_ts: float = 0.0

        self._video_duration: float = 0.0
        self._player_proc = None  # ffplay 子进程
        self._time_start: float = 0.0
        self._time_end: float = 0.0
        self._current_position: float = 0.0

    # ── 属性 ──
    @property
    def regions(self) -> list:
        return list(self._regions)

    @regions.setter
    def regions(self, val: list):
        self._regions = list(val)
        self._label.update()

    @property
    def current_frame(self) -> Optional[np.ndarray]:
        return self._current_frame

    @property
    def video_path(self) -> Optional[str]:
        return self._video_path

    @property
    def is_image(self) -> bool:
        return self._is_image

    @property
    def time_start(self) -> float:
        return self._time_start

    @property
    def time_end(self) -> float:
        return self._time_end

    def set_hw_accel(self, enabled: bool):
        self._hw_accel = enabled

    def load_video(self, path: str):
        if self._ffmpeg:
            try:
                self._ffmpeg.close()
            except Exception:
                pass
            self._ffmpeg = None
        from core.ffmpeg_reader import FFmpegReader
        ff = FFmpegReader(path, hw_accel=self._hw_accel)
        if not ff.open():
            print(f"[VIDEO] FFmpeg.open() FAILED for {path}")
            return
        frame = ff.read()
        if frame is None or frame.size == 0:
            print(f"[VIDEO] first frame read FAILED for {path}")
            ff.close()
            return
        self._video_path = path
        self._is_image = False
        self._ffmpeg = ff
        self._video_duration = ff.duration
        self._current_frame = frame.copy()
        self._display_frame(self._current_frame)
        if self._video_duration > 0:
            max_t = int(self._video_duration * 10)
            self._timeline_start.setRange(0, max_t)
            self._timeline_start.setValue(0)
            self._timeline_end.setRange(0, max_t)
            self._timeline_end.setValue(max_t)
            self._time_start = 0.0
            self._time_end = self._video_duration
            self._update_time_labels()
            self._time_range_widget.show()
            self._preview_slider.setRange(0, max_t)
            self._preview_slider.setValue(0)
            self._update_preview_label()
            self._play_bar_widget.show()
        self._label.update()
        self.video_loaded.emit(path)

    def load_image(self, path: str):
        if self._ffmpeg:
            try:
                self._ffmpeg.close()
            except Exception:
                pass
            self._ffmpeg = None
        self._video_path = path
        self._is_image = True
        self._video_duration = 0
        self._time_range_widget.hide()
        self._play_bar_widget.hide()
        img = _imread_unicode(path)
        if img is None:
            self._current_frame = None
            self._display_pixmap = None
            self._label.setText("无法打开图片")
            self._label.update()
            return
        self._current_frame = img.copy()
        self._display_frame(self._current_frame)
        self._label.update()
        # 清空旧区域，避免上次残留的区域坐标导致全帧 fallback 不生效
        self._regions.clear()
        self._selected_region_index = -1
        self.video_loaded.emit(path)

    def capture_test_frame(self, image: np.ndarray = None):
        if image is not None:
            self._current_frame = image.copy()
        elif self._ffmpeg and hasattr(self._ffmpeg, 'is_opened') and self._ffmpeg.is_opened():
            frame = self._ffmpeg.read()
            if frame is not None and frame.size > 0:
                self._current_frame = frame.copy()
        if self._current_frame is not None:
            self._display_frame(self._current_frame)
            self.frame_captured.emit(self._current_frame)

    def seek_to(self, position_sec: float):
        ff = self._ffmpeg
        if ff and hasattr(ff, 'is_opened') and ff.is_opened():
            try:
                frame = ff.seek_sec(position_sec)
                if frame is not None and frame.size > 0:
                    self._current_frame = frame.copy()
                    self._display_frame(self._current_frame)
            except Exception:
                pass
        val = int(position_sec * 10)
        if 0 <= val <= self._preview_slider.maximum():
            self._preview_slider.blockSignals(True)
            self._preview_slider.setValue(val)
            self._preview_slider.blockSignals(False)
        self._current_position = position_sec
        self._update_preview_label()

    def _on_play_pause(self):
        """播放/暂停（视频用 -nodisp + QTimer 渲染帧，音频用 -nodisp）。"""
        if self._is_playing:
            self._pause_video()
        else:
            self._play_video()

    def _play_video(self):
        """开始播放（ffplay -nodisp 播音频 + QTimer 同步预览条）。"""
        vp = self._video_path
        if not vp:
            return
        self._stop_player()
        if self._current_position >= self._video_duration - 0.1:
            self._current_position = 0.0
        self._is_playing = True
        self._btn_play.setText("⏸")
        import subprocess, os, time, shutil, sys
        ffplay = shutil.which("ffplay")
        if not ffplay:
            ext = ".exe" if sys.platform == "win32" else ""
            ffplay = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core", f"ffplay{ext}")
            if not os.path.isfile(ffplay):
                ffplay = "ffplay"
        cmd = [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet",
               "-ss", str(self._current_position), str(vp)]
        try:
            self._player_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception:
            self._pause_video()
            return
        self._play_start_real = self._current_position
        self._play_start_ts = time.time()
        self._play_timer.start()

    def _pause_video(self):
        """暂停播放。"""
        self._is_playing = False
        self._btn_play.setText("▶")
        self._play_timer.stop()
        self._stop_player()
        self.seek_to(self._current_position)

    def _on_stop_playback(self):
        """停止播放。"""
        self._pause_video()
        self._current_position = 0.0
        self.seek_to(0.0)
        self._update_preview_label()

    def _stop_player(self):
        """终止 ffplay 子进程（音频或视频）。"""
        if self._player_proc:
            try:
                self._player_proc.terminate()
                self._player_proc.wait(timeout=3)
            except Exception:
                try:
                    self._player_proc.kill()
                except Exception:
                    pass
            self._player_proc = None

    def _on_play_tick(self):
        """播放定时器回调：仅检查播放是否结束，不更新预览条。"""
        if self._player_proc and self._player_proc.poll() is not None:
            self._pause_video()
            return

    def _on_preview_slider_press(self):
        self._pause_video()
        if self._video_duration <= 0:
            return
        self._current_position = self._preview_slider.value() / 10.0
        self._update_preview_label()
        self.seek_to(self._current_position)

    def _on_preview_slider_move(self, val: int):
        if self._video_duration <= 0:
            return
        self._current_position = val / 10.0
        self._update_preview_label()
        self.seek_to(self._current_position)

    def _on_preview_slider_release(self):
        if self._video_duration <= 0:
            return
        self._current_position = self._preview_slider.value() / 10.0
        self._update_preview_label()
        self.seek_to(self._current_position)

    def _update_preview_label(self):
        m1, s1 = divmod(int(self._current_position), 60)
        m2, s2 = divmod(int(self._video_duration), 60)
        self._preview_time_label.setText(f"{m1:02d}:{s1:02d} / {m2:02d}:{s2:02d}")

    def _on_timeline_seek_start(self):
        self._time_start = self._timeline_start.value() / 10.0
        if self._timeline_start.value() > self._timeline_end.value():
            self._timeline_end.setValue(self._timeline_start.value())
            self._time_end = self._time_start
        self._update_time_labels()
        self.seek_to(self._time_start)

    def _on_timeline_seek_end(self):
        self._time_end = self._timeline_end.value() / 10.0
        if self._timeline_end.value() < self._timeline_start.value():
            self._timeline_start.setValue(self._timeline_end.value())
            self._time_start = self._time_end
        self._update_time_labels()
        self.seek_to(self._time_end)

    def _update_time_labels(self):
        for v, lb in [(self._time_start, self._time_start_label),
                       (self._time_end, self._time_end_label)]:
            m, s = divmod(int(v), 60)
            lb.setText(f"{m:02d}:{s:02d}")

    def set_region_defaults(self, engine: str, prompt: str = "", template: str = ""):
        self._default_engine = engine
        self._default_prompt = prompt
        self._default_template = template

    def clear_regions(self):
        self._regions.clear()
        self._selected_region_index = -1
        self._label.update()

    def add_region(self, x: int, y: int, w: int, h: int, name: str = "") -> dict:
        if not name:
            self._region_counter += 1
            name = f"区域{self._region_counter}"
        color = self._color_pool[len(self._regions) % len(self._color_pool)]
        r = {"name": name, "x": x, "y": y, "w": w, "h": h, "color": color,
             "engine": self._default_engine, "prompt": self._default_prompt,
             "template": self._default_template}
        self._regions.append(r)
        self.regions_changed.emit(list(self._regions))
        self._label.update()
        return r

    def remove_region(self, index: int):
        if 0 <= index < len(self._regions):
            self._regions.pop(index)
        if self._selected_region_index >= len(self._regions):
            self._selected_region_index = len(self._regions) - 1
        self.regions_changed.emit(list(self._regions))
        self._label.update()

    def update_region(self, index: int, props: dict, emit_signal: bool = True):
        if 0 <= index < len(self._regions):
            self._regions[index].update(props)
        if emit_signal:
            self.regions_changed.emit(list(self._regions))
        self._label.update()

    def select_region(self, index: int):
        self._selected_region_index = index
        self._label.update()

    def get_roi_image(self, region_index: int) -> Optional[np.ndarray]:
        if self._current_frame is None:
            return None
        if 0 <= region_index < len(self._regions):
            r = self._regions[region_index]
            x, y, w, h = r["x"], r["y"], r["w"], r["h"]
            if w > 0 and h > 0:
                return self._current_frame[y:y + h, x:x + w].copy()
        return self._current_frame

    def _display_frame(self, frame: np.ndarray):
        h, w, ch = frame.shape
        if ch == 3:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            rgb = frame
        self._display_pixmap = QPixmap.fromImage(
            QImage(rgb.tobytes(), w, h, ch * w, QImage.Format_RGB888))
        self._label.update()

    def _label_to_frame_coords(self, label_x: int, label_y: int) -> QPoint:
        pix = self._display_pixmap
        if pix is None or pix.isNull():
            return QPoint(-1, -1)
        lw, lh = self._label.width(), self._label.height()
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            return QPoint(-1, -1)
        scale = min(lw / pw, lh / ph)
        img_w, img_h = int(pw * scale), int(ph * scale)
        ox, oy = (lw - img_w) // 2, (lh - img_h) // 2
        # 钳制到图片可见区域内
        cx = max(ox, min(ox + img_w - 1, label_x))
        cy = max(oy, min(oy + img_h - 1, label_y))
        fx = int((cx - ox) * pw / img_w)
        fy = int((cy - oy) * ph / img_h)
        return QPoint(max(0, min(pw - 1, fx)), max(0, min(ph - 1, fy)))

    def _is_in_image_bounds(self, pos: QPoint) -> bool:
        """检查标签坐标是否在可见图片区域内。"""
        pix = self._display_pixmap
        if pix is None or pix.isNull():
            return False
        lw, lh = self._label.width(), self._label.height()
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            return False
        scale = min(lw / pw, lh / ph)
        img_w, img_h = int(pw * scale), int(ph * scale)
        ox, oy = (lw - img_w) // 2, (lh - img_h) // 2
        return ox <= pos.x() < ox + img_w and oy <= pos.y() < oy + img_h

    def _clamp_move_to_frame(self, r: dict):
        """移动 ROI 时，钳制左上角坐标使整个矩形不超出帧边界（禁止移出）。"""
        pix = self._display_pixmap
        if pix is None or pix.isNull():
            return
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            return
        w, h = r["w"], r["h"]
        r["x"] = max(0, min(pw - w, r["x"]))
        r["y"] = max(0, min(ph - h, r["y"]))

    def _get_region_at(self, pos: QPoint, margin: int = 6) -> Tuple[int, str]:
        pix = self._display_pixmap
        if pix is None or pix.isNull():
            return -1, ""
        lw, lh = self._label.width(), self._label.height()
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            return -1, ""
        scale = min(lw / pw, lh / ph)
        img_w, img_h = int(pw * scale), int(ph * scale)
        ox, oy = (lw - img_w) // 2, (lh - img_h) // 2

        def to_label(fx, fy):
            return QPoint(int(fx * img_w / pw) + ox, int(fy * img_h / ph) + oy)

        for i, r in enumerate(self._regions):
            p1 = to_label(r["x"], r["y"])
            p2 = to_label(r["x"] + r["w"], r["y"] + r["h"])
            x1, y1, x2, y2 = p1.x(), p1.y(), p2.x(), p2.y()
            px, py = pos.x(), pos.y()
            mx, my = (x1 + x2) // 2, (y1 + y2) // 2
            for corner, cx, cy in [("tl", x1, y1), ("tr", x2, y1),
                                   ("bl", x1, y2), ("br", x2, y2)]:
                if abs(px - cx) <= margin and abs(py - cy) <= margin:
                    return i, corner
            if abs(py - y1) <= margin and abs(px - mx) <= margin * 3:
                return i, "top"
            if abs(py - y2) <= margin and abs(px - mx) <= margin * 3:
                return i, "bottom"
            if abs(px - x1) <= margin and abs(py - my) <= margin * 3:
                return i, "left"
            if abs(px - x2) <= margin and abs(py - my) <= margin * 3:
                return i, "right"
            if x1 <= px <= x2 and y1 <= py <= y2:
                return i, "move"
        return -1, ""

    # ── 拖放 ──
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if p.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm',
                                       '.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus',
                                       '.png', '.jpg', '.jpeg', '.bmp')):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        if len(paths) > 1:
            # 多个文件 → 发送到批量队列
            self.files_dropped.emit(paths)
        elif len(paths) == 1:
            path = paths[0]
            ext = Path(path).suffix.lower()
            self._regions.clear()
            self._selected_region_index = -1
            from core.asr_engine import SUPPORTED_AUDIO_EXTS
            if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
                self.load_video(path)
            elif ext in SUPPORTED_AUDIO_EXTS:
                self._audio_dropped(path)
            elif ext in ('.png', '.jpg', '.jpeg', '.bmp'):
                self.load_image(path)

    # ── 粘贴（Ctrl+V） ──
    def keyPressEvent(self, event: QKeyEvent):
        """捕获 Ctrl+V 粘贴事件，将剪贴板中的文件路径加载到预览区。"""
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_V:
            clipboard = QApplication.clipboard()
            mime = clipboard.mimeData()
            if mime and mime.hasUrls():
                paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
                if paths:
                    from core.asr_engine import SUPPORTED_AUDIO_EXTS
                    valid_paths = [p for p in paths if Path(p).suffix.lower() in (
                        '.mp4', '.mkv', '.avi', '.mov', '.webm',
                        '.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus',
                        '.png', '.jpg', '.jpeg', '.bmp'
                    )]
                    if valid_paths:
                        self._handle_pasted_files(valid_paths)
                    return
        super().keyPressEvent(event)

    def _handle_pasted_files(self, paths: list):
        """处理粘贴的文件列表（与 dropEvent 行为一致）。"""
        from core.asr_engine import SUPPORTED_AUDIO_EXTS
        if len(paths) > 1:
            self.files_dropped.emit(paths)
        elif len(paths) == 1:
            path = paths[0]
            ext = Path(path).suffix.lower()
            self._regions.clear()
            self._selected_region_index = -1
            if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
                self.load_video(path)
            elif ext in SUPPORTED_AUDIO_EXTS:
                self._audio_dropped(path)
            elif ext in ('.png', '.jpg', '.jpeg', '.bmp'):
                self.load_image(path)

    def _audio_dropped(self, path: str):
        """拖放/粘贴音频文件时的处理。"""
        self._video_path = path
        self._is_image = False
        self._current_frame = None
        self._display_pixmap = None
        self._video_duration = 0.0
        self._label._placeholder_text = "🎵 已加载音频文件\n仅支持语音识别和纠错"
        self._label.update()
        self._time_range_widget.hide()
        self._play_bar_widget.show()
        self._update_preview_label()
        self.video_loaded.emit(path)

    def show_play_controls(self):
        """显示播放栏。"""
        self._play_bar_widget.show()

    # ── 鼠标 ──
    def _on_mouse_press(self, event: QMouseEvent):
        if self._display_pixmap is None or self._display_pixmap.isNull():
            return
        # 仅在图片可见区域内处理
        if not self._is_in_image_bounds(event.pos()):
            return
        self._start_point = event.pos()
        self._end_point = event.pos()
        idx, handle = self._get_region_at(event.pos())
        if idx >= 0:
            if handle == "move":
                self._moving_region_index = idx
                r = self._regions[idx]
                pix = self._display_pixmap
                lw, lh = self._label.width(), self._label.height()
                pw, ph = pix.width(), pix.height()
                scale = min(lw / pw, lh / ph)
                img_w, img_h = int(pw * scale), int(ph * scale)
                ox, oy = (lw - img_w) // 2, (lh - img_h) // 2
                p1 = QPoint(int(r["x"] * img_w / pw) + ox,
                            int(r["y"] * img_h / ph) + oy)
                self._drag_offset = event.pos() - p1
            else:
                self._resizing_region_index = idx
                self._resize_handle = handle
        else:
            self._drawing = True

    def _on_mouse_move(self, event: QMouseEvent):
        if self._display_pixmap is None or self._display_pixmap.isNull():
            return
        self._end_point = event.pos()
        if self._moving_region_index >= 0:
            r = self._regions[self._moving_region_index]
            new_pos = event.pos() - self._drag_offset
            fp = self._label_to_frame_coords(new_pos.x(), new_pos.y())
            r["x"], r["y"] = fp.x(), fp.y()
            # 禁止移出帧边界（ROI 停在边界上，不被强制缩小）
            self._clamp_move_to_frame(r)
            self._label.update()
            return
        if self._resizing_region_index >= 0:
            r = self._regions[self._resizing_region_index]
            pix = self._display_pixmap
            pw, ph = pix.width(), pix.height()
            # 钳制鼠标帧坐标到图片边界内
            fp = self._label_to_frame_coords(event.pos().x(), event.pos().y())
            fx, fy = max(0, min(pw - 1, fp.x())), max(0, min(ph - 1, fp.y()))
            h = self._resize_handle
            if "l" in h:
                r["x"] = max(0, min(fx, r["x"] + r["w"] - 5))
            if "r" in h:
                r["w"] = max(5, min(fx - r["x"], pw - r["x"]))
            if "t" in h:
                r["y"] = max(0, min(fy, r["y"] + r["h"] - 5))
            if "b" in h:
                r["h"] = max(5, min(fy - r["y"], ph - r["y"]))
            self._label.update()
            return
        if self._drawing:
            self._label.update()
            return
        idx, handle = self._get_region_at(event.pos())
        if handle in ("tl", "br"):
            self._label.setCursor(Qt.SizeFDiagCursor)
        elif handle in ("tr", "bl"):
            self._label.setCursor(Qt.SizeBDiagCursor)
        elif handle in ("left", "right"):
            self._label.setCursor(Qt.SizeHorCursor)
        elif handle in ("top", "bottom"):
            self._label.setCursor(Qt.SizeVerCursor)
        elif handle == "move":
            self._label.setCursor(Qt.SizeAllCursor)
        else:
            self._label.setCursor(Qt.CrossCursor)

    def _on_mouse_release(self, event: QMouseEvent):
        if self._drawing:
            self._drawing = False
            # 钳制起点/终点到图片可见区域内
            p1 = self._label_to_frame_coords(self._start_point.x(),
                                              self._start_point.y())
            p2 = self._label_to_frame_coords(self._end_point.x(),
                                              self._end_point.y())
            if p1.x() < 0 or p2.x() < 0:
                pass  # 超出图片区域，忽略
            else:
                x, y = min(p1.x(), p2.x()), min(p1.y(), p2.y())
                w, h = abs(p2.x() - p1.x()), abs(p2.y() - p1.y())
                if w > 5 and h > 5:
                    self.add_region(x, y, w, h)
        self._moving_region_index = -1
        self._resizing_region_index = -1
        self._resize_handle = ""
        self._label.setCursor(Qt.CrossCursor)
        self._label.update()

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._label.update()

    def closeEvent(self, event):
        """关闭预览控件 —— 强制终止所有子进程。"""
        print(f"[VideoPreview] 🧹 清理播放器/FFmpeg...")
        self._pause_video()
        # 强制杀死 ffplay 进程
        if self._player_proc:
            try:
                print(f"[VideoPreview]   ⏹ 杀死 ffplay (PID {self._player_proc.pid})")
                self._player_proc.kill()
                self._player_proc.wait(2)
            except Exception:
                pass
            self._player_proc = None
        # 关闭 FFmpeg 读取器
        if self._ffmpeg:
            try:
                self._ffmpeg.close()
            except Exception:
                pass
            self._ffmpeg = None
        # 停止播放定时器
        if getattr(self, '_play_timer', None):
            try:
                self._play_timer.stop()
            except Exception:
                pass
        print(f"[VideoPreview] ✅ 清理完成")
        super().closeEvent(event)
