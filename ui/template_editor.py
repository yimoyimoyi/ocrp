"""提示词模板编辑器弹窗。"""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.i18n import _


class TemplateEditorDialog(QDialog):
    """提示词模板编辑器弹窗 —— 从 ConfigPanel 的模板 Tab 入口打开。"""

    template_saved = pyqtSignal(str, str)    # (name, prompt)
    template_deleted = pyqtSignal(str)       # (name)
    prompt_changed = pyqtSignal(str)         # (prompt_text)

    def __init__(self, names: list[str], contents: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("📝 提示词模板编辑器"))
        self.setMinimumSize(700, 500)
        self._names = list(names)
        self._contents = dict(contents)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        sel_row = QHBoxLayout()
        sel_row.setSpacing(4)
        sel_row.addWidget(QLabel(_("模板:")))
        self._combo = QComboBox()
        self._combo.setEditable(False)
        self._combo.addItems(self._names)
        self._combo.currentTextChanged.connect(self._on_selected)
        sel_row.addWidget(self._combo, 1)
        layout.addLayout(sel_row)

        layout.addWidget(QLabel(_("提示词内容（点击下方按钮插入占位符）:")))
        self._prompt_edit = QTextEdit()
        self._prompt_edit.setPlaceholderText(_("输入提示词..."))
        self._prompt_edit.textChanged.connect(
            lambda: self.prompt_changed.emit(self._prompt_edit.toPlainText()))
        layout.addWidget(self._prompt_edit, 1)

        # ── 占位符按钮 ──
        ph_row = QHBoxLayout()
        ph_row.setSpacing(4)
        ph_row.addWidget(QLabel(_("插入:")))
        for ph_text, ph_label in [
            ("{原始结果}", "原始结果"),
            ("{上下文}", "上下文"),
            ("{环境信息}", "环境信息"),
        ]:
            b = QPushButton(ph_label)
            b.setToolTip(f"在光标位置插入 {ph_text}")
            b.clicked.connect(lambda checked, t=ph_text: self._insert_at_cursor(t))
            ph_row.addWidget(b)
        btn_def = QPushButton(_("默认结构"))
        btn_def.setToolTip("填入默认提示词结构示例（含所有可用占位符）")
        btn_def.clicked.connect(self._load_default_structure)
        ph_row.addWidget(btn_def)
        ph_row.addStretch()
        layout.addLayout(ph_row)

        # ── CRUD 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        for text, slot in [
            (_("➕ 新建"), self._on_new),
            (_("💾 保存"), self._on_save),
            (_("✏ 重命名"), self._on_rename),
            (_("🗑 删除"), self._on_delete),
        ]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        btn_row.addStretch()
        btn = QPushButton(_("关闭"))
        btn.clicked.connect(self.accept)
        btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        # 初始选择第一个模板
        if self._names:
            self._combo.setCurrentIndex(0)

    def _on_selected(self, name: str):
        if name and name in self._contents:
            self._prompt_edit.setPlainText(self._contents[name])

    def _insert_at_cursor(self, text: str):
        cursor = self._prompt_edit.textCursor()
        cursor.insertText(text)
        self._prompt_edit.setFocus()

    def _load_default_structure(self):
        default = (
            "你是一个专业的字幕校对助手。\n"
            "请根据上下文纠正OCR识别结果中的明显错误，保留原格式。\n\n"
            "上下文信息：\n{上下文}\n\n"
            "环境摘要：\n{环境信息}\n\n"
            "时间戳：{时间戳} | 区域：{区域} | 引擎：{引擎} | 语言：{语言}\n\n"
            "待处理的文本：\n{原始结果}"
        )
        self._prompt_edit.setPlainText(default)

    def _on_new(self):
        name, ok = QInputDialog.getText(self, _("新建模板"), _("模板名称:"))
        if ok and name.strip():
            if name not in self._names:
                self._names.append(name)
                self._combo.addItem(name)
                self._contents[name] = ""
                self.template_saved.emit(name, "")
            self._combo.setCurrentText(name)
            self._prompt_edit.setPlainText(self._contents.get(name, ""))

    def _on_save(self):
        name = self._combo.currentText()
        if name:
            content = self._prompt_edit.toPlainText()
            self._contents[name] = content
            self.template_saved.emit(name, content)

    def _on_rename(self):
        old = self._combo.currentText()
        if not old:
            return
        new, ok = QInputDialog.getText(self, _("重命名模板"), _("新名称:"), text=old)
        if ok and new.strip() and new != old:
            if new in self._names:
                QMessageBox.warning(self, _("重命名失败"), _("模板名称 '{}' 已存在。").format(new))
                return
            idx = self._names.index(old)
            self._names[idx] = new
            self._combo.setItemText(self._combo.currentIndex(), new)
            self._contents[new] = self._contents.pop(old, "")
            self.template_deleted.emit(old)
            self.template_saved.emit(new, self._contents[new])

    def _on_delete(self):
        name = self._combo.currentText()
        if not name:
            return
        if QMessageBox.question(
            self, _("确认删除"), _("确定要删除模板 '{}' 吗？").format(name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            idx = self._combo.currentIndex()
            self._combo.removeItem(idx)
            self._names.remove(name)
            self._contents.pop(name, None)
            self.template_deleted.emit(name)
