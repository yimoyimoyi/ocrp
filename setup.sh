#!/bin/bash
# ================================================================
#  ORCP Setup (Linux)
#  Usage:  bash setup.sh [--cpu|--gpu|--no-ffmpeg]
# ================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

FORCE_CPU=false; FORCE_GPU=false; SKIP_FFMPEG=false
for arg in "$@"; do
    case "$arg" in
        --cpu)       FORCE_CPU=true ;;
        --gpu)       FORCE_GPU=true ;;
        --no-ffmpeg) SKIP_FFMPEG=true ;;
        -h|--help)
            echo "Usage: bash setup.sh [OPTIONS]"
            echo "  --cpu         Force CPU mode"
            echo "  --gpu         Force GPU mode"
            echo "  --no-ffmpeg   Skip FFmpeg install"
            exit 0 ;;
    esac
done

echo ""
echo "========================================="
echo "  ORCP Setup (Linux)"
echo "========================================="
echo ""

# [1] Detect distro ---------------------------------------------------
info "[1/7] Detect system..."

detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release; echo "$ID"
    elif command -v lsb_release &>/dev/null; then
        lsb_release -si | tr '[:upper:]' '[:lower:]'
    else
        echo "unknown"
    fi
}

DISTRO=$(detect_distro)
KERNEL=$(uname -m)
echo "      Distro: $DISTRO, Arch: $KERNEL"

# [2] GPU detect -------------------------------------------------------
info "[2/7] Detect GPU..."

USE_GPU=false
if $FORCE_CPU; then
    echo "      CPU mode (forced)"
elif $FORCE_GPU; then
    USE_GPU=true
    echo "      GPU mode (forced)"
elif command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    USE_GPU=true
    echo "      GPU detected:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || true
else
    echo "      No GPU. CPU mode."
fi

# [3] System packages --------------------------------------------------
info "[3/7] System packages..."

case "$DISTRO" in
    ubuntu|debian|linuxmint|pop)
        PKGS=("python3" "python3-dev" "python3-venv" "python3-pip" "git" "curl" "wget" "build-essential")
        if ! $SKIP_FFMPEG; then PKGS+=("ffmpeg" "libavcodec-extra"); fi
        if $USE_GPU; then PKGS+=("nvidia-cuda-toolkit" "libcudnn8" "libcudnn8-dev"); fi
        sudo apt-get update -qq
        sudo apt-get install -y -qq "${PKGS[@]}" || true
        ;;
    fedora|rhel|centos|rocky|almalinux)
        PKGS=("python3" "python3-devel" "git" "curl" "wget" "gcc" "gcc-c++" "make")
        if ! $SKIP_FFMPEG; then
            if ! rpm -q ffmpeg &>/dev/null; then
                warn "FFmpeg needs RPM Fusion"
                sudo dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" 2>/dev/null || true
            fi
            PKGS+=("ffmpeg" "ffmpeg-devel")
        fi
        if $USE_GPU; then PKGS+=("cuda-toolkit" "libcudnn8" "libcudnn8-devel"); fi
        sudo dnf install -y "${PKGS[@]}" || true
        ;;
    arch|manjaro|endeavouros)
        PKGS=("python" "git" "curl" "wget" "base-devel")
        if ! $SKIP_FFMPEG; then PKGS+=("ffmpeg"); fi
        if $USE_GPU; then PKGS+=("cuda" "cudnn"); fi
        sudo pacman -S --noconfirm --needed "${PKGS[@]}"
        ;;
    opensuse*)
        PKGS=("python3" "python3-devel" "git" "curl" "wget" "gcc" "gcc-c++" "make")
        if ! $SKIP_FFMPEG; then PKGS+=("ffmpeg-4"); fi
        if $USE_GPU; then PKGS+=("cuda-toolkit" "libcudnn8"); fi
        sudo zypper install -y "${PKGS[@]}" || true
        ;;
    *)
        warn "Unknown distro '$DISTRO'. Please install manually:"
        echo "  - Python 3.12+, FFmpeg, git, curl, wget, build-essential"
        if $USE_GPU; then echo "  - NVIDIA CUDA Toolkit + cuDNN"; fi
        read -rp "Press Enter to continue..." _
        ;;
esac

# [4] Python version ---------------------------------------------------
info "[4/7] Check Python version..."

PYTHON_CMD=""
for cmd in python3.12 python3.13 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
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
    err "Python >= 3.11 required. Install Python 3.12+ manually."
    echo "  Ubuntu: sudo apt install python3.12 python3.12-dev python3.12-venv"
    echo "  Or:     curl https://pyenv.run | bash && pyenv install 3.12"
    exit 1
fi
echo "      $($PYTHON_CMD --version)"

# [5] uv ---------------------------------------------------------------
info "[5/7] Install uv..."

if ! command -v uv &>/dev/null; then
    echo "      Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        pip3 install uv --user
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi
echo "      $(uv --version 2>/dev/null || echo 'N/A')"

# [6] Python dependencies ----------------------------------------------
info "[6/7] Sync dependencies (uv sync)..."

[ ! -f .python-version ] && echo "3.12" > .python-version

if $USE_GPU; then
    export PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL:-https://download.paddlepaddle.org/whl/cu118}"
fi

uv sync || {
    if $USE_GPU; then
        warn "GPU version failed. Retry CPU..."
        USE_GPU=false
        uv sync || { err "uv sync failed."; exit 1; }
    else
        err "uv sync failed."
        exit 1
    fi
}

# [7] cuDNN 8 check ----------------------------------------------------
info "[7/7] Check cuDNN 8 (GPU ASR)..."

if $USE_GPU; then
    mkdir -p models/asr/lib
    if uv run python -c "import ctypes; ctypes.CDLL('libcudnn_ops_infer.so.8')" 2>/dev/null; then
        echo "      cuDNN 8 ready - GPU ASR enabled"
    else
        echo "      cuDNN 8 not found. ASR uses CPU."
        echo ""
        echo "      For GPU ASR, install cuDNN 8:"
        echo "      1. https://developer.nvidia.com/cudnn"
        echo "      2. Download cuDNN 8.9 for CUDA 12.x"
        echo "      3. Copy 3 SO files to models/asr/lib/"
        echo "         libcudnn_ops_infer.so.8"
        echo "         libcudnn_cnn_infer.so.8"
        echo "         libcudnn.so.8"
        echo ""
    fi
fi

# Done -------------------------------------------------------------------
echo ""
echo "========================================="
echo "  Setup complete!"
echo ""
echo "  Launch:   bash orcp_gui.sh"
echo "  Diagnose: bash diagnose.sh"
echo "========================================="
echo ""
