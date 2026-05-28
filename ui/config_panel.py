"""配置面板 —— 纯状态管理类，不含 UI 控件。

SettingsDialog 是唯一的设置 UI 入口；ConfigPanel 负责：
  - 存储所有处理参数（mode_params）
  - 提供公共属性访问器供 Main Window 读写
  - 管理模板名/内容映射
  - 发射信号通知外部状态变更
"""

from PyQt5.QtCore import QObject, pyqtSignal

from core.config_manager import MODE_PARAMS_DEFAULTS


class ConfigPanel(QObject):
    """纯状态管理类，替代原先隐藏的 QWidget ConfigPanel。"""

    prompt_changed = pyqtSignal(str)
    mode_changed = pyqtSignal(dict)
    hw_accel_changed = pyqtSignal(bool)
    template_created = pyqtSignal(str)
    template_saved = pyqtSignal(str, str)
    template_deleted = pyqtSignal(str)
    filter_add_requested = pyqtSignal(str)
    filter_remove_requested = pyqtSignal(str)
    extract_env_clicked = pyqtSignal()
    collapse_requested = pyqtSignal()
    template_edit_requested = pyqtSignal()
    template_selected_for_correction = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._params: dict = dict(MODE_PARAMS_DEFAULTS)
        self._proofread_enabled: bool = False
        self._template_names: list[str] = ["通用OCR"]
        self._template_contents: dict[str, str] = {}
        self._region_names: list[str] = []
        self._sort_rules: list[tuple[str, str, str]] = []  # [(prefix, name, suffix)]

    # ── 核心数据接口 ──

    def get_mode_params(self) -> dict:
        """返回当前所有模式参数的副本。"""
        return dict(self._params)

    def apply_mode_params(self, params: dict):
        """将保存的参数应用到内部状态，发射 mode_changed 信号。"""
        self._params.update(params)
        self.mode_changed.emit(dict(self._params))

    def set_proofread_enabled(self, val: bool):
        self._proofread_enabled = val
        self._params["corr_proofread"] = val

    # ── 公共属性访问器（替代直接 widget 访问）──

    @property
    def corr_enabled(self) -> bool:
        return self._params.get("corr_enabled", False)

    @corr_enabled.setter
    def corr_enabled(self, val: bool):
        self._params["corr_enabled"] = val

    @property
    def post_sim_dedup(self) -> bool:
        return self._params.get("post_sim_dedup", True)

    @post_sim_dedup.setter
    def post_sim_dedup(self, val: bool):
        self._params["post_sim_dedup"] = val

    @property
    def corr_translate(self) -> bool:
        return self._params.get("corr_translate", False)

    @corr_translate.setter
    def corr_translate(self, val: bool):
        self._params["corr_translate"] = val

    @property
    def sentinel_enabled(self) -> bool:
        return self._params.get("sentinel_enabled", True)

    @sentinel_enabled.setter
    def sentinel_enabled(self, val: bool):
        self._params["sentinel_enabled"] = val

    @property
    def subtitle_mode(self) -> str:
        return self._params.get("subtitle_mode", "流式字幕（去重）")

    @subtitle_mode.setter
    def subtitle_mode(self, val: str):
        self._params["subtitle_mode"] = val

    @property
    def process_mode(self) -> str:
        return self._params.get("process_mode", "OCR + ASR（完整流程）")

    @process_mode.setter
    def process_mode(self, val: str):
        self._params["process_mode"] = val

    @property
    def corr_preset_name(self) -> str:
        return self._params.get("corr_preset", "")

    @corr_preset_name.setter
    def corr_preset_name(self, val: str):
        self._params["corr_preset"] = val

    @property
    def prompt_text(self) -> str:
        return self._params.get("corr_prompt", "")

    @prompt_text.setter
    def prompt_text(self, val: str):
        self._params["corr_prompt"] = val

    @property
    def corr_summary_prompt(self) -> str:
        return self._params.get("corr_summary_prompt", "")

    @corr_summary_prompt.setter
    def corr_summary_prompt(self, val: str):
        self._params["corr_summary_prompt"] = val

    @property
    def corr_system_prompt(self) -> str:
        return self._params.get("corr_system_prompt", "")

    @corr_system_prompt.setter
    def corr_system_prompt(self, val: str):
        self._params["corr_system_prompt"] = val

    @property
    def asr_model(self) -> str:
        return self._params.get("asr_model_path", "")

    @asr_model.setter
    def asr_model(self, val: str):
        self._params["asr_model_path"] = val

    @property
    def asr_language(self) -> str:
        return self._params.get("asr_language", "zh")

    @asr_language.setter
    def asr_language(self, val: str):
        self._params["asr_language"] = val

    @property
    def asr_region_name(self) -> str:
        return self._params.get("asr_region_name", "语音")

    @asr_region_name.setter
    def asr_region_name(self, val: str):
        self._params["asr_region_name"] = val

    @property
    def asr_model_size(self) -> str:
        return self._params.get("asr_model_size", "large-v3")

    @asr_model_size.setter
    def asr_model_size(self, val: str):
        self._params["asr_model_size"] = val

    # ── 模板管理 ──

    def set_template_names(self, names: list[str]):
        self._template_names = list(names)

    def set_template_contents(self, contents: dict[str, str]):
        self._template_contents = dict(contents)

    def select_template(self, name: str):
        self._current_template = name

    def get_template_content(self, name: str) -> str:
        return self._template_contents.get(name, "")

    def _open_template_editor(self):
        """打开模板编辑器弹窗（延迟导入避免循环）。"""
        from ui.template_editor import TemplateEditorDialog
        dlg = TemplateEditorDialog(self._template_names, self._template_contents)
        dlg.template_saved.connect(self.template_saved.emit)
        dlg.template_deleted.connect(self.template_deleted.emit)
        dlg.prompt_changed.connect(self.prompt_changed.emit)
        dlg.exec_()

    def _on_corr_template_selected(self, name: str):
        """AI 纠错模板选择：将选中模板内容注入到 corrector。"""
        if not name or name == "（选择模板）":
            self.template_selected_for_correction.emit("")
            return
        content = self._template_contents.get(name, "")
        if content:
            self.template_selected_for_correction.emit(content)

    # ── 排序规则 ──

    def set_sort_rules(self, rules: list[tuple[str, str, str]]):
        self._sort_rules = list(rules)

    def get_sort_rules(self) -> list[tuple[str, str, str]]:
        return list(self._sort_rules)

    def set_region_names(self, names: list[str]):
        self._region_names = list(names)

