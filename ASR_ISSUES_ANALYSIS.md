# ASR 系统问题分析报告

## 🔴 严重问题

### 1. **asr_engine.py - 子进程退出后错误访问 (Line 240-245)**
**问题**：当子进程退出时，代码先赋值 `self._proc = None`，然后试图访问 `self._proc.poll()`
```python
# 错误的代码
self._proc = None
self._ready = False
return {"status": "error",
        "message": f"ASR server exited before request (code={self._proc.poll() if self._proc else '?'})..."}
        #                                                              ↑ self._proc 已是 None
```
**影响**：在子进程意外退出时，错误消息中会显示 `code=?` 而不是实际的退出码  
**修复**：需要在赋值前保存退出码
```python
poll_code = self._proc.poll() if self._proc else None
self._proc = None
self._ready = False
return {"status": "error", 
        "message": f"ASR server exited: code={poll_code}"}
```

---

### 2. **asr_server.py - 模型加载失败时变量未定义 (Line 228)**
**问题**：当 GPU 加载失败、回退到 CPU 时，代码引用可能未定义的变量 `dl_root`
```python
model = WhisperModel(
    model_arg,  # ← model_arg 在异常处理块外可能未定义
    device="cpu",
    compute_type="int8",
    download_root=dl_root if 'dl_root' in dir() else None,  # ← 危险的用法
)
```
**影响**：如果第一次 WhisperModel() 调用在赋值 `model_arg` 前就抛异常，CPU 回退会失败  
**修复**：需要保证变量初始化
```python
model_arg = None
dl_root = None
try:
    local = _resolve_local_model_path(model_dir, model_size)
    if local:
        model_arg = local
        dl_root = None
    else:
        model_arg = model_size
        dl_root = model_dir if model_dir and os.path.isdir(model_dir) else None
    ...
```

---

### 3. **asr_engine.py - Windows 上响应延迟 (Line 270-276)**
**问题**：Windows 不支持 select() on pipes，改用 sleep(0.1)，导致最多 100ms 延迟
```python
if sys.platform != "win32":
    ready, _, _ = select.select([self._proc.stdout], [], [], 0.5)
    if not ready:
        continue
else:
    # Windows 不支持 select on pipes，用轮询
    _time.sleep(0.1)  # ← 每次轮询固定延迟 100ms，响应不及时
```
**影响**：Windows 用户感受到延迟、超时概率增加  
**修复**：使用更小的 sleep 间隔或其他机制

---

## 🟡 中等问题

### 4. **asr_engine.py - 不规范的导入别名 (Line 248, 266)**
**问题**：使用 `import time as _time` 来避免名称冲突，但这是一个迹象
```python
import time as _time
...
deadline = _time.time() + timeout
...
_time.sleep(0.1)
```
**影响**：代码可读性降低、维护困难  
**修复**：重命名变量而不是导入别名
```python
import time
...
deadline = time.time() + timeout
...
time.sleep(0.1)
```

---

### 5. **asr_server.py - JSON 解析异常处理不完整 (Line 241)**
**问题**：当接收到无效 JSON 时，错误消息被发送但线程继续
```python
try:
    req = json.loads(line)
except json.JSONDecodeError:
    _send({"status": "error", "message": "Invalid JSON"})
    continue  # 继续循环，可能导致输入不同步
```
**影响**：如果客户端发送了损坏的 JSON，可能导致协议混乱  
**修复**：可考虑增加日志或更严格的验证

---

### 6. **asr_engine.py - _stop_server 中异常处理不完整 (Line 306-312)**
**问题**：当向 stdin 写入失败时，直接 catch 所有异常
```python
def _stop_server(self):
    if self._proc is None:
        return
    try:
        _send_json(self._proc.stdin, {"cmd": "shutdown"})  # ← 可能失败
        self._proc.wait(timeout=10)
    except Exception:  # ← 太宽泛
        try:
            self._proc.kill()
```
**影响**：如果 stdin 已关闭，会静默失败  
**修复**：分别处理不同的异常类型

---

### 7. **asr_engine.py - AudioProcessWorker 中临时文件可能泄漏 (Line 333-337)**
**问题**：在 `extract_audio_from_video` 和 `convert_to_wav` 时创建临时文件
```python
audio_path = extract_audio_from_video(self._audio_source, ...)
if not audio_path:
    self.error.emit("音频提取失败")
    return  # ← 临时文件可能未清理
```
**影响**：长期运行可能导致磁盘空间满  
**修复**：需要在 run() 的 finally 块中清理临时文件

---

## 🟢 轻微问题

### 8. **asr_engine.py - 缺少日志上下文**
**问题**：很多地方使用 print() 而不是日志系统
```python
print(f"[ASR] 请求已发送: cmd={req.get('cmd')}")
```
**建议**：使用 logging 模块以便集中管理和过滤日志

---

### 9. **asr_engine.py - 超时处理不够细粒度**
**问题**：整个请求共享一个 timeout，但模型加载和推理应该分别计时
```python
def _send_request(self, req: dict, timeout: float = 300.0) -> dict:
    # 300s = 5分钟，太长可能导致前端冻结
```
**建议**：分离连接超时、读取超时、总超时

---

### 10. **asr_server.py - 缺少配置验证**
**问题**：加载配置后没有验证必要的字段
```python
def _load_config(config_path: str = None) -> dict:
    # ... 返回配置
    # 没有检查 model_dir 是否存在、device 是否有效等
```

---

## 📋 问题优先级

| 优先级 | 问题 | 影响范围 |
|-------|------|--------|
| P0 | #1: 子进程退出后错误访问 | 错误诊断困难 |
| P0 | #2: 模型加载变量未定义 | CPU 回退失败 |
| P1 | #3: Windows 响应延迟 | Windows 用户体验 |
| P2 | #4: 不规范导入别名 | 代码维护性 |
| P2 | #5: JSON 异常处理 | 协议可靠性 |
| P3 | #6-10: 其他问题 | 边界情况、可观测性 |

---

## ✅ 修复建议

1. **立即修复** #1 和 #2（P0 问题）
2. **优化** Windows 上的响应延迟（#3）
3. **整理代码** 移除不规范的导入别名（#4）
4. **增加日志** 使用标准日志系统（#8）
5. **改进异常处理** 分别处理不同异常（#6）
