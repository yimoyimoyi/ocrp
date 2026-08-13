"""菜单栏构建器。

方法体从 main_window.py 原样搬移（AST 提取，零改动）。
通过 _ViewBase 与 MainWindow 双向委托共享状态。
"""

from PySide6.QtGui import QAction, QActionGroup

from core.i18n import LANGUAGE_DISPLAY_NAMES, LanguageManager, _
from ui.views.base import _ViewBase


class MenuBarView(_ViewBase):
    """菜单栏构建器。"""

    def build(self):
        mb = self.menuBar()

        # ── 参数设置菜单 ──
        # R11 UX：移除「⚙ 全部参数...」——它与「基础设置...」行为完全相同
        # （tab_index -1 与 0 都落到 tab 0），保留 5 个直达 tab 入口
        self._settings_menu = mb.addMenu(_("参数设置(&P)"))
        self._settings_menu_actions = []
        for label, tab_idx in [
            (_("基础设置..."), 0),
            (_("语音识别..."), 1),
            (_("OCR 字幕处理..."), 2),
            (_("AI 纠错..."), 3),
            (_("结果输出..."), 4),
        ]:
            action = QAction(label, self._mgr)
            action.triggered.connect(lambda checked, idx=tab_idx: self._open_settings(idx))
            self._settings_menu.addAction(action)
            self._settings_menu_actions.append(action)
        # 重新加载配置（2.4：外部手改 JSON 后读盘刷新，避免 UI 覆盖丢失）
        self._settings_menu.addSeparator()
        self._reload_config_action = QAction(_("🔄 重新加载配置..."), self._mgr)
        self._reload_config_action.triggered.connect(self._on_reload_config)
        self._settings_menu.addAction(self._reload_config_action)

        # ── 显示菜单 ──
        self._display_menu = mb.addMenu(_("显示(&V)"))
        self._display_theme_action = QAction(_("切换主题 (亮色/暗色)"), self._mgr)
        self._display_theme_action.triggered.connect(self._toggle_theme)
        self._display_menu.addAction(self._display_theme_action)
        self._display_menu.addSeparator()
        self._display_settings_action = QAction(_("显示设置..."), self._mgr)
        self._display_settings_action.triggered.connect(self._open_display_settings)
        self._display_menu.addAction(self._display_settings_action)

        # ── 纠错快捷菜单 ──
        self._corr_menu = mb.addMenu(_("纠错(&C)"))
        self._corr_preset_action = QAction(_("API 预设管理..."), self._mgr)
        self._corr_preset_action.triggered.connect(self._on_menu_preset_manage)
        self._corr_menu.addAction(self._corr_preset_action)

        # ── 模板菜单 ──
        self._template_menu = mb.addMenu(_("模板(&T)"))
        self._template_action_group = QActionGroup(self._mgr)
        self._template_action_group.setExclusive(True)
        self._template_menu.addSeparator()
        self._template_edit_action = QAction(_("📝 编辑模板..."), self._mgr)
        self._template_edit_action.triggered.connect(self._on_template_edit)
        self._template_menu.addAction(self._template_edit_action)
        self._template_menu.addSeparator()
        self._template_import_action = QAction(_("📥 导入模板..."), self._mgr)
        self._template_import_action.triggered.connect(self._on_template_import)
        self._template_menu.addAction(self._template_import_action)
        self._template_export_action = QAction(_("📤 导出模板..."), self._mgr)
        self._template_export_action.triggered.connect(self._on_template_export)
        self._template_menu.addAction(self._template_export_action)

        # ── 批量菜单 ──
        self._batch_menu = mb.addMenu(_("批量(&B)"))
        self._batch_clear_action = QAction(_("🗑 清空队列"), self._mgr)
        self._batch_clear_action.triggered.connect(self._on_batch_clear)
        self._batch_menu.addAction(self._batch_clear_action)

        # ── 语言菜单 ──
        self._language_menu = mb.addMenu(_("语言(&L)"))
        self._lang_action_group = QActionGroup(self._mgr)
        self._lang_action_group.setExclusive(True)
        current_lang = LanguageManager().current_language
        for code, display in LANGUAGE_DISPLAY_NAMES.items():
            action = QAction(display, self._mgr)
            action.setCheckable(True)
            action.setChecked(code == current_lang)
            action.triggered.connect(lambda checked, c=code: self._on_switch_language(c))
            self._lang_action_group.addAction(action)
            self._language_menu.addAction(action)
