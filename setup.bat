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
echo [%date% %time%] === Install started ===>>"%LOG%"

echo.
echo =========================================
echo   ORCP Setup
echo   Log: install.log
echo =========================================
echo.

rem -- [1] GPU detect ---------------------------------------------------
set "USE_GPU=0"
if defined FORCE_GPU set "USE_GPU=1"
if defined FORCE_CPU set "USE_GPU=0"

echo [1/7] Detect GPU...

if not defined FORCE_CPU if not defined FORCE_GPU (
    nvidia-smi >nul 2>&1
    if !errorlevel! equ 0 (
        set "USE_GPU=1"
        echo        GPU detected:
        for /f "tokens=*" %%i in ('nvidia-smi --query-gpu^=name --format^=csv,noheader 2^>nul') do echo          %%i
    ) else (
        echo        No GPU found. CPU mode.
    )
)

rem -- [2] Python -------------------------------------------------------
echo.
echo [2/7] Check Python...

set "PYTHON_CMD="
for %%c in (python3.13 python3.12 python3.11 python3 python) do (
    where %%c >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%v in ('%%c --version 2^>^&1') do set "pv=%%v"
        echo !pv! | findstr /R "3\.1[1-9]" >nul
        if !errorlevel! equ 0 (
            set "PYTHON_CMD=%%c"
            goto :pyok
        )
    )
)
echo [ERROR] Python 3.11+ required.
echo         Download: https://www.python.org/downloads/
pause
exit /b 1
:pyok
echo        %pv%
call :log "Python: %pv%"

rem -- [3] uv -----------------------------------------------------------
echo.
echo [3/7] Check uv...

where uv >nul 2>&1
if !errorlevel! neq 0 (
    echo        Installing uv...
    "%PYTHON_CMD%" -m pip install uv --quiet --user 2>>"%LOG%"
    for /f "tokens=*" %%a in ('"%PYTHON_CMD%" -c "import site; print(site.USER_BASE)"') do set "PATH=!PATH!;%%a\Scripts"
)
for /f "tokens=*" %%v in ('uv --version 2^>^&1') do echo        uv: %%v
call :log "uv: installed"

rem -- [4] FFmpeg -------------------------------------------------------
echo.
echo [4/7] Check FFmpeg...

set "FF_OK=0"
if exist "core\ffmpeg.exe" if exist "core\ffprobe.exe" set "FF_OK=1"
where ffmpeg >nul 2>&1 && set "FF_OK=1"

if !FF_OK! equ 1 (
    echo        FFmpeg OK
    goto :ffdone
)

if defined SKIP_FFMPEG (
    echo        Skipped (--no-ffmpeg)
    goto :ffdone
)

set "FURL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
set "FZIP=%TEMP%\orcp_ffmpeg.zip"
set "FEXT=%TEMP%\orcp_ff_extract"

echo        Downloading FFmpeg...
call :log "Downloading FFmpeg..."

if exist "%FEXT%" rmdir /s /q "%FEXT%" >nul 2>&1

powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%FURL%' -OutFile '%FZIP%' -UseBasicParsing" 2>>"%LOG%"
if !errorlevel! neq 0 (
    echo        Download failed. Please install manually:
    echo        https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    echo        Extract ffmpeg.exe, ffprobe.exe to core\
    call :log "FFmpeg download failed"
    goto :ffdone
)

mkdir "%FEXT%" 2>nul
powershell -NoProfile -Command "Expand-Archive -Path '%FZIP%' -DestinationPath '%FEXT%' -Force" 2>>"%LOG%"

for /d %%d in ("%FEXT%\ffmpeg-*") do (
    if exist "%%d\bin\ffmpeg.exe" (
        mkdir core 2>nul
        copy /y "%%d\bin\ffmpeg.exe"  "core\ffmpeg.exe"  >nul
        copy /y "%%d\bin\ffprobe.exe" "core\ffprobe.exe" >nul
        set "FF_OK=1"
        echo        FFmpeg installed to core\
        call :log "FFmpeg installed"
    )
)

del "%FZIP%" >nul 2>&1
if exist "%FEXT%" rmdir /s /q "%FEXT%" >nul 2>&1

:ffdone

rem -- [5] Clean old venv (if --reinstall) -----------------------------
if defined REINSTALL (
    echo.
    echo [5/7] Clean old venv...
    if exist ".venv" rmdir /s /q ".venv" >nul 2>&1
    call :log "Cleaned old venv"
)

rem -- [6] uv sync ------------------------------------------------------
echo.
echo [6/7] Sync dependencies (uv sync)...
call :log "uv sync starting..."

uv sync 2>>"%LOG%"
if !errorlevel! neq 0 (
    if !USE_GPU! equ 1 (
        echo        GPU version failed, retry CPU...
        call :log "GPU failed, retry CPU"
        set "USE_GPU=0"
        uv sync 2>>"%LOG%"
        if !errorlevel! neq 0 (
            echo [ERROR] uv sync failed. See install.log
            call :log "uv sync failed"
            pause
            exit /b 1
        )
    ) else (
        echo [ERROR] uv sync failed.
        pause
        exit /b 1
    )
)
call :log "uv sync done"

rem -- [7] cuDNN 8 check ------------------------------------------------
echo.
echo [7/7] Check cuDNN 8 (GPU ASR)...

if !USE_GPU! equ 1 (
    mkdir models\asr\lib 2>nul
    uv run python -c "import ctypes; ctypes.CDLL('cudnn_cnn_infer64_8.dll')" 2>nul
    if !errorlevel! equ 0 (
        echo        cuDNN 8 ready - GPU ASR enabled
        call :log "cuDNN 8 OK"
    ) else (
        echo        cuDNN 8 not found. ASR will use CPU.
        echo.
        echo        For GPU ASR, install cuDNN 8:
        echo        1. https://developer.nvidia.com/cudnn
        echo        2. Download cuDNN 8.9 for CUDA 12.x
        echo        3. Copy 3 DLLs to models\asr\lib\:
        echo           cudnn_ops_infer64_8.dll
        echo           cudnn_cnn_infer64_8.dll
        echo           cudnn64_8.dll
        echo.
        call :log "cuDNN 8 not found"
    )
)

rem -- done -------------------------------------------------------------
echo.
echo =========================================
echo   Setup complete!
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

:log
echo [%date% %time%] %*>>"%LOG%"
exit /b 0
