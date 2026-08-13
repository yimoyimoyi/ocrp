"""设置域对象包 —— 配置单一事实源（方案 A：读文件、改内存、单笔写回）。"""

from core.settings.base import ConfigObject
from core.settings.domains import AsrConfig, CorrectionConfig, OcrEnginesConfig, UiStateConfig
from core.settings.rebuild import RebuildRouter
from core.settings.registry import ConfigRegistry

__all__ = [
    "AsrConfig",
    "ConfigObject",
    "ConfigRegistry",
    "CorrectionConfig",
    "OcrEnginesConfig",
    "RebuildRouter",
    "UiStateConfig",
]
