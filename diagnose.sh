#!/bin/bash
# ================================================================
#  ORCP Diagnostics (Linux / macOS)
#  Usage: bash diagnose.sh
# ================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}[OK]${NC}      $*"; }
warn() { echo -e "  ${YELLOW}[MISSING]${NC} $*"; }
err()  { echo -e "  ${RED}[ERROR]${NC}   $*"; }
info() { echo -e "  ${CYAN}[INFO]${NC}    $*"; }

echo ""
echo "========================================="
echo "  ORCP - Diagnostics"
echo "========================================="
echo ""

# [1] System -----------------------------------------------------------
echo "--- 1. System ---"
echo "  OS:    $(uname -s) $(uname -r)"
echo "  Arch:  $(uname -m)"
echo "  User:  $(whoami)"
echo ""

# [2] Python -----------------------------------------------------------
echo "--- 2. Python ---"
if command -v python3 &>/dev/null; then
    ok "$(python3 --version 2>&1)"
elif command -v python &>/dev/null; then
    ok "$(python --version 2>&1)"
else
    warn "Python 3 not found"
fi
echo ""

# [3] Package managers ------------------------------------------------
echo "--- 3. Package managers ---"
command -v uv &>/dev/null && ok "uv: $(uv --version)" || warn "uv not found"
command -v pip3 &>/dev/null && ok "pip3: $(pip3 --version | head -c 50)..." || info "pip3 not found"
echo ""

# [4] FFmpeg -----------------------------------------------------------
echo "--- 4. FFmpeg ---"
if command -v ffmpeg &>/dev/null; then
    ok "$(ffmpeg -version 2>&1 | head -1)"
elif [ -f "core/ffmpeg" ] || [ -f "core/ffmpeg.exe" ]; then
    ok "core/ffmpeg"
else
    warn "FFmpeg not found"
fi
echo ""

# [5] GPU --------------------------------------------------------------
echo "--- 5. GPU ---"
if command -v nvidia-smi &>/dev/null; then
    ok "NVIDIA GPU detected"
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | while read -r line; do
        echo "         $line"
    done
else
    info "No NVIDIA GPU (CPU mode)"
fi
echo ""

# [6] cuDNN 8 ----------------------------------------------------------
echo "--- 6. cuDNN 8 (GPU ASR) ---"
if command -v python3 &>/dev/null; then
    if python3 -c "import ctypes; ctypes.CDLL('libcudnn_ops_infer.so.8')" 2>/dev/null; then
        ok "cuDNN 8 ready"
    elif python3 -c "import ctypes; ctypes.CDLL('cudnn_cnn_infer64_8.dll')" 2>/dev/null; then
        ok "cuDNN 8 ready (Windows)"
    else
        warn "cuDNN 8 not found (GPU ASR disabled)"
    fi
else
    info "Skipped (Python not found)"
fi
echo ""

# [7] Python packages -------------------------------------------------
echo "--- 7. Python packages ---"
[ -d ".venv" ] && ok ".venv exists" || info "No .venv yet"
[ -f "pyproject.toml" ] && ok "pyproject.toml" || warn "pyproject.toml missing"
echo ""

# [8] Project structure ------------------------------------------------
echo "--- 8. Project files ---"
for d in core ui models config output; do
    [ -d "$d" ] && ok "$d/" || warn "$d/ missing"
done
[ -f "ocr_gui.py" ] && ok "ocr_gui.py" || warn "ocr_gui.py missing"
[ -f "setup.sh" ] && ok "setup.sh" || info "setup.sh not found"
echo ""

# [9] Logs -------------------------------------------------------------
echo "--- 9. Install log ---"
if [ -f install.log ]; then
    ok "install.log (last 3 lines)"
    tail -3 install.log | sed 's/^/         /'
else
    info "No install.log yet"
fi
echo ""

# [10] Summary ---------------------------------------------------------
echo "--- 10. Summary ---"

MISSING=0
command -v uv &>/dev/null || { warn "Install uv: pip install uv"; MISSING=1; }
command -v ffmpeg &>/dev/null || { warn "Install FFmpeg: bash setup.sh"; MISSING=1; }
command -v python3 &>/dev/null || command -v python &>/dev/null || { warn "Install Python 3.12+"; MISSING=1; }

if [ "$MISSING" -eq 0 ]; then
    echo ""
    ok "Ready! Launch with: bash orcp_gui.sh"
else
    echo ""
    warn "Run: bash setup.sh  to fix missing dependencies"
fi

echo ""
echo "========================================="
echo "  Diagnostics complete."
echo "========================================="
echo ""
