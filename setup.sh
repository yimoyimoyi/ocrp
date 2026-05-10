#!/bin/bash
# =============================================================================
# ORCP - OCR 处理工具 Linux 一键安装脚本
# =============================================================================
# 用法:
#   bash setup.sh              # 交互式安装（推荐）
#   bash setup.sh --cpu        # 强制纯 CPU 模式
#   bash setup.sh --gpu        # 自动检测 GPU 并配置 CUDA 支持
#   bash setup.sh --no-ffmpeg  # 跳过 ffmpeg 系统安装（已有则使用）
#
set -euo pipefail

# ── 彩色输出 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }

# ── 参数解析 ────────────────────────────────────────────────────────────────
FORCE_CPU=false; FORCE_GPU=false; SKIP_FFMPEG=false
for arg in "$@"; do
    case "$arg" in
        --cpu)        FORCE_CPU=true ;;
        --gpu)        FORCE_GPU=true ;;
        --no-ffmpeg)  SKIP_FFMPEG=true ;;
        -h|--help)
            echo "用法: bash setup.sh [选项]"
            echo "  --cpu        强制纯 CPU 模式（不安装 CUDA 依赖）"
            echo "  --gpu        自动检测 GPU 并配置 CUDA 支持"
            echo "  --no-ffmpeg  跳过 ffmpeg 系统安装（适用于已有系统 ffmpeg）"
            exit 0 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║        ORCP - OCR 处理工具  Linux 安装程序               ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── 1. 检测 Linux 发行版 ────────────────────────────────────────────────────
step "1/7 检测系统环境"

detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    elif command -v lsb_release >/dev/null 2>&1; then
        lsb_release -si | tr '[:upper:]' '[:lower:]'
    else
        echo "unknown"
    fi
}

DISTRO=$(detect_distro)
KERNEL=$(uname -m)
info "发行版: $DISTRO, 架构: $KERNEL"

# ── 2. 检测/确认 GPU 模式 ──────────────────────────────────────────────────
step "2/7 检测 GPU 状态"

USE_GPU=false

if $FORCE_CPU; then
    info "强制 CPU 模式"
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    USE_GPU=true
    info "✅ 检测到 NVIDIA GPU:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || true
elif $FORCE_GPU; then
    warn "未检测到 nvidia-smi，但仍按 GPU 模式配置"
    USE_GPU=true
else
    info "未检测到 NVIDIA GPU，使用纯 CPU 模式"
fi

# ── 3. 安装系统依赖 ─────────────────────────────────────────────────────────
step "3/7 安装系统依赖"

install_pkgs_apt() {
    local pkgs=("$@")
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${pkgs[@]}"
}

install_pkgs_dnf() {
    sudo dnf install -y "${@}"
}

install_pkgs_pacman() {
    sudo pacman -S --noconfirm --needed "${@}"
}

install_pkgs_zypper() {
    sudo zypper install -y "${@}"
}

case "$DISTRO" in
    ubuntu|debian|linuxmint|pop)
        PKG_MGR="apt"
        info "使用 apt 安装系统包..."
        SYS_PKGS=("python3" "python3-dev" "python3-venv" "python3-pip" "git" "curl" "wget" "build-essential")
        if ! $SKIP_FFMPEG; then
            SYS_PKGS+=("ffmpeg" "libavcodec-extra")
        fi
        if $USE_GPU; then
            SYS_PKGS+=("nvidia-cuda-toolkit" "libcudnn8" "libcudnn8-dev")
        fi
        install_pkgs_apt "${SYS_PKGS[@]}"
        # libcudnn8 可能不在标准仓库，忽略错误
        ;;
    fedora|rhel|centos|rocky|almalinux)
        PKG_MGR="dnf"
        info "使用 dnf 安装系统包..."
        SYS_PKGS=("python3" "python3-devel" "git" "curl" "wget" "gcc" "gcc-c++" "make")
        if ! $SKIP_FFMPEG; then
            # Fedora 需要 RPM Fusion
            if ! rpm -q ffmpeg >/dev/null 2>&1; then
                warn "ffmpeg 需要 RPM Fusion 仓库"
                sudo dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" 2>/dev/null || true
                SYS_PKGS+=("ffmpeg" "ffmpeg-devel")
            fi
        fi
        if $USE_GPU; then
            SYS_PKGS+=("cuda-toolkit" "libcudnn8" "libcudnn8-devel")
        fi
        install_pkgs_dnf "${SYS_PKGS[@]}" || true
        ;;
    arch|manjaro|endeavouros)
        PKG_MGR="pacman"
        info "使用 pacman 安装系统包..."
        SYS_PKGS=("python" "git" "curl" "wget" "base-devel")
        if ! $SKIP_FFMPEG; then
            SYS_PKGS+=("ffmpeg")
        fi
        if $USE_GPU; then
            SYS_PKGS+=("cuda" "cudnn")
        fi
        install_pkgs_pacman "${SYS_PKGS[@]}"
        ;;
    opensuse*)
        PKG_MGR="zypper"
        info "使用 zypper 安装系统包..."
        SYS_PKGS=("python3" "python3-devel" "git" "curl" "wget" "gcc" "gcc-c++" "make")
        if ! $SKIP_FFMPEG; then
            SYS_PKGS+=("ffmpeg-4")
        fi
        if $USE_GPU; then
            SYS_PKGS+=("cuda-toolkit" "libcudnn8")
        fi
        install_pkgs_zypper "${SYS_PKGS[@]}" || true
        ;;
    *)
        warn "未知发行版 '$DISTRO'，请手动安装以下依赖:"
        echo "  - Python 3.12+ (python3, python3-dev/python3-devel)"
        echo "  - FFmpeg (ffmpeg, ffprobe, ffplay)"
        if $USE_GPU; then
            echo "  - NVIDIA CUDA Toolkit + cuDNN"
        fi
        echo "  - git, curl, wget, build-essential (gcc, g++, make)"
        echo ""
        read -rp "按 Enter 继续（假设依赖已安装）..." _
        ;;
