"""域配置对象基类 —— attrs dict + 事件 + 原子写 + schema 校验（方案 A）。

总则：读文件、改内存、单笔写回；域对象是跨域业务参数的单一事实源，
替代 mode_params 双向镜像与手写同步分支（docs/设置流程复杂性与简化方案.md）。
"""

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.config_manager import CONFIG_DIR, _config_lock, atomic_write_json, load_json_with_comments

logger = logging.getLogger(__name__)


class ConfigObject(QObject):
    """域配置对象基类。

    - ``DEFAULTS`` 是本域的默认值注册中心（模板/UI/消费方共用来源）
    - ``DEAD_KEYS`` 在载入时清理（schema 治理：只清确认死键，未知键透传保留）
    - ``commit()`` 原子写回（复用 config_manager._config_lock 线性化写）
    - ``changed`` 信号携带变更键名（"" 表示整体重载），由 RebuildRouter 消费
    """

    changed = Signal(str)

    FILENAME: str = ""
    DEFAULTS: dict = {}
    SCHEMA: dict = {}
    DEAD_KEYS: tuple[str, ...] = ()

    def __init__(self, config_dir: Path | str | None = None):
        super().__init__()
        self._config_dir = Path(config_dir) if config_dir else CONFIG_DIR
        self._path = self._config_dir / self.FILENAME
        self._data: dict = {}
        self.load()

    # ── 加载与治理 ──
    def load(self) -> None:
        """读盘 + 死键清理 + 默认值补齐 + schema 校验（告警不阻断）。"""
        if self._path.exists():
            try:
                data = load_json_with_comments(self._path)
                if not isinstance(data, dict):
                    raise ValueError(f"{self.FILENAME} 不是有效的对象")
            except Exception as e:
                logger.warning("加载 %s 失败，回退默认值: %s", self.FILENAME, e)
                data = {}
        else:
            data = {}
        self._scrub(data)
        merged = deepcopy(self.DEFAULTS)
        merged.update(data)  # 文件值优先，缺键补默认（自愈）
        self._data = merged
        self._validate()

    def _scrub(self, data: dict) -> None:
        """死键清理（原地修改）。子类可覆盖做嵌套治理。"""
        for key in self.DEAD_KEYS:
            data.pop(key, None)

    def _validate(self) -> None:
        """schema 校验：仅告警不阻断（与现有加载行为一致）。"""
        if not self.SCHEMA:
            return
        try:
            from core.config_schema import validate_config

            ok, errors = validate_config(self._data, self.SCHEMA, self.FILENAME)
            if not ok:
                logger.warning("%s 校验失败: %s", self.FILENAME, "; ".join(errors[:3]))
        except Exception as e:
            logger.warning("%s schema 校验异常: %s", self.FILENAME, e)

    # ── 读取 ──
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def get_all(self) -> dict:
        return deepcopy(self._data)

    # ── 写入 ──
    def set(self, key: str, value: Any, persist: bool = False) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        if persist:
            self.commit()
        self.changed.emit(key)

    def update(self, patch: dict, persist: bool = True) -> None:
        """批量更新（键级幂等：值未变不重复写盘/发信号）。"""
        changed_keys = [k for k, v in patch.items() if self._data.get(k) != v]
        if not changed_keys:
            return
        self._data.update({k: patch[k] for k in changed_keys})
        if persist:
            self.commit()
        for k in changed_keys:
            self.changed.emit(k)

    def commit(self) -> None:
        """原子写盘（复用 config_manager 的写锁与原子写）。"""
        with _config_lock:
            atomic_write_json(self._path, self._data)

    def reload(self) -> None:
        """重读磁盘；内容变化时发整体重载信号。"""
        old = self._data
        self.load()
        if old != self._data:
            self.changed.emit("")

    def reset_to_defaults(self) -> None:
        """恢复本域出厂默认（内存 + 写盘）。"""
        self._data = deepcopy(self.DEFAULTS)
        self.commit()
        self.changed.emit("")
