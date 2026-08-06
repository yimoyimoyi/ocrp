"""视图构建器基类 —— 属性双向委托 MainWindow。

设计目的：main_window 拆分时方法体原样搬移（零改动），
- 读取：视图上未定义的属性/方法自动转发到 MainWindow（__getattr__）
- 写入：视图上赋值的控件属性自动挂到 MainWindow（__setattr__），
  保证 _retranslate_ui 等 MainWindow 方法照常访问控件
- 反向：MainWindow.__getattr__ 转发视图类方法（见 MainWindow）
"""


class _ViewBase:
    """视图构建器基类：与 MainWindow 双向委托。"""

    def __init__(self, mgr):
        object.__setattr__(self, "_mgr", mgr)

    def __getattr__(self, name):
        return getattr(self._mgr, name)

    def __setattr__(self, name, value):
        if name == "_mgr":
            object.__setattr__(self, name, value)
        else:
            # 控件属性统一挂到 MainWindow，保持跨方法可见
            setattr(self._mgr, name, value)
