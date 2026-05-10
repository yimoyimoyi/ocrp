@echo off
setlocal enabledelayedexpansion
title ORCP - OCR 处理工具 (GUI)
color 0A
chcp 65001 >nul

set http_proxy=
set https_proxy=
set all_proxy=
set CUDA_MODULE_LOADING=LAZY
set PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

echo.
echo ╔══════════════════════════════════════════╗
echo ║      ORCP - OCR 处理工具 (GUI)          ║
echo ╚══════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ── 首次运行：检测缺失依赖 ──
set "NEED_SETUP="

where uv >nul 2>&1
if errorlevel 1 (
    echo ⚠ 未找到 uv 包管理器
    set "NEED_SETUP=1"
)

:: 检测 FFmpeg（PATH + core\ 目录 + 常见包管理器路径）
set "HAS_FFMPEG=0"
where ffmpeg >nul 2>&1 && set "HAS_FFMPEG=1"
if exist "core\ffmpeg.exe"    set "HAS_FFMPEG=1"
if exist "core\ffmpeg"        set "HAS_FFMPEG=1"
:: winget/scoop/choco 路径（与 core/ffmpeg_reader.py 中的 _WIN_FFMPEG_EXTRA_PATHS 一致）
for %%d in (
    "C:\Program Files\FFmpeg\bin"
    "%USERPROFILE%\scoop\apps\ffmpeg\current\bin"
    "C:\ProgramData\chocolatey\bin"
    "C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin"
) do (
    if exist "%%~d\ffmpeg.exe" set "HAS_FFMPEG=1"
)
if !HAS_FFMPEG! equ 0 (
    echo ⚠ 未找到 FFmpeg
    set "NEED_SETUP=1"
)

:: 检测 Python 包是否已安装（检查关键包）
uv run python -c "import numpy, cv2, requests" 2>nul
if errorlevel 1 (
    echo ⚠ Python 依赖待安装
    set "NEED_SETUP=1"
)

if defined NEED_SETUP (
    echo.
    echo ──────────────────────────────────────────────
    echo   检测到缺少必要的运行依赖。
    echo   是否运行安装脚本？ [Y/n]
    echo ──────────────────────────────────────────────
    set /p "CHOICE=   "
    if /i "!CHOICE!"=="n" (
        echo   请手动运行: setup.bat  ^(完整安装^)
        echo   或:         setup.bat --ffmpeg-only  ^(仅安装 FFmpeg^)
        echo.
        pause
        exit /b 1
    )
    echo.
    call setup.bat
    if errorlevel 1 (
        echo ❌ 安装失败，请检查错误信息后重试。
        pause
        exit /b 1
    )
)

echo [*] 同步依赖 (uv sync) ...
uv sync
if errorlevel 1 (
    color 0C
    echo ❌ 依赖安装失败
    pause
    exit /b 1
)

echo.
echo [*] 启动 ORCP GUI ...
echo -------------------------------------------------------

uv run python ocr_gui.py

echo -------------------------------------------------------
echo ✅ ORCP 已退出。
pause >nul
