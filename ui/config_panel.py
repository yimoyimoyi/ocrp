"""配置面板 —— 纯状态管理类，不含 UI 控件。

SettingsDialog 是唯一的设置 UI 入口；ConfigPanel 负责：
  - 存储 UI 状态参数（mode_params 白名单）
  - 读透合并业务参数（asr/corr 域对象，方案 A：文件是持久化事实源）
  - 提供公共属性访问器供 Main Window 读写
  - 管理模板名/内容映射
  - 发射信号通知外部状态变更
"""

from PySide6.QtCore import QObject, Signal

from core.config_manager import MODE_PARAMS_DEFAULTS
from core.utils import SUBTITLE_MODE_STREAM, normalize_subtitle_mode


class ConfigPanel(QObject):
    """纯状态管理类，替代原先隐藏的 QWidget ConfigPanel。"""

    prompt_changed = Signal(str)
    mode_changed = Signal(dict)
    template_saved = Signal(str, str)
    template_deleted = Signal(str)
    filter_add_requested = Signal(str)
    filter_remove_requested = Signal(str)
    extract_env_clicked = Signal()

    def __init__(self, parent=None, registry=None):
        super().__init__(parent)
        # registry 为 None 时（测试/独立使用）回退 _params 直存模式
        self._registry = registry
        self._params: dict = dict(MODE_PARAMS_DEFAULTS)
        self._polish_enabled: bool = False
        self._template_names: list[str] = ["通用OCR"]
        self._template_contents: dict[str, str] = {}
        self._region_names: list[str] = []
        self._sort_rules: list[tuple[str, str, str]] = []  # [(prefix, name, suffix)]

    # ── 核心数据接口 ──

    def get_mode_params(self) -> dict:
        """返回当前所有模式参数的副本（白名单 + 域对象读透合并）。

        workflow/workers 继续消费完整键集（含 corr_enabled/asr_language 等
        业务键），业务键实时取自域对象（持久化单一事实源）。
        """
        merged = dict(self._params)
        reg = self._registry
        if reg is not None:
            for mp_key, file_key in reg.asr.KEY_MAP.items():
                merged.setdefault(mp_key, reg.asr.get(file_key))
            for mp_key, file_key in reg.correction.KEY_MAP.items():
                merged.setdefault(mp_key, reg.correction.get(file_key))
        return merged

    def apply_mode_params(self, params: dict):
        """将保存的参数应用到内部状态，发射 mode_changed 信号。

        只接受 UI 状态白名单键；业务键由域对象承担，此处静默忽略
        （R1/R2：避免对话框把业务键写回 mode_params 镜像）。
        """
        self._params.update({k: v for k, v in params.items() if k in self._params})
        self.mode_changed.emit(dict(self._params))

    def set_polish_enabled(self, val: bool):
        self._polish_enabled = val
        if self._registry is not None:
            self._registry.correction.set("enable_polish", val)
        else:
            self._params["corr_polish"] = val

    # ── 公共属性访问器（替代直接 widget 访问）──
    # 业务属性经域对象读写（文件键），UI 状态属性留在 _params

    @property
    def corr_enabled(self) -> bool:
        if self._registry is not None:
            return bool(self._registry.correction.get("enabled", False))
        return bool(self._params.get("corr_enabled", False))

    @corr_enabled.setter
    def corr_enabled(self, val: bool):
        if self._registry is not None:
            self._registry.correction.set("enabled", val)
        else:
            self._params["corr_enabled"] = val

    @property
    def post_sim_dedup(self) -> bool:
        return bool(self._params.get("post_sim_dedup", True))

    @post_sim_dedup.setter
    def post_sim_dedup(self, val: bool):
        self._params["post_sim_dedup"] = val

    @property
    def corr_translate(self) -> bool:
        if self._registry is not None:
            return bool(self._registry.correction.get("translate_mode", False))
        return bool(self._params.get("corr_translate", False))

    @corr_translate.setter
    def corr_translate(self, val: bool):
        if self._registry is not None:
            self._registry.correction.set("translate_mode", val)
        else:
            self._params["corr_translate"] = val

    @property
    def sentinel_enabled(self) -> bool:
        return bool(self._params.get("sentinel_enabled", True))

    @sentinel_enabled.setter
    def sentinel_enabled(self, val: bool):
        self._params["sentinel_enabled"] = val

    @property
    def subtitle_mode(self) -> str:
        """字幕模式（唯一规范 token：stream / regular，P21 解耦）。"""
        return normalize_subtitle_mode(self._params.get("subtitle_mode", SUBTITLE_MODE_STREAM))

    @subtitle_mode.setter
    def subtitle_mode(self, val: str):
        # 统一存储规范 token，避免配置值与 UI 语言绑定（P21）
        self._params["subtitle_mode"] = normalize_subtitle_mode(val)

    @property
    def process_mode(self) -> str:
        return str(self._params.get("process_mode", "OCR + ASR（完整流程）"))

    @process_mode.setter
    def process_mode(self, val: str):
        self._params["process_mode"] = val

    @property
    def corr_preset_name(self) -> str:
        return str(self._params.get("corr_preset", ""))

    @corr_preset_name.setter
    def corr_preset_name(self, val: str):
        self._params["corr_preset"] = val

    @property
    def prompt_text(self) -> str:
        if self._registry is not None:
            return str(self._registry.correction.get("correction_prompt", ""))
        return str(self._params.get("corr_prompt", ""))

    @prompt_text.setter
    def prompt_text(self, val: str):
        if self._registry is not None:
            self._registry.correction.set("correction_prompt", val)
        else:
            self._params["corr_prompt"] = val

    @property
    def corr_summary_prompt(self) -> str:
        return str(self._params.get("corr_summary_prompt", ""))

    @corr_summary_prompt.setter
    def corr_summary_prompt(self, val: str):
        self._params["corr_summary_prompt"] = val

    @property
    def corr_system_prompt(self) -> str:
        if self._registry is not None:
            return str(self._registry.correction.get("correction_system_prompt", ""))
        return str(self._params.get("corr_system_prompt", ""))

    @corr_system_prompt.setter
    def corr_system_prompt(self, val: str):
        if self._registry is not None:
            self._registry.correction.set("correction_system_prompt", val)
        else:
            self._params["corr_system_prompt"] = val

    @property
    def asr_model(self) -> str:
        return str(self._params.get("asr_model_path", ""))

    @asr_model.setter
    def asr_model(self, val: str):
        self._params["asr_model_path"] = val

    @property
    def asr_language(self) -> str:
        if self._registry is not None:
            return str(self._registry.asr.get("language", "zh"))
        return str(self._params.get("asr_language", "zh"))

    @asr_language.setter
    def asr_language(self, val: str):
        if self._registry is not None:
            self._registry.asr.set("language", val)
        else:
            self._params["asr_language"] = val

    @property
    def asr_region_name(self) -> str:
        if self._registry is not None:
            return str(self._registry.asr.get("asr_region_name", "语音"))
        return str(self._params.get("asr_region_name", "语音"))

    @asr_region_name.setter
    def asr_region_name(self, val: str):
        if self._registry is not None:
            self._registry.asr.set("asr_region_name", val)
        else:
            self._params["asr_region_name"] = val

    @property
    def asr_model_size(self) -> str:
        if self._registry is not None:
            return str(self._registry.asr.get("model_size", "large-v3"))
        return str(self._params.get("asr_model_size", "large-v3"))

    @asr_model_size.setter
    def asr_model_size(self, val: str):
        if self._registry is not None:
            self._registry.asr.set("model_size", val)
        else:
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
        dlg.exec()

    # ── 排序规则 ──

    def set_sort_rules(self, rules: list[tuple[str, str, str]]):
        self._sort_rules = list(rules)

    def get_sort_rules(self) -> list[tuple[str, str, str]]:
        return list(self._sort_rules)

    def set_region_names(self, names: list[str]):
        self._region_names = list(names)
