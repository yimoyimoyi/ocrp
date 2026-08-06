# Changelog

本文件记录 ORCP 的所有重要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- 新增 `CHANGELOG.md` 版本变更记录
- 新增 `CONTRIBUTING.md` 开发贡献指南
- 新增 `ruff` / `mypy` / `pytest` 工具配置
- 新增 `tests/` 目录结构
- 新增 `pyproject.toml` 可选依赖分组（`dev` / `gpu` / `docs`）
- 新增 `tests/test_result_table.py`：表格模型化核心行为单元测试（14 项，含编辑流程/HTML 泄漏/背景重叠回归保护）

### Changed
- **pyproject.toml**: 从 80+ 条精确锁定的传递依赖精简为 ~20 条直接依赖
- **pyproject.toml**: 补充项目元数据（作者、许可证、分类、关键词、URL）
- **pyproject.toml**: 版本号从 `0.1.0` 升级至 `0.2.0`
- **.gitignore**: 补充 IDE、日志、测试覆盖率、OS 临时文件等条目

### GUI 框架迁移（PyQt5 → PySide6）
- **依赖**: `PyQt5>=5.15` → `PySide6>=6.7`；qt-material 由 PyQt5 边缘支持变为 PySide6 原生支持（删除 3 处警告过滤器）
- **QMediaPlayer 适配**: `QMediaContent` 删除 → `setSource` + 显式 `QAudioOutput`；`stateChanged` → `playbackStateChanged`；`error` → `errorOccurred`；异步 seek 监听 `LoadedMedia`
- **API 迁移**: 55 处导入、~120 处信号、9 处 `exec_`；QAction/QActionGroup 移至 QtGui；DLL 预加载顺序保留
- **WMF 后端环境变量移除**（Qt6 多媒体后端为 FFmpeg）

### UI 架构重构（阶段 4b）
- **main_window.py**: 2345 行 → ~1560 行骨架；拆分 5 个视图构建器（`ui/views/`，_ViewBase 双向委托）
- **MessageService**: `ui/services/` 弹窗统一封装，10 处 QMessageBox 直连清零
- **入口收敛**: 字幕模式/去重开关 → 工具栏唯一入口，右侧面板重复控件删除
- **状态栏精简**: 移除引擎/时间标签（保留状态标签 + 进度条）

### core 流程层拆分（阶段 4b）
- **workflow_manager.py 上帝类（1608 行/74 方法）** → `core/workflow/` 子包：门面 + ocr_flow/asr_flow/correction_flow/batch_flow（_FlowBase 双向委托，含写转发）
- **workers.py** 移至 `core/workers.py`，消除 core→ui 依赖倒置

### 表格与表单（阶段 4c）
- **result_table.py**: QTableWidget+cellWidget → QTableView + QAbstractTableModel + delegate（1 万行 5.6s）
- **settings_dialog.py**: 数据驱动表单（59 条字段描述表 + 8 条引擎字段，~200 行同步样板 → 75 行通用函数）
- **配置键解耦**: `subtitle_mode` 存储键改为内部标识（stream/regular），兼容旧翻译文本自动迁移

### Bug 修复（实测驱动）
- 停止/暂停失效：_FlowBase 缺失写转发导致 worker 状态错位（根因）；OCR 子进程未终止导致停止延迟 19s
- 双击清空：model 未提供 EditRole；QTextEdit toHtml 文档头泄漏；编辑器透明背景导致重叠渲染
- QThread destroyed while running：stop 后残留线程强制 terminate 兜底 + 启动前回收旧 worker

## [0.1.0] - 2026-01-01

### Added
- 初始版本发布
- 多引擎 OCR：PaddleOCR / OpenAI Vision / Ollama Vision / LlamaCpp
- 语音识别（ASR）：基于 faster-whisper，子进程隔离 CUDA 环境
- AI 纠错：LLM API 二次校对，支持流式输出
- 流式字幕模式：哨兵检测 + 去重 + 缓冲区
- 常规字幕模式：固定间隔采样 + 去重
- PyQt5 GUI：视频预览、ROI 绘制、区域管理、结果表格
- 暗色/亮色主题切换
- 批量处理：多文件队列，自动导出
- 多格式导出：SRT / TXT / JSON / CSV
- 跨平台支持：Windows / Linux
- 一键安装脚本：`setup.bat`（Windows）/ `setup.sh`（Linux）
- GPU/CPU 自动检测与切换
- 配置自动迁移与持久化
