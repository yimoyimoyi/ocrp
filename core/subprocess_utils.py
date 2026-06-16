"""基于 QProcess 的子进程管理器。

替代 subprocess.Popen + 手动 threading.Thread 的管道管理模式，
使用 Qt 原生信号处理 stdout/stderr，消除手动线程管理和轮询循环。

必须在有事件循环的线程中使用：
  - 主线程：有 Qt 主事件循环，直接可用
  - QThread：run() 中需调用 QCoreApplication.processEvents() 或 exec_()
"""

import json
import time
from collections.abc import Callable

from PyQt5.QtCore import QCoreApplication, QObject, QProcess, QProcessEnvironment, pyqtSignal

from core.logger import get_logger

logger = get_logger(__name__)


class QtSubprocessManager(QObject):
    """基于 QProcess 的子进程管理器，提供同步接口。

    用法::

        mgr = QtSubprocessManager()
        if mgr.start("python", ["server.py", "--config", "cfg.json"], ready_keyword="ready", timeout=120):
            mgr.send_json({"cmd": "transcribe", "audio": "test.wav"})
            resp = mgr.read_json_response(timeout=300)
            mgr.shutdown()

    信号：
        ready()           —— 子进程就绪（检测到 ready_keyword）
        error_occurred()  —— 启动失败或进程异常退出
    """

    ready = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._stderr_lines: list[str] = []
        self._ready = False
        self._ready_keyword = "ready"
        # stdout 行缓冲
        self._stdout_buffer: str = ""
        # 已解析的 JSON 响应队列
        self._response_queue: list[dict] = []
        # segment 流式队列
        self._segment_queue: list[dict] = []
        # 启动/就绪状态
        self._start_error: str | None = None

    # ── 生命周期 ──

    def start(
        self,
        program: str,
        args: list[str],
        env: dict[str, str] | None = None,
        ready_keyword: str = "ready",
        timeout: float = 120.0,
    ) -> bool:
        """启动子进程并等待 ready 信号。

        Args:
            program: 可执行文件路径
            args: 命令行参数
            env: 额外环境变量（合并到当前环境）
            ready_keyword: stderr 中出现此关键字表示就绪
            timeout: 等待就绪的超时秒数

        Returns:
            True 如果子进程就绪，False 如果启动失败或超时
        """
        self._proc = QProcess()
        self._proc.setProcessChannelMode(QProcess.SeparateChannels)

        if env:
            qenv = QProcessEnvironment.systemEnvironment()
            for k, v in env.items():
                qenv.insert(k, v)
            self._proc.setProcessEnvironment(qenv)

        self._ready_keyword = ready_keyword
        self._ready = False
        self._stderr_lines.clear()
        self._stdout_buffer = ""
        self._response_queue.clear()
        self._segment_queue.clear()
        self._start_error = None

        self._proc.readyReadStandardError.connect(self._on_stderr_ready)
        self._proc.readyReadStandardOutput.connect(self._on_stdout_ready)

        self._proc.start(program, args)
        if not self._proc.waitForStarted(5000):
            self._start_error = "子进程启动失败（waitForStarted 超时）"
            logger.error("QProcess 启动失败: %s %s", program, args)
            self._cleanup_proc()
            return False

        # 等待 ready（轮询 + processEvents 保持事件循环活跃）
        deadline = time.time() + timeout
        while time.time() < deadline:
            QCoreApplication.processEvents(QEventLoop_AllEvents, 100)
            if self._ready:
                logger.info("QProcess 就绪: %s", program)
                return True
            if self._proc.state() == QProcess.NotRunning:
                err = "\n".join(self._stderr_lines[-20:])
                logger.error("QProcess 提前退出: %s", err[:300])
                self._cleanup_proc()
                return False

        # 超时
        err = "\n".join(self._stderr_lines[-20:])
        logger.error("QProcess 等待就绪超时 (%ds): %s", timeout, err[:300])
        self.kill()
        return False

    def send_json(self, obj: dict):
        """发送 JSON 行到子进程 stdin。"""
        if not self._proc or self._proc.state() == QProcess.NotRunning:
            logger.warning("send_json: 子进程未运行")
            return
        data = json.dumps(obj, ensure_ascii=False) + "\n"
        self._proc.writeData(data.encode("utf-8"))

    def read_json_response(self, timeout: float = 300.0) -> dict | None:
        """同步等待一条 JSON 响应。

        在等待期间持续调用 processEvents() 保持事件循环活跃。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._response_queue:
                return self._response_queue.pop(0)
            QCoreApplication.processEvents(QEventLoop_AllEvents, 200)
            if self._proc and self._proc.state() == QProcess.NotRunning:
                # 进程已退出，检查剩余缓冲
                if self._response_queue:
                    return self._response_queue.pop(0)
                return None
        logger.warning("read_json_response 超时 (%.0fs)", timeout)
        return None

    def read_segments(self, on_segment: Callable[[dict], None], timeout: float = 300.0) -> str | None:
        """同步读取流式 segment，直到收到 done 或 error。

        Args:
            on_segment: 每收到一个 segment 时的回调
            timeout: 总超时秒数

        Returns:
            None 如果成功完成，错误消息字符串如果出错
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 处理已缓冲的 segment
            while self._segment_queue:
                seg = self._segment_queue.pop(0)
                on_segment(seg)
            # 检查响应队列中的 done/error
            while self._response_queue:
                resp = self._response_queue.pop(0)
                status = resp.get("status", "")
                if status == "done":
                    return None
                elif status == "error":
                    return resp.get("message", "unknown error")
            QCoreApplication.processEvents(QEventLoop_AllEvents, 200)
            if self._proc and self._proc.state() == QProcess.NotRunning:
                if self._segment_queue:
                    while self._segment_queue:
                        seg = self._segment_queue.pop(0)
                        on_segment(seg)
                return "进程意外退出"
        return f"超时 ({timeout}s)"

    def shutdown(self, timeout: float = 10.0):
        """发送 shutdown 命令并等待进程优雅退出。"""
        if not self._proc:
            return
        if self._proc.state() == QProcess.NotRunning:
            self._proc = None
            self._ready = False
            return
        try:
            self.send_json({"cmd": "shutdown"})
            if not self._proc.waitForFinished(int(timeout * 1000)):
                logger.warning("QProcess shutdown 超时，强制终止")
                self._proc.kill()
                self._proc.waitForFinished(3000)
        except Exception as e:
            logger.debug("QProcess shutdown 异常: %s", e)
            try:
                self._proc.kill()
                self._proc.waitForFinished(3000)
            except Exception:
                pass
        self._proc = None
        self._ready = False

    def kill(self):
        """强制终止子进程。"""
        if self._proc and self._proc.state() != QProcess.NotRunning:
            try:
                self._proc.kill()
                self._proc.waitForFinished(3000)
            except Exception as e:
                logger.debug("QProcess kill 异常: %s", e)
        self._proc = None
        self._ready = False

    def is_running(self) -> bool:
        """子进程是否正在运行。"""
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    @property
    def stderr_lines(self) -> list[str]:
        """获取 stderr 输出行（用于错误诊断）。"""
        return list(self._stderr_lines)

    # ── 内部信号处理 ──

    def _on_stderr_ready(self):
        """处理 stderr 数据，检测 ready 关键字。"""
        if not self._proc:
            return
        data = self._proc.readAllStandardError().data().decode("utf-8", errors="replace")
        for line in data.split("\n"):
            line = line.rstrip()
            if not line:
                continue
            self._stderr_lines.append(line)
            if self._ready_keyword in line and not self._ready:
                self._ready = True
                self.ready.emit()

    def _on_stdout_ready(self):
        """处理 stdout 数据，按行分割并解析 JSON。"""
        if not self._proc:
            return
        data = self._proc.readAllStandardOutput().data().decode("utf-8", errors="replace")
        self._stdout_buffer += data
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("QProcess stdout 非 JSON 行: %s", line[:100])
                continue
            status = obj.get("status", "")
            if status == "segment":
                self._segment_queue.append(obj)
            self._response_queue.append(obj)

    def _cleanup_proc(self):
        """清理进程资源。"""
        if self._proc:
            try:
                self._proc.readyReadStandardError.disconnect()
                self._proc.readyReadStandardOutput.disconnect()
            except Exception:
                pass
            try:
                if self._proc.state() != QProcess.NotRunning:
                    self._proc.kill()
                    self._proc.waitForFinished(2000)
            except Exception:
                pass
            self._proc = None
        self._ready = False


