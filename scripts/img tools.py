#!/usr/bin/env python3
"""
FFmpeg 高质量媒体处理工具 v3.1
- 外部 QSS 样式加载（styles/default.qss）
- 统一的 UI 缩放参数 (ui_scale)
- 完整的界面记忆功能
- JSON 配置自动加载/保存
- 预设管理功能
"""

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Union


def _load_json_with_comments(filepath: Union[str, Path]) -> Any:
    """读取 JSON 文件，自动去除 // 行注释和 /* */ 块注释后解析"""
    with open(filepath, encoding="utf-8") as f:
        text = f.read()
    # 去除 /* ... */ 块注释（非贪婪匹配）
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # 去除 // 行注释（注意避开 http:// 之类的链接）
    lines = []
    for line in text.split('\n'):
        # 查找行内第一个 // 且不在字符串内的位置（简化处理）
        idx = line.find('//')
        if idx >= 0:
            # 如果 // 前面有引号包裹则不处理（含链接）
            before = line[:idx]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                line = before
        lines.append(line)
    clean = '\n'.join(lines)
    return json.loads(clean)
from PyQt5.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# ================= 基础路径与初始化 =================
BASE_DIR = Path(sys.argv[0]).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
CONFIG_DIR = BASE_DIR / "config"
STYLES_DIR = BASE_DIR / "styles"

for d in (OUTPUT_DIR, TEMP_DIR, CONFIG_DIR):
    d.mkdir(exist_ok=True)

_FF_EXT = ".exe" if sys.platform == "win32" else ""
FFMPEG = shutil.which("ffmpeg") or str(BASE_DIR.parent / "core" / f"ffmpeg{_FF_EXT}")
FFPROBE = shutil.which("ffprobe") or str(BASE_DIR.parent / "core" / f"ffprobe{_FF_EXT}")

# ================= 默认配置 =================
DEFAULT_CONFIG = {
    "general": {
        "max_threads": 2,
        "output_dir": "output",
        "auto_open_output": True,
        "auto_load_preset": False,
        "auto_load_preset_path": "",
        "language": "zh-CN",
        # ---- UI 缩放 ----
        "ui_scale": 1.0,
        # ---- 窗口状态记忆 ----
        "window_x": -1,        # -1 = 由系统决定
        "window_y": -1,
        "window_width": 1100,
        "window_height": 920,
        "splitter_sizes": [],  # QSplitter 各区域大小
        "column_widths": [],   # 表格列宽
        "last_file_dir": "",   # 上次文件选择目录
        "last_mode_index": 0,  # 上次使用的功能模式
        # ---- 主题样式 ----
        "theme": "default"     # 当前选中的样式（对应 styles/*.qss 文件名，不含扩展名）
    },
    "mode_params": {
        "0": {"p1": "15", "p2": "720", "ss": "", "t": ""},
        "1": {"p1": "10", "p2": "png", "ss": "", "t": ""},
        "2": {"p1": "", "p2": "", "ss": "", "t": ""},
        "3": {"p1": "", "p2": "", "ss": "", "t": ""},
        "4": {"p1": "22", "p2": "", "ss": "", "t": ""},
        "5": {"p1": "", "p2": "", "ss": "00:00:00", "t": "10"},
        "6": {"p1": "", "p2": "mp4", "ss": "", "t": ""},
        "7": {"p1": "", "p2": "", "ss": "", "t": ""},
        "8": {"p1": "", "p2": "", "ss": "", "t": ""}
    },
    "gpu_enabled": True,
    "audio_mode": 0
}


