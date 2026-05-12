@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title ORCP - Windows 安装脚本

:: =============================================================================
:: ORCP - OCR 处理工具  Windows 一键安装脚本
:: =============================================================================
:: 用法:
::   setup.bat                         交互式安装
::   setup.bat --cpu                   强制纯 CPU 模式
::   setup.bat --gpu                   强制 GPU 模式
::   setup.bat --no-ffmpeg             跳过 FFmpeg 安装
::   setup.bat --ffmpeg-only           仅安装 FFmpeg
:: =============================================================================

set "FORCE_CPU="
set "FORCE_GPU="
set "SKIP_FFMPEG="
set "FFMPEG_ONLY="
for %%a in (%*) do (
    if /i "%%a"=="--cpu"         set "FORCE_CPU=1"
    if /i "%%a"=="--gpu"         set "FORCE_GPU=1"
    if /i "%%a"=="--no-ffmpeg"   set "SKIP_FFMPEG=1"
    if /i "%%a"=="--ffmpeg-only" set "FFMPEG_ONLY=1"
    if /i "%%a"=="-h"            goto :help
    if /i "%%a"=="--help"        goto :help
)

cd /d "%~dp0"

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║        ORCP - OCR 处理工具  Windows 安装程序             ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

:: ── 1. 检测 GPU ────────────────────────────────────────────────────────────
echo [1/6] 检测系统环境 ...

set "USE_GPU=0"
if defined FORCE_GPU  set "USE_GPU=1"
if defined FORCE_CPU  set "USE_GPU=0"

if defined FORCE_CPU  goto :skip_gpu_detect
if defined FORCE_GPU  goto :skip_gpu_detect

nvidia-smi >nul 2>&1
if !errorlevel! equ 0 (
    set "USE_GPU=1"
    echo [INFO] 检测到 NVIDIA GPU:
    for /f "tokens=*" %%i in ('nvidia-smi --query-gpu^=name --format^=csv,noheader 2^>nul') do echo         %%i
) else (
    echo [INFO] 未检测到 NVIDIA GPU，使用纯 CPU 模式
)

:skip_gpu_detect

:: ── 2. 检查 Python ─────────────────────────────────────────────────────────
if defined FFMPEG_ONLY goto :install_ffmpeg

echo.
echo [2/6] 检查 Python ...

set "PYTHON_CMD="
for %%c in (python3.12 python3.13 python3.11 python3 python) do (
    where %%c >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%v in ('%%c --version 2^>^&1') do set "pyver=%%v"
        echo !pyver! | findstr /R "3\.1[12345]" >nul
        if !errorlevel! equ 0 (
            set "PYTHON_CMD=%%c"
            goto :found_python
        )
    )
)

echo [ERROR] 需要 Python >= 3.11，但未找到。
echo         请从 https://www.python.org/downloads/ 下载安装 Python 3.12+
echo         （安装时请勾选 "Add Python to PATH"）
pause
exit /b 1

:found_python
echo [INFO] Python: %pyver%

:: ── 3. 安装 uv ─────────────────────────────────────────────────────────────
echo.
echo [3/6] 检查 uv 包管理器 ...

where uv >nul 2>&1
if !errorlevel! neq 0 (
    echo [INFO] 正在安装 uv ...
    pip install uv --user
    :: 刷新 PATH
    for /f "tokens=*" %%a in ('python -c "import site; print(site.USER_BASE)"') do set "PATH=!PATH!;%%a\Scripts"
    where uv >nul 2>&1
    if !errorlevel! neq 0 (
        echo [ERROR] uv 安装失败，请手动安装: pip install uv
        pause
        exit /b 1
    )
)

for /f "tokens=*" %%v in ('uv --version 2^>^&1') do echo [INFO] uv: %%v

:: ── 4. 安装 FFmpeg ─────────────────────────────────────────────────────────
:install_ffmpeg
echo.
echo [4/6] 安装 FFmpeg ...

set "FFMPEG_INSTALLED=0"

:: 检查 core/ 目录是否已有捆绑的 FFmpeg
if exist "core\ffmpeg.exe" if exist "core\ffprobe.exe" (
    echo [INFO] 已在 core\ 目录找到捆绑的 FFmpeg
    set "FFMPEG_INSTALLED=1"
    goto :ffmpeg_done
)

:: 检查系统 PATH 中是否有
where ffmpeg >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO] 系统已有 FFmpeg
    set "FFMPEG_INSTALLED=1"
    goto :ffmpeg_done
)