# Qt 事件循环标志常量（避免每次调用时 import）
from PyQt5.QtCore import QEventLoop

QEventLoop_AllEvents = QEventLoop.AllEvents


class SharedMemoryManager:
    """共享内存管理器 —— 用于主进程与子进程间零拷贝传输大型数组数据。

    典型用法::

        shm = SharedMemoryManager("orcp_ocr_img", capacity=10 * 1024 * 1024)

        # 主进程：写入图像
        shm.write_array(image_array)

        # 通过 JSON 告知子进程共享内存名称和尺寸
        send_json({"cmd": "recognize", "shm_name": shm.name, "width": w, "height": h, "channels": c})

        # 子进程：读取图像
        img = SharedMemoryManager.read_array_from(shm_name, width, height, channels)

        # 清理
        shm.close()

    设计要点：
    - 主进程创建并拥有共享内存（creates=True），负责 unlink
    - 子进程通过名称访问（creates=False），只 close 不 unlink
    - 共享内存块持久化到显式 unlink 或系统重启（Windows）
    - 使用 atexit 确保主进程退出时清理
    """

    def __init__(self, name: str = "orcp_ocr_img", capacity: int = 10 * 1024 * 1024):
        """初始化共享内存管理器。

        Args:
            name: 共享内存块名称（跨进程唯一标识）
            capacity: 初始容量（字节），自动扩展
        """
        from multiprocessing.shared_memory import SharedMemory

        self._name = name
        self._capacity = capacity
        self._shm: SharedMemory | None = None
        self._init_shared_memory()
        import atexit

        atexit.register(self.close)

    def _init_shared_memory(self):
        """创建或重新连接共享内存块。"""
        from multiprocessing.shared_memory import SharedMemory

        try:
            # 尝试连接已有块
            self._shm = SharedMemory(name=self._name, create=False)
            if self._shm.size < self._capacity:
                # 容量不足，重建
                self._shm.close()
                self._shm.unlink()
                self._shm = SharedMemory(name=self._name, create=True, size=self._capacity)
        except FileNotFoundError:
            # 不存在，创建新块
            self._shm = SharedMemory(name=self._name, create=True, size=self._capacity)
        except Exception:
            # 其他错误（如权限），尝试重建
            try:
                self._shm = SharedMemory(name=self._name, create=True, size=self._capacity)
            except Exception as e:
                logger.error("共享内存创建失败: %s", e)
                self._shm = None

    @property
    def name(self) -> str:
        """共享内存块名称（传给子进程）。"""
        return self._name

    def write_array(self, arr) -> int:
        """将 numpy 数组写入共享内存。

        Args:
            arr: numpy 数组（uint8）

        Returns:
            写入的字节数
        """
        import numpy as np

        if self._shm is None:
            self._init_shared_memory()
        if self._shm is None:
            raise RuntimeError("共享内存不可用")

        raw = np.ascontiguousarray(arr).tobytes()
        n = len(raw)

        # 容量不足时自动扩展
        if n > self._shm.size:
            self._shm.close()
            self._shm.unlink()
            from multiprocessing.shared_memory import SharedMemory

            self._capacity = max(n, self._capacity * 2)
            self._shm = SharedMemory(name=self._name, create=True, size=self._capacity)

        self._shm.buf[:n] = raw
        return n

    @staticmethod
    def read_array_from(name: str, width: int, height: int, channels: int = 3):
        """从共享内存读取 numpy 数组（子进程端调用）。

        Args:
            name: 共享内存块名称
            width: 图像宽度
            height: 图像高度
            channels: 通道数

        Returns:
            numpy 数组 (height, width, channels), dtype=uint8
        """
        from multiprocessing.shared_memory import SharedMemory

        import numpy as np

        shm = SharedMemory(name=name, create=False)
        try:
            n = width * height * channels
            arr = np.frombuffer(shm.buf[:n], dtype=np.uint8).reshape((height, width, channels))
            return arr.copy()  # copy 确保不持有共享内存引用
        finally:
            shm.close()  # 只 close，不 unlink（主进程负责 unlink）

    def close(self):
        """关闭并释放共享内存（主进程调用）。"""
        if self._shm is not None:
            try:
                self._shm.close()
                self._shm.unlink()
            except Exception:
                pass
            self._shm = None
