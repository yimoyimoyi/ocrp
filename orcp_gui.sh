#!/bin/bash
# ================================================================
#  ORCP GUI Launcher (Linux)
#  Usage:  bash orcp_gui.sh [--setup] [--cpu|--gpu]
# ================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

DO_SETUP=false; SETUP_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --setup) DO_SETUP=true ;;
        --cpu)   DO_SETUP=true; SETUP_ARGS+=("--cpu") ;;
        --gpu)   DO_SETUP=true; SETUP_ARGS+=("--gpu") ;;
        -h|--help)
            echo "Usage: bash orcp_gui.sh [OPTIONS]"
            echo "  (none)       Launch ORCP GUI"
            echo "  --setup      Run full install then launch"
            echo "  --cpu        CPU mode"
            echo "  --gpu        GPU mode"
            echo "  -h, --help   Show this help"
            exit 0 ;;
    esac
done

echo ""
echo "==========================================="
echo "  ORCP - OCR / ASR Processing Tool"
echo "==========================================="
echo ""

# locate Python (prefer .venv) ----------------------------------------
PYTHON_EXE=""
if [ -f ".venv/bin/python" ]; then
    PYTHON_EXE=".venv/bin/python"
    info "Python: .venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_EXE="python3"
    warn ".venv not found, using system python3"
elif command -v python &>/dev/null; then
    PYTHON_EXE="python"
    warn ".venv not found, using system python"
fi

if [ -z "$PYTHON_EXE" ]; then
    err "Python not found. Run: bash setup.sh"
    exit 1
fi
info "Python: $("$PYTHON_EXE" --version 2>&1)"

# run setup if needed --------------------------------------------------
if $DO_SETUP; then
    if [ ! -f setup.sh ]; then
        err "setup.sh not found. Cannot install."
        exit 1
    fi
    info "Running setup..."
    bash setup.sh "${SETUP_ARGS[@]}" || { err "Setup failed."; exit 1; }
fi

# quick dep check ------------------------------------------------------
if ! "$PYTHON_EXE" -c "import numpy, cv2, requests" 2>/dev/null; then
    warn "Python packages incomplete. Running setup..."
    bash setup.sh || { err "Setup failed."; exit 1; }
fi

# check FFmpeg ---------------------------------------------------------
if ! command -v ffmpeg &>/dev/null; then
    warn "FFmpeg not found. Video features limited."
fi

# env ------------------------------------------------------------------
unset http_proxy HTTPS_PROXY https_proxy HTTP_PROXY all_proxy ALL_PROXY 2>/dev/null || true
export CUDA_MODULE_LOADING=LAZY
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# launch ---------------------------------------------------------------
echo ""
info "Starting ORCP GUI..."
echo ""

"$PYTHON_EXE" ocr_gui.py && {
    echo ""
    info "ORCP exited normally."
    exit 0
} || {
    echo ""
    err "GUI startup failed."
    echo ""
    warn "Common causes:"
    echo "  1. Missing packages   -> bash setup.sh"
    echo "  2. Qt5 not available  -> sudo apt install python3-pyqt5"
    echo "  3. No display server  -> check DISPLAY variable"
    echo "  4. cuDNN 8 missing    -> https://developer.nvidia.com/cudnn"
    echo ""
    exit 1
}
