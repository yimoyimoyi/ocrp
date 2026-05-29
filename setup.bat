@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title ORCP Setup

set "DIR=%~dp0"
cd /d "%DIR%"
set "LOG=%DIR%install.log"

rem -- parse args -------------------------------------------------------
set "FORCE_CPU="
set "FORCE_GPU="
set "SKIP_FFMPEG="
set "REINSTALL="

for %%a in (%*) do (
    if /i "%%a"=="--cpu"        set "FORCE_CPU=1"
    if /i "%%a"=="--gpu"        set "FORCE_GPU=1"
    if /i "%%a"=="--no-ffmpeg"  set "SKIP_FFMPEG=1"
    if /i "%%a"=="--reinstall"  set "REINSTALL=1"
    if /i "%%a"=="-h"           goto :help
    if /i "%%a"=="--help"       goto :help
)

cls
echo.>>"%LOG%"
call :log "=== Install started ==="

echo.
echo =========================================
echo   ORCP Setup
echo   Log: install.log
echo =========================================
echo.

rem -- [1/8] GPU detect -------------------------------------------------
call :step "1/8" "Detect GPU"

set "USE_GPU=0"
if defined FORCE_GPU set "USE_GPU=1"
if defined FORCE_GPU goto :gpu_done
if defined FORCE_CPU goto :gpu_done

nvidia-smi >nul 2>&1
if !errorlevel! neq 0 goto :gpu_cpu
set "USE_GPU=1"
echo     GPU detected:
for /f "tokens=*" %%i in ('nvidia-smi --query-gpu^=name --format^=csv,noheader 2^>nul') do echo       %%i
goto :gpu_done

:gpu_cpu
echo     No GPU found - CPU mode
:gpu_done
call :log "GPU=%USE_GPU%"

rem -- [2/8] uv (installer) ---------------------------------------------
call :step "2/8" "Check uv"

set "UV_EXE="
where uv >nul 2>&1 && set "UV_EXE=1"

if not defined UV_EXE goto :install_uv
for /f "delims=" %%v in ('uv --version 2^>^&1') do echo     %%v
goto :uv_ok

:install_uv
echo     Installing uv (standalone)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] uv install failed. Install manually: https://docs.astral.sh/uv/
    call :log "uv install failed"
    pause
    exit /b 1
)
for /f "delims=" %%v in ('uv --version 2^>^&1') do echo     %%v

:uv_ok
call :log "uv ready"

rem -- [3/8] Python (uv-managed) ----------------------------------------
call :step "3/8" "Install Python 3.12"

set "UV_PY_OK=0"
uv python find 2>nul | findstr /R "3\.1[2-3]" >nul && set "UV_PY_OK=1"
if !UV_PY_OK! equ 1 (
    for /f "delims=" %%v in ('uv python find 2^>^&1') do echo     Found: %%v
    goto :pyok
)

echo 3.12 > .python-version
echo     Downloading Python 3.12...
uv python install 3.12
if !errorlevel! equ 0 goto :pyok

echo     Python 3.12 failed, trying 3.13...
echo 3.13 > .python-version
uv python install 3.13
if !errorlevel! equ 0 goto :pyok

rem fallback - system Python
echo     Download failed. Searching system Python...
for %%c in (python3.13 python3.12 python3 python) do (
    where %%c >nul 2>&1
    if not !errorlevel! equ 0 (
    ) else (
        for /f "delims=" %%v in ('%%c --version 2^>^&1') do (
            echo %%v | findstr /R "3\.1[1-3]" >nul
            if not !errorlevel! equ 0 (
            ) else (
                echo     Using system: %%v
                for /f "tokens=2" %%w in ("%%v") do (
                    for /f "tokens=1-3 delims=." %%a in ("%%w") do echo %%a.%%b.%%c > .python-version
                )
                goto :pyok
            )
        )
    )
)

echo [ERROR] Cannot get Python 3.12/3.13.
call :log "Python install failed"
pause
exit /b 1

:pyok
for /f "delims=" %%v in ('uv python find 2^>^&1') do echo     %%v
call :log "Python ready"

