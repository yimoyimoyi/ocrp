"""视频预览组件 —— 支持拖放视频帧、图片、鼠标拖动画矩形 ROI 区域。
QLabel 子类手动绘制 pixmap（保持宽高比居中）+ ROI 叠加层。
"""

import os
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.i18n import _
from core.logger import get_logger
from ui.theme_tokens import FONT_FAMILY

logger = get_logger(__name__)

# ── 覆盖层绘制常量（原为散落的字面量/重复颜色）──
OVERLAY_FONT_LARGE = 14  # 区域名标签
OVERLAY_FONT_SMALL = 10  # 区域计数/提示
OVERLAY_FONT_TINY = 9  # 小区域标签
OVERLAY_SUBTITLE_FONT_RATIO = 0.035  # 字幕字号 = min(宽,高) × 该比例
OVERLAY_SUBTITLE_FONT_MIN = 12

#: 区域缺省色（无 color 字段且调色板为空时使用）
DEFAULT_REGION_COLOR = QColor(0, 200, 100)
#: 缺省色的半透明填充（绘制选区）
DEFAULT_REGION_FILL = QColor(0, 200, 100, 30)
#: 调色板（按区域序轮转）
REGION_COLOR_POOL: tuple[QColor, ...] = (
    QColor(0, 200, 100),
    QColor(200, 100, 0),
    QColor(100, 100, 255),
    QColor(255, 200, 0),
    QColor(200, 0, 150),
    QColor(0, 200, 200),
)


def label_text_color(bg_color: QColor) -> QColor:
    """区域名标签文字颜色：按标签背景亮度自适应（R13 修复）。

    此前恒为白色——区域颜色为白色时白底白字完全不可见；
    浅色图片/亮色区域同理。亮度 > 140 判为亮底用深字，否则用白字。
    """
    if bg_color.lightness() > 140:
        return QColor(20, 20, 20)
    return QColor(255, 255, 255)


class ImageLoadWorker(QThread):
    """后台加载图片（文件读取 + 解码）。"""

    loaded = Signal(object, str)  # (np.ndarray, path)
    error = Signal()

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        img = _imread_unicode(self._path)
        if img is not None:
            self.loaded.emit(img.copy(), self._path)
        else:
            self.error.emit()


