@echo off
setlocal enabledelayedexpansion
title ORCP

set "DIR=%~dp0"
cd /d "%DIR%"

set http_proxy=+
set https_proxy=
set all_proxy=
set CUDA_MODULE_LOADING=LAZY
set PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

echo.
echo =========================================
echo   ORCP - OCR / ASR Processing Tool
echo =========================================
echo.

rem -- locate Python -----------------------------------------------------
set "PYTHON_EXE="

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=%DIR%.venv\Scripts\python.exe"
    echo [INFO] Using venv Python
) else (
    for %%c in (python3 python) do (
        where %%c >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_EXE=%%c"
            goto :have_python
        )
    )
)

if not defined PYTHON_EXE (
    echo [ERROR] Python not found.
    echo        Run setup.bat to install dependencies.
    pause
    exit /b 1
)
:have_python

echo [INFO] Python: %PYTHON_EXE%
echo.

rem -- check packages ----------------------------------------------------
"%PYTHON_EXE%" -c "import numpy, cv2, requests" 2>nul
if errorlevel 1 (
    echo [WARN] Python packages incomplete.
    echo        Running setup.bat...
    call "%DIR%setup.bat"
    if errorlevel 1 (
        echo [ERROR] Setup failed.
        pause
        exit /b 1
    )
)

rem -- check FFmpeg ------------------------------------------------------
set "HAS_FFMPEG=0"
where ffmpeg >nul 2>&1 && set "HAS_FFMPEG=1"
if exist "core\ffmpeg.exe" set "HAS_FFMPEG=1"
for %%d in (
    "C:\Program Files\FFmpeg\bin"
    "%USERPROFILE%\scoop\apps\ffmpeg\current\bin"
    "C:\ProgramData\chocolatey\bin"
    "C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin"
) do (
    if exist "%%~d\ffmpeg.exe" set "HAS_FFMPEG=1"
)
if !HAS_FFMPEG! equ 0 (
    echo [WARN] FFmpeg not found. Video features limited.
    echo        Run setup.bat to auto-install.
    echo.
)

rem -- launch ------------------------------------------------------------
echo [INFO] Starting ORCP GUI...
echo.

"%PYTHON_EXE%" ocr_gui.py
if errorlevel 1 (
    echo.
    echo =========================================
    echo   Startup failed.
    echo.
    echo   Try:
    echo     1. Run setup.bat to reinstall
    echo     2. Run diagnose.bat to check env
    echo     3. See install.log for details
    echo =========================================
    echo.
    pause
    exit /b 1
)

exit /b 0
