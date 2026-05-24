"""共享测试夹具（fixtures）。"""

import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest.fixture
def sample_image():
    """返回一张用于测试的空白图片（numpy array）。"""
    import numpy as np

    return np.zeros((100, 200, 3), dtype=np.uint8)


@pytest.fixture
def config_dir():
    """返回项目 config/ 目录路径。"""
    return ROOT_DIR / "config"


@pytest.fixture
def tmp_output(tmp_path):
    """返回临时输出目录。"""
    out = tmp_path / "output"
    out.mkdir()
    return out
