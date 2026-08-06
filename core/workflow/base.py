"""流程子域基类 —— 未定义的属性/方法自动委托给 WorkflowManager。

设计目的：子域拆分时方法体原样搬移（零改动），跨域调用
（如 _on_asr_finished → _do_ocr_pass）通过委托透明解析。
"""


class _FlowBase:
    """委托基类：flow 上未定义的任何属性/方法都转发到 manager。

    - 读（__getattr__）：flow 未定义的属性 → manager
    - 写（__setattr__）：flow 方法体内的 self.xxx = ... 一律写到 manager，
      保证 worker 状态（_video_worker 等）对 stop/pause/cleanup 可见
      （拆分前的行为是写入同一个 WorkflowManager 实例）
    """

    def __init__(self, mgr):
        object.__setattr__(self, "_mgr", mgr)

    def __getattr__(self, name):
        return getattr(self._mgr, name)

    def __setattr__(self, name, value):
        if name == "_mgr":
            object.__setattr__(self, name, value)
        else:
            setattr(self._mgr, name, value)
