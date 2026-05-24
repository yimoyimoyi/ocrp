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

### Changed
- **pyproject.toml**: 从 80+ 条精确锁定的传递依赖精简为 ~20 条直接依赖
- **pyproject.toml**: 补充项目元数据（作者、许可证、分类、关键词、URL）
- **pyproject.toml**: 版本号从 `0.1.0` 升级至 `0.2.0`
- **.gitignore**: 补充 IDE、日志、测试覆盖率、OS 临时文件等条目

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