:: 检查 winget 安装的（可能不在 PATH 但已安装）
where winget >nul 2>&1
if !errorlevel! equ 0 (
    winget list --name "FFmpeg" --exact >nul 2>&1
    if !errorlevel! equ 0 (
        echo [INFO] FFmpeg 已通过 winget 安装
        set "FFMPEG_INSTALLED=1"
        goto :ffmpeg_done
    )
)

:: 检查 scoop
where scoop >nul 2>&1
if !errorlevel! equ 0 (
    scoop list | findstr /i "ffmpeg" >nul 2>&1
    if !errorlevel! equ 0 (
        echo [INFO] FFmpeg 已通过 scoop 安装
        set "FFMPEG_INSTALLED=1"
        goto :ffmpeg_done
    )
)

:: 检查 chocolatey
where choco >nul 2>&1
if !errorlevel! equ 0 (
    choco list --local-only ffmpeg >nul 2>&1
    if !errorlevel! equ 0 (
        echo [INFO] FFmpeg 已通过 choco 安装
        set "FFMPEG_INSTALLED=1"
        goto :ffmpeg_done
    )
)

if defined SKIP_FFMPEG (
    echo [WARN] --no-ffmpeg 跳过安装
    goto :ffmpeg_done
)

echo [INFO] 正在下载 FFmpeg 静态构建到 core\ 目录 ...
echo         来源: BtbN/FFmpeg-Builds (GitHub)

set "FFMPEG_URL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
set "FFMPEG_ZIP=%TEMP%\orcp_ffmpeg_download.zip"
set "FFMPEG_EXTRACT=%TEMP%\orcp_ffmpeg_extract"

:: 下载
echo [INFO] 下载中, 请稍候 ...
powershell -NoProfile -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
    "try { Invoke-WebRequest -Uri '%FFMPEG_URL%' -OutFile '%FFMPEG_ZIP%' -UseBasicParsing } " ^
    "catch { Write-Error '下载失败, 请检查网络连接'; exit 1 }"
if !errorlevel! neq 0 (
    echo [WARN] 自动下载失败，尝试使用 winget 安装 ...
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install --id Gyan.FFmpeg --exact --accept-package-agreements --silent
        if !errorlevel! equ 0 (
            echo [INFO] 已通过 winget 安装 FFmpeg
            set "FFMPEG_INSTALLED=1"
            goto :ffmpeg_done
        )
    )
    echo [ERROR] 无法自动安装 FFmpeg，请手动下载:
    echo         https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    echo         解压后将 bin\ 目录下的 ffmpeg.exe, ffprobe.exe, ffplay.exe
    echo         复制到: %CD%\core\
    goto :ffmpeg_done
)

:: 解压
echo [INFO] 解压中 ...
if exist "%FFMPEG_EXTRACT%" rmdir /s /q "%FFMPEG_EXTRACT%"
mkdir "%FFMPEG_EXTRACT%" 2>nul
powershell -NoProfile -Command ^
    "Expand-Archive -Path '%FFMPEG_ZIP%' -DestinationPath '%FFMPEG_EXTRACT%' -Force"
if !errorlevel! neq 0 (
    echo [ERROR] 解压失败
    goto :cleanup_ffmpeg
)

:: 查找并复制二进制文件到 core/
echo [INFO] 复制 FFmpeg 到 core\ 目录 ...
for /d %%d in ("%FFMPEG_EXTRACT%\*") do (
    if exist "%%d\bin\ffmpeg.exe" (
        copy /y "%%d\bin\ffmpeg.exe"  "core\ffmpeg.exe"  >nul
        copy /y "%%d\bin\ffprobe.exe" "core\ffprobe.exe" >nul
        copy /y "%%d\bin\ffplay.exe"  "core\ffplay.exe"  >nul 2>nul
        goto :ffmpeg_copied
    )
)
:: 有些构建包结构不同
for /d %%d in ("%FFMPEG_EXTRACT%\ffmpeg-*") do (
    if exist "%%d\bin\ffmpeg.exe" (
        copy /y "%%d\bin\ffmpeg.exe"  "core\ffmpeg.exe"  >nul
        copy /y "%%d\bin\ffprobe.exe" "core\ffprobe.exe" >nul
        copy /y "%%d\bin\ffplay.exe"  "core\ffplay.exe"  >nul 2>nul
        goto :ffmpeg_copied
    )
)