rem -- [4/8] FFmpeg -----------------------------------------------------
call :step "4/8" "Check FFmpeg"

set "FF_OK=0"
if exist "core\ffmpeg.exe" if exist "core\ffprobe.exe" set "FF_OK=1"
where ffmpeg >nul 2>&1 && set "FF_OK=1"

if !FF_OK! equ 1 (
    echo     FFmpeg OK
    goto :ffdone
)

if defined SKIP_FFMPEG (
    echo     Skipped (--no-ffmpeg)
    goto :ffdone
)

set "FZIP=%TEMP%\orcp_ffmpeg.zip"
set "FEXT=%TEMP%\orcp_ff_extract"

echo     Downloading FFmpeg...
call :log "Downloading FFmpeg"
if exist "%FEXT%" rmdir /s /q "%FEXT%" >nul 2>&1

rem Multi-source fallback: GitHub direct -> ghproxy mirror -> alternative mirror
set "FF_DL_OK=0"
for %%u in (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    "https://mirror.ghproxy.com/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    "https://ghproxy.net/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
) do (
    if !FF_DL_OK! equ 0 (
        echo     Trying: %%~u
        powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%%~u' -OutFile '%FZIP%' -UseBasicParsing -TimeoutSec 120; exit 0 } catch { exit 1 }"
        if !errorlevel! equ 0 if exist "%FZIP%" (
            set "FF_DL_OK=1"
            call :log "FFmpeg downloaded from %%~u"
        )
    )
)

if !FF_DL_OK! equ 0 (
    echo     [ERROR] Download failed (all sources exhausted).
    echo     Install manually: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    echo     Extract ffmpeg.exe, ffprobe.exe to core\
    call :log "FFmpeg download failed"
    set "FFMPEG_MISSING=1"
    goto :ffdone
)

echo     Extracting...
mkdir "%FEXT%" 2>nul
powershell -NoProfile -Command "Expand-Archive -Path '%FZIP%' -DestinationPath '%FEXT%' -Force"

for /d %%d in ("%FEXT%\ffmpeg-*") do (
    if exist "%%d\bin\ffmpeg.exe" (
        mkdir core 2>nul
        copy /y "%%d\bin\ffmpeg.exe"  "core\ffmpeg.exe"  >nul
        copy /y "%%d\bin\ffprobe.exe" "core\ffprobe.exe" >nul
        set "FF_OK=1"
        echo     FFmpeg installed to core\
        call :log "FFmpeg installed"
    )
)

del "%FZIP%" >nul 2>&1
if exist "%FEXT%" rmdir /s /q "%FEXT%" >nul 2>&1

:ffdone

rem -- [5/8] Clean old venv (if --reinstall) ----------------------------
if defined REINSTALL (
    call :step "5/8" "Clean old venv"
    if exist ".venv" (
        echo     Removing .venv...
        rmdir /s /q ".venv" >nul 2>&1
    )
    del uv.lock 2>nul
    call :log "Cleaned old venv"
) else (
    echo.
    echo [5/8] Skip ^(use --reinstall to clean venv^)
)

rem -- [6/8] uv sync ----------------------------------------------------
call :step "6/8" "Sync dependencies"

set "UV_SYNC_OPTS=--index-strategy unsafe-best-match"
if !USE_GPU! equ 1 goto :sync_gpu

echo     Mode: CPU
echo     Source: PaddlePaddle CPU + PyPI
uv sync %UV_SYNC_OPTS%
if !errorlevel! equ 0 goto :sync_ok
goto :sync_fail

:sync_gpu
echo     Mode: GPU
echo     Source: PaddlePaddle CUDA 12.6 + PyTorch CUDA 12.6 + PyPI
echo     NOTE: torch cu126 bundles CUDA DLLs, paddlepaddle-gpu uses nvidia pip packages
set "TORCH_IDX=https://download.pytorch.org/whl/cu126"
set "PADDLE_IDX=https://www.paddlepaddle.org.cn/packages/stable/cu126/"

