# 贡献指南

感谢你对 ORCP 项目的关注！以下是参与开发的指南。

## 开发环境搭建

### 前置条件
- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) 包管理器（推荐）
- Git

### 快速开始

```bash
# 克隆仓库
git clone https://github.com/yimoyimoyi/orcp.git
cd orcp

# 创建虚拟环境并安装依赖（含开发工具）
uv venv
uv pip install -e ".[dev]"

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux:
source .venv/bin/activate
```

## 代码规范

### 格式化与静态检查

项目使用 [Ruff](https://docs.astral.sh/ruff/) 进行代码格式化和 lint 检查：

```bash
# 检查代码质量
ruff check .

# 自动修复
ruff check --fix .

# 格式化
ruff format .

# 类型检查
mypy core/ ui/
```

### 代码风格
- 行长度上限：120 字符
- 字符串引号：双引号 `"`
- 缩进：4 空格
- 导入排序：Ruff isort 自动处理
- 命名约定：
  - 类名：`PascalCase`
  - 函数/变量：`snake_case`（Qt 回调使用 `mixedCase`）
  - 常量：`UPPER_SNAKE_CASE`

### 类型注解
- 新增代码应添加类型注解
- 使用 `from __future__ import annotations` 启用延迟注解
- 优先使用 `Optional[X]` 而非 `X | None`（兼容性）

## 项目结构

```
orcp/
├── ocr_gui.py              # 应用入口（DLL 加载、QApplication 初始化）
├── config_manager.py       # 配置管理器（JSON 注释支持）
├── style_loader.py         # QSS 样式表加载
├── pyproject.toml          # 项目配置、依赖、工具链
├── config/                 # 配置文件目录
│   ├── settings.json       # UI 状态持久化（gitignored）
│   ├── ocr_engines.json    # OCR 引擎默认参数
│   ├── asr_engines.json    # ASR 引擎参数
│   ├── ai_correction.json  # AI 纠错配置
│   ├── api_presets.json    # API 连接预设
│   ├── prompt_templates.json
│   ├── filters.json        # 关键词过滤
│   └── ui_config.json      # UI 标签与默认值
├── core/                   # 核心业务逻辑
│   ├── workflow_manager.py # 业务流程编排
│   ├── ocr_engine.py       # OCR 引擎抽象与实现
│   ├── asr_engine.py       # ASR 引擎（子进程隔离）
│   ├── asr_server.py       # ASR 子进程服务
│   ├── ai_correction.py    # LLM 纠错
│   ├── frame_processor.py  # 视频帧解码 + 字幕检测
│   ├── result_processor.py # 去重、过滤、导出
│   ├── ffmpeg_reader.py    # FFmpeg 视频解码
│   ├── filter_manager.py   # 关键词过滤
│   ├── prompt_manager.py   # 提示词模板
│   └── api_preset_manager.py
├── ui/                     # PyQt5 用户界面
│   ├── main_window.py      # 主窗口（菜单、工具栏、布局）
│   ├── config_panel.py     # 参数配置面板
│   ├── video_preview.py    # 视频预览 + ROI 绘制
│   ├── region_manager.py   # 区域管理器
│   ├── result_table.py     # 结果表格
│   ├── settings_dialog.py  # 设置对话框
│   ├── display_dialog.py   # 显示对话框
│   └── workers.py          # QThread 工作线程
├── styles/                 # QSS 样式表
├── scripts/                # 辅助脚本
├── tests/                  # 测试目录
└── models/                 # ASR 模型（gitignored）
```

## 架构原则

### UI 与业务逻辑分离
- `ui/` 层只负责界面渲染和用户交互
- `core/workflow_manager.py` 封装所有业务流程
- 通过 **信号/槽** 和 **依赖注入** 通信，避免 UI 层直接调用核心逻辑

### 线程安全
- 所有耗时操作（OCR、ASR、AI 纠错）在 `QThread` 子线程执行
- 通过 Qt 信号将结果回传主线程
- 禁止在子线程直接操作 UI 控件

### DLL 隔离（Windows）
- PaddleOCR 和 faster-whisper 有冲突的 CUDA 依赖
- ASR 通过子进程 `asr_server.py` 隔离运行
- `ocr_gui.py` 中的 DLL 预加载顺序：torch/lib → PyQt5

### 配置系统
- `config_manager.py` 支持 JSON 注释（`//` 和 `/* */`）
- `settings.json` 自动迁移，向后兼容
- 新增配置项需在 `MODE_PARAMS_DEFAULTS` 中注册默认值

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <description>

[optional body]
[optional footer]
```

### 类型
| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `style` | 代码格式（不影响逻辑） |
| `refactor` | 重构（不新增功能/修复 Bug） |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具链变更 |

### 示例
```
feat(ocr): 新增 LlamaCpp 引擎支持
fix(asr): 修复 CUDA DLL 加载顺序导致的崩溃
docs(readme): 补充 GPU ASR 安装说明
refactor(workflow): 将批量处理逻辑提取到独立方法
```

## 测试

```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_frame_processor.py

# 跳过需要 GPU 的测试
pytest -m "not gpu"

# 跳过慢速测试
pytest -m "not slow"
```

## 发布流程

1. 更新 `pyproject.toml` 中的版本号
2. 更新 `CHANGELOG.md`（将 `[Unreleased]` 内容移至新版本）
3. 创建 Git tag：`git tag v0.2.0`
4. 推送：`git push origin main --tags`

## 问题反馈

- 使用 [GitHub Issues](https://github.com/yimoyimoyi/orcp/issues) 报告 Bug
- 提供复现步骤、日志输出、系统环境信息
- 日志文件：`install.log`（安装日志）/ 控制台输出（运行日志）
