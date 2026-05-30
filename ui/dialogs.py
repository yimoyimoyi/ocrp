"""对话框组件 —— 引擎配置、API 预设管理等。"""


from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from core.i18n import _
from core.logger import get_logger
from core.utils import fetch_models_from_url, populate_model_combo

logger = get_logger(__name__)


class EngineConfigDialog(QDialog):
    """引擎配置对话框（从菜单栏唤起）。
    内置可用性检测、模型列表获取、保存预设。
    """

    def __init__(self, engine_name: str, config: dict, is_local: bool = False,
                 engine_manager=None, parent=None):
        super().__init__(parent)
        self._engine_name = engine_name
        self._engine_mgr = engine_manager
        self.setWindowTitle(_("引擎配置 - {}").format(engine_name))
        self.setMinimumWidth(440)
        self._result = dict(config)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(6)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText(_("sk-xxx"))
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        self._api_key_edit.setText(config.get("api_key", ""))
        form.addRow(_("API Key:"), self._api_key_edit)

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText(_("https://api.openai.com/v1"))
        self._base_url_edit.setText(config.get("base_url", ""))
        form.addRow(_("Base URL:"), self._base_url_edit)

        self._model_edit = QComboBox()
        self._model_edit.setEditable(True)
        self._model_edit.setInsertPolicy(QComboBox.NoInsert)
        self._model_edit.lineEdit().setPlaceholderText(_("gpt-4o"))
        self._model_edit.setEditText(config.get("model", ""))
        form.addRow(_("模型:"), self._model_edit)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(1, 300)
        self._timeout_spin.setValue(config.get("timeout", 30))
        self._timeout_spin.setSuffix(" 秒")
        form.addRow(_("超时:"), self._timeout_spin)

        self._gpu_check = QCheckBox(_("启用 GPU 加速"))
        self._gpu_check.setChecked(config.get("device") == "gpu" or config.get("use_gpu", False))
        self._gpu_check.setVisible(is_local)
        form.addRow("", self._gpu_check)

        # PaddleOCR 模型版本选择（仅本地引擎显示）
        self._paddle_version_combo = QComboBox()
        self._paddle_version_combo.addItems([_("PP-OCRv5_server (高精度/慢)"), _("PP-OCRv5_mobile (平衡)"), _("PP-OCRv4 (快速)")])
        self._paddle_version_combo.setVisible(is_local and engine_name == "paddleocr")
        ver = config.get("ocr_version") or ""
        if "v4" in ver:
            self._paddle_version_combo.setCurrentIndex(2)
        elif "mobile" in ver:
            self._paddle_version_combo.setCurrentIndex(1)
        else:
            self._paddle_version_combo.setCurrentIndex(0)
        form.addRow(_("模型版本:"), self._paddle_version_combo)

        # 角度检测开关
        self._angle_check = QCheckBox(_("启用角度检测"))
        self._angle_check.setChecked(config.get("use_angle_cls", True))
        self._angle_check.setVisible(is_local and engine_name == "paddleocr")
        form.addRow("", self._angle_check)

        layout.addLayout(form)

        # 保存预设按钮
        save_preset_row = QHBoxLayout()
        btn_save_preset = QPushButton(_("💾 保存为 API 预设"))
        btn_save_preset.setToolTip(_("将当前 API Key / Base URL / 模型/超时保存为预设，供纠错等功能快速使用"))
        btn_save_preset.clicked.connect(self._on_save_preset)
        save_preset_row.addWidget(btn_save_preset)
        save_preset_row.addStretch()
        layout.addLayout(save_preset_row)

        # 检测行
        check_row = QHBoxLayout()
        check_row.setSpacing(4)
        self._status_label = QLabel("")
        check_row.addWidget(self._status_label, 1)
        btn_check = QPushButton(_("🔄 检测可用性"))
        btn_check.clicked.connect(self._on_check)
        check_row.addWidget(btn_check)
        btn_models = QPushButton(_("📋 获取模型"))
        btn_models.clicked.connect(self._on_get_models)
        check_row.addWidget(btn_models)
        layout.addLayout(check_row)

        layout.addSpacing(8)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_save_preset(self):
        """将当前配置保存为 API 预设（基于引擎名称或自定义名称）。"""
        from core.api_preset_manager import APIPresetManager
        mgr = APIPresetManager()
        cfg = {
            "api_key": self._api_key_edit.text(),
            "base_url": self._base_url_edit.text(),
            "model": self._model_edit.currentText(),
            "timeout": self._timeout_spin.value(),
        }
        name = f"{self._engine_name} 预设"
        mgr.add_preset(name, cfg)
        self._status_label.setText(_("✅ 已保存预设: {}").format(name))

    def _on_check(self):
        self._status_label.setText(_("⏳ 检测中..."))
        self._run_http_action("check")

    def _on_get_models(self):
        self._status_label.setText(_("⏳ 获取模型列表..."))
        self._run_http_action("models")

    def _run_http_action(self, action: str):
        """后台线程执行 HTTP 请求，不阻塞 UI。"""
        try:
            eng = self._engine_mgr.get_engine(self._engine_name) if self._engine_mgr else None
        except Exception as e:
            logger.warning("获取引擎失败: %s", e)
            self._status_label.setText(_("⚠ 引擎未初始化"))
            return
        if not eng:
            self._status_label.setText(_("⚠ 引擎未初始化"))
            return

        from ui.workers import HttpCheckWorker
        self._http_worker = HttpCheckWorker(eng, action)
        self._http_worker.result.connect(lambda r: self._on_http_result(r))
        self._http_worker.error.connect(lambda e: self._status_label.setText(f"❌ {e[:30]}"))
        self._http_worker.start()

    def _on_http_result(self, result: dict):
        if result["type"] == "check":
            self._status_label.setText(_("✅ 可用") if result["data"] else _("❌ 不可用"))
        elif result["type"] == "models":
            models = result["data"]
            if models:
                self.set_models(models)
                self._status_label.setText(_("✅ {n} 个").format(n=len(models)))
            else:
                self._status_label.setText(_("⚠ 未获取到模型"))

    def _on_accept(self):
        ver_map = {0: None, 1: "PP-OCRv5_mobile", 2: "PP-OCRv4"}
        self._result = {
            "api_key": self._api_key_edit.text(),
            "base_url": self._base_url_edit.text(),
            "model": self._model_edit.currentText(),
            "timeout": self._timeout_spin.value(),
            "device": "gpu" if self._gpu_check.isChecked() else "cpu",
            "ocr_version": ver_map.get(self._paddle_version_combo.currentIndex()),
            "use_angle_cls": self._angle_check.isChecked(),
        }
        self.accept()

    def get_config(self) -> dict:
        return dict(self._result)

    def set_models(self, models: list[str]):
        populate_model_combo(self._model_edit, models)


class PresetManageDialog(QDialog):
    """API 预设管理对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
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
        # 后台线程执行 HTTP 请求，pyqtSignal 跨线程投递结果到主线程
        import threading

        class _FetchBridge(QObject):
            done = pyqtSignal(object)
            err = pyqtSignal(str)

        bridge = _FetchBridge()
        bridge.done.connect(self._on_fetch_done)
        bridge.err.connect(lambda msg: self._model_status.setText(f"❌ {msg[:20]}"))
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
        reply = QMessageBox.question(self, _("确认删除"), f"确定要删除预设「{name}」吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._mgr.delete_preset(name)
            self._refresh_list()

    def _on_save(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, _("提示"), _("请输入预设名称。"))
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
