# ORCP 启动脚本使用指南

## 📋 概览

ORCP 提供了多个启动和安装脚本，简化了初始设置和日常使用。本文档说明每个脚本的用途和用法。

---

## 🚀 快速开始

### Windows 用户
1. **首次使用**：双击运行 `setup.bat` (完整安装)
2. **启动应用**：双击运行 `orcp_gui.bat`
3. **诊断问题**：运行 `diagnose.bat`

### Linux 用户
1. **首次使用**：运行 `bash setup.sh` (完整安装)
2. **启动应用**：运行 `bash orcp_gui.sh`
3. **诊断问题**：运行 `bash diagnose.sh`

---

## 📄 脚本详解

### 1. `setup.bat` (Windows) / `setup.sh` (Linux)

**目的**：完整的一键安装脚本

#### Windows 用法

```batch
setup.bat                    # 交互式安装（推荐）
setup.bat --cpu              # 强制 CPU 模式
setup.bat --gpu              # 强制 GPU 模式 (需要 NVIDIA 显卡)
setup.bat --no-ffmpeg        # 跳过 FFmpeg 安装
setup.bat --reinstall        # 删除旧虚拟环境，重新安装
setup.bat -h                 # 显示帮助信息
```

#### Linux 用法

```bash
bash setup.sh                # 交互式安装（推荐）
bash setup.sh --cpu          # 强制 CPU 模式
bash setup.sh --gpu          # 强制 GPU 模式
bash setup.sh --no-ffmpeg    # 跳过 FFmpeg 安装
bash setup.sh -h             # 显示帮助信息
```

#### 功能流程

1. **系统检测** - 检测 OS、GPU、Python 版本
2. **Python 检查** - 确保已安装 Python >= 3.11
3. **uv 安装** - 安装高速包管理器 uv (如未安装)
4. **FFmpeg 安装** - 自动下载或检测系统 FFmpeg
5. **依赖同步** - 使用 uv 同步所有 Python 依赖
6. **cuDNN 8 检查** - 检查 GPU ASR 加速库 (可选)
7. **验证** - 验证核心依赖是否可用

#### 安装日志

安装过程中的所有信息记录在 `install.log` 中，遇到问题时可查看该日志。

---

### 2. `orcp_gui.bat` (Windows) / `orcp_gui.sh` (Linux)

**目的**：启动 ORCP GUI 应用

#### Windows 用法

```batch
orcp_gui.bat                 # 直接启动 GUI (已安装依赖时)
```

#### Linux 用法

```bash
bash orcp_gui.sh             # 直接启动 GUI
bash orcp_gui.sh --setup     # 先完整安装，再启动
bash orcp_gui.sh --cpu       # CPU 模式安装并启动
bash orcp_gui.sh --gpu       # GPU 模式安装并启动
```

#### 工作流程

1. **依赖检查** - 检查 uv, FFmpeg, Python 包是否就绪
2. **首次运行处理** - 如果依赖缺失，提示用户运行 setup.sh/bat
3. **同步依赖** - 运行 `uv sync` 确保依赖最新
4. **设置环境变量** - 配置 CUDA, 清除代理等
5. **启动 GUI** - 运行 `python ocr_gui.py`

#### 错误处理

如果 GUI 启动失败，脚本会：
- 显示常见问题及解决方案
- 建议运行 `setup.bat/sh` 重新安装
- 提示查看 `install.log` 获取详细信息

---

### 3. `diagnose.bat` (Windows) / `diagnose.sh` (Linux)

**目的**：快速诊断系统环境和依赖状态

#### Windows 用法

```batch
diagnose.bat                 # 运行完整诊断
```

#### Linux 用法

```bash
bash diagnose.sh             # 运行完整诊断
```

#### 诊断项目

1. **系统环境** - OS, 架构, 用户等
2. **Python 环境** - 版本, 路径, 配置
3. **包管理工具** - uv, pip 等
4. **关键依赖** - FFmpeg, Git 等
5. **GPU/CUDA** - NVIDIA GPU 检测, CUDA 版本
6. **cuDNN 8** - GPU ASR 加速库检测
7. **Python 包** - 虚拟环境, 依赖状态
8. **文件系统** - 项目结构完整性
9. **日志** - install.log 内容
10. **启动建议** - 基于诊断结果的建议操作

#### 用途

诊断脚本在以下情况下很有帮助：
- ❌ 启动失败时，快速定位问题
- 🔧 重新安装前，确认当前状态
- 📋 报告问题时，获取系统信息
- 🎯 验证安装成功

---

## 🐛 常见问题解决

### 问题 1：Python 版本过低

**症状**：`setup.bat` 显示 "需要 Python >= 3.11"

**解决**：
1. 访问 https://www.python.org/downloads/
2. 下载 Python 3.12+ (请选 64-bit)
3. 安装时 **务必勾选** "Add Python to PATH"
4. 重新打开命令行，运行 `setup.bat`

### 问题 2：FFmpeg 安装失败

**症状**：`setup.bat` 无法自动下载 FFmpeg

