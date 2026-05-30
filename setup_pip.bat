@echo off
setlocal enabledelayedexpansion
title ORCP Setup (pip)

set "DIR=%~dp0"
cd /d "%DIR%"
set "LOG=%DIR%install.log"

cls
echo.>>"%LOG%"
call :log "=== pip Setup started ==="
echo.
echo =========================================
echo   ORCP Setup (pip mode)
echo   Log: install.log
echo =========================================
echo.

rem -- [1/5] Python -------------------------------------------------------
call :step "1/5" "Check Python 3.12+"
where python >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] Python not found. Install Python 3.12+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo     %%v
python -c "import sys; assert sys.version_info >= (3, 12), f'Python 3.12+ required, got {sys.version}'" 2>nul
if !errorlevel! neq 0 (
    echo [ERROR] Python 3.12+ required
    pause
    exit /b 1
)
call :log "Python ready"

rem -- [2/5] FFmpeg -------------------------------------------------------
call :step "2/5" "Check FFmpeg"
if exist "core\ffmpeg.exe" if exist "core\ffprobe.exe" goto :ffdone
where ffmpeg >nul 2>&1 && goto :ffdone

set "FZIP=%TEMP%\orcp_ffmpeg.zip"
set "FEXT=%TEMP%\orcp_ff_extract"
echo     Downloading FFmpeg...
if exist "%FEXT%" rmdir /s /q "%FEXT%" >nul 2>&1

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

rem -- [3/5] Create venv ---------------------------------------------------
call :step "3/5" "Create virtual environment"
if exist ".venv\Scripts\python.exe" (
    echo     venv already exists
    goto :venv_ok
)
echo     Creating venv...
python -m venv .venv
if !errorlevel! neq 0 (
    echo [ERROR] Failed to create venv
    pause
    exit /b 1
)
:venv_ok
call :log "venv ready"

rem -- [4/5] Install dependencies ------------------------------------------
call :step "4/5" "Install dependencies (pip)"
echo     Installing dependencies...
.venv\Scripts\pip.exe install --upgrade pip 2>nul
.venv\Scripts\pip.exe install -e .
if !errorlevel! neq 0 (
    echo [ERROR] pip install failed. See install.log
    call :log "pip install FAILED"
    pause
    exit /b 1
)
call :log "pip install done"

rem -- [5/5] Verify PaddleOCR ----------------------------------------------
call :step "5/5" "Verify PaddleOCR"
.venv\Scripts\python.exe -c "from paddleocr import PaddleOCR; print('    PaddleOCR OK')" 2>&1
if !errorlevel! neq 0 (
    echo     [WARN] PaddleOCR import failed - use API engines
    call :log "PaddleOCR FAILED"
) else (
    call :log "PaddleOCR OK"
)

rem -- done -----------------------------------------------------------------
echo.
echo =========================================
echo   Setup complete!
echo.
echo   Launch:   orcp_gui.bat
echo   Reinstall: del .venv ^&^& setup_pip.bat
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
