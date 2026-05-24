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

set "FURL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
set "FZIP=%TEMP%\orcp_ffmpeg.zip"
set "FEXT=%TEMP%\orcp_ff_extract"
echo     Downloading FFmpeg...
if exist "%FEXT%" rmdir /s /q "%FEXT%" >nul 2>&1
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%FURL%' -OutFile '%FZIP%' -UseBasicParsing"
if !errorlevel! neq 0 (
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

set "UV_EXTRA=--extra-index-url https://download.pytorch.org/whl/cu126 --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/"

if not exist ".venv" (
    echo     First install - creating venv (~2.5GB, 5-15 min)...
    uv sync --index-strategy unsafe-best-match %UV_EXTRA%
    goto :sync_done
)

echo     Updating existing venv...
uv sync --index-strategy unsafe-best-match %UV_EXTRA%
if !errorlevel! equ 0 goto :sync_done

echo     [WARN] Incremental sync failed, retrying with clean lock...
del uv.lock 2>nul
uv sync --index-strategy unsafe-best-match %UV_EXTRA%
if !errorlevel! equ 0 goto :sync_done

echo     [WARN] GPU sync failed, retrying CPU...
del uv.lock 2>nul
uv sync --index-strategy unsafe-best-match
if !errorlevel! neq 0 (
    echo [ERROR] Sync failed. See install.log
    pause
    exit /b 1
)
echo     Fallback to CPU mode

:sync_done
rem Ensure paddlepaddle-gpu is installed (uv sync may resolve CPU version)
uv pip install paddlepaddle-gpu --reinstall --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/ 2>nul
if !errorlevel! neq 0 (
    echo     [WARN] paddlepaddle-gpu not available - ASR+CPU OCR works
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