:ffmpeg_copied
if exist "core\ffmpeg.exe" (
    echo [INFO] FFmpeg/FFprobe/FFplay 已安装到 core\ 目录
    set "FFMPEG_INSTALLED=1"
) else (
    echo [WARN] 未能自动复制 FFmpeg, 请手动将 ffmpeg.exe/ffprobe.exe/ffplay.exe 放到 core\ 目录
)

:cleanup_ffmpeg
del "%FFMPEG_ZIP%" >nul 2>&1
if exist "%FFMPEG_EXTRACT%" rmdir /s /q "%FFMPEG_EXTRACT%" >nul 2>&1

:ffmpeg_done
if defined FFMPEG_ONLY goto :done

:: ── 5. 安装 Python 依赖 ────────────────────────────────────────────────────
echo.
echo [5/6] 同步 Python 依赖 (uv sync) ...

uv sync
if !errorlevel! neq 0 (
    if !USE_GPU! equ 1 (
        echo [WARN] GPU 版 PaddlePaddle 安装失败，尝试 CPU 版本 ...
        set "USE_GPU=0"
        uv sync
    )
)

:: ── cuDNN 8 检查（GPU 语音识别加速）────────────────────────────
if !USE_GPU! equ 1 (
    echo [INFO] 检查 cuDNN 8 (GPU 语音识别加速)...

    :: 创建 lib 目录
    if not exist "models\asr\lib" mkdir "models\asr\lib"

    uv run python -c "import ctypes; ctypes.CDLL('cudnn_ops_infer64_8.dll'); print('OK')" 2>nul
    if !errorlevel! equ 0 (
        echo [INFO] ✅ cuDNN 8 已就绪，GPU ASR 可用
    ) else (
        echo [INFO] ⚠ cuDNN 8 未找到，ASR 将使用 CPU 模式
        echo.
        echo    如需 GPU 语音识别加速，请手动安装 cuDNN 8：
        echo    1. 访问 https://developer.nvidia.com/cudnn（免费注册账号）
        echo    2. 下载 cuDNN 8.9 for CUDA 12.x (Windows zip)
        echo    3. 解压后将以下 3 个 DLL 复制到 models\asr\lib\:
        echo         cudnn_ops_infer64_8.dll
        echo         cudnn_cnn_infer64_8.dll
        echo         cudnn64_8.dll
        echo.
        echo    💡 不影响 OCR 功能和 CPU ASR，可稍后安装。
        echo.
    )
)

:cudnn8_done

if !errorlevel! neq 0 (
    echo [ERROR] 依赖安装失败
    pause
    exit /b 1
)

echo [INFO] Python 依赖安装完成

:: ── 6. 验证 ────────────────────────────────────────────────────────────────
echo.
echo [6/6] 验证安装 ...

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║            安装完成！                                    ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

echo 环境信息:
echo   OS:       Windows
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo   Python:   %%v
for /f "tokens=*" %%v in ('uv --version 2^>^&1')   do echo   uv:       %%v
where ffmpeg >nul 2>&1 && (ffmpeg -version 2>nul | findstr /r "^ffmpeg" & echo   ffmpeg:   ^(PATH^)) || (
    if exist "core\ffmpeg.exe" (echo   ffmpeg:   core\ffmpeg.exe) else (echo   ffmpeg:   N/A)
)
if !USE_GPU! equ 1 (
    for /f "tokens=*" %%i in ('nvidia-smi --query-gpu^=name --format^=csv,noheader 2^>nul') do echo   GPU:      %%i
)

echo.
echo 功能验证:
uv run python -c "import numpy, cv2, requests; print('  numpy, cv2, requests: OK')" 2>nul || echo [WARN] 基础依赖导入异常

if !USE_GPU! equ 1 (
    uv run python -c "import paddle; print('  paddle: ', paddle.__version__); paddle.is_compiled_with_cuda() and print('  GPU: OK') or print('  GPU: NO')" 2>nul || echo [WARN] PaddlePaddle GPU 检查失败
)

:done
echo.
echo ══════════════════════════════════════════════════════════
echo   启动 ORCP:   orcp_gui.bat
echo   或直接:      uv run python ocr_gui.py
echo ══════════════════════════════════════════════════════════
echo.
pause >nul
exit /b 0

:help
echo 用法: setup.bat [选项]
echo   --cpu           强制纯 CPU 模式
echo   --gpu           强制 GPU 模式（需 NVIDIA 显卡）
echo   --no-ffmpeg     跳过 FFmpeg 安装
echo   --ffmpeg-only   仅安装 FFmpeg，不安装 Python 依赖
exit /b 0