# ================= QSS 样式加载器 =================
class StyleLoader:
    """加载外部 QSS 文件并应用缩放参数（sizes.json 外部化）"""

    # 内置默认值（仅当 sizes.json 不存在时使用）
    _DEFAULT_SIZES = {
        "base_font": 13, "small_font": 12, "title_font": 14, "mono_font": 13,
        "btn_font": 13, "btn_padding_v": 6, "btn_padding_h": 16, "btn_radius": 4,
        "combo_padding_v": 4, "combo_padding_h": 8, "combo_min_height": 24,
        "combo_dropdown_width": 24, "input_padding_v": 4, "input_padding_h": 8,
        "input_min_height": 24, "spin_padding": 4, "spin_min_height": 24,
        "checkbox_spacing": 6, "checkbox_size": 16, "table_item_padding": 4,
        "header_padding_v": 6, "header_padding_h": 6, "progress_height": 22,
        "splitter_height": 3, "group_margin_top": 12, "group_padding_top": 16,
    }

    _sizes_path = STYLES_DIR / "sizes.json"

    @staticmethod
    def _ensure_default_sizes_file():
        """如果 sizes.json 不存在，用默认值创建"""
        if not STYLES_DIR.exists():
            STYLES_DIR.mkdir(parents=True, exist_ok=True)
        if not StyleLoader._sizes_path.exists():
            template = {
                "description": "UI 控件基础尺寸定义文件 (scale=1.0 时的参考值)",
                "note": "修改此文件后，可通过菜单 '工具 \u2192 重新加载样式' 即时生效",
                "sizes": {}
            }
            for key, val in StyleLoader._DEFAULT_SIZES.items():
                template["sizes"][key] = {
                    "default": val, "min": 1, "max": 60,
                    "label": key, "group": "未分类"
                }
            try:
                with open(StyleLoader._sizes_path, "w", encoding="utf-8") as f:
                    json.dump(template, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    @staticmethod
    def get_sizes_meta():
        """返回 sizes.json 的完整元数据 dict（含 label/group/min/max）"""
        StyleLoader._ensure_default_sizes_file()
        try:
            data = _load_json_with_comments(StyleLoader._sizes_path)
            return data.get("sizes", {})
        except Exception:
            # 从内置默认值构建简易元数据
            return {k: {"default": v, "min": 1, "max": 60, "label": k, "group": "未分类"}
                    for k, v in StyleLoader._DEFAULT_SIZES.items()}

    @staticmethod
    def get_base_sizes():
        """返回 {key: base_value} 字典（不含元数据）"""
        meta = StyleLoader.get_sizes_meta()
        return {k: v.get("default", StyleLoader._DEFAULT_SIZES.get(k, 12))
                for k, v in meta.items()}

    @staticmethod
    def save_sizes(sizes_dict):
        """保存用户调整后的尺寸到 sizes.json

        Args:
            sizes_dict: {key: new_base_value} 字典
        """
        StyleLoader._ensure_default_sizes_file()
        try:
            data = _load_json_with_comments(StyleLoader._sizes_path)
        except Exception:
            data = {"sizes": {}}
        meta = data.setdefault("sizes", {})
        for key, val in sizes_dict.items():
            if key in meta:
                meta[key]["default"] = val
            else:
                meta[key] = {"default": val, "min": 1, "max": 60, "label": key, "group": "未分类"}
        try:
            with open(StyleLoader._sizes_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def reset_sizes_to_defaults():
        """将所有尺寸重置为内置默认值并保存"""
        meta = StyleLoader.get_sizes_meta()
        for key in meta:
            if key in StyleLoader._DEFAULT_SIZES:
                meta[key]["default"] = StyleLoader._DEFAULT_SIZES[key]
        try:
            with open(StyleLoader._sizes_path, "w", encoding="utf-8") as f:
                json.dump({"description": "UI 控件基础尺寸定义文件",
                           "note": "修改此文件后，可通过菜单重新加载样式即时生效",
                           "sizes": meta}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def list_styles():
        """扫描 styles 目录，返回可用样式名称列表"""
        if not STYLES_DIR.exists():
            return ["default"]
        qss_files = sorted(STYLES_DIR.glob("*.qss"))
        names = [f.stem for f in qss_files]
        return names if names else ["default"]

    @staticmethod
    def get_theme_label(theme_name):
        """从 themes.json 获取主题的中文显示名称"""
        themes_path = STYLES_DIR / "themes.json"
        if themes_path.exists():
            try:
                data = _load_json_with_comments(themes_path)
                themes = data.get("themes", {})
                if theme_name in themes:
                    return themes[theme_name].get("label", theme_name)
            except Exception:
                pass
        # 后备默认映射
        labels = {"default": "深色主题 (Dark)", "light": "浅色主题 (Light)"}
        return labels.get(theme_name, theme_name)

    @staticmethod
    def load(theme="default", scale=1.0):
        """加载 QSS 文件，按缩放比例替换模板变量

        Args:
            theme: 样式名称（对应 styles/{theme}.qss）
            scale: 缩放比例（0.7 ~ 1.5）
        """
        qss_path = STYLES_DIR / f"{theme}.qss"
        if not qss_path.exists():
            qss_path = STYLES_DIR / "default.qss"
            if not qss_path.exists():
                return ""

        try:
            with open(qss_path, encoding="utf-8") as f:
                template = f.read()
        except Exception:
            return ""

        # 从外部 sizes.json 加载基础尺寸
        base_sizes = StyleLoader.get_base_sizes()
        # 计算缩放后的尺寸
        sizes = {}
        for key, base_val in base_sizes.items():
            sizes[key] = max(1, round(base_val * scale))

        # 替换模板变量
        result = template
        for key, val in sizes.items():
            result = result.replace("{{" + key + "}}", str(val))

        return result


# ================= 配置管理器 =================
class ConfigManager:
    """管理 JSON 配置的加载/保存"""

    def __init__(self):
        self.config_path = CONFIG_DIR / "config.json"
        self.presets_path = CONFIG_DIR / "presets.json"
        self.config = self._load_config()
        self.presets = self._load_presets()

    def _load_config(self):
        """加载主配置，若不存在则创建默认配置"""
        if self.config_path.exists():
            try:
                cfg = _load_json_with_comments(self.config_path)
                return self._merge_defaults(cfg)
            except Exception:
                return dict(DEFAULT_CONFIG)
        else:
            self._save_config(DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)

    def _save_config(self, cfg):
        """保存配置到 JSON 文件"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def save_config(self):
        """保存当前配置"""
        self._save_config(self.config)

    def _merge_defaults(self, cfg):
        """递归合并默认值，确保配置结构完整"""
        result = dict(DEFAULT_CONFIG)
        for key, value in cfg.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value
        return result

    def _load_presets(self):
        """加载预设列表"""
        if self.presets_path.exists():
            try:
                data = _load_json_with_comments(self.presets_path)
                return data.get("presets", [])
            except Exception:
                return []
        else:
            self._save_presets([])
            return []

    def _save_presets(self, presets):
        """保存预设列表到 JSON 文件"""
        with open(self.presets_path, "w", encoding="utf-8") as f:
            json.dump({"presets": presets}, f, ensure_ascii=False, indent=2)

    def save_presets(self):
        """保存当前预设列表"""
        self._save_presets(self.presets)

    def get_mode_params(self, mode_idx):
        """获取指定模式的默认参数"""
        key = str(mode_idx)
        return self.config.get("mode_params", {}).get(key, {"p1": "", "p2": "", "ss": "", "t": ""})

    def set_mode_params(self, mode_idx, params):
        """保存指定模式的参数"""
        self.config.setdefault("mode_params", {})[str(mode_idx)] = params
        self.save_config()

    def get_general(self):
        """获取通用设置"""
        return self.config.get("general", DEFAULT_CONFIG["general"])

    def get_scale(self):
        """获取 UI 缩放值"""
        return self.get_general().get("ui_scale", 1.0)

    def get_theme(self):
        """获取当前主题样式名称"""
        return self.get_general().get("theme", "default")

    def export_config(self, filepath):
        """导出配置到指定文件"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def import_config(self, filepath):
        """从指定文件导入配置"""
        cfg = _load_json_with_comments(filepath)
        self.config = self._merge_defaults(cfg)
        self.save_config()
        return self.config

    def add_preset(self, name, mode_index, p1, p2, ss, t, use_gpu, audio_mode):
        """添加一个新预设"""
        preset = {
            "name": name,
            "mode_index": mode_index,
            "p1": p1,
            "p2": p2,
            "ss": ss,
            "t": t,
            "use_gpu": use_gpu,
            "audio_mode": audio_mode
        }
        for i, p in enumerate(self.presets):
            if p["name"] == name:
                self.presets[i] = preset
                self.save_presets()
                return
        self.presets.append(preset)
        self.save_presets()

    def delete_preset(self, name):
        """删除指定名称的预设"""
        self.presets = [p for p in self.presets if p["name"] != name]
        self.save_presets()

    def apply_preset(self, name):
        """应用指定预设，返回预设数据"""
        for p in self.presets:
            if p["name"] == name:
                return p
        return None


# ================= 模式加载器 =================
class ModeLoader:
    """从外部 modes/modes.json 加载功能模式定义"""

    _modes_path = BASE_DIR / "modes" / "modes.json"
    _modes_data = None  # 缓存

    @staticmethod
    def _ensure_file():
        """确保 modes.json 存在，缺失时创建默认文件"""
        if not ModeLoader._modes_path.exists():
            try:
                (BASE_DIR / "modes").mkdir(parents=True, exist_ok=True)
                default = {
                    "description": "FFmpeg 功能模式定义文件",
                    "note": "可自由添加/修改模式。handler 为特殊处理函数名，留空则使用 commands 模板。",
                    "modes": [
                        {"id": 0, "name": "高质量 GIF (256色调色板)",
                         "params": [{"key": "p1", "label": "FPS", "placeholder": "默认15"},
                                    {"key": "p2", "label": "宽度", "placeholder": "默认720"}],
                         "show_audio_mode": False, "single_thread": False, "multi_file": False,
                         "handler": None, "commands": []}
                    ]
                }
                with open(ModeLoader._modes_path, "w", encoding="utf-8") as f:
                    json.dump(default, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    @staticmethod
    def load():
        """加载 modes.json, 返回模式列表; 失败返回空列表"""
        ModeLoader._ensure_file()
        if ModeLoader._modes_data is not None:
            return ModeLoader._modes_data
        try:
            data = _load_json_with_comments(ModeLoader._modes_path)
            ModeLoader._modes_data = data.get("modes", [])
        except Exception:
            ModeLoader._modes_data = []
        return ModeLoader._modes_data

    @staticmethod
    def reload():
        """清除缓存，强制重新加载"""
        ModeLoader._modes_data = None
        return ModeLoader.load()

    @staticmethod
    def get_mode_names():
        """返回模式名称列表"""
        return [m["name"] for m in ModeLoader.load()]

    @staticmethod
    def get_mode(index):
        """按索引获取模式定义; 越界返回 None"""
        modes = ModeLoader.load()
        if 0 <= index < len(modes):
            return modes[index]
        return None

    @staticmethod
    def get_param_placeholders(index):
        """返回 (p1_placeholder, p2_placeholder)"""
        mode = ModeLoader.get_mode(index)
        if not mode:
            return ("留空默认", "留空默认")
        params = mode.get("params", [])
        p1 = params[0]["placeholder"] if len(params) > 0 else "留空默认"
        p2 = params[1]["placeholder"] if len(params) > 1 else "留空默认"
        return (p1, p2)

    @staticmethod
    def get_param_labels(index):
        """返回 (p1_label, p2_label)"""
        mode = ModeLoader.get_mode(index)
        if not mode:
            return ("参数1", "参数2")
        params = mode.get("params", [])
        p1 = params[0]["label"] if len(params) > 0 else "参数1"
        p2 = params[1]["label"] if len(params) > 1 else "参数2"
        return (p1, p2)

    @staticmethod
    def has_handler(index):
        """检查模式是否有 Python handler"""
        mode = ModeLoader.get_mode(index)
        return mode is not None and bool(mode.get("handler"))

    @staticmethod
    def get_handler_name(index):
        """返回 handler 名称; 无则返回空字符串"""
        mode = ModeLoader.get_mode(index)
        if mode:
            return mode.get("handler", "") or ""
        return ""

    @staticmethod
    def get_flag(index, key, default=False):
        """获取模式标志位"""
        mode = ModeLoader.get_mode(index)
        if mode:
            return mode.get(key, default)
        return default

    @staticmethod
    def get_commands(index, use_gpu=True):
        """获取命令模板列表
        
        优先返回 commands_gpu/commands_cpu（若存在），
        否则返回通用 commands。
        """
        mode = ModeLoader.get_mode(index)
        if not mode:
            return []
        if use_gpu:
            cmds = mode.get("commands_gpu") or mode.get("commands") or []
        else:
            cmds = mode.get("commands_cpu") or mode.get("commands") or []
        return cmds

    @staticmethod
    def resolve_template(cmd_tokens, ctx):
        """将命令模板中的 {{var}} 替换为实际值
        
        Args:
            cmd_tokens: 字符串列表，每个元素可能含 {{var}} 或 {{var|default}}
            ctx: 上下文变量字典
        
        Returns:
            替换后的字符串列表（空字符串被过滤）
        """
        import re
        result = []
        for token in cmd_tokens:
            def replacer(m):
                expr = m.group(1)
                if "|" in expr:
                    var_name, default_val = expr.split("|", 1)
                else:
                    var_name, default_val = expr, ""
                val = ctx.get(var_name, "")
                return str(val) if val else default_val
            resolved = re.sub(r"\{\{(.+?)\}\}", replacer, token)
            if resolved:
                result.append(resolved)
        return result


# ================= 工具函数 =================
def time_to_seconds(time_str):
    try:
        if not time_str:
            return 0
        parts = time_str.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except:
        return 0


# ================= 信号管理器 =================
class WorkerSignals(QObject):
    log = pyqtSignal(str, str)          # 文件名, 信息
    progress = pyqtSignal(str, int)     # 文件名, 进度
    finished = pyqtSignal(str, bool, str)  # 文件名, 是否成功, 输出目录
    row_remove = pyqtSignal(str)        # 文件路径


# ================= 工作线程 =================
class FFmpegWorker(QRunnable):
    def __init__(self, file_path, cmds):
        super().__init__()
        self.file_path = file_path
        self.file_name = Path(file_path).name if file_path != "MERGE_TASK" else "合并任务"
        self.cmds = cmds
        self.signals = WorkerSignals()
        self.process = None
        self.is_killed = False

    @pyqtSlot()
    def run(self):
        success = True
        last_out_dir = str(OUTPUT_DIR)

        try:
            for i, cmd in enumerate(self.cmds, 1):
                if self.is_killed:
                    break

                self.signals.log.emit(self.file_name, f"🚀 [步骤 {i}/{len(self.cmds)}] 开始...")

                out_path = Path(cmd[-1])
                last_out_dir = str(out_path.parent)

                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )

                total_duration = 0
                for line in self.process.stdout:
                    if self.is_killed:
                        self.process.terminate()
                        break

                    if "Duration:" in line and total_duration == 0:
                        match = re.search(r"Duration:\s(\d+:\d+:\d+\.\d+)", line)
                        if match:
                            total_duration = time_to_seconds(match.group(1))

                    if "time=" in line:
                        match = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
                        if match and total_duration > 0:
                            curr = time_to_seconds(match.group(1))
                            prog = int((curr / total_duration) * 100)
                            self.signals.progress.emit(self.file_path, min(prog, 100))

                    if "frame=" not in line and "size=" not in line:
                        if line.strip():
                            self.signals.log.emit(self.file_name, line.strip())

                self.process.wait()
                if self.process.returncode != 0 and not self.is_killed:
                    success = False
                    break

            palette_file = TEMP_DIR / f"palette_{Path(self.file_path).stem}.png"
            if palette_file.exists():
                palette_file.unlink()

        except Exception:
            self.signals.log.emit(self.file_name, f"❌ 异常: {traceback.format_exc()}")
            success = False
        finally:
            if not self.is_killed:
                self.signals.finished.emit(self.file_name, success, last_out_dir)
                self.signals.row_remove.emit(self.file_path)

    def stop(self):
        self.is_killed = True
        if self.process:
            self.process.terminate()


# ================= 预设管理对话框 =================
class PresetDialog(QDialog):
    def __init__(self, config_mgr, parent=None):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.setWindowTitle("🎛️ 预设管理")
        self.setMinimumSize(500, 400)
        self.init_ui()
        self.load_presets()

    def init_ui(self):
        layout = QVBoxLayout(self)
        self.list_presets = QListWidget()
        self.list_presets.currentRowChanged.connect(self.on_selection_changed)
        layout.addWidget(QLabel("已保存的预设:"))
        layout.addWidget(self.list_presets)

        btn_layout = QHBoxLayout()
        self.btn_apply = QPushButton("✅ 应用预设")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self.apply_preset)
        self.btn_delete = QPushButton("🗑️ 删除预设")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self.delete_preset)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)

        btn_layout.addWidget(self.btn_apply)
        btn_layout.addWidget(self.btn_delete)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

    def load_presets(self):
        self.list_presets.clear()
        for p in self.config_mgr.presets:
            item = QListWidgetItem(p["name"])
            item.setData(Qt.UserRole, p["name"])
            self.list_presets.addItem(item)

    def on_selection_changed(self, row):
        has_selection = row >= 0
        self.btn_apply.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)

    def apply_preset(self):
        item = self.list_presets.currentItem()
        if item:
            name = item.data(Qt.UserRole)
            self.done(QMessageBox.Yes)
            self.selected_preset = name

    def delete_preset(self):
        item = self.list_presets.currentItem()
        if item:
            name = item.data(Qt.UserRole)
            reply = QMessageBox.question(
                self, "确认删除", f"确定要删除预设「{name}」吗？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.config_mgr.delete_preset(name)
                self.load_presets()


# ================= 高级设置对话框 =================
class SettingsDialog(QDialog):
    def __init__(self, config_mgr, parent=None):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.setWindowTitle("设置")
        self.setMinimumSize(680, 680)
        self._size_spinboxes = {}  # {key: QSpinBox}
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ============ 显示设置 ============
        grp_display = QGroupBox("显示设置")
        display_layout = QVBoxLayout()

        # 缩放滑块
        scale_row = QHBoxLayout()
        self.slider_scale = QSlider(Qt.Horizontal)
        self.slider_scale.setRange(70, 150)
        self.slider_scale.setTickPosition(QSlider.TicksBelow)
        self.slider_scale.setTickInterval(10)
        self.slider_scale.valueChanged.connect(self.on_scale_changed)
        self.lbl_scale_value = QLabel("100%")
        self.lbl_scale_value.setMinimumWidth(50)
        self.lbl_scale_value.setAlignment(Qt.AlignCenter)

        scale_row.addWidget(QLabel("小"))
        scale_row.addWidget(self.slider_scale, 1)
        scale_row.addWidget(QLabel("大"))
        scale_row.addWidget(self.lbl_scale_value)
        display_layout.addWidget(QLabel("界面字体和控件整体缩放:"))
        display_layout.addLayout(scale_row)

        # 主题选择
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("界面主题:"))
        self.combo_theme = QComboBox()
        self.combo_theme.setMinimumWidth(200)
        theme_row.addWidget(self.combo_theme, 1)
        theme_row.addStretch()
        display_layout.addLayout(theme_row)

        grp_display.setLayout(display_layout)
        layout.addWidget(grp_display)

        # ============ 通用设置 ============
        grp_general = QGroupBox("通用设置")
        form = QFormLayout()

        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 8)
        self.spin_threads.setToolTip("同时处理的最大任务数（增大可提高吞吐量，但会增加CPU负载）")
        form.addRow("最大并发线程数:", self.spin_threads)

        self.cb_auto_open = QCheckBox("完成后自动打开输出目录")
        form.addRow("", self.cb_auto_open)

        self.cb_auto_load = QCheckBox("启动时自动加载上次的预设配置")
        form.addRow("", self.cb_auto_load)

        grp_general.setLayout(form)
        layout.addWidget(grp_general)

        # ============ 输出路径 ============
        grp_output = QGroupBox("输出路径")
        out_layout = QHBoxLayout()
        self.edit_output_dir = QLineEdit()
        self.edit_output_dir.setReadOnly(True)
        self.btn_browse = QPushButton("浏览...")
        self.btn_browse.clicked.connect(self.browse_output)
        out_layout.addWidget(self.edit_output_dir)
        out_layout.addWidget(self.btn_browse)
        grp_output.setLayout(out_layout)
        layout.addWidget(grp_output)

        # ============ 高级尺寸调整 ============
        grp_sizes = QGroupBox("高级尺寸调整")
        grp_sizes.setToolTip("修改 styles/sizes.json 中的基础尺寸值，配合缩放比例共同决定最终显示大小")
        sizes_layout = QVBoxLayout()

        # 从外部 sizes.json 加载元数据
        meta = StyleLoader.get_sizes_meta()
        # 按 group 分组
        groups = {}
        for key, info in meta.items():
            grp = info.get("group", "其他")
            groups.setdefault(grp, []).append((key, info))

        # 创建表格：分组 | 参数说明 | 当前值 | 最小 | 最大 | 重置
        self.table_sizes = QTableWidget(0, 4)
        self.table_sizes.setHorizontalHeaderLabels(["分组", "参数说明", "当前值", "重置"])
        self.table_sizes.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_sizes.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_sizes.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_sizes.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table_sizes.verticalHeader().setVisible(False)
        self.table_sizes.setAlternatingRowColors(True)
        self.table_sizes.setSelectionBehavior(QAbstractItemView.SelectRows)

        # 按分组顺序填充表格
        group_order = ["字体", "按钮", "输入控件", "表格", "其他", "未分类"]
        row = 0
        for grp_name in group_order:
            if grp_name not in groups:
                continue
            for key, info in groups[grp_name]:
                self.table_sizes.insertRow(row)
                # 分组名
                self.table_sizes.setItem(row, 0, QTableWidgetItem(grp_name))
                # 参数说明
                self.table_sizes.setItem(row, 1, QTableWidgetItem(info.get("label", key)))
                # 当前值（QSpinBox）
                spin = QSpinBox()
                spin.setRange(info.get("min", 0), info.get("max", 60))
                spin.setValue(info.get("default", 12))
                spin.setToolTip(f"键名: {key}\n范围: {info.get('min',0)} ~ {info.get('max',60)}")
                self.table_sizes.setCellWidget(row, 2, spin)
                self._size_spinboxes[key] = spin
                # 重置按钮
                default_val = StyleLoader._DEFAULT_SIZES.get(key, 12)
                btn_reset = QPushButton("重置")
                btn_reset.setObjectName("sizeResetBtn")
                btn_reset.setFixedSize(50, 24)
                btn_reset.clicked.connect(lambda checked, k=key, dv=default_val: self._reset_size(k, dv))
                self.table_sizes.setCellWidget(row, 3, btn_reset)
                row += 1

        self.table_sizes.setMinimumHeight(min(row * 32 + 30, 300))
        sizes_layout.addWidget(self.table_sizes)

        # 底部按钮行
        size_btn_row = QHBoxLayout()
        btn_reset_all = QPushButton("全部重置为默认值")
        btn_reset_all.setObjectName("resetAllSizesBtn")
        btn_reset_all.clicked.connect(self._reset_all_sizes)
        size_btn_row.addWidget(btn_reset_all)
        size_btn_row.addStretch()
        lbl_sizes_hint = QLabel("提示：修改后点击「确定」保存，然后使用菜单「工具 → 重新加载样式」预览效果")
        lbl_sizes_hint.setObjectName("sizesHintLabel")
        size_btn_row.addWidget(lbl_sizes_hint)
        sizes_layout.addLayout(size_btn_row)

        grp_sizes.setLayout(sizes_layout)
        layout.addWidget(grp_sizes)

        # ============ 关于 ============
        grp_about = QGroupBox("关于")
        about_layout = QVBoxLayout()
        about_label = QLabel(
            "FFmpeg 高质量媒体处理工具 v3.1\n"
            "支持 JSON 配置导入/导出、预设管理、GPU加速\n"
            "外部 QSS 样式、UI 缩放、界面状态记忆\n"
            "样式尺寸定义文件: styles/sizes.json\n"
            "主题元数据文件: styles/themes.json"
        )
        about_label.setWordWrap(True)
        about_layout.addWidget(about_label)
        grp_about.setLayout(about_layout)
        layout.addWidget(grp_about)

        # ============ 按钮 ============
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.save_settings)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    # ---------- 辅助方法 ----------

    def _reset_size(self, key, default_val):
        """重置单个尺寸到默认值"""
        if key in self._size_spinboxes:
            self._size_spinboxes[key].setValue(default_val)

    def _reset_all_sizes(self):
        """重置所有尺寸到内置默认值"""
        StyleLoader.reset_sizes_to_defaults()
        # 重新加载 UI
        self._size_spinboxes.clear()
        self.table_sizes.setRowCount(0)
        meta = StyleLoader.get_sizes_meta()
        groups = {}
        for key, info in meta.items():
            grp = info.get("group", "其他")
            groups.setdefault(grp, []).append((key, info))
        group_order = ["字体", "按钮", "输入控件", "表格", "其他", "未分类"]
        row = 0
        for grp_name in group_order:
            if grp_name not in groups:
                continue
            for key, info in groups[grp_name]:
                self.table_sizes.insertRow(row)
                self.table_sizes.setItem(row, 0, QTableWidgetItem(grp_name))
                self.table_sizes.setItem(row, 1, QTableWidgetItem(info.get("label", key)))
                spin = QSpinBox()
                spin.setRange(info.get("min", 0), info.get("max", 60))
                spin.setValue(info.get("default", 12))
                spin.setToolTip(f"键名: {key}\n范围: {info.get('min',0)} ~ {info.get('max',60)}")
                self.table_sizes.setCellWidget(row, 2, spin)
                self._size_spinboxes[key] = spin
                default_val = StyleLoader._DEFAULT_SIZES.get(key, 12)
                btn_reset = QPushButton("重置")
                btn_reset.setObjectName("sizeResetBtn")
                btn_reset.setFixedSize(50, 24)
                btn_reset.clicked.connect(lambda checked, k=key, dv=default_val: self._reset_size(k, dv))
                self.table_sizes.setCellWidget(row, 3, btn_reset)
                row += 1

    def on_scale_changed(self, val):
        self.lbl_scale_value.setText(f"{val}%")

    def load_settings(self):
        general = self.config_mgr.get_general()
        self.spin_threads.setValue(general.get("max_threads", 2))
        self.cb_auto_open.setChecked(general.get("auto_open_output", True))
        self.cb_auto_load.setChecked(general.get("auto_load_preset", False))
        self.edit_output_dir.setText(str(Path(general.get("output_dir", "output"))))
        scale = int(general.get("ui_scale", 1.0) * 100)
        self.slider_scale.setValue(scale)
        self.lbl_scale_value.setText(f"{scale}%")

        # 填充主题下拉列表（从 themes.json 读取显示名称）
        self.combo_theme.blockSignals(True)
        self.combo_theme.clear()
        available = StyleLoader.list_styles()
        current_theme = general.get("theme", "default")
        selected_idx = 0
        for i, name in enumerate(available):
            display = StyleLoader.get_theme_label(name)
            self.combo_theme.addItem(display, name)
            if name == current_theme:
                selected_idx = i
        self.combo_theme.setCurrentIndex(selected_idx)
        self.combo_theme.blockSignals(False)

    def save_settings(self):
        self.config_mgr.config["general"]["max_threads"] = self.spin_threads.value()
        self.config_mgr.config["general"]["auto_open_output"] = self.cb_auto_open.isChecked()
        self.config_mgr.config["general"]["auto_load_preset"] = self.cb_auto_load.isChecked()
        self.config_mgr.config["general"]["output_dir"] = self.edit_output_dir.text()
        self.config_mgr.config["general"]["ui_scale"] = self.slider_scale.value() / 100.0
        self.config_mgr.config["general"]["theme"] = self.combo_theme.currentData()
        self.config_mgr.save_config()

        # 保存尺寸调整
        sizes_to_save = {}
        for key, spin in self._size_spinboxes.items():
            sizes_to_save[key] = spin.value()
        StyleLoader.save_sizes(sizes_to_save)

        self.accept()

    def browse_output(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if dir_path:
            self.edit_output_dir.setText(dir_path)


# ================= GUI 主窗口 =================
class FFmpegGUI(QWidget):
    @staticmethod
    def MODE_NAMES():
        """从外部 modes.json 动态加载模式名称列表"""
        return ModeLoader.get_mode_names()

    def __init__(self):
        super().__init__()
        self.config_mgr = ConfigManager()
        self.threadpool = QThreadPool()
        self.active_workers = {}
        self.total_tasks = 0
        self.completed_tasks = 0
        self.file_progress = {}
        self.merge_task_progress = 0

        self.threadpool.setMaxThreadCount(
            self.config_mgr.get_general().get("max_threads", 2)
        )

        self.init_ui()
        self.check_env()
        self.apply_auto_load_preset()
        self.restore_window_state()

    def restore_window_state(self):
        """从配置恢复窗口位置和大小"""
        general = self.config_mgr.get_general()
        w = general.get("window_width", 1100)
        h = general.get("window_height", 920)
        x = general.get("window_x", -1)
        y = general.get("window_y", -1)
        self.resize(w, h)
        if x >= 0 and y >= 0:
            self.move(x, y)

    def apply_auto_load_preset(self):
        """启动时自动加载预设"""
        general = self.config_mgr.get_general()
        if general.get("auto_load_preset", False):
            preset_path = general.get("auto_load_preset_path", "")
            if preset_path and os.path.exists(preset_path):
                try:
                    self.load_config_from_file(preset_path, quiet=True)
                    self.log(f"<font color='#4CAF50'>📂 已自动加载配置: {preset_path}</font>")
                except Exception as e:
                    self.log(f"<font color='orange'>⚠️ 自动加载配置失败: {e}</font>")

    def check_env(self):
        if not shutil.which("ffmpeg") and not os.path.exists(FFMPEG):
            self.log("<font color='red'>❌ 错误: 未找到 ffmpeg。请安装或放置在脚本目录下。</font>")
            self.btn_run.setEnabled(False)
        else:
            self.log("<font color='#4CAF50'>✅ FFmpeg 检测通过</font>")

    def log(self, msg):
        self.log_output.append(msg)
        self.log_output.moveCursor(self.log_output.textCursor().End)

    # ================= 应用 QSS 样式 =================
    def apply_style_sheet(self):
        """加载并应用外部 QSS 样式表，同时强制指定全局调色板"""
        app = QApplication.instance()
        theme = self.config_mgr.get_theme()
        scale = self.config_mgr.get_scale()

        # 强制指定全局 QPalette，覆盖 Windows 系统默认白色
        palette = QPalette()
        if theme == "light":
            palette.setColor(QPalette.Window, QColor("#fcfcfc"))
            palette.setColor(QPalette.WindowText, QColor("#333333"))
            palette.setColor(QPalette.Base, QColor("#ffffff"))
            palette.setColor(QPalette.Text, QColor("#333333"))
            palette.setColor(QPalette.Button, QColor("#f5f5f5"))
            palette.setColor(QPalette.ButtonText, QColor("#333333"))
            palette.setColor(QPalette.Highlight, QColor("#cce5ff"))
            palette.setColor(QPalette.HighlightedText, QColor("#004080"))
            palette.setColor(QPalette.AlternateBase, QColor("#f5f5f5"))
            palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
            palette.setColor(QPalette.ToolTipText, QColor("#333333"))
            palette.setColor(QPalette.Link, QColor("#0067c0"))
        else:
            # 默认深色调色板
            palette.setColor(QPalette.Window, QColor("#1e1e1e"))
            palette.setColor(QPalette.WindowText, QColor("#e0e0e0"))
            palette.setColor(QPalette.Base, QColor("#252526"))
            palette.setColor(QPalette.Text, QColor("#dcdcdc"))
            palette.setColor(QPalette.Button, QColor("#333333"))
            palette.setColor(QPalette.ButtonText, QColor("#efefef"))
            palette.setColor(QPalette.Highlight, QColor("#094771"))
            palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
            palette.setColor(QPalette.AlternateBase, QColor("#2d2d2d"))
            palette.setColor(QPalette.ToolTipBase, QColor("#333333"))
            palette.setColor(QPalette.ToolTipText, QColor("#ffffff"))
            palette.setColor(QPalette.Link, QColor("#4a9eff"))
        if app:
            app.setPalette(palette)

        # 加载 QSS 样式表
        qss = StyleLoader.load(theme=theme, scale=scale)
        if qss:
            self.setStyleSheet(qss)
        else:
            self.log("<font color='orange'>⚠️ 未找到样式文件，使用内置样式。</font>")

    # ================= UI 初始化 =================
    def init_ui(self):
        self.setWindowTitle("FFmpeg 高质量媒体处理工具 v3.1")
        self.setAcceptDrops(True)

        # 应用外部 QSS 样式（带缩放）
        self.apply_style_sheet()

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 4, 8, 8)
        main_layout.setSpacing(6)

        # 菜单栏
        self.create_menu(main_layout)

        # 主分割器
        self.main_splitter = QSplitter(Qt.Vertical)

        # --- 1. 队列区 ---
        container_top = QWidget()
        layout_top = QVBoxLayout(container_top)
        layout_top.setContentsMargins(0, 0, 0, 0)
        group_queue = QGroupBox("📅 任务队列 (支持拖拽多文件)")
        queue_layout = QVBoxLayout()
        queue_layout.setSpacing(4)

        self.table_queue = QTableWidget(0, 3)
        self.table_queue.setHorizontalHeaderLabels(["状态", "文件名", "完整路径"])
        self.table_queue.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_queue.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_queue.setColumnWidth(0, 130)
        self.table_queue.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_queue.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_queue.setAlternatingRowColors(True)
        self.table_queue.verticalHeader().setDefaultSectionSize(28)
        self.table_queue.verticalHeader().setVisible(False)

        # 从配置恢复列宽
        self.restore_table_column_widths()

        queue_layout.addWidget(self.table_queue)

        btn_q_layout = QHBoxLayout()
        btn_q_layout.setSpacing(8)
        self.btn_select = QPushButton("➕ 添加文件")
        self.btn_select.setObjectName("btnAddFiles")
        self.btn_select.clicked.connect(self.select_files)

        self.btn_clear = QPushButton("🗑️ 清空已完成")
        self.btn_clear.setObjectName("btnClearDone")
        self.btn_clear.clicked.connect(self.clear_queue)

        self.btn_remove_sel = QPushButton("✖️ 移除选中")
        self.btn_remove_sel.setObjectName("btnRemoveSel")
        self.btn_remove_sel.clicked.connect(self.remove_selected)

        btn_q_layout.addWidget(self.btn_select)
        btn_q_layout.addWidget(self.btn_remove_sel)
        btn_q_layout.addWidget(self.btn_clear)
        btn_q_layout.addStretch()

        self.lbl_file_count = QLabel("文件数: 0")
        self.lbl_file_count.setObjectName("fileCountLabel")
        btn_q_layout.addWidget(self.lbl_file_count)

        queue_layout.addLayout(btn_q_layout)
        group_queue.setLayout(queue_layout)
        layout_top.addWidget(group_queue)

        self.main_splitter.addWidget(container_top)

        # --- 2. 设置与控制区 ---
        container_mid = QWidget()
        layout_mid = QVBoxLayout(container_mid)
        layout_mid.setContentsMargins(0, 0, 0, 0)
        layout_mid.setSpacing(6)

        group_set = QGroupBox("🛠️ 参数设置与控制")
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setContentsMargins(10, 16, 10, 10)

        # 第一行
        grid.addWidget(QLabel("功能模式:"), 0, 0)
        self.combo_action = QComboBox()
        self.combo_action.addItems(self.MODE_NAMES())
        self.combo_action.currentIndexChanged.connect(self.on_mode_changed)
        grid.addWidget(self.combo_action, 0, 1)

        self.cb_gpu = QCheckBox("🚀 GPU 加速 (NVENC)")
        self.cb_gpu.setChecked(self.config_mgr.config.get("gpu_enabled", True))
        self.cb_gpu.stateChanged.connect(self.update_preview)
        grid.addWidget(self.cb_gpu, 0, 2)

        self.btn_preset = QPushButton("🎛️ 预设")
        self.btn_preset.setObjectName("btnPreset")
        self.btn_preset.clicked.connect(self.manage_presets)
        grid.addWidget(self.btn_preset, 0, 3)

        # 第二行
        grid.addWidget(QLabel("参数1 (FPS/CRF/...):"), 1, 0)
        self.input_p1 = QLineEdit()
        self.input_p1.setPlaceholderText("留空默认")
        self.input_p1.textChanged.connect(self.update_preview)
        grid.addWidget(self.input_p1, 1, 1)

        grid.addWidget(QLabel("参数2 (宽度/格式/...):"), 1, 2)
        self.input_p2 = QLineEdit()
        self.input_p2.setPlaceholderText("留空默认")
        self.input_p2.textChanged.connect(self.update_preview)
        grid.addWidget(self.input_p2, 1, 3)

        # 第三行
        grid.addWidget(QLabel("开始时间:"), 2, 0)
        self.input_ss = QLineEdit()
        self.input_ss.setPlaceholderText("00:00:00")
        self.input_ss.textChanged.connect(self.update_preview)
        grid.addWidget(self.input_ss, 2, 1)

        grid.addWidget(QLabel("持续时长:"), 2, 2)
        self.input_t = QLineEdit()
        self.input_t.setPlaceholderText("秒数")
        self.input_t.textChanged.connect(self.update_preview)
        grid.addWidget(self.input_t, 2, 3)

        # 第四行
        grid.addWidget(QLabel("音频处理模式:"), 3, 0)
        self.combo_audio_mode = QComboBox()
        self.combo_audio_mode.addItems(["混合为单音轨", "替换音轨", "添加音轨"])
        self.combo_audio_mode.setToolTip(
            "混合为单音轨：视频原音+所有外部音频混合成一个\n"
            "替换音轨：丢弃视频原音，外部音频混合成一个\n"
            "添加音轨：保留视频原音，每个外部音频作为独立音轨"
        )
        self.combo_audio_mode.currentIndexChanged.connect(self.update_preview)
        grid.addWidget(self.combo_audio_mode, 3, 1)
        self.combo_audio_mode.hide()

        self.lbl_threads = QLabel("并发数:")
        self.lbl_threads.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self.lbl_threads, 3, 2)
        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 8)
        self.spin_threads.setValue(self.config_mgr.get_general().get("max_threads", 2))
        self.spin_threads.valueChanged.connect(self.on_threads_changed)
        self.spin_threads.setToolTip("同时处理的最大任务数")
        grid.addWidget(self.spin_threads, 3, 3)

        group_set.setLayout(grid)
        layout_mid.addWidget(group_set)

        # 预设快捷栏
        preset_bar = QHBoxLayout()
        preset_bar.setSpacing(6)
        preset_bar.addWidget(QLabel("快速预设:"))
        self.combo_quick_preset = QComboBox()
        self.combo_quick_preset.setMinimumWidth(200)
        self.combo_quick_preset.addItem("(无)")
        self.combo_quick_preset.currentIndexChanged.connect(self.on_quick_preset_selected)
        preset_bar.addWidget(self.combo_quick_preset)

        self.btn_save_preset = QPushButton("💾 保存当前为预设")
        self.btn_save_preset.setObjectName("btnSavePreset")
        self.btn_save_preset.clicked.connect(self.save_current_as_preset)
        preset_bar.addWidget(self.btn_save_preset)

        self.btn_delete_preset = QPushButton("🗑️ 删除预设")
        self.btn_delete_preset.setObjectName("btnDeletePreset")
        self.btn_delete_preset.clicked.connect(self.delete_quick_preset)
        preset_bar.addWidget(self.btn_delete_preset)
        preset_bar.addStretch()
        layout_mid.addLayout(preset_bar)

        self.refresh_quick_presets()

        # 命令预览
        self.cmd_preview = QTextEdit()
        self.cmd_preview.setObjectName("previewOutput")
        self.cmd_preview.setMinimumHeight(60)
        self.cmd_preview.setMaximumHeight(100)
        self.cmd_preview.setReadOnly(True)
        self.cmd_preview.setPlaceholderText("FFmpeg 指令预览...")
        layout_mid.addWidget(self.cmd_preview)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        layout_mid.addWidget(self.progress_bar)

        # 控制按钮
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(8)

        self.btn_run = QPushButton("▶️ 开始处理")
        self.btn_run.setObjectName("btnRun")
        self.btn_run.setFixedHeight(42)
        self.btn_run.clicked.connect(self.start_batch)

        self.btn_stop = QPushButton("⏹️ 停止")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stop.setFixedHeight(42)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_all)

        self.btn_open_dir = QPushButton("📂 输出目录")
        self.btn_open_dir.setObjectName("btnOpenDir")
        self.btn_open_dir.setFixedHeight(42)
        self.btn_open_dir.clicked.connect(lambda: os.startfile(str(OUTPUT_DIR)))

        ctrl_layout.addWidget(self.btn_run, 2)
        ctrl_layout.addWidget(self.btn_stop, 1)
        ctrl_layout.addWidget(self.btn_open_dir, 1)
        layout_mid.addLayout(ctrl_layout)

        self.main_splitter.addWidget(container_mid)

        # --- 3. 日志区 ---
        container_bot = QWidget()
        layout_bot = QVBoxLayout(container_bot)
        layout_bot.setContentsMargins(0, 0, 0, 0)
        group_log = QGroupBox("📜 运行日志")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(6, 16, 6, 6)

        log_toolbar = QHBoxLayout()
        self.btn_clear_log = QPushButton("🧹 清空日志")
        self.btn_clear_log.setObjectName("btnClearLog")
        self.btn_clear_log.clicked.connect(lambda: self.log_output.clear())
        log_toolbar.addStretch()
        log_toolbar.addWidget(self.btn_clear_log)
        log_layout.addLayout(log_toolbar)

        self.log_output = QTextEdit()
        self.log_output.setObjectName("logOutput")
        self.log_output.setReadOnly(True)
        log_layout.addWidget(self.log_output)
        group_log.setLayout(log_layout)
        layout_bot.addWidget(group_log)

        self.main_splitter.addWidget(container_bot)

        # 分割器比例
        self.main_splitter.setStretchFactor(0, 2)
        self.main_splitter.setStretchFactor(1, 4)
        self.main_splitter.setStretchFactor(2, 3)

        # 恢复分割器位置
        self.restore_splitter_sizes()

        main_layout.addWidget(self.main_splitter, 1)
        self.setLayout(main_layout)

        # 加载参数
        self.load_mode_params()
        self.update_preview()

        # 恢复上次的模式索引
        self.restore_last_mode()

    # ================= UI 记忆：恢复/保存 =================

    def restore_table_column_widths(self):
        """从配置恢复表格列宽"""
        general = self.config_mgr.get_general()
        widths = general.get("column_widths", [])
        if len(widths) >= 3:
            self.table_queue.setColumnWidth(0, widths[0])
            self.table_queue.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
            # 列 1 和 2 保持 Stretch 模式

    def restore_splitter_sizes(self):
        """从配置恢复分割器位置"""
        general = self.config_mgr.get_general()
        sizes = general.get("splitter_sizes", [])
        if len(sizes) >= 3:
            self.main_splitter.setSizes(sizes)

    def restore_last_mode(self):
        """从配置恢复上次使用的功能模式"""
        general = self.config_mgr.get_general()
        last_mode = general.get("last_mode_index", 0)
        if 0 <= last_mode < len(self.MODE_NAMES()):
            self.combo_action.setCurrentIndex(last_mode)

    def save_ui_state(self):
        """保存所有 UI 状态到配置"""
        general = self.config_mgr.config["general"]
        # 窗口几何
        pos = self.pos()
        general["window_x"] = pos.x()
        general["window_y"] = pos.y()
        general["window_width"] = self.width()
        general["window_height"] = self.height()
        # 分割器
        general["splitter_sizes"] = self.main_splitter.sizes()
        # 列宽
        general["column_widths"] = [
            self.table_queue.columnWidth(0),
            self.table_queue.columnWidth(1),
            self.table_queue.columnWidth(2),
        ]
        # 最后使用的模式
        general["last_mode_index"] = self.combo_action.currentIndex()
        # 保存
        self.config_mgr.save_config()

    # ================= 菜单 =================
    def _strip_menu_frame(self, menu):
        """去除 QMenu 的 Windows 原生窗口边框，消除白色边缘"""
        flags = menu.windowFlags()
        menu.setWindowFlags(flags | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)

    def create_menu(self, main_layout):
        menubar = QMenuBar(self)
        menubar.setObjectName("mainMenuBar")

        # 文件菜单
        file_menu = menubar.addMenu("📁 文件")
        self._strip_menu_frame(file_menu)
        act_import = QAction("📥 导入配置 (JSON)...", self)
        act_import.triggered.connect(self.import_config)
        file_menu.addAction(act_import)

        act_export = QAction("📤 导出配置 (JSON)...", self)
        act_export.triggered.connect(self.export_config)
        file_menu.addAction(act_export)

        file_menu.addSeparator()

        act_settings = QAction("⚙️ 高级设置...", self)
        act_settings.triggered.connect(self.open_settings)
        file_menu.addAction(act_settings)

        file_menu.addSeparator()

        act_reload_style = QAction("🔄 重新加载样式", self)
        act_reload_style.triggered.connect(self.reload_style)
        file_menu.addAction(act_reload_style)

        file_menu.addSeparator()

        act_exit = QAction("退出", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # 工具菜单
        tool_menu = menubar.addMenu("🔧 工具")
        self._strip_menu_frame(tool_menu)
        act_presets = QAction("🎛️ 管理预设...", self)
        act_presets.triggered.connect(self.manage_presets)
        tool_menu.addAction(act_presets)

        act_save_preset = QAction("💾 保存当前设置为预设", self)
        act_save_preset.triggered.connect(self.save_current_as_preset)
        tool_menu.addAction(act_save_preset)

        tool_menu.addSeparator()

        act_open_output = QAction("📂 打开输出目录", self)
        act_open_output.triggered.connect(lambda: os.startfile(str(OUTPUT_DIR)))
        tool_menu.addAction(act_open_output)

        act_open_config = QAction("📂 打开配置目录", self)
        act_open_config.triggered.connect(lambda: os.startfile(str(CONFIG_DIR)))
        tool_menu.addAction(act_open_config)

        # 帮助菜单
        help_menu = menubar.addMenu("❓ 帮助")
        self._strip_menu_frame(help_menu)
        act_about = QAction("关于", self)
        act_about.triggered.connect(self.show_about)
        help_menu.addAction(act_about)

        main_layout.setMenuBar(menubar)

    def reload_style(self):
        """重新加载 QSS 样式（调整缩放后调用）"""
        self.apply_style_sheet()
        self.log("<font color='#4CAF50'>✅ 样式已重新加载</font>")

    def show_about(self):
        QMessageBox.about(self, "关于 FFmpeg 媒体处理工具",
            "<h3>FFmpeg 高质量媒体处理工具 v3.1</h3>"
            "<p>基于 FFmpeg 的批量媒体处理工具</p>"
            "<p><b>功能列表:</b></p>"
            "<ul>"
            "<li>高质量 GIF 生成 (256色调色板)</li>"
            "<li>帧序列提取 (PNG/JPG)</li>"
            "<li>音视频分离</li>"
            "<li>标准化转码 / 智能压缩</li>"
            "<li>快速裁剪 / 格式转换</li>"
            "<li>音视频合并 / 多音轨混合</li>"
            "</ul>"
            "<p><b>v3.1 新特性:</b></p>"
            "<ul>"
            "<li>外部 QSS 样式加载 (styles/default.qss)</li>"
            "<li>统一 UI 缩放参数 (0.7x~1.5x)</li>"
            "<li>窗口位置/大小/分割器记忆</li>"
            "<li>表格列宽记忆</li>"
            "<li>上次使用模式记忆</li>"
            "</ul>"
        )

    # ================= 配置管理 =================
    def load_config_from_file(self, filepath, quiet=False):
        try:
            cfg = self.config_mgr.import_config(filepath)
            self.cb_gpu.setChecked(cfg.get("gpu_enabled", True))
            self.spin_threads.setValue(cfg.get("general", {}).get("max_threads", 2))
            self.threadpool.setMaxThreadCount(self.spin_threads.value())

            mode_params = cfg.get("mode_params", {})
            idx = self.combo_action.currentIndex()
            params = mode_params.get(str(idx), {})
            self.input_p1.setText(params.get("p1", ""))
            self.input_p2.setText(params.get("p2", ""))
            self.input_ss.setText(params.get("ss", ""))
            self.input_t.setText(params.get("t", ""))

            if "audio_mode" in cfg:
                self.combo_audio_mode.setCurrentIndex(cfg["audio_mode"])
                self.config_mgr.config["audio_mode"] = cfg["audio_mode"]

            # 重新应用缩放和样式
            self.reload_style()

            self.update_preview()
            if not quiet:
                self.log(f"<font color='#4CAF50'>✅ 配置已导入: {filepath}</font>")
            return True
        except Exception as e:
            if not quiet:
                self.log(f"<font color='red'>❌ 导入配置失败: {e}</font>")
            return False

    def import_config(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "导入配置", str(CONFIG_DIR), "JSON 文件 (*.json)"
        )
        if filepath:
            self.load_config_from_file(filepath)

    def export_config(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出配置", str(CONFIG_DIR / "my_config.json"), "JSON 文件 (*.json)"
        )
        if filepath:
            try:
                self.save_current_state_to_config()
                self.config_mgr.export_config(filepath)
                self.log(f"<font color='#4CAF50'>✅ 配置已导出: {filepath}</font>")
            except Exception as e:
                self.log(f"<font color='red'>❌ 导出配置失败: {e}</font>")

    def save_current_state_to_config(self):
        idx = self.combo_action.currentIndex()
        params = {
            "p1": self.input_p1.text().strip(),
            "p2": self.input_p2.text().strip(),
            "ss": self.input_ss.text().strip(),
            "t": self.input_t.text().strip()
        }
        self.config_mgr.set_mode_params(idx, params)
        self.config_mgr.config["gpu_enabled"] = self.cb_gpu.isChecked()
        self.config_mgr.config["general"]["max_threads"] = self.spin_threads.value()
        self.config_mgr.config["audio_mode"] = self.combo_audio_mode.currentIndex()
        self.config_mgr.save_config()

    def open_settings(self):
        dialog = SettingsDialog(self.config_mgr, self)
        if dialog.exec_() == QDialog.Accepted:
            self.threadpool.setMaxThreadCount(
                self.config_mgr.get_general().get("max_threads", 2)
            )
            self.spin_threads.setValue(
                self.config_mgr.get_general().get("max_threads", 2)
            )
            global OUTPUT_DIR
            out_dir = self.config_mgr.get_general().get("output_dir", "output")
            OUTPUT_DIR = BASE_DIR / out_dir
            OUTPUT_DIR.mkdir(exist_ok=True)
            # 缩放改变 → 重新加载样式
            self.reload_style()
            self.log("<font color='#4CAF50'>✅ 设置已保存，样式已更新</font>")

    # ================= 预设管理 =================
    def refresh_quick_presets(self):
        current = self.combo_quick_preset.currentText()
        self.combo_quick_preset.blockSignals(True)
        self.combo_quick_preset.clear()
        self.combo_quick_preset.addItem("(无)")
        for p in self.config_mgr.presets:
            self.combo_quick_preset.addItem(p["name"])
        idx = self.combo_quick_preset.findText(current)
        if idx >= 0:
            self.combo_quick_preset.setCurrentIndex(idx)
        self.combo_quick_preset.blockSignals(False)

    def on_quick_preset_selected(self, index):
        if index <= 0:
            return
        name = self.combo_quick_preset.currentText()
        preset = self.config_mgr.apply_preset(name)
        if preset:
            self.apply_preset_data(preset)
            self.log(f"<font color='#4CAF50'>✅ 已应用预设: {name}</font>")

    def apply_preset_data(self, preset):
        self.combo_action.setCurrentIndex(preset.get("mode_index", 0))
        self.input_p1.setText(preset.get("p1", ""))
        self.input_p2.setText(preset.get("p2", ""))
        self.input_ss.setText(preset.get("ss", ""))
        self.input_t.setText(preset.get("t", ""))
        self.cb_gpu.setChecked(preset.get("use_gpu", True))
        if "audio_mode" in preset:
            self.combo_audio_mode.setCurrentIndex(preset["audio_mode"])
        self.update_preview()

    def save_current_as_preset(self):
        name, ok = QInputDialog.getText(self, "保存预设", "请输入预设名称:")
        if ok and name.strip():
            self.config_mgr.add_preset(
                name=name.strip(),
                mode_index=self.combo_action.currentIndex(),
                p1=self.input_p1.text().strip(),
                p2=self.input_p2.text().strip(),
                ss=self.input_ss.text().strip(),
                t=self.input_t.text().strip(),
                use_gpu=self.cb_gpu.isChecked(),
                audio_mode=self.combo_audio_mode.currentIndex()
            )
            self.refresh_quick_presets()
            self.log(f"<font color='#4CAF50'>✅ 预设已保存: {name}</font>")

    def delete_quick_preset(self):
        name = self.combo_quick_preset.currentText()
        if name and name != "(无)":
            reply = QMessageBox.question(
                self, "确认删除", f"确定要删除预设「{name}」吗？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.config_mgr.delete_preset(name)
                self.refresh_quick_presets()
                self.log(f"<font color='orange'>🗑️ 预设已删除: {name}</font>")

    def manage_presets(self):
        dialog = PresetDialog(self.config_mgr, self)
        result = dialog.exec_()
        self.refresh_quick_presets()
        if result == QMessageBox.Yes and hasattr(dialog, 'selected_preset'):
            preset = self.config_mgr.apply_preset(dialog.selected_preset)
            if preset:
                self.apply_preset_data(preset)
                self.log(f"<font color='#4CAF50'>✅ 已应用预设: {dialog.selected_preset}</font>")

    # ================= 模式切换 =================
    def on_mode_changed(self, index):
        """根据外部 modes.json 定义切换 UI 状态"""
        show_audio = ModeLoader.get_flag(index, "show_audio_mode", False)
        if show_audio:
            self.combo_audio_mode.show()
            self.lbl_threads.hide()
            self.spin_threads.hide()
        else:
            self.combo_audio_mode.hide()
            self.lbl_threads.show()
            self.spin_threads.show()

        self.load_mode_params()
        self.update_preview()
        self.update_param_placeholders(index)

    def update_param_placeholders(self, index):
        """从外部 modes.json 加载参数占位符"""
        p1, p2 = ModeLoader.get_param_placeholders(index)
        self.input_p1.setPlaceholderText(p1)
        self.input_p2.setPlaceholderText(p2)

    def load_mode_params(self):
        idx = self.combo_action.currentIndex()
        params = self.config_mgr.get_mode_params(idx)
        self.input_p1.setText(params.get("p1", ""))
        self.input_p2.setText(params.get("p2", ""))
        self.input_ss.setText(params.get("ss", ""))
        self.input_t.setText(params.get("t", ""))

    def on_threads_changed(self, val):
        self.threadpool.setMaxThreadCount(val)
        self.config_mgr.config["general"]["max_threads"] = val

    # ================= 核心逻辑 =================
    def _build_template_context(self, fpath):
        """构建模板变量上下文"""
        f = Path(fpath)
        p1, p2 = self.input_p1.text().strip(), self.input_p2.text().strip()
        ss, t = self.input_ss.text().strip(), self.input_t.text().strip()
        use_gpu = self.cb_gpu.isChecked()
        return {
            "ffmpeg": str(FFMPEG),
            "ffprobe": str(FFPROBE),
            "input": str(f),
            "input_stem": f.stem,
            "input_ext": f.suffix,
            "output_dir": str(OUTPUT_DIR),
            "temp_dir": str(TEMP_DIR),
            "ss": ss,
            "t": t,
            "p1": p1,
            "p2": p2,
            "vcodec": "h264_nvenc" if use_gpu else "libx264",
            "use_gpu": "1" if use_gpu else "0",
        }

    def generate_cmds(self, fpath):
        """根据外部 modes.json 定义生成 FFmpeg 命令列表"""
        idx = self.combo_action.currentIndex()
        ctx = self._build_template_context(fpath)

        # 收集所有文件列表
        all_files = []
        for row in range(self.table_queue.rowCount()):
            all_files.append(self.table_queue.item(row, 2).text())

        # 检查是否有 handler
        handler_name = ModeLoader.get_handler_name(idx)
        if handler_name == "merge":
            return self._handle_merge_cmd(all_files, ctx)
        elif handler_name == "audio_mix":
            return self._handle_audio_mix_cmd(all_files, ctx)
        elif handler_name == "convert":
            return self._handle_convert_cmd(ctx)

        # 通用模板命令生成
        use_gpu = self.cb_gpu.isChecked()
        templates = ModeLoader.get_commands(idx, use_gpu=use_gpu)
        if not templates:
            return []

        cmds = []
        ss_val = ctx.get("ss", "")
        t_val = ctx.get("t", "")
        for tmpl in templates:
            resolved = ModeLoader.resolve_template(tmpl, ctx)
            if resolved:
                # 在模板解析后，统一插入条件性的 -ss 和 -t 标志
                # 模板中不再包含 {{ss}} 和 {{t}}，由这里集中处理
                insert_pos = 1  # 在 ffmpeg 可执行路径之后插入
                if ss_val:
                    resolved[insert_pos:insert_pos] = ["-ss", ss_val]
                    insert_pos += 2
                if t_val:
                    # -t 在 -i INPUT 之后；找到 -i 的位置
                    try:
                        i_pos = resolved.index("-i")
                        resolved[i_pos + 2:i_pos + 2] = ["-t", t_val]
                    except ValueError:
                        resolved.insert(insert_pos, "-t")
                        resolved.insert(insert_pos + 1, t_val)
                cmds.append(resolved)
        return cmds

    def _handle_merge_cmd(self, all_files, ctx):
        """处理音视频合并（复杂逻辑，保留 Python handler）"""
        video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv',
                           '.m4v', '.mpg', '.mpeg', '.ts', '.m2ts', '.webm'}
        video_path = None
        audio_paths = []
        for fp in all_files:
            ext = Path(fp).suffix.lower()
            if ext in video_extensions:
                if video_path is None:
                    video_path = fp
                else:
                    audio_paths.append(fp)
            else:
                audio_paths.append(fp)
        if video_path is None:
            return []
        out = Path(ctx["output_dir"]) / f"{Path(video_path).stem}_merged.mp4"
        audio_mode = self.combo_audio_mode.currentIndex()

        cmd = [ctx["ffmpeg"], "-i", video_path]
        for aud in audio_paths:
            cmd += ["-i", aud]

        if audio_mode == 2:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
            for i in range(1, len(audio_paths) + 1):
                cmd += ["-map", f"{i}:a:0?"]
            cmd += ["-c:v", "copy", "-c:a", "aac", "-y", str(out)]
        else:
            if audio_mode == 1:
                input_audio_indices = list(range(1, len(audio_paths) + 1))
            else:
                input_audio_indices = [0] if Path(video_path).suffix.lower() in video_extensions else []
                input_audio_indices.extend(range(1, len(audio_paths) + 1))
            if not input_audio_indices:
                cmd += ["-map", "0:v:0", "-an", "-c:v", "copy", "-y", str(out)]
            elif len(input_audio_indices) == 1:
                cmd += ["-map", "0:v:0", "-map", f"{input_audio_indices[0]}:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-y", str(out)]
            else:
                filter_parts = [f"[{idx}:a:0]" for idx in input_audio_indices]
                filter_str = f"{''.join(filter_parts)}amix=inputs={len(input_audio_indices)}:duration=longest[a]"
                cmd += ["-filter_complex", filter_str, "-map", "0:v:0", "-map", "[a]",
                        "-c:v", "copy", "-c:a", "aac", "-y", str(out)]
        return [cmd]

    def _handle_audio_mix_cmd(self, all_files, ctx):
        """处理多音轨混合（保留 Python handler）"""
        if len(all_files) < 2:
            return []
        out = Path(ctx["output_dir"]) / f"{Path(all_files[0]).stem}_mixed.mp3"
        cmd = [ctx["ffmpeg"]]
        for aud in all_files:
            cmd += ["-i", aud]
        cmd += ["-filter_complex", f"amix=inputs={len(all_files)}:duration=longest",
                "-c:a", "libmp3lame", "-y", str(out)]
        return [cmd]

    def _handle_convert_cmd(self, ctx):
        """处理格式转换（需要 webp 条件判断，保留 Python handler）"""
        ext = ctx.get("p2", "") or "mp4"
        out_path = Path(ctx["output_dir"]) / f"{ctx['input_stem']}.{ext}"
        f = Path(ctx["input"])
        base = [ctx["ffmpeg"]]
        if ctx.get("ss"):
            base += ["-ss", ctx["ss"]]
        base += ["-i", str(f)]
        if ctx.get("t"):
            base += ["-t", ctx["t"]]
        if ext.lower() == "webp":
            return [base + ["-vcodec", "libwebp", "-q:v", "90",
                           "-compression_level", "6", "-loop", "0", "-y", str(out_path)]]
        else:
            return [base + ["-y", str(out_path)]]

    def update_preview(self):
        if self.table_queue.rowCount() > 0:
            f = self.table_queue.item(0, 2).text()
            cmds = self.generate_cmds(f)
            if cmds:
                preview = " && ".join([" ".join(c) for c in cmds])
                self.cmd_preview.setText("预览: " + preview)
            else:
                self.cmd_preview.setText("预览: 无效的命令（可能缺少输入文件）")
        else:
            self.cmd_preview.clear()

    # --- 任务控制 ---
    def start_batch(self):
        rows = self.table_queue.rowCount()
        if rows == 0:
            QMessageBox.warning(self, "提示", "请先添加文件到任务队列！")
            return

        idx = self.combo_action.currentIndex()
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)

        self.save_current_state_to_config()

        self.total_tasks = 0
        self.completed_tasks = 0
        self.file_progress.clear()
        self.merge_task_progress = 0

        is_multi = ModeLoader.get_flag(idx, "multi_file", False)
        is_single = ModeLoader.get_flag(idx, "single_thread", False)

        if is_single:
            self.threadpool.setMaxThreadCount(1)
        else:
            self.threadpool.setMaxThreadCount(self.spin_threads.value())

        if is_multi:  # 合并/混合等多文件模式
            if rows < 2:
                self.log("<font color='red'>⚠️ 该模式需要至少2个文件！</font>")
                self.btn_run.setEnabled(True)
                self.btn_stop.setEnabled(False)
                return

            fpath = self.table_queue.item(0, 2).text()
            if "MERGE_TASK" in self.active_workers:
                self.log("已有合并任务在运行，请等待完成。")
                return

            for row in range(rows):
                self.table_queue.setItem(row, 0, QTableWidgetItem("合并处理中..."))

            cmds = self.generate_cmds(fpath)
            if not cmds:
                self.log("⚠️ 任务创建失败：请检查队列文件数量或格式。")
                self.btn_run.setEnabled(True)
                return

            worker = FFmpegWorker("MERGE_TASK", cmds)
            worker.signals.log.connect(self.on_log)
            worker.signals.progress.connect(self.on_merge_progress)
            worker.signals.finished.connect(self.on_finished_merge)
            worker.signals.row_remove.connect(self.on_merge_complete)

            self.active_workers["MERGE_TASK"] = worker
            self.total_tasks = 1
            self.completed_tasks = 0
            self.threadpool.start(worker)
            return

        for row in range(rows):
            fpath = self.table_queue.item(row, 2).text()
            if fpath in self.active_workers:
                continue

            self.table_queue.setItem(row, 0, QTableWidgetItem("等待中..."))
            cmds = self.generate_cmds(fpath)
            if not cmds:
                self.log(f"⚠️ 无法为文件 {Path(fpath).name} 生成命令，跳过。")
                continue

            worker = FFmpegWorker(fpath, cmds)
            worker.signals.log.connect(self.on_log)
            worker.signals.progress.connect(self.on_progress)
            worker.signals.finished.connect(self.on_finished)
            worker.signals.row_remove.connect(self.remove_table_row)

            self.active_workers[fpath] = worker
            self.total_tasks += 1
            self.threadpool.start(worker)

        if self.total_tasks == 0:
            self.btn_run.setEnabled(True)
            self.btn_stop.setEnabled(False)

    def on_merge_progress(self, fpath, val):
        self.merge_task_progress = val
        self.progress_bar.setValue(val)

    def on_finished_merge(self, fname, success, out_dir):
        status = "✅ 完成" if success else "❌ 失败"
        self.log(f"<b>结果: {fname} {status}</b>")
        self.completed_tasks += 1
        if success and self.completed_tasks == self.total_tasks:
            if os.name == "nt":
                general = self.config_mgr.get_general()
                if general.get("auto_open_output", True):
                    os.startfile(out_dir)

    def on_merge_complete(self, _):
        self.table_queue.setRowCount(0)
        if "MERGE_TASK" in self.active_workers:
            del self.active_workers["MERGE_TASK"]
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(100)
        self.update_file_count()

    def stop_all(self):
        for worker in self.active_workers.values():
            worker.stop()
        self.active_workers.clear()
        self.log("<font color='orange'>⚠️ 已尝试停止所有任务</font>")
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

    # --- 信号槽 ---
    def on_log(self, fname, msg):
        self.log(f"[{fname}] {msg}")

    def on_progress(self, fpath, val):
        self.file_progress[fpath] = val
        if self.file_progress:
            avg_progress = sum(self.file_progress.values()) // len(self.file_progress)
            self.progress_bar.setValue(avg_progress)
        else:
            self.progress_bar.setValue(val)
        for row in range(self.table_queue.rowCount()):
            item = self.table_queue.item(row, 2)
            if item and item.text() == fpath:
                self.table_queue.setItem(row, 0, QTableWidgetItem(f"⏳ {val}%"))

    def on_finished(self, fname, success, out_dir):
        status = "✅ 完成" if success else "❌ 失败"
        self.log(f"<b>结果: {fname} {status}</b>")
        self.completed_tasks += 1
        for row in range(self.table_queue.rowCount()):
            item = self.table_queue.item(row, 1)
            if item and item.text() == fname:
                self.table_queue.setItem(row, 0, QTableWidgetItem("✅ 完成" if success else "❌ 失败"))

        if self.completed_tasks == self.total_tasks:
            self.btn_run.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.progress_bar.setValue(100)
            if success and out_dir:
                general = self.config_mgr.get_general()
                if general.get("auto_open_output", True):
                    if os.name == "nt":
                        os.startfile(out_dir)

    def remove_table_row(self, fpath):
        if fpath in self.active_workers:
            del self.active_workers[fpath]
        for row in range(self.table_queue.rowCount()):
            item = self.table_queue.item(row, 2)
            if item and item.text() == fpath:
                self.table_queue.removeRow(row)
                break
        self.update_file_count()

    def remove_selected(self):
        rows = set()
        for item in self.table_queue.selectedItems():
            rows.add(item.row())
        for row in sorted(rows, reverse=True):
            fpath = self.table_queue.item(row, 2).text()
            if fpath not in self.active_workers:
                self.table_queue.removeRow(row)
        self.update_file_count()
        self.update_preview()

    def update_file_count(self):
        count = self.table_queue.rowCount()
        self.lbl_file_count.setText(f"文件数: {count}")

    # --- UI 辅助 ---
    def select_files(self):
        general = self.config_mgr.get_general()
        default_dir = general.get("last_file_dir", "")
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择媒体文件",
            default_dir,
            "媒体文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.m4v *.mpg *.mpeg *.ts *.webm *.gif *.png *.jpg *.jpeg *.mp3 *.aac *.wav *.flac *.ogg);;所有文件 (*.*)"
        )
        if files:
            # 记忆目录
            self.config_mgr.config["general"]["last_file_dir"] = str(Path(files[0]).parent)
            for f in files:
                row = self.table_queue.rowCount()
                self.table_queue.insertRow(row)
                self.table_queue.setItem(row, 0, QTableWidgetItem("待处理"))
                self.table_queue.setItem(row, 1, QTableWidgetItem(Path(f).name))
                self.table_queue.setItem(row, 2, QTableWidgetItem(f))
            self.update_file_count()
            self.update_preview()

    def clear_queue(self):
        removed = 0
        for row in range(self.table_queue.rowCount() - 1, -1, -1):
            fpath = self.table_queue.item(row, 2).text()
            if fpath not in self.active_workers:
                self.table_queue.removeRow(row)
                removed += 1
        if removed > 0:
            self.update_file_count()
            self.update_preview()
            self.log(f"🧹 已清理 {removed} 个已完成/待处理的任务")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        files = [url.toLocalFile() for url in event.mimeData().urls()]
        if files:
            self.config_mgr.config["general"]["last_file_dir"] = str(Path(files[0]).parent)
        for f in files:
            row = self.table_queue.rowCount()
            self.table_queue.insertRow(row)
            self.table_queue.setItem(row, 0, QTableWidgetItem("待处理"))
            self.table_queue.setItem(row, 1, QTableWidgetItem(Path(f).name))
            self.table_queue.setItem(row, 2, QTableWidgetItem(f))
        self.update_file_count()
        self.update_preview()

    def closeEvent(self, event):
        """窗口关闭 — 保存所有 UI 状态"""
        self.stop_all()
        self.save_ui_state()
        event.accept()


# ================= 启动 =================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    gui = FFmpegGUI()
    gui.show()
    sys.exit(app.exec_())
