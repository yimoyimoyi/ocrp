"""MessageService 统一弹窗封装测试。

覆盖三件事：
1. question 的 Yes/No → bool 极性映射（破坏性操作确认的语义基石）
2. 文件/文本输入对话框静态方法映射与空串归一化
3. 归属 widget 已全部改为经注入的 MessageService 弹窗（AST 守卫防回归）
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from core.i18n import _
from ui.services import MessageService

ROOT = Path(__file__).resolve().parent.parent

# 本任务接管的文件：弹窗调用必须全部经 MessageService
OWNED_FILES = [
    "ui/main_window.py",
    "ui/settings_dialog.py",
    "ui/result_table.py",
    "ui/dialogs.py",
    "ui/template_editor.py",
    "ui/region_manager.py",
]

_DIALOG_NAMES = {"QMessageBox", "QFileDialog", "QInputDialog"}


class TestQuestionPolarity:
    """question() 必须把 Yes 映射为 True、No 映射为 False。"""

    def test_yes_returns_true(self):
        svc = MessageService()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes) as question:
            assert svc.question("标题", "消息") is True
        question.assert_called_once()
        # 参数透传：parent/title/message/按钮组合/默认按钮
        args = question.call_args.args
        assert args[0] is None
        assert args[1] == "标题"
        assert args[2] == "消息"
        assert args[3] == (QMessageBox.Yes | QMessageBox.No)
        assert args[4] == QMessageBox.No

    def test_no_returns_false(self):
        svc = MessageService()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            assert svc.question("标题", "消息") is False


class TestMessageStatics:
    """error/info/warning 映射到对应的 QMessageBox 静态方法。"""

    def test_error_maps_to_critical(self):
        svc = MessageService()
        with mock.patch.object(QMessageBox, "critical") as critical:
            svc.error("E", "boom")
        critical.assert_called_once_with(None, "E", "boom")

    def test_info_maps_to_information(self):
        svc = MessageService()
        with mock.patch.object(QMessageBox, "information") as information:
            svc.info("I", "hi")
        information.assert_called_once_with(None, "I", "hi")

    def test_warning_maps_to_warning(self):
        svc = MessageService()
        with mock.patch.object(QMessageBox, "warning") as warning:
            svc.warning("W", "careful")
        warning.assert_called_once_with(None, "W", "careful")


class TestFileDialogs:
    """open_file/open_files/save_file 映射正确静态方法并把空串归一化。"""

    def test_open_file_maps_and_normalises_cancel(self):
        svc = MessageService()
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("", "")) as fd:
            assert svc.open_file("T", "D", "F") is None
        fd.assert_called_once_with(None, "T", "D", "F")

    def test_open_file_returns_path(self):
        svc = MessageService()
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("a.json", "JSON")):
            assert svc.open_file("T", "D", "F") == "a.json"

    def test_open_files_maps_and_normalises_empty(self):
        svc = MessageService()
        with mock.patch.object(QFileDialog, "getOpenFileNames", return_value=(["a.mp4", "b.mp4"], "")) as fd:
            assert svc.open_files("T", "D", "F") == ["a.mp4", "b.mp4"]
        fd.assert_called_once_with(None, "T", "D", "F")
        with mock.patch.object(QFileDialog, "getOpenFileNames", return_value=([], "")):
            assert svc.open_files("T", "D", "F") == []

    def test_save_file_maps_and_normalises_cancel(self):
        svc = MessageService()
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=("", "")) as fd:
            assert svc.save_file("T", "D", "F") is None
        fd.assert_called_once_with(None, "T", "D", "F")
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=("out.srt", "")):
            assert svc.save_file("T", "D", "F") == "out.srt"

    def test_get_text_maps_and_passes_initial_text(self):
        svc = MessageService()
        with mock.patch.object(QInputDialog, "getText", return_value=("新名字", True)) as gt:
            assert svc.get_text("标题", "标签", "旧名字") == ("新名字", True)
        gt.assert_called_once_with(None, "标题", "标签", text="旧名字")

    def test_get_text_reports_cancel(self):
        svc = MessageService()
        with mock.patch.object(QInputDialog, "getText", return_value=("", False)):
            assert svc.get_text("标题", "标签") == ("", False)


class TestWidgetRouting:
    """已接入 MessageService 的控件：注入 fake 后断言走 service、不再弹真框。"""

    def test_region_manager_clear_all_confirmed(self, qapp):
        from ui.region_manager import RegionManagerWidget

        svc = mock.MagicMock()
        svc.question.return_value = True
        widget = RegionManagerWidget(message_service=svc)
        emitted = []
        widget.regions_cleared.connect(lambda: emitted.append(True))

        widget._on_clear_all()

        svc.question.assert_called_once_with(_("确认清空"), _("确定要清空所有区域吗？"))
        assert emitted == [True]

    def test_region_manager_clear_all_cancelled(self, qapp):
        from ui.region_manager import RegionManagerWidget

        svc = mock.MagicMock()
        svc.question.return_value = False
        widget = RegionManagerWidget(message_service=svc)
        emitted = []
        widget.regions_cleared.connect(lambda: emitted.append(True))

        widget._on_clear_all()

        svc.question.assert_called_once()
        assert emitted == []

    def test_result_table_empty_export_warns_via_service(self, qapp):
        from ui.result_table import ResultTableWidget

        svc = mock.MagicMock()
        widget = ResultTableWidget(message_service=svc)
        emitted = []
        widget.export_requested.connect(lambda *args: emitted.append(args))

        widget._on_export("srt")

        svc.info.assert_called_once_with(_("提示"), _("暂无识别结果可导出。"))
        svc.save_file.assert_not_called()
        assert emitted == []

    def test_result_table_export_uses_service_save_file(self, qapp):
        from ui.result_table import ResultTableWidget

        svc = mock.MagicMock()
        svc.save_file.return_value = "out.srt"
        widget = ResultTableWidget(message_service=svc)
        widget._results = [{"raw": "x"}]
        emitted = []
        widget.export_requested.connect(lambda fmt, path: emitted.append((fmt, path)))

        widget._on_export("srt")

        svc.save_file.assert_called_once()
        assert emitted == [("srt", "out.srt")]

    def test_template_editor_new_uses_service_get_text(self, qapp):
        from ui.template_editor import TemplateEditorDialog

        svc = mock.MagicMock()
        svc.get_text.return_value = ("新模板", True)
        dialog = TemplateEditorDialog(["A"], {"A": "p"}, message_service=svc)

        dialog._on_new()

        svc.get_text.assert_called_once_with(_("新建模板"), _("模板名称:"))
        assert dialog._combo.count() == 2

    def test_template_editor_delete_cancel_keeps_template(self, qapp):
        from ui.template_editor import TemplateEditorDialog

        svc = mock.MagicMock()
        svc.question.return_value = False
        dialog = TemplateEditorDialog(["A"], {"A": "p"}, message_service=svc)

        dialog._on_delete()

        svc.question.assert_called_once()
        assert dialog._combo.count() == 1

    def test_template_editor_delete_confirm_removes_template(self, qapp):
        from ui.template_editor import TemplateEditorDialog

        svc = mock.MagicMock()
        svc.question.return_value = True
        dialog = TemplateEditorDialog(["A"], {"A": "p"}, message_service=svc)
        deleted = []
        dialog.template_deleted.connect(deleted.append)

        dialog._on_delete()

        assert dialog._combo.count() == 0
        assert deleted == ["A"]

    def test_preset_dialog_delete_routes_through_service(self, qapp):
        from core import api_preset_manager
        from ui.dialogs import PresetManageDialog

        svc = mock.MagicMock()
        svc.question.return_value = True
        with mock.patch.object(api_preset_manager, "APIPresetManager") as mgr_cls:
            mgr_cls.return_value.get_names.return_value = ["预设A"]
            mgr_cls.return_value.get_preset.return_value = None
            dialog = PresetManageDialog(message_service=svc)
            dialog._on_delete()

        svc.question.assert_called_once()
        mgr_cls.return_value.delete_preset.assert_called_once_with("预设A")

    def test_settings_reject_polarity(self, qapp, tmp_path):
        """dirty 时取消：service 返回 False 必须保留对话框，True 才关闭。"""
        from core.settings.registry import ConfigRegistry
        from ui.config_panel import ConfigPanel
        from ui.settings_dialog import SettingsDialog

        registry = ConfigRegistry(tmp_path)
        svc = mock.MagicMock()
        dialog = SettingsDialog(config_panel=ConfigPanel(registry=registry), registry=registry, message_service=svc)
        dialog._mark_dirty()

        with mock.patch.object(QDialog, "reject") as super_reject:
            svc.question.return_value = False
            dialog.reject()
            super_reject.assert_not_called()

            svc.question.return_value = True
            dialog.reject()
            super_reject.assert_called_once()

        assert svc.question.call_count == 2


class TestNoDirectDialogCalls:
    """AST 守卫：归属文件中不得再出现直接调用 Qt 弹窗静态方法的语句。"""

    @pytest.mark.parametrize("rel_path", OWNED_FILES)
    def test_no_direct_dialog_static_calls(self, rel_path):
        path = ROOT / rel_path
        assert path.is_file(), f"归属文件不存在: {rel_path}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in _DIALOG_NAMES:
                offenders.append(f"{rel_path}:{node.lineno}: {func.value.id}.{func.attr}()")

        assert offenders == [], "以下弹窗调用未走 MessageService:\n" + "\n".join(offenders)

    def test_service_is_the_only_dialog_site_in_ui(self):
        """整个 ui/ 包内，直接调用 Qt 弹窗静态方法的只剩 message_service.py。"""
        offenders = []
        for path in (ROOT / "ui").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id in _DIALOG_NAMES
                    and path.name != "message_service.py"
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {func.value.id}.{func.attr}()")
        assert offenders == [], "ui/ 内仍有绕过 MessageService 的弹窗调用:\n" + "\n".join(offenders)