rem Step 1: Base sync
echo     Syncing base dependencies...
uv sync %UV_SYNC_OPTS%
if !errorlevel! neq 0 goto :sync_fail

rem Step 2: Check and install GPU torch if missing
echo     Checking GPU packages...
uv pip show torch 2>nul | findstr "+cu126" >nul
if !errorlevel! equ 0 (
    echo     GPU torch already installed
) else (
    echo     Installing GPU torch (CUDA 12.6)...
    uv pip install torch torchvision torchaudio --extra-index-url "%TORCH_IDX%" --no-deps --force-reinstall
    if !errorlevel! neq 0 (
        echo     [WARN] GPU torch install failed - will use CPU
    )
)

rem Step 3: Check and install paddlepaddle-gpu if missing
uv pip show paddlepaddle-gpu >nul 2>&1
if !errorlevel! equ 0 (
    echo     paddlepaddle-gpu already installed
) else (
    echo     Installing paddlepaddle-gpu (CUDA 12.6)...
    uv pip install paddlepaddle-gpu --extra-index-url "%PADDLE_IDX%" --no-deps --force-reinstall
    if !errorlevel! neq 0 (
        echo     [WARN] paddlepaddle-gpu not available - will use CPU paddle
    )
)
goto :sync_ok

:sync_fail
echo [ERROR] uv sync failed. See install.log
call :log "uv sync failed"
pause
exit /b 1

:sync_ok
call :log "uv sync done"

rem -- [7/8] Verify PaddleOCR -------------------------------------------
call :step "7/8" "Verify PaddleOCR"
echo     Testing import...
uv run python -c "from paddleocr import PaddleOCR; print('    PaddleOCR import OK')" 2>&1
if !errorlevel! neq 0 (
    echo     [WARN] PaddleOCR import failed - local engine may not work
    call :log "PaddleOCR import FAILED"
) else (
    call :log "PaddleOCR OK"
)

rem -- [8/8] cuDNN 8 check (GPU ASR) ------------------------------------
call :step "8/8" "Check cuDNN 8 (GPU ASR)"

if !USE_GPU! neq 1 (
    echo     Skipped - CPU mode
    goto :done
)

mkdir models\asr\lib 2>nul
uv run python -c "import ctypes; ctypes.CDLL('cudnn_cnn_infer64_8.dll')" 2>nul
if !errorlevel! equ 0 (
    echo     cuDNN 8 ready - GPU ASR enabled
    call :log "cuDNN 8 OK"
    goto :done
)
echo     cuDNN 8 not found - ASR will use CPU
echo.
echo     For GPU ASR, install cuDNN 8:
echo     1. https://developer.nvidia.com/cudnn
echo     2. Download cuDNN 8.9 for CUDA 12.x
echo     3. Copy 3 DLLs to models\asr\lib\:
echo        cudnn_ops_infer64_8.dll  cudnn_cnn_infer64_8.dll  cudnn64_8.dll
echo.
call :log "cuDNN 8 not found"

rem -- done -------------------------------------------------------------
:done
echo.
echo =========================================
echo   Setup complete!
if defined FFMPEG_MISSING (
    echo.
    echo   [WARN] FFmpeg not installed! Video processing will not work.
    echo   Download manually: https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    echo   Extract ffmpeg.exe, ffprobe.exe to core\
)
echo.
echo   Launch:   orcp_gui.bat
echo   Diagnose: diagnose.bat
echo   Log:      type install.log
echo =========================================
echo.
call :log "Setup complete"
timeout /t 2 /nobreak
exit /b 0

:help
echo Usage: setup.bat [OPTIONS]
echo   ^(none^)        Interactive install
echo   --cpu         Force CPU mode
echo   --gpu         Force GPU mode
echo   --no-ffmpeg   Skip FFmpeg install
echo   --reinstall   Clean venv and reinstall
echo   -h, --help    Show this help
exit /b 0

:step
echo.
echo [%~1] %~2...
call :log "Step %~1: %~2"
exit /b 0

:log
echo [%date% %time%] %*>>"%LOG%"
exit /b 0
