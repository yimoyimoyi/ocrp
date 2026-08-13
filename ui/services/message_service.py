"""弹窗/文件对话框统一封装 —— 替代信号直连 QMessageBox，可注入 mock 测试。"""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox


class MessageService:
    """主窗口交互服务：弹窗与文件对话框统一入口。

    MainWindow 持有单例，所有 QMessageBox/QFileDialog 调用改经此服务，
    便于测试时注入 mock 验证交互行为。
    """

    def __init__(self, parent=None):
        self._parent = parent
        self._toasts: list[QLabel] = []
        self._animations: list[QPropertyAnimation] = []  # 持有动画引用防 GC

    def _hold_animation(self, anim: QPropertyAnimation):
        """持有动画引用（Qt parent 链不防 Python GC），finished 后释放。"""
        self._animations.append(anim)

        def _release():
            if anim in self._animations:
                self._animations.remove(anim)

        anim.finished.connect(_release)

    # ── 弹窗 ──
    def error(self, title: str, message: str):
        QMessageBox.critical(self._parent, title, message)

    def info(self, title: str, message: str):
        QMessageBox.information(self._parent, title, message)

    def warning(self, title: str, message: str):
        QMessageBox.warning(self._parent, title, message)

    def question(self, title: str, message: str) -> bool:
        """确认对话框，返回用户是否确认。"""
        reply = QMessageBox.question(
            self._parent,
            title,
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    # ── toast 反馈（2.11：保存成功/引擎重启/文件外部修改等轻提示）──
    def info_toast(self, message: str, timeout_ms: int = 2500):
        """轻量 toast：主窗口右下角浮层，淡入淡出，最多同屏 3 条。

        与状态栏并存：进度类信息仍走状态栏，一次性反馈用 toast。
        """
        parent = self._parent
        if parent is None or QApplication.instance() is None:
            return
        toast = QLabel(message, parent)
        toast.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        toast.setAttribute(Qt.WA_DeleteOnClose)
        toast.setStyleSheet(
            "QLabel { background: rgba(40, 40, 40, 0.92); color: #eee;"
            "border-radius: 6px; padding: 8px 14px; font-size: 12px; }"
        )
        toast.adjustSize()
        self._toasts.append(toast)
        if len(self._toasts) > 3:  # 最多同时 3 条，新的顶掉最旧
            old = self._toasts.pop(0)
            old.close()
        # 右下角堆叠定位：Qt.ToolTip 是独立顶层窗口，用全局坐标（父窗口局部坐标需 mapToGlobal）
        from PySide6.QtCore import QPoint, QTimer

        pos = parent.mapToGlobal(
            QPoint(
                parent.width() - toast.width() - 16,
                parent.height() - toast.height() - 16 - 40 * (len(self._toasts) - 1),
            )
        )
        toast.move(pos)
        toast.show()
        # 淡入/淡出动画必须挂 parent=toast——动画对象无 parent 且无引用持有
        # 时会被 GC 回收，淡出永不执行（toast 不消失的历史 bug）
        fade_in = QPropertyAnimation(toast, b"windowOpacity", toast)
        fade_in.setDuration(180)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)
        self._hold_animation(fade_in)
        fade_in.start()

        # 定时淡出并销毁（单发 QTimer 主线程调度，避免跨线程 UI 调用）
        def _fade_out():
            if toast in self._toasts:
                self._toasts.remove(toast)
            fade = QPropertyAnimation(toast, b"windowOpacity", toast)
            fade.setDuration(300)
            fade.setStartValue(1.0)
            fade.setEndValue(0.0)
            fade.finished.connect(toast.close)
            self._hold_animation(fade)
            fade.start()

        expire = QTimer(toast)
        expire.setSingleShot(True)
        expire.timeout.connect(_fade_out)
        expire.start(timeout_ms)

    # ── 文件对话框 ──
    def open_file(self, title: str, directory: str = "", filter_str: str = "") -> str | None:
        path, _ = QFileDialog.getOpenFileName(self._parent, title, directory, filter_str)
        return path or None

    def open_files(self, title: str, directory: str = "", filter_str: str = "") -> list[str]:
        paths, _ = QFileDialog.getOpenFileNames(self._parent, title, directory, filter_str)
        return list(paths)

    def save_file(self, title: str, directory: str = "", filter_str: str = "") -> str | None:
        path, _ = QFileDialog.getSaveFileName(self._parent, title, directory, filter_str)
        return path or None