def _imread_unicode(path: str) -> np.ndarray | None:
    """Unicode 安全读取图片（np.fromfile + cv2.imdecode）。"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size > 0 else None
    except Exception as e:
        logger.warning("加载图片失败: %s", e)
        return None


class AudioLoadWorker(QThread):
    """后台加载音频（ffprobe 获取时长）。"""

    loaded = Signal(str, float)  # (path, duration)
    error = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            from core.ffmpeg_reader import _get_video_info

            info = _get_video_info(self._path)
            self.loaded.emit(self._path, info.get("duration", 0.0))
        except Exception as e:
            self.error.emit(str(e))


class _SeekResultBridge(QObject):
    """跨线程信号桥：后台 seek 线程将解码帧投递到主线程。"""

    frame_ready = Signal(object)  # np.ndarray


class _ClickableSlider(QSlider):
    """支持鼠标单点直接精准跳转的水平滑块。"""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            pos = event.position().x() if hasattr(event, "position") else event.x()
            w = max(1, self.width())
            ratio = max(0.0, min(1.0, pos / w))
            val = int(self.minimum() + ratio * (self.maximum() - self.minimum()))
            self.setValue(val)
            self.sliderMoved.emit(val)
            event.accept()
        super().mousePressEvent(event)


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
        self._pixmap_ref = lambda: None  # () -> Optional[QPixmap]
        self._regions_ref = lambda: []  # () -> List[dict]
        self._selected_ref = lambda: -1  # () -> int
        self._color_pool_ref = lambda: []  # () -> List[QColor]
        self._drawing_ref = lambda: False  # () -> bool
        self._start_point_ref = lambda: QPoint()
        self._end_point_ref = lambda: QPoint()
        self._subtitle_ref = lambda: ""  # () -> str
        self._placeholder_text = _(
            "拖放视频/图片文件到此处\n或 Ctrl+V 粘贴文件路径\n\nSpace 播放/暂停 · ← → 快进/退 5s · , . 逐帧 · [ ] 100ms · S 切换速度"
        )

    def set_refs(
        self, pixmap_fn, regions_fn, selected_fn, color_pool_fn, drawing_fn, start_fn, end_fn, subtitle_fn=None
    ):
        """设置对 VideoPreviewWidget 状态的引用。"""
        self._pixmap_ref = pixmap_fn
        self._regions_ref = regions_fn
        self._selected_ref = selected_fn
        self._color_pool_ref = color_pool_fn
        self._drawing_ref = drawing_fn
        self._start_point_ref = start_fn
        self._end_point_ref = end_fn
        self._subtitle_ref = subtitle_fn or (lambda: "")

    def paintEvent(self, event):
        """手动绘制：背景 → 居中 pixmap → ROI 矩形 → OSD 字幕叠加 → 拖拽预览。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        pix = self._pixmap_ref()
        lw, lh = self.width(), self.height()

        # 背景（渐变）
        from PySide6.QtGui import QLinearGradient

        grad = QLinearGradient(0, 0, 0, lh)
        grad.setColorAt(0, QColor(18, 18, 30))
        grad.setColorAt(1, QColor(8, 8, 16))
        painter.fillRect(0, 0, lw, lh, grad)

        if pix is None or pix.isNull():
            # 占位文字 — 主标题居中 + 副标题底部（防播放栏遮挡）
            painter.setPen(QColor(0x9A, 0xA0, 0xA6))
            painter.setFont(QFont(FONT_FAMILY, OVERLAY_FONT_LARGE))
            lines = self._placeholder_text.split("\n")
            main_text = "\n".join(lines[:2])
            painter.drawText(QRect(0, 0, lw, lh - 56), Qt.AlignCenter, main_text)
            if len(lines) > 2:
                painter.setFont(QFont(FONT_FAMILY, OVERLAY_FONT_SMALL))
                painter.setPen(QColor(0x9A, 0xA0, 0xA6, 120))
                hint_text = "\n".join(lines[2:])
                painter.drawText(QRect(0, lh - 60, lw, 50), Qt.AlignCenter, hint_text)
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

            color = r.get("color", color_pool[i % len(color_pool)] if color_pool else DEFAULT_REGION_COLOR)

            # 半透明填充
            fill = QColor(color)
            fill.setAlpha(25 if i == selected else 15)
            painter.fillRect(rx, ry, rw, rh, fill)

            # 边框（选中区域加粗虚线）
            pen = QPen(color, 2.5 if i == selected else 1.5)
            if i == selected:
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawRoundedRect(rx, ry, rw, rh, 3, 3)

            # 绘制 8 个显式控制手柄与尺寸标签（仅选中区域）
            if i == selected:
                painter.save()
                handle_size = 7
                half = handle_size // 2
                painter.setPen(QPen(QColor(30, 30, 30), 1))
                painter.setBrush(QColor(255, 255, 255))
                xm = rx + rw // 2
                ym = ry + rh // 2
                points = [
                    (rx, ry),
                    (xm, ry),
                    (rx + rw, ry),
                    (rx + rw, ym),
                    (rx + rw, ry + rh),
                    (xm, ry + rh),
                    (rx, ry + rh),
                    (rx, ym),
                ]
                for px, py in points:
                    painter.drawRect(px - half, py - half, handle_size, handle_size)

                # 绘制当前 ROI 尺寸与坐标指示标签
                coord_text = f"X:{fx} Y:{fy}  {fw}×{fh}"
                painter.setFont(QFont("Consolas", 8, QFont.Bold))
                fm = painter.fontMetrics()
                cw = fm.horizontalAdvance(coord_text) + 8
                ch = fm.height() + 4
                cx = rx + rw - cw
                cy = ry + rh + 4 if ry + rh + ch + 4 < lh else ry - ch - 4
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(20, 20, 20, 200))
                painter.drawRoundedRect(cx, cy, cw, ch, 3, 3)
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(cx + 4, cy + fm.ascent() + 2, coord_text)
                painter.restore()

            # 区域名标签（带背景）
            name = r.get("name", "")
            if name:
                painter.setFont(QFont(FONT_FAMILY, OVERLAY_FONT_TINY, QFont.Bold))
                fm = painter.fontMetrics()
                tw = fm.horizontalAdvance(name) + 10
                th = fm.height() + 4
                lx = rx + 1
                ly = ry - th - 1 if ry > th + 2 else ry + rh + 1
                # 标签背景：选中区域加深（与边框虚线 + 填充加深共同标记选中态）
                lbl_bg = QColor(color)
                lbl_bg.setAlpha(225 if i == selected else 180)
                painter.setPen(Qt.NoPen)
                painter.setBrush(lbl_bg)
                painter.drawRoundedRect(lx, ly, tw, th, 3, 3)
                # 标签文字：按背景亮度自适应（R13：白色区域此前白底白字不可见）
                painter.setPen(label_text_color(lbl_bg))
                painter.setBrush(Qt.NoBrush)
                painter.drawText(lx + 5, ly + fm.ascent() + 2, name)

        # 绘制 OSD 画面字幕叠加层（Subtitle Overlay）
        sub_text = self._subtitle_ref() if hasattr(self, "_subtitle_ref") and self._subtitle_ref else ""
        if sub_text:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            font_size = max(OVERLAY_SUBTITLE_FONT_MIN, int(min(lw, lh) * OVERLAY_SUBTITLE_FONT_RATIO))
            sub_font = QFont(FONT_FAMILY, font_size, QFont.Bold)
            painter.setFont(sub_font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(sub_text) + 24
            th = fm.height() + 10
            sub_w = min(tw, img_w - 20)
            sub_x = ox + (img_w - sub_w) // 2
            sub_y = oy + img_h - th - max(10, int(img_h * 0.05))
            sub_rect = QRect(sub_x, sub_y, sub_w, th)

            # 半透明黑色背板
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 165))
            painter.drawRoundedRect(sub_rect, 6, 6)

            # 居中文本
            painter.setPen(QColor(255, 255, 255))
            elided = fm.elidedText(sub_text, Qt.ElideRight, sub_w - 16)
            painter.drawText(sub_rect, Qt.AlignCenter, elided)
            painter.restore()

        # 拖拽中的矩形预览（带尺寸提示）
        if self._drawing_ref():
            sp = self._start_point_ref()
            ep = self._end_point_ref()
            x = min(sp.x(), ep.x())
            y = min(sp.y(), ep.y())
            w = abs(ep.x() - sp.x())
            h = abs(ep.y() - sp.y())
            # 半透明预览填充
            painter.fillRect(x, y, w, h, DEFAULT_REGION_FILL)
            painter.setPen(QPen(DEFAULT_REGION_COLOR, 1.5, Qt.DashLine))
            painter.drawRect(x, y, w, h)
            # 尺寸提示
            if w > 20 and h > 10:
                painter.setPen(QColor(200, 200, 200, 180))
                painter.setFont(QFont("Consolas", 8))
                painter.drawText(x + 4, y + h - 4, f"{w}×{h}")

        painter.end()


