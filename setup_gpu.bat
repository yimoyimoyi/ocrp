@echo off
setlocal enabledelayedexpansion
title ORCP Setup (GPU)

set "DIR=%~dp0"
cd /d "%DIR%"
set "LOG=%DIR%install.log"
set "USE_GPU=1"

cls
echo.>>"%LOG%"
call :log "=== GPU Setup started ==="
echo.
echo =========================================
echo   ORCP Setup (GPU Mode - CUDA 12.6)
echo   Log: install.log
echo =========================================
echo.

rem -- check NVIDIA GPU --------------------------------------------------
call :step "1/7" "Check NVIDIA GPU"
nvidia-smi >nul 2>&1
if !errorlevel! neq 0 (
    echo     [WARN] No NVIDIA GPU or driver not found
    echo     GPU setup requires NVIDIA GPU with latest driver
    echo     Use setup_cpu.bat for CPU-only mode
    pause
    exit /b 1
)
call :log "GPU detected"

rem -- [2/7] uv ---------------------------------------------------------
call :step "2/7" "Check uv"
where uv >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%v in ('uv --version 2^>^&1') do echo     %%v
    goto :uv_ok
)
echo     Installing uv...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] uv install failed
    pause
    exit /b 1
)
:uv_ok
call :log "uv ready"

rem -- [3/7] Python -----------------------------------------------------
call :step "3/7" "Check Python 3.12+"
uv python find 2>nul | findstr /R "3\.1[2-9]" >nul
if !errorlevel! equ 0 goto :pyok
echo     Downloading Python 3.12...
echo 3.12 > .python-version
uv python install 3.12 2>nul
if !errorlevel! equ 0 goto :pyok
echo 3.13 > .python-version
uv python install 3.13 2>nul
if !errorlevel! equ 0 goto :pyok
echo [ERROR] Cannot get Python 3.12+. Install manually.
pause
exit /b 1
:pyok
for /f "delims=" %%v in ('uv python find 2^>^&1') do echo     %%v
call :log "Python ready"

rem -- [4/7] FFmpeg -----------------------------------------------------
call :step "4/7" "Check FFmpeg"
if exist "core\ffmpeg.exe" if exist "core\ffprobe.exe" goto :ffdone

set "FZIP=%TEMP%\orcp_ffmpeg.zip"
set "FEXT=%TEMP%\orcp_ff_extract"
echo     Downloading FFmpeg...
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
        )
    )
)

if !FF_DL_OK! equ 0 (
    echo     [WARN] FFmpeg download failed - install manually
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
        echo     FFmpeg installed to core\
        call :log "FFmpeg installed"
    )
)
del "%FZIP%" >nul 2>&1
if exist "%FEXT%" rmdir /s /q "%FEXT%" >nul 2>&1
:ffdone

rem -- [5/7] Sync dependencies (GPU, incremental) -----------------------
call :step "5/7" "Sync dependencies (GPU - CUDA 12.6)"
echo     Source: PyTorch CUDA 12.6 + PaddlePaddle CUDA 12.6 + PyPI

set "TORCH_IDX=https://download.pytorch.org/whl/cu126"
set "PADDLE_IDX=https://www.paddlepaddle.org.cn/packages/stable/cu126/"

rem Step 1: Base sync (CPU deps first, then replace with GPU)
echo     Syncing base dependencies...
uv sync --index-strategy unsafe-best-match 2>nul
if !errorlevel! neq 0 (
    echo     [WARN] Base sync failed, retrying with clean lock...
    del uv.lock 2>nul
    uv sync --index-strategy unsafe-best-match
    if !errorlevel! neq 0 (
        echo [ERROR] Sync failed. See install.log
        pause
        exit /b 1
    )
)

rem Step 2: Remove CPU versions (avoid namespace conflict with GPU packages)
echo     Replacing CPU packages with GPU...
uv pip uninstall paddlepaddle 2>nul
uv pip uninstall torch torchvision torchaudio 2>nul

rem Step 3: Install GPU torch (CUDA 12.6)
echo     Installing torch (CUDA 12.6)...
uv pip install torch torchvision torchaudio --index-url "%TORCH_IDX%"
if !errorlevel! neq 0 (
    echo     [ERROR] GPU torch install failed - GPU OCR will not work
    call :log "GPU torch FAILED"
    set "GPU_FAILED=1"
) else (
    echo     GPU torch installed
    call :log "GPU torch OK"
)

rem Step 4: Install paddlepaddle-gpu (CUDA 12.6, pulls nvidia-* runtime DLLs)
if not defined GPU_FAILED (
    echo     Installing paddlepaddle-gpu (CUDA 12.6)...
    uv pip install paddlepaddle-gpu --index-url "%PADDLE_IDX%"
    if !errorlevel! neq 0 (
        echo     [ERROR] paddlepaddle-gpu install failed
        call :log "paddlepaddle-gpu FAILED"
        set "GPU_FAILED=1"
    ) else (
        echo     paddlepaddle-gpu installed
        call :log "paddlepaddle-gpu OK"
    )
)

rem Step 5: Verify GPU
echo     Verifying GPU...
uv run python -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'; print(f'    GPU OK: {torch.cuda.get_device_name(0)} (CUDA {torch.version.cuda})')" 2>&1
if !errorlevel! neq 0 (
    echo     [WARN] GPU verification failed - check GPU driver and CUDA environment
    call :log "GPU verify FAILED"
    set "GPU_FAILED=1"
)

call :log "uv sync done"

rem -- [6/7] Verify PaddleOCR -------------------------------------------
call :step "6/7" "Verify PaddleOCR"
uv run python -c "from paddleocr import PaddleOCR; print('    PaddleOCR OK')" 2>&1
if !errorlevel! neq 0 (
    echo     [WARN] PaddleOCR import failed - GPU OCR may not work
    call :log "PaddleOCR FAILED"
) else (
    call :log "PaddleOCR OK"
)

rem -- [7/7] cuDNN 8 (GPU ASR) -----------------------------------------
call :step "7/7" "Check cuDNN 8 (GPU ASR)"
mkdir models\asr\lib 2>nul
uv run python -c "import ctypes; ctypes.CDLL('cudnn_cnn_infer64_8.dll')" 2>nul
if !errorlevel! equ 0 (
    echo     cuDNN 8 ready - GPU ASR enabled
    call :log "cuDNN 8 OK"
    goto :done
)
echo     cuDNN 8 not found - ASR will use CPU
echo     For GPU ASR, download cuDNN 8.9 for CUDA 12.x to models\asr\lib\
call :log "cuDNN 8 not found"

rem -- done -------------------------------------------------------------
:done
echo.
echo =========================================
echo   Setup complete! (GPU Mode)
echo.
echo   Launch:   orcp_gui.bat
echo   Reinstall: del .venv ^&^& setup_gpu.bat
echo =========================================
echo.
call :log "Setup complete"
pause
exit /b 0

:step
echo.
echo [%~1] %~2...
call :log "Step %~1: %~2"
exit /b 0

:log
echo [%date% %time%] %*>>"%LOG%"
exit /b 0