**解决**：
1. 手动下载：https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
2. 解压后，复制 `bin/` 目录中的 `ffmpeg.exe`, `ffprobe.exe` 到项目的 `core/` 目录

### 问题 3：GPU ASR 不工作

**症状**：ASR 使用 CPU 运行，GPU 没有被利用

**解决**：
1. 确认有 NVIDIA GPU：运行 `diagnose.bat` 或 `bash diagnose.sh`
2. 检查 cuDNN 8：从 https://developer.nvidia.com/cudnn 下载
3. 解压后，复制 3 个 DLL/SO 到 `models/asr/lib/`：
   - Windows: `cudnn_ops_infer64_8.dll`, `cudnn_cnn_infer64_8.dll`, `cudnn64_8.dll`
   - Linux: `libcudnn_ops_infer.so.8`, `libcudnn_cnn_infer.so.8`, `libcudnn.so.8`

### 问题 4：Qt5 错误（Linux）

**症状**：`Could not find the Qt platform plugin`

**解决**：
```bash
# Ubuntu/Debian
sudo apt install python3-pyqt5

# Fedora
sudo dnf install python3-pyqt5

# Arch
sudo pacman -S python-pyqt5
```

### 问题 5：网络问题导致下载失败

**症状**：`setup.bat` 下载 FFmpeg 超时

**解决**：
1. 检查网络连接
2. 尝试手动下载：https://github.com/BtbN/FFmpeg-Builds/releases/latest
3. 或使用 `setup.bat --no-ffmpeg` 跳过自动安装

---

## 🔧 高级用法

### 完全重新安装

```batch
# Windows
setup.bat --reinstall

# Linux (删除虚拟环境后)
rm -rf .venv
bash setup.sh
```

### CPU 模式（禁用 GPU）

```batch
# Windows
setup.bat --cpu
orcp_gui.bat

# Linux
bash setup.sh --cpu
bash orcp_gui.sh
```

### 仅安装 FFmpeg

```batch
# Windows (旧版本，已移除)
# 改用：setup.bat --no-ffmpeg，然后手动放置 FFmpeg

# Linux 示例
sudo apt install ffmpeg  # Ubuntu/Debian
sudo dnf install ffmpeg  # Fedora
```

### 跳过 FFmpeg 安装

```batch
# Windows
setup.bat --no-ffmpeg

# Linux
bash setup.sh --no-ffmpeg
```

---

## 📊 文件说明

| 文件 | 说明 |
|------|------|
| `setup.bat` | Windows 完整安装脚本 |
| `setup.sh` | Linux 完整安装脚本 |
| `orcp_gui.bat` | Windows GUI 启动脚本 |
| `orcp_gui.sh` | Linux GUI 启动脚本 |
| `diagnose.bat` | Windows 诊断脚本 |
| `diagnose.sh` | Linux 诊断脚本 |
| `install.log` | 安装日志 (运行 setup 后生成) |
| `pyproject.toml` | Python 项目配置 |
| `.python-version` | Python 版本锁定文件 (uv 使用) |

---

## 🎯 工作流示例

### 场景 1：首次使用 (Windows)

```batch
1. 解压项目文件
2. 双击 setup.bat (等待安装完成)
3. 双击 orcp_gui.bat (启动应用)
```

### 场景 2：首次使用 (Linux)

```bash
1. 解压项目文件
2. bash setup.sh (等待安装完成)
3. bash orcp_gui.sh (启动应用)
```

### 场景 3：排查问题

```bash
# 1. 运行诊断
bash diagnose.sh       # 或 diagnose.bat

# 2. 查看诊断结果，找到问题所在

# 3. 重新运行安装
bash setup.sh --reinstall

# 4. 尝试启动
bash orcp_gui.sh
```

### 场景 4：更新依赖

```bash
# Windows
setup.bat --reinstall

# Linux
bash setup.sh --reinstall
```

---

## 📝 脚本改进说明

本版本脚本相比之前的版本有以下改进：

### 安装脚本 (setup.bat/sh)
- ✅ 改进的错误处理和日志记录
- ✅ 8 步安装过程，清晰的进度提示
- ✅ cuDNN 8 检测和安装指导
- ✅ GPU/CPU 自动选择或强制指定
- ✅ FFmpeg 自动下载和安装
- ✅ 安装日志记录到 `install.log`

### GUI 启动脚本 (orcp_gui.bat/sh)
- ✅ 自动依赖检查和提示
- ✅ 首次运行自动触发安装
- ✅ 清晰的错误消息和常见问题建议
- ✅ 环境变量自动配置

### 诊断脚本 (diagnose.bat/sh)
- ✅ 系统环境全面检查
- ✅ GPU/CUDA 状态详细报告
- ✅ 依赖完整性验证
- ✅ 启动建议智能生成

---

## 🆘 获取帮助

1. **查看日志**：`cat install.log` (Linux) 或 `type install.log` (Windows)
2. **运行诊断**：`bash diagnose.sh` 或 `diagnose.bat`
3. **查看文档**：查看项目 README.md
4. **检查配置**：查看 `config/` 目录下的配置文件

---

**祝使用愉快！** 🎉