class VideoPreviewWidget(QWidget):
    video_loaded = Signal(str)
    frame_captured = Signal(object)
    position_changed = Signal(float)
    regions_changed = Signal(list)
    files_dropped = Signal(list)  # 拖放多个文件到队列

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)  # 允许接收键盘事件（Ctrl+V 粘贴）
        self.setMinimumSize(320, 240)
        self._video_path: str | None = None
        self._ffmpeg: object = None
        self._player = None  # FFmpegPlayer 实例（替代 ffplay 子进程）
        self._hw_accel: bool = False
        self._current_frame: np.ndarray | None = None
        self._display_pixmap: QPixmap | None = None
        self._is_image: bool = False
        self._subtitle_overlay_text = ""
        self._regions: list[dict] = []
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
        self._time_start: float = 0.0
        self._time_end: float = 0.0
        self._resize_handle = ""
        self._drag_offset = QPoint()
        self._color_pool = list(REGION_COLOR_POOL)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 使用子类 _PreviewLabel，手动控制 pixmap 绘制 + ROI 叠加
        self._label = _PreviewLabel(self)
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
            lambda: getattr(self, "_subtitle_overlay_text", ""),
        )
        self._label.mousePressEvent = self._on_mouse_press
        self._label.mouseMoveEvent = self._on_mouse_move
        self._label.mouseReleaseEvent = self._on_mouse_release
        layout.addWidget(self._label)

        # 时间选择器
        tr = QHBoxLayout()
        tr.setSpacing(4)
        tr.addWidget(QLabel(_("开始:")))
        self._time_start_label = QLabel("00:00")
        tr.addWidget(self._time_start_label)
        self._timeline_start = QSlider(Qt.Horizontal)
        self._timeline_start.setRange(0, 0)
        self._timeline_start.sliderReleased.connect(self._on_timeline_seek_start)
        tr.addWidget(self._timeline_start, 1)
        tr.addWidget(QLabel(_("结束:")))
        self._time_end_label = QLabel("00:00")
        tr.addWidget(self._time_end_label)
        self._timeline_end = QSlider(Qt.Horizontal)
        self._timeline_end.setRange(0, 0)
        self._timeline_end.sliderReleased.connect(self._on_timeline_seek_end)
        tr.addWidget(self._timeline_end, 1)
        self._time_range_widget = QWidget(self)
        self._time_range_widget.setLayout(tr)
        self._time_range_widget.hide()
        layout.addWidget(self._time_range_widget)

        # ── 现代播放控件 ──
        play_bar = QHBoxLayout()
        play_bar.setSpacing(3)
        play_bar.setContentsMargins(6, 4, 6, 4)

        # 后退 5 秒
        self._btn_back5 = QPushButton("⏪")
        self._btn_back5.setToolTip(_("后退 5 秒 (←)"))
        self._btn_back5.setObjectName("btnPlayerCtrl")
        self._btn_back5.setFixedWidth(32)
        self._btn_back5.clicked.connect(lambda: self._skip(-5))
        play_bar.addWidget(self._btn_back5)

        # 播放/暂停
        self._btn_play = QPushButton("▶")
        self._btn_play.setToolTip(_("播放/暂停 (Space)"))
        self._btn_play.setObjectName("btnPlay")
        self._btn_play.setFixedWidth(36)
        self._btn_play.clicked.connect(self._on_play_pause)
        play_bar.addWidget(self._btn_play)

        # 前进 5 秒
        self._btn_fwd5 = QPushButton("⏩")
        self._btn_fwd5.setToolTip(_("前进 5 秒 (→)"))
        self._btn_fwd5.setObjectName("btnPlayerCtrl")
        self._btn_fwd5.setFixedWidth(32)
        self._btn_fwd5.clicked.connect(lambda: self._skip(5))
        play_bar.addWidget(self._btn_fwd5)

        # 停止
        self._btn_stop_play = QPushButton("⏹")
        self._btn_stop_play.setToolTip(_("停止播放"))
        self._btn_stop_play.setObjectName("btnStopPlay")
        self._btn_stop_play.setFixedWidth(32)
        self._btn_stop_play.clicked.connect(self._on_stop_playback)
        play_bar.addWidget(self._btn_stop_play)

        play_bar.addSpacing(8)

        # 进度条（支持单击精准直接跳转）
        self._preview_slider = _ClickableSlider(Qt.Horizontal)
        self._preview_slider.setObjectName("previewSlider")
        self._preview_slider.setRange(0, 0)
        self._preview_slider.setTracking(True)
        self._preview_slider.sliderPressed.connect(self._on_preview_slider_press)
        self._preview_slider.sliderMoved.connect(self._on_preview_slider_move)
        self._preview_slider.sliderReleased.connect(self._on_preview_slider_release)
        play_bar.addWidget(self._preview_slider, 1)

        # 时间标签
        self._preview_time_label = QLabel("00:00.0 / 00:00")
        self._preview_time_label.setObjectName("previewTimeLabel")
        play_bar.addWidget(self._preview_time_label)

        play_bar.addSpacing(4)

        # 速度切换按钮
        self._btn_speed = QPushButton("1.0x")
        self._btn_speed.setObjectName("btnSpeed")
        self._btn_speed.setFixedWidth(42)
        self._btn_speed.setToolTip(_("播放速度：点击切换"))
        self._btn_speed.clicked.connect(self._on_cycle_speed)
        play_bar.addWidget(self._btn_speed)

        self._play_bar_widget = QWidget(self)
        self._play_bar_widget.setObjectName("playBarWidget")
        self._play_bar_widget.setLayout(play_bar)
        self._play_bar_widget.hide()
        layout.addWidget(self._play_bar_widget)

        # 播放状态
        self._is_playing = False
        self._current_position: float = 0.0
        self._video_duration: float = 0.0
        self._slider_dragging = False
        self._is_audio: bool = False
        self._audio_seek_pending: bool = False  # 标记音频正在 seek，忽略初始位置更新

        # 拖拽防抖定时器（实时预览帧）
        self._drag_seek_timer = QTimer()
        self._drag_seek_timer.setSingleShot(True)
        self._drag_seek_timer.setInterval(80)  # 80ms 防抖
        self._drag_seek_timer.timeout.connect(self._on_drag_seek)

        # 音频播放（QMediaPlayer 提供真实音频输出）
        self._audio_player: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None  # Qt6 必须显式音频输出，否则无声
        self._audio_speed: float = 1.0
        self._audio_speed_idx: int = 3

    # ── 属性 ──
    @property
    def regions(self) -> list:
        return list(self._regions)

    @regions.setter
    def regions(self, val: list):
        self._regions = list(val)
        self._label.update()

    @property
    def current_frame(self) -> np.ndarray | None:
        return self._current_frame

    @property
    def video_path(self) -> str | None:
        return self._video_path

    @property
    def audio_cache_path(self) -> str | None:
        """返回缓存的完整音频 WAV 路径（16000Hz mono），供 ASR 复用。"""
        if hasattr(self, "_audio_temp") and self._audio_temp:
            return self._audio_temp
        return None

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

    def clear(self):
        """清空预览区：停止播放、释放 FFmpeg、重置状态、显示占位提示。"""
        self._on_stop_playback()
        if self._ffmpeg:
            try:
                self._ffmpeg.close()
            except Exception as e:
                logger.debug("FFmpeg 关闭异常: %s", e)
            self._ffmpeg = None
        self._display_pixmap = None
        self._current_frame = None
        self._video_path = None
        self._is_image = False
        self._is_audio = False
        self._label._placeholder_text = _(
            "拖放视频文件到此处\n或点击「打开视频/图片」加载文件\n\nSpace 播放/暂停 · ← → 快进/退 5s · S 切换速度"
        )
        self._label.update()

    def load_video(self, path: str):
        """异步加载视频：ffprobe + Popen + 首帧读取在后台线程执行。"""
        self._is_audio = False
        if self._ffmpeg:
            try:
                self._ffmpeg.close()
            except Exception as e:
                logger.debug("FFmpeg 关闭异常: %s", e)
            self._ffmpeg = None

        from core.workers import VideoLoadWorker

        self._load_worker = VideoLoadWorker(path, self._hw_accel)
        self._load_worker.loaded.connect(self._on_video_loaded)
        self._load_worker.error.connect(self._on_video_load_error)
        self._load_worker.start()
        # UI 进入加载状态
        self._label._placeholder_text = _("⏳ 加载中...")
        self._label.update()

    def _cleanup_audio_temp(self):
        """清理预提取的音频临时文件。"""
        if hasattr(self, "_audio_temp") and self._audio_temp:
            try:
                os.unlink(self._audio_temp)
            except OSError:
                pass
            self._audio_temp = None

    def _on_video_loaded(self, frame, info):
        """视频加载完成（在主线程执行）。"""
        self._cleanup_audio_temp()
        self._video_path = self._load_worker._path
        self._is_image = False
        self._regions.clear()
        self._selected_region_index = -1
        self._ffmpeg = info["reader"]
        self._video_duration = info["duration"]
        self._video_fps = info.get("fps", 25.0) or 25.0
        # 缓存预提取的音频（加载阶段已完成，播放时直接可用）
        self._audio_temp = info.get("audio_path")
        self._current_frame = frame.copy()
        self._display_frame(self._current_frame)
        if self._video_duration > 0:
            max_t = int(self._video_duration * 100)
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
            self._init_player()
        self._label._placeholder_text = ""
        self._label.update()
        self.video_loaded.emit(self._video_path)

    def _on_video_load_error(self, msg):
        """视频加载失败（在主线程执行）。"""
        logger.error("视频加载失败: %s", msg)
        self._label._placeholder_text = _("❌ 加载失败: {msg}\n拖放视频文件到此处").format(msg=msg)
        self._label.update()
        self._load_worker = None

    def load_image(self, path: str):
        """异步加载图片：文件读取和解码在后台 QThread 执行。"""
        self._is_audio = False
        if self._ffmpeg:
            try:
                self._ffmpeg.close()
            except Exception as e:
                logger.debug("FFmpeg 关闭异常: %s", e)
            self._ffmpeg = None
        self._video_path = path
        self._is_image = True
        self._video_duration = 0
        self._time_range_widget.hide()
        self._play_bar_widget.hide()

        self._image_load_worker = ImageLoadWorker(path)
        self._image_load_worker.loaded.connect(self._on_image_loaded)
        self._image_load_worker.error.connect(self._on_image_load_error)
        self._image_load_worker.start()

        self._label._placeholder_text = _("⏳ 加载中...")
        self._label.update()

    def _on_image_loaded(self, img, path):
        self._current_frame = img
        self._display_frame(self._current_frame)
        self._label._placeholder_text = ""
        self._label.update()
        self._regions.clear()
        self._selected_region_index = -1
        self.video_loaded.emit(path)

    def _on_image_load_error(self):
        self._current_frame = None
        self._display_pixmap = None
        self._label._placeholder_text = _("❌ 无法打开图片\n拖放文件到此处")
        self._label.update()

    def load_audio(self, path: str):
        """异步加载纯音频文件 —— ffprobe 在后台 QThread 执行。"""
        self._cleanup_audio_temp()
        if self._ffmpeg:
            try:
                self._ffmpeg.close()
            except Exception as e:
                logger.debug("FFmpeg 关闭异常: %s", e)
            self._ffmpeg = None
        self._video_path = path
        self._is_image = False
        self._is_audio = True
        self._current_frame = None
        self._display_pixmap = None
        self._current_position = 0.0
        self._player = None
        self._label.update()
        self._time_range_widget.hide()

        self._audio_load_worker = AudioLoadWorker(path)
        self._audio_load_worker.loaded.connect(self._on_audio_loaded)
        self._audio_load_worker.error.connect(self._on_audio_load_error)
        self._audio_load_worker.start()

        self._label._placeholder_text = _("⏳ 加载中...")
        self._label.update()

    def _on_audio_loaded(self, path: str, duration: float):
        """音频加载完成（在主线程执行）。"""
        self._video_duration = duration
        self._preview_slider.setRange(0, max(1, int(duration * 100)))
        self.video_loaded.emit(path)
        self._label._placeholder_text = _("🎵 已加载音频文件\n仅支持语音识别和纠错")
        self._label.update()
        self.show_play_controls()

    def _on_audio_load_error(self, msg: str):
        """音频加载失败（在主线程执行）。"""
        logger.error("音频加载失败: %s", msg)
        self._label._placeholder_text = _("❌ 加载失败: {msg}\n拖放文件到此处").format(msg=msg)
        self._label.update()

    def capture_test_frame(self, image: np.ndarray = None):
        if image is not None:
            self._current_frame = image.copy()
        elif self._ffmpeg and hasattr(self._ffmpeg, "is_opened") and self._ffmpeg.is_opened():
            frame = self._ffmpeg.read()
            if frame is not None and frame.size > 0:
                self._current_frame = frame.copy()
        if self._current_frame is not None:
            self._display_frame(self._current_frame)
            self.frame_captured.emit(self._current_frame)

    def set_subtitle_overlay(self, text: str):
        """设置当前在视频预览画面上叠加渲染的字幕文字。"""
        if getattr(self, "_subtitle_overlay_text", "") != text:
            self._subtitle_overlay_text = text
            self._label.update()

    def seek_to(self, position_sec: float):
        """异步 seek：立即更新 UI，后台线程解码帧。"""
        # 立即更新滑块和标签（不等待帧）
        val = int(position_sec * 100)
        if 0 <= val <= self._preview_slider.maximum():
            self._preview_slider.blockSignals(True)
            self._preview_slider.setValue(val)
            self._preview_slider.blockSignals(False)
        self._current_position = position_sec
        self._update_preview_label()
        self.position_changed.emit(position_sec)

        # 后台线程执行 seek + 解码，不阻塞 UI
        self._seek_target = position_sec
        QTimer.singleShot(20, self._do_seek)

    def _do_seek(self):
        """执行实际的 seek 操作（由 QTimer 触发，在主线程）。"""
        target = getattr(self, "_seek_target", None)
        if target is None:
            return
        if abs(self._current_position - target) > 0.05:
            return
        ff = self._ffmpeg
        if ff and hasattr(ff, "is_opened") and ff.is_opened():
            import threading

            if not hasattr(self, "_seek_lock"):
                self._seek_lock = threading.Lock()

            self._seek_seq = getattr(self, "_seek_seq", 0) + 1
            current_seq = self._seek_seq

            bridge = _SeekResultBridge()

            def _on_frame(frame, seq=current_seq):
                if seq == getattr(self, "_seek_seq", 0):
                    self._apply_seek_frame(frame)

            bridge.frame_ready.connect(_on_frame)

            def _seek_and_update():
                with self._seek_lock:
                    if current_seq != getattr(self, "_seek_seq", 0):
                        return
                    try:
                        frame = ff.seek_sec(target)
                        if frame is not None and frame.size > 0:
                            bridge.frame_ready.emit(frame.copy())
                    except Exception as e:
                        logger.warning("后台 seek 失败: %s", e)

            threading.Thread(target=_seek_and_update, daemon=True).start()

    def _apply_seek_frame(self, frame):
        """在主线程应用 seek 结果帧。"""
        self._current_frame = frame
        self._display_frame(frame)

    def _init_player(self):
        """初始化 FFmpegPlayer（延迟到 load_video 时调用）。"""
        if self._player:
            self._player.stop()
        from core.ffmpeg_player import FFmpegPlayer

        info = {"duration": self._video_duration}
        self._player = FFmpegPlayer(self._video_path, hw_accel=self._hw_accel, video_info=info)
        self._player.frame_callback = self._on_player_frame
        self._player.finished_callback = self._on_player_finished
        self._player.error_callback = self._on_player_error

    def _on_play_pause(self):
        """播放/暂停切换。"""
        if not self._video_path:
            return
        if self._is_playing:
            if self._player:
                self._player.pause()
            if self._audio_player:
                self._audio_player.pause()
                self._is_playing = False
            self._btn_play.setText("▶")
        else:
            if self._current_position >= self._video_duration - 0.1:
                self._current_position = 0.0
            if self._player:
                self._player.play(self._current_position)
                self._start_video_audio()
            elif self._is_audio:
                self._start_audio_playback()
            self._is_playing = True
            self._btn_play.setText("⏸")

    def _ensure_audio_player(self):
        """创建 QMediaPlayer + QAudioOutput（Qt6 必须显式音频输出，否则无声）。"""
        if self._audio_player is None:
            self._audio_player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._audio_player.setAudioOutput(self._audio_output)
            self._audio_player.positionChanged.connect(self._on_audio_position)
            self._audio_player.durationChanged.connect(self._on_audio_duration)
            self._audio_player.playbackStateChanged.connect(self._on_audio_state)
            self._audio_player.errorOccurred.connect(self._on_video_audio_error)

    def _on_audio_position(self, pos_ms: int):
        if getattr(self, "_audio_seek_pending", False) or self._slider_dragging:
            return
        if self._is_audio:
            sec = pos_ms / 1000.0
            self._current_position = sec
            self._set_slider(sec)
            self._update_preview_label()
            self.position_changed.emit(sec)

    def _on_audio_duration(self, dur_ms: int):
        if dur_ms > 0 and self._is_audio:
            dur_sec = dur_ms / 1000.0
            self._video_duration = dur_sec
            self._preview_slider.setRange(0, int(dur_sec * 100))
            self._update_preview_label()

    def _on_audio_state(self, state):
        if self._is_audio and state == QMediaPlayer.PlaybackState.StoppedState:
            self._on_player_finished()

    def _on_video_audio_error(self, error, error_string: str = ""):
        logger.warning("音频播放错误: %s (%s)", error, error_string)

    def _play_audio_source(self, url: QUrl):
        """加载并播放音频源。

        Qt6 的 setSource 为异步加载，立即 setPosition 可能被忽略（从 0 播放），
        因此监听 mediaStatusChanged(LoadedMedia) 完成定位后再继续。
        """
        # 标记正在 seek，忽略初始的位置更新
        self._audio_seek_pending = True
        self._audio_player.setSource(url)
        self._audio_player.play()
        self._audio_player.mediaStatusChanged.connect(self._on_audio_status_for_seek)
        # 延迟重置标记，给 QMediaPlayer 时间完成 seek
        QTimer.singleShot(200, lambda: setattr(self, "_audio_seek_pending", False))

    def _on_audio_status_for_seek(self, status):
        """媒体源加载完成后定位到当前播放位置（一次性）。"""
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._audio_player.setPosition(int(self._current_position * 1000))
            try:
                self._audio_player.mediaStatusChanged.disconnect(self._on_audio_status_for_seek)
            except (TypeError, RuntimeError):
                pass

    def _start_audio_playback(self):
        """使用 QMediaPlayer 播放音频文件（真实音频输出）。"""
        self._ensure_audio_player()
        self._play_audio_source(QUrl.fromLocalFile(self._video_path))

    def _start_video_audio(self):
        """启动视频音频播放（音频已在加载阶段预提取）。"""
        self._ensure_audio_player()
        # 检查音频是否已预提取
        if not self._audio_temp:
            return  # 无音频流（静音播放）
        self._play_audio_source(QUrl.fromLocalFile(self._audio_temp))

    def stop_playback(self, reset_position: bool = True):
        """停止播放。

        ``reset_position=False`` 用于"先停止、再跳到指定时间"的场景（点击/编辑结果
        行跳转）。此时**不能**回到起点：``reset_position=True`` 分支里的
        ``seek_to(0.0)`` 会 emit ``position_changed(0.0)``，结果表据此
        ``sync_play_position`` 先高亮/滚动到覆盖 0 秒的首行 —— 用户看到的就是
        "跳转到结果列表初始行"（若调用方随后忘记再 emit 目标时间，就会永久停在首行）。
        """
        if self._player:
            self._player.stop()
        if self._audio_player:
            self._audio_player.stop()
        self._is_playing = False
        self._btn_play.setText("▶")
        if reset_position:
            self._current_position = 0.0
            self._update_preview_label()
            self._set_slider(0.0)
            self.seek_to(0.0)

    def _on_stop_playback(self):
        """停止播放并回到起点（停止按钮语义）。"""
        self.stop_playback(reset_position=True)

    def _skip(self, delta_sec: float):
        """前进/后退指定秒数。"""
        new_pos = max(0.0, min(self._video_duration, self._current_position + delta_sec))
        self._current_position = new_pos
        if self._player and self._is_playing:
            self._player.seek(new_pos)
            if self._audio_player:
                self._audio_player.setPosition(int(new_pos * 1000))
        elif self._audio_player:
            self._audio_player.setPosition(int(new_pos * 1000))
        elif not self._is_audio:
            self.seek_to(new_pos)
        self._update_preview_label()
        self._set_slider(new_pos)

    def _on_cycle_speed(self):
        """循环切换播放速度。"""
        if self._player:
            spd = self._player.cycle_speed()
            if self._audio_player:
                self._audio_player.setPlaybackRate(spd)
        elif self._is_audio:
            spd = self._cycle_audio_speed()
            if self._audio_player:
                self._audio_player.setPlaybackRate(spd)
        else:
            return
        self._btn_speed.setText(f"{spd:g}x")

    def _cycle_audio_speed(self) -> float:
        AUDIO_SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
        self._audio_speed_idx = (self._audio_speed_idx + 1) % len(AUDIO_SPEEDS)
        self._audio_speed = AUDIO_SPEEDS[self._audio_speed_idx]
        return self._audio_speed

    def _on_player_frame(self, frame: np.ndarray, timestamp: float):
        """播放器帧回调（在主线程执行）。"""
        if self._slider_dragging:
            return
        self._current_frame = frame.copy()
        self._current_position = timestamp
        self._display_frame(self._current_frame)
        self._set_slider(timestamp)
        self._update_preview_label()
        self.position_changed.emit(timestamp)

    def _on_player_finished(self):
        """播放器自然结束。"""
        self._is_playing = False
        if self._audio_player:
            self._audio_player.stop()
        self._btn_play.setText("▶")
        self._current_position = self._video_duration
        self._update_preview_label()
        self._set_slider(self._video_duration)

    def _on_player_error(self, msg: str):
        logger.error("播放器错误: %s", msg)
        self._is_playing = False
        self._btn_play.setText("▶")

    def _set_slider(self, seconds: float):
        """安全设置滑块位置。"""
        val = int(seconds * 100)
        if 0 <= val <= self._preview_slider.maximum():
            self._preview_slider.blockSignals(True)
            self._preview_slider.setValue(val)
            self._preview_slider.blockSignals(False)

    def _on_preview_slider_press(self):
        self._slider_dragging = True
        if self._player and self._is_playing:
            self._player.pause()

    def _on_preview_slider_move(self, val: int):
        if self._video_duration <= 0:
            return
        self._current_position = val / 100.0
        self._update_preview_label()
        # 防抖：拖拽过程中实时预览帧
        self._drag_seek_timer.start()

    def _on_drag_seek(self):
        """防抖回调：拖拽过程中 seek 到当前位置并显示帧。"""
        if self._slider_dragging:
            self.seek_to(self._current_position)

    def _on_preview_slider_release(self):
        self._slider_dragging = False
        self._drag_seek_timer.stop()
        if self._video_duration <= 0:
            return
        self._current_position = self._preview_slider.value() / 100.0
        self._update_preview_label()
        if self._player and self._is_playing:
            self._player.seek(self._current_position)
            self._player.resume()
            if self._audio_player:
                self._audio_player.setPosition(int(self._current_position * 1000))
        else:
            self.seek_to(self._current_position)

    def _update_preview_label(self):
        pos = max(0.0, self._current_position)
        dur = max(0.0, self._video_duration)
        h1 = int(pos // 3600)
        m1 = int((pos % 3600) // 60)
        s1 = int(pos % 60)
        ms1 = int((pos - int(pos)) * 1000)

        h2 = int(dur // 3600)
        m2 = int((dur % 3600) // 60)
        s2 = int(dur % 60)
        ms2 = int((dur - int(dur)) * 1000)

        if h2 > 0 or h1 > 0:
            pos_str = f"{h1:02d}:{m1:02d}:{s1:02d}.{ms1:03d}"
            dur_str = f"{h2:02d}:{m2:02d}:{s2:02d}.{ms2:03d}"
        else:
            pos_str = f"{m1:02d}:{s1:02d}.{ms1:03d}"
            dur_str = f"{m2:02d}:{s2:02d}.{ms2:03d}"
        self._preview_time_label.setText(f"{pos_str} / {dur_str}")

    def _on_timeline_seek_start(self):
        self._time_start = self._timeline_start.value() / 100.0
        if self._timeline_start.value() > self._timeline_end.value():
            self._timeline_end.setValue(self._timeline_start.value())
            self._time_end = self._time_start
        self._update_time_labels()
        self.seek_to(self._time_start)

    def _on_timeline_seek_end(self):
        self._time_end = self._timeline_end.value() / 100.0
        if self._timeline_end.value() < self._timeline_start.value():
            self._timeline_start.setValue(self._timeline_end.value())
            self._time_start = self._time_end
        self._update_time_labels()
        self.seek_to(self._time_end)

    def _update_time_labels(self):
        for v, lb in [(self._time_start, self._time_start_label), (self._time_end, self._time_end_label)]:
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
            name = _("区域{idx}").format(idx=self._region_counter)
        color = self._color_pool[len(self._regions) % len(self._color_pool)]
        r = {
            "name": name,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "color": color,
            "engine": self._default_engine,
            "prompt": self._default_prompt,
            "template": self._default_template,
        }
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

    def get_roi_image(self, region_index: int) -> np.ndarray | None:
        if self._current_frame is None:
            return None
        if 0 <= region_index < len(self._regions):
            r = self._regions[region_index]
            x, y, w, h = r["x"], r["y"], r["w"], r["h"]
            if w > 0 and h > 0:
                return self._current_frame[y : y + h, x : x + w].copy()
        return self._current_frame

    def _display_frame(self, frame: np.ndarray):
        h, w, ch = frame.shape
        if ch == 3:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            rgb = frame
        self._display_pixmap = QPixmap.fromImage(QImage(rgb.tobytes(), w, h, ch * w, QImage.Format_RGB888))
        self._label.update()

    def _get_image_display_rect(self):
        """返回 (img_w, img_h, offset_x, offset_y) 或 None（无有效图像时）。"""
        pix = self._display_pixmap
        if pix is None or pix.isNull():
            return None
        lw, lh = self._label.width(), self._label.height()
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            return None
        scale = min(lw / pw, lh / ph)
        img_w, img_h = int(pw * scale), int(ph * scale)
        ox, oy = (lw - img_w) // 2, (lh - img_h) // 2
        return img_w, img_h, ox, oy

    def _label_to_frame_coords(self, label_x: int, label_y: int) -> QPoint:
        r = self._get_image_display_rect()
        if r is None:
            return QPoint(-1, -1)
        img_w, img_h, ox, oy = r
        pw, ph = self._display_pixmap.width(), self._display_pixmap.height()
        # 钳制到图片可见区域内
        cx = max(ox, min(ox + img_w - 1, label_x))
        cy = max(oy, min(oy + img_h - 1, label_y))
        fx = int((cx - ox) * pw / img_w)
        fy = int((cy - oy) * ph / img_h)
        return QPoint(max(0, min(pw - 1, fx)), max(0, min(ph - 1, fy)))

    def _is_in_image_bounds(self, pos: QPoint) -> bool:
        """检查标签坐标是否在可见图片区域内。"""
        r = self._get_image_display_rect()
        if r is None:
            return False
        img_w, img_h, ox, oy = r
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

    def _apply_edge_snapping(self, r: dict, snap_threshold: int = 6):
        """当 ROI 边缘距离图像边界小于阈值时自动吸附。"""
        pix = self._display_pixmap
        if pix is None or pix.isNull():
            return
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            return

        # 左/顶吸附
        if r["x"] <= snap_threshold:
            r["w"] += r["x"]
            r["x"] = 0
        if r["y"] <= snap_threshold:
            r["h"] += r["y"]
            r["y"] = 0
        # 右/底吸附
        if abs((r["x"] + r["w"]) - pw) <= snap_threshold:
            r["w"] = pw - r["x"]
        if abs((r["y"] + r["h"]) - ph) <= snap_threshold:
            r["h"] = ph - r["y"]

    def _get_region_at(self, pos: QPoint, margin: int = 8) -> tuple[int, str]:
        r = self._get_image_display_rect()
        if r is None:
            return -1, ""
        img_w, img_h, ox, oy = r
        pw, ph = self._display_pixmap.width(), self._display_pixmap.height()

        def to_label(fx, fy):
            return QPoint(int(fx * img_w / pw) + ox, int(fy * img_h / ph) + oy)

        # 优先检测当前选中区域的 8 个手柄
        selected = self._selected_region_index
        indices = [selected] if 0 <= selected < len(self._regions) else []
        indices += [i for i in range(len(self._regions)) if i != selected]

        for i in indices:
            reg = self._regions[i]
            p1 = to_label(reg["x"], reg["y"])
            p2 = to_label(reg["x"] + reg["w"], reg["y"] + reg["h"])
            x1, y1, x2, y2 = p1.x(), p1.y(), p2.x(), p2.y()
            xm, ym = (x1 + x2) // 2, (y1 + y2) // 2
            px, py = pos.x(), pos.y()

            # 1) 8 个手柄点检测（四角 + 四边中点）
            handles = [
                ("tl", x1, y1),
                ("top", xm, y1),
                ("tr", x2, y1),
                ("right", x2, ym),
                ("br", x2, y2),
                ("bottom", xm, y2),
                ("bl", x1, y2),
                ("left", x1, ym),
            ]
            for handle_name, hx, hy in handles:
                if abs(px - hx) <= margin and abs(py - hy) <= margin:
                    return i, handle_name

            # 2) 四条边检测：沿整条边的 full-width 检测
            if abs(py - y1) <= margin and x1 - margin <= px <= x2 + margin:
                return i, "top"
            if abs(py - y2) <= margin and x1 - margin <= px <= x2 + margin:
                return i, "bottom"
            if abs(px - x1) <= margin and y1 - margin <= py <= y2 + margin:
                return i, "left"
            if abs(px - x2) <= margin and y1 - margin <= py <= y2 + margin:
                return i, "right"

            # 3) 内部 → 移动
            if x1 <= px <= x2 and y1 <= py <= y2:
                return i, "move"
        return -1, ""

    # ── 拖放 ──
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if p.lower().endswith(
                    (
                        ".mp4",
                        ".mkv",
                        ".avi",
                        ".mov",
                        ".webm",
                        ".mp3",
                        ".wav",
                        ".flac",
                        ".ogg",
                        ".m4a",
                        ".aac",
                        ".wma",
                        ".opus",
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".bmp",
                    )
                ):
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

            if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
                self.load_video(path)
            elif ext in SUPPORTED_AUDIO_EXTS:
                self._audio_dropped(path)
            elif ext in (".png", ".jpg", ".jpeg", ".bmp"):
                self.load_image(path)

    # ── 粘贴（Ctrl+V）和键盘快捷键 ──
    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        mods = event.modifiers()

        # Ctrl+V 粘贴文件
        if mods == Qt.ControlModifier and key == Qt.Key_V:
            clipboard = QApplication.clipboard()
            mime = clipboard.mimeData()
            if mime and mime.hasUrls():
                paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
                if paths:
                    valid_paths = [
                        p
                        for p in paths
                        if Path(p).suffix.lower()
                        in (
                            ".mp4",
                            ".mkv",
                            ".avi",
                            ".mov",
                            ".webm",
                            ".mp3",
                            ".wav",
                            ".flac",
                            ".ogg",
                            ".m4a",
                            ".aac",
                            ".wma",
                            ".opus",
                            ".png",
                            ".jpg",
                            ".jpeg",
                            ".bmp",
                        )
                    ]
                    if valid_paths:
                        self._handle_pasted_files(valid_paths)
                    return

        # ── ROI 选区微调快捷键 ──
        if self._selected_region_index >= 0 and 0 <= self._selected_region_index < len(self._regions):
            reg = self._regions[self._selected_region_index]
            step = 5 if (mods & Qt.ShiftModifier) else 1
            pix = self._display_pixmap
            pw = pix.width() if pix else 1920
            ph = pix.height() if pix else 1080

            # Delete / Backspace: 删除选区
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                self.remove_region(self._selected_region_index)
                return

            # Escape: 取消选中
            if key == Qt.Key_Escape:
                self.select_region(-1)
                return

            # Ctrl + 方向键: 微调移动 ROI (1px / 5px)
            if (mods & Qt.ControlModifier) and not (mods & Qt.AltModifier):
                if key == Qt.Key_Left:
                    reg["x"] = max(0, reg["x"] - step)
                elif key == Qt.Key_Right:
                    reg["x"] = min(pw - reg["w"], reg["x"] + step)
                elif key == Qt.Key_Up:
                    reg["y"] = max(0, reg["y"] - step)
                elif key == Qt.Key_Down:
                    reg["y"] = min(ph - reg["h"], reg["y"] + step)
                else:
                    reg = None
                if reg is not None:
                    self._clamp_move_to_frame(reg)
                    self.regions_changed.emit(self._regions)
                    self._label.update()
                    return

            # Alt + 方向键: 微调缩放 ROI 大小 (1px / 5px)
            if (mods & Qt.AltModifier) and not (mods & Qt.ControlModifier):
                if key == Qt.Key_Left:
                    reg["w"] = max(5, reg["w"] - step)
                elif key == Qt.Key_Right:
                    reg["w"] = min(pw - reg["x"], reg["w"] + step)
                elif key == Qt.Key_Up:
                    reg["h"] = max(5, reg["h"] - step)
                elif key == Qt.Key_Down:
                    reg["h"] = min(ph - reg["y"], reg["h"] + step)
                else:
                    reg = None
                if reg is not None:
                    self.regions_changed.emit(self._regions)
                    self._label.update()
                    return

        # Space 播放/暂停
        if key == Qt.Key_Space and mods == Qt.NoModifier:
            if self._video_path and not self._is_image:
                self._on_play_pause()
                return

        # , (Comma) 或 Alt+Left: 逐帧后退 (-1 帧)
        if (key == Qt.Key_Comma and mods == Qt.NoModifier) or (key == Qt.Key_Left and mods == Qt.AltModifier):
            self._step_frame(-1)
            return

        # . (Period) 或 Alt+Right: 逐帧前进 (+1 帧)
        if (key == Qt.Key_Period and mods == Qt.NoModifier) or (key == Qt.Key_Right and mods == Qt.AltModifier):
            self._step_frame(1)
            return

        # [ 或 Shift+Left: 微调 -100ms
        if (key == Qt.Key_BracketLeft and mods == Qt.NoModifier) or (key == Qt.Key_Left and mods == Qt.ShiftModifier):
            self._skip(-0.1)
            return

        # ] 或 Shift+Right: 微调 +100ms
        if (key == Qt.Key_BracketRight and mods == Qt.NoModifier) or (key == Qt.Key_Right and mods == Qt.ShiftModifier):
            self._skip(0.1)
            return

        # ← → 前进后退 5 秒
        if key == Qt.Key_Left and mods == Qt.NoModifier:
            self._skip(-5)
            return
        if key == Qt.Key_Right and mods == Qt.NoModifier:
            self._skip(5)
            return

        # ↑ ↓ 前进后退 30 秒
        if key == Qt.Key_Up and mods == Qt.NoModifier:
            self._skip(-30)
            return
        if key == Qt.Key_Down and mods == Qt.NoModifier:
            self._skip(30)
            return

        # S 切换速度
        if key == Qt.Key_S and mods == Qt.NoModifier:
            self._on_cycle_speed()
            return

        super().keyPressEvent(event)

    def _step_frame(self, step: int = 1):
        """逐帧微调播放位置（默认 25fps 或原生 fps）。"""
        fps = getattr(self, "_video_fps", 25.0) or 25.0
        frame_dur = 1.0 / max(1.0, fps)
        self._skip(step * frame_dur)

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
            if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
                self.load_video(path)
            elif ext in SUPPORTED_AUDIO_EXTS:
                self._audio_dropped(path)
            elif ext in (".png", ".jpg", ".jpeg", ".bmp"):
                self.load_image(path)

    def _audio_dropped(self, path: str):
        """拖放/粘贴音频文件时的处理。"""
        self._video_path = path
        self._is_image = False
        self._is_audio = True
        self._current_frame = None
        self._display_pixmap = None
        self._current_position = 0.0
        # 获取音频时长
        try:
            from core.ffmpeg_reader import _get_video_info

            info = _get_video_info(path)
            self._video_duration = info.get("duration", 0.0)
        except Exception:
            self._video_duration = 0.0
        self._preview_slider.setRange(0, max(1, int(self._video_duration * 100)))
        self._label._placeholder_text = _("🎵 已加载音频文件\n仅支持语音识别和纠错")
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
            self.select_region(idx)
            if handle == "move":
                self._moving_region_index = idx
                r = self._regions[idx]
                rect = self._get_image_display_rect()
                img_w, img_h, ox, oy = rect
                pw, ph = self._display_pixmap.width(), self._display_pixmap.height()
                p1 = QPoint(int(r["x"] * img_w / pw) + ox, int(r["y"] * img_h / ph) + oy)
                self._drag_offset = event.pos() - p1
            else:
                self._resizing_region_index = idx
                self._resize_handle = handle
        else:
            self.select_region(-1)
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
            self._apply_edge_snapping(r)
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
            is_top = h in ("t", "tl", "tr", "top")
            is_bottom = h in ("b", "bl", "br", "bottom")
            is_left = h in ("l", "tl", "bl", "left")
            is_right = h in ("r", "tr", "br", "right")
            if is_left:
                # 左侧拖拽：保持右边界不变
                _right = r["x"] + r["w"]
                r["x"] = max(0, min(fx, _right - 5))
                r["w"] = _right - r["x"]
            if is_right:
                # 右侧拖拽：保持左边界不变
                r["w"] = max(5, min(fx - r["x"], pw - r["x"]))
            if is_top:
                # 顶部拖拽：保持下边界不变
                _bottom = r["y"] + r["h"]
                r["y"] = max(0, min(fy, _bottom - 5))
                r["h"] = _bottom - r["y"]
            if is_bottom:
                # 底部拖拽：保持上边界不变
                r["h"] = max(5, min(fy - r["y"], ph - r["y"]))
            self._apply_edge_snapping(r)
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
            p1 = self._label_to_frame_coords(self._start_point.x(), self._start_point.y())
            p2 = self._label_to_frame_coords(self._end_point.x(), self._end_point.y())
            if p1.x() < 0 or p2.x() < 0:
                pass  # 超出图片区域，忽略
            else:
                x, y = min(p1.x(), p2.x()), min(p1.y(), p2.y())
                w, h = abs(p2.x() - p1.x()), abs(p2.y() - p1.y())
                if w > 5 and h > 5:
                    new_reg = {"x": x, "y": y, "w": w, "h": h}
                    self._apply_edge_snapping(new_reg)
                    self.add_region(new_reg["x"], new_reg["y"], new_reg["w"], new_reg["h"])
                    self.select_region(len(self._regions) - 1)
        was_moved = self._moving_region_index >= 0 or self._resizing_region_index >= 0
        self._moving_region_index = -1
        self._resizing_region_index = -1
        self._resize_handle = ""
        self._label.setCursor(Qt.CrossCursor)
        self._label.update()
        if was_moved:
            # 拖拽/调整大小结束后同步更新后的坐标到区域管理器
            self.regions_changed.emit(self._regions)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._label.update()

    def closeEvent(self, event):
        """关闭预览控件 —— 清理播放器和 FFmpeg。"""
        logger.info("清理播放器/FFmpeg...")
        # 停止定时器
        if hasattr(self, "_drag_seek_timer"):
            self._drag_seek_timer.stop()
        # 停止后台 worker
        if hasattr(self, "_load_worker") and self._load_worker:
            try:
                self._load_worker.terminate()
                self._load_worker.wait(2000)
            except Exception as e:
                logger.debug("load_worker 终止异常: %s", e)
            self._load_worker = None
        # 停止播放器
        if self._player:
            try:
                self._player.stop()
            except Exception as e:
                logger.warning("播放器停止异常: %s", e)
            self._player = None
        # 关闭音频播放器
        if hasattr(self, "_audio_player") and self._audio_player:
            try:
                self._audio_player.stop()
            except Exception as e:
                logger.debug("音频播放器停止异常: %s", e)
            self._audio_player = None
        if hasattr(self, "_audio_output") and self._audio_output:
            self._audio_output = None
        # 清理临时文件
        if hasattr(self, "_audio_temp") and self._audio_temp:
            try:
                os.unlink(self._audio_temp)
            except OSError:
                pass
            self._audio_temp = None
        # 关闭 FFmpeg
        if self._ffmpeg:
            try:
                self._ffmpeg.close()
            except Exception as e:
                logger.debug("FFmpeg 关闭异常: %s", e)
            self._ffmpeg = None
        logger.info("播放器/FFmpeg 清理完成")
        super().closeEvent(event)
