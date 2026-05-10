#!/bin/bash
# =============================================================================
# ORCP - OCR 处理工具 (GUI) - Linux 启动脚本
# =============================================================================
# 用法:
#   bash orcp_gui.sh              # 启动 ORCP GUI
#   bash orcp_gui.sh --setup      # 先运行完整安装再启动
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'

# ── 参数解析 ────────────────────────────────────────────────────────────────
DO_SETUP=false
SETUP_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --setup)  DO_SETUP=true ;;
        --cpu)    DO_SETUP=true; SETUP_ARGS+=("--cpu") ;;
        --gpu)    DO_SETUP=true; SETUP_ARGS+=("--gpu") ;;
    esac
done

# ── 首次运行检查 ────────────────────────────────────────────────────────────
check_prerequisites() {
    local missing=()

    if ! command -v uv &>/dev/null; then
        missing+=("uv (pip install uv)")
    fi
    if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
        missing+=("python3")
    fi
    if ! command -v ffmpeg &>/dev/null && ! command -v ffprobe &>/dev/null; then
        missing+=("ffmpeg")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo -e "${RED}⚠ 缺少以下依赖:${NC}"
        for m in "${missing[@]}"; do echo "  - $m"; done
        echo ""
        echo -e "${CYAN}运行 bash setup.sh 一键安装所有依赖？ [Y/n]${NC}"
        read -rp "> " answer
        if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
            DO_SETUP=true
        else
            echo "请手动安装缺失依赖后重新运行。"
            exit 1
        fi
    fi
}

# ── 运行安装 ────────────────────────────────────────────────────────────────
if $DO_SETUP; then
    if [ -f setup.sh ]; then
        echo -e "${GREEN}[*] 运行安装脚本...${NC}"
        bash setup.sh "${SETUP_ARGS[@]}"
    else
        echo -e "${RED}❌ 未找到 setup.sh，请手动安装依赖。${NC}"
        exit 1
    fi
else
    check_prerequisites
fi

# ── 同步依赖 ────────────────────────────────────────────────────────────────
echo -e "${GREEN}[*] 同步依赖 (uv sync) ...${NC}"
uv sync

# ── 环境变量 ────────────────────────────────────────────────────────────────
# 清除代理（避免干扰本地 API 调用）
unset http_proxy  HTTPS_PROXY
unset https_proxy HTTP_PROXY
unset all_proxy   ALL_PROXY

# CUDA 延迟加载（避免不必要的 CUDA 初始化）
export CUDA_MODULE_LOADING=LAZY
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# ── 启动 ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║      ORCP - OCR 处理工具 (GUI)          ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}[*] 启动 ORCP GUI ...${NC}"
echo "-------------------------------------------------------"

uv run python ocr_gui.py

echo "-------------------------------------------------------"
echo -e "${GREEN}✅ ORCP 已退出。${NC}"
