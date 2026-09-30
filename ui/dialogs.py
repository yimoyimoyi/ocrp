"""对话框组件 —— 引擎配置、API 预设管理等。"""

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from core.i18n import _
from core.logger import get_logger
from core.utils import fetch_models_from_url, populate_model_combo
from ui.services import MessageService

logger = get_logger(__name__)


class PresetManageDialog(QDialog):
    """API 预设管理对话框。"""

    def __init__(self, parent=None, message_service: MessageService | None = None):
        super().__init__(parent)
        self._message_service = message_service or MessageService(self)
        self.setWindowTitle(_("API 预设管理"))
        self.setMinimumWidth(500)
        from core.api_preset_manager import APIPresetManager

        self._mgr = APIPresetManager()
        self._mgr.reload()

        layout = QVBoxLayout(self)

        # 上排：预设列表
        row = QHBoxLayout()
        self._list = QListWidget()
        self._list.addItems(self._mgr.get_names())
        self._list.currentTextChanged.connect(self._on_selection)
        row.addWidget(self._list, 1)

        btn_col = QVBoxLayout()
        btn_add = QPushButton(_("+ 新建"))
        btn_add.clicked.connect(self._on_add)
        btn_col.addWidget(btn_add)
        btn_del = QPushButton(_("- 删除"))
        btn_del.clicked.connect(self._on_delete)
        btn_col.addWidget(btn_del)
        btn_col.addStretch()
        row.addLayout(btn_col)
        layout.addLayout(row)

        # 下排：编辑区
        form = QFormLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(_("预设名称"))
        form.addRow(_("名称:"), self._name_edit)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(_("http://127.0.0.1:8080"))
        form.addRow(_("Base URL:"), self._url_edit)

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText(_("API Key（可选）"))
        form.addRow(_("API Key:"), self._key_edit)

        model_row = QHBoxLayout()
        self._model_edit = QComboBox()
        self._model_edit.setEditable(True)
        self._model_edit.setInsertPolicy(QComboBox.NoInsert)
        self._model_edit.lineEdit().setPlaceholderText(_("模型名（可选）"))
        self._model_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        model_row.addWidget(self._model_edit, 1)
        self._model_status = QLabel("")
        self._model_status.setMinimumWidth(100)
        btn_models = QPushButton(_("📋 获取模型"))
        btn_models.setToolTip(_("从 Base URL 获取可用模型列表"))
        btn_models.clicked.connect(self._on_get_models)
        model_row.addWidget(self._model_status)
        model_row.addWidget(btn_models)
        form.addRow(_("模型:"), model_row)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 300)
        self._timeout_spin.setValue(30)
        self._timeout_spin.setSuffix(" 秒")
        form.addRow(_("超时:"), self._timeout_spin)

        layout.addLayout(form)

        btn_save = QPushButton(_("💾 保存"))
        btn_save.clicked.connect(self._on_save)
        layout.addWidget(btn_save)

        layout.addSpacing(8)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_get_models(self):
        """从当前 Base URL 获取可用模型列表。"""
        base_url = self._url_edit.text().strip()
        if not base_url:
            self._model_status.setText(_("⚠ 请先输入 Base URL"))
            return
        self._model_status.setText(_("⏳ 获取中..."))
        QApplication.processEvents()
        # 后台线程执行 HTTP 请求，Signal 跨线程投递结果到主线程
        import threading

        class _FetchBridge(QObject):
            done = Signal(object)
            err = Signal(str)

        bridge = _FetchBridge()
        bridge.done.connect(self._on_fetch_done)

        def _on_err(msg: str):
            self._model_status.setText(_("❌ {msg}").format(msg=msg[:40]))
            self._model_status.setToolTip(msg)

        bridge.err.connect(_on_err)
        api_key = self._key_edit.text()

        def _fetch():
            try:
                models = fetch_models_from_url(base_url, api_key)
                bridge.done.emit(models)
            except Exception as e:
                bridge.err.emit(str(e))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_fetch_done(self, models):
        if models:
            self._set_model_list(models)
            self._model_status.setText(_("✅ {n} 个").format(n=len(models)))
        else:
            self._model_status.setText(_("⚠ 未获取到模型"))

    def _set_model_list(self, models: list[str]):
        """填充模型下拉列表。"""
        populate_model_combo(self._model_edit, models)

    def _on_selection(self, name: str):
        preset = self._mgr.get_preset(name)
        if preset:
            self._name_edit.setText(name)
            self._url_edit.setText(preset.get("base_url", ""))
            self._key_edit.setText(preset.get("api_key", ""))
            model = preset.get("model", "")
            self._model_edit.setEditText(model)
            self._model_status.setText("")
            self._timeout_spin.setValue(preset.get("timeout", 30))

    def _on_add(self):
        self._name_edit.clear()
        self._url_edit.clear()
        self._key_edit.clear()
        self._model_edit.clear()
        self._model_status.setText("")
        self._timeout_spin.setValue(30)
        self._list.clearSelection()

    def _on_delete(self):
        name = self._list.currentItem().text() if self._list.currentItem() else ""
        if not name:
            return
        if self._message_service.question(
            _("确认删除"),
            _("确定要删除预设「{name}」吗？").format(name=name),
        ):
            self._mgr.delete_preset(name)
            self._refresh_list()

    def _on_save(self):
        name = self._name_edit.text().strip()
        if not name:
            self._message_service.warning(_("提示"), _("请输入预设名称。"))
            return
        cfg = {
            "api_key": self._key_edit.text(),
            "base_url": self._url_edit.text(),
            "model": self._model_edit.currentText(),
            "timeout": self._timeout_spin.value(),
        }
        existing = self._mgr.get_preset(name)
        if existing:
            self._mgr.update_preset(name, cfg)
        else:
            self._mgr.add_preset(name, cfg)
        self._refresh_list()
        # 重新选中
        for i in range(self._list.count()):
            if self._list.item(i).text() == name:
                self._list.setCurrentRow(i)
                break

    def _refresh_list(self):
        self._list.blockSignals(True)
        cur = self._list.currentItem().text() if self._list.currentItem() else ""
        self._list.clear()
        self._mgr.reload()
        names = self._mgr.get_names()
        self._list.addItems(names)
        if cur in names:
            items = self._list.findItems(cur, Qt.MatchExactly)
            if items:
                self._list.setCurrentRow(self._list.row(items[0]))
        self._list.blockSignals(False)