esac

info "系统依赖安装完成"

# ── 4. 安装/确认 Python 版本 ───────────────────────────────────────────────
step "4/7 检查 Python 版本"

PYTHON_CMD=""
for cmd in python3.12 python3.13 python3.11 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    error "需要 Python >= 3.11，但未找到。请手动安装 Python 3.12+。"
    echo "  Ubuntu/Debian: sudo apt install python3.12 python3.12-dev python3.12-venv"
    echo "  Fedora:        sudo dnf install python3.12 python3.12-devel"
    echo "  或使用 pyenv:  curl https://pyenv.run | bash && pyenv install 3.12"
    exit 1
fi
info "Python: $($PYTHON_CMD --version)"

# ── 5. 安装 uv 包管理器 ────────────────────────────────────────────────────
step "5/7 安装 uv 包管理器"

if ! command -v uv >/dev/null 2>&1; then
    info "正在安装 uv ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 添加到当前 PATH
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        # 备选：pip 安装
        pip3 install uv --user
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi

if command -v uv >/dev/null 2>&1; then
    info "uv 版本: $(uv --version)"
else
    error "uv 安装失败，尝试用 pip 安装: pip3 install uv"
    exit 1
fi

# ── 6. 安装 Python 依赖 ────────────────────────────────────────────────────
step "6/7 安装 Python 依赖"

info "同步 Python 依赖 (uv sync) ..."
# 确保 .python-version 存在
if [ ! -f .python-version ]; then
    echo "3.12" > .python-version
fi

# GPU/CPU 模式自动调整依赖
if $USE_GPU; then
    info "GPU 模式: paddlepaddle-gpu >= 2.6"
    export PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL:-https://download.paddlepaddle.org/whl/cu118}"
else
    info "CPU 模式: paddlepaddle >= 2.6"
fi

uv sync
if [ $? -ne 0 ]; then
    # 如果 GPU paddle 版本失败，尝试 CPU
    if $USE_GPU; then
        warn "GPU 版 PaddlePaddle 安装失败，尝试 CPU 版本..."
        uv sync 2>/dev/null || true
    fi
fi

info "Python 依赖安装完成"

# ── 7. 验证安装 ─────────────────────────────────────────────────────────────
step "7/7 验证安装"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            安装完成！                                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${CYAN}环境信息:${NC}"
echo "  OS:       $(uname -s) $(uname -r)"
echo "  Python:   $($PYTHON_CMD --version 2>&1)"
echo "  uv:       $(uv --version 2>/dev/null || echo 'N/A')"
echo "  ffmpeg:   $(ffmpeg -version 2>/dev/null | head -1 || echo 'N/A')"
if $USE_GPU; then
    echo "  GPU:      $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'N/A')"
    echo "  CUDA:     $(nvcc --version 2>/dev/null | grep -oP 'V\K[\d.]+' || echo 'N/A')"
fi
echo ""

# 快速功能验证
echo -e "${CYAN}功能验证:${NC}"
if uv run python -c "import numpy; print('  numpy:', numpy.__version__)" 2>/dev/null; then true; else warn "numpy 导入失败"; fi
if uv run python -c "import cv2; print('  cv2:  ', cv2.__version__)" 2>/dev/null; then true; else warn "opencv 导入失败"; fi
if uv run python -c "import requests; print('  requests:', requests.__version__)" 2>/dev/null; then true; else warn "requests 导入失败"; fi

if $USE_GPU; then
    echo ""
    echo -e "${CYAN}GPU 依赖检查:${NC}"
    uv run python -c "import paddle; print('  paddle:  ', paddle.__version__); print('  GPU:     ', 'YES' if paddle.is_compiled_with_cuda() else 'NO')" 2>/dev/null || warn "PaddlePaddle GPU 检查失败（可能已安装 CPU 版本）"
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  启动 ORCP:  bash orcp_gui.sh${NC}"
echo -e "${GREEN}  或直接:    uv run python ocr_gui.py${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
