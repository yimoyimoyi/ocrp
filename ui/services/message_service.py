"""弹窗/文件对话框统一封装 —— 替代信号直连 QMessageBox，可注入 mock 测试。"""

from PySide6.QtWidgets import QFileDialog, QMessageBox


class MessageService:
    """主窗口交互服务：弹窗与文件对话框统一入口。

    MainWindow 持有单例，所有 QMessageBox/QFileDialog 调用改经此服务，
    便于测试时注入 mock 验证交互行为。
    """

    def __init__(self, parent=None):
        self._parent = parent

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
