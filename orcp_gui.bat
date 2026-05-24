@echo off
setlocal enabledelayedexpansion
title ORCP

set "DIR=%~dp0"
cd /d "%DIR%"

set CUDA_MODULE_LOADING=LAZY
set PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

echo.
echo =========================================
echo   ORCP - OCR / ASR Processing Tool
echo =========================================
echo.

rem -- locate Python (pythonw.exe = no console window) -------------------
set "PYTHON_EXE="

if exist ".venv\Scripts\pythonw.exe" (
    set "PYTHON_EXE=%DIR%.venv\Scripts\pythonw.exe"
    echo [INFO] Python: pythonw
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=%DIR%.venv\Scripts\python.exe"
    echo [INFO] Python: python
) else (
    echo [ERROR] .venv not found. Run setup_gpu.bat or setup_cpu.bat.
    pause
    exit /b 1
)

rem -- quick FFmpeg check -------------------------------------------------
set "HAS_FFMPEG=0"
where ffmpeg >nul 2>&1 && set "HAS_FFMPEG=1"
if exist "core\ffmpeg.exe" set "HAS_FFMPEG=1"
if !HAS_FFMPEG! equ 0 (
    echo [WARN] FFmpeg not found - video features limited.
    echo        Run setup_gpu.bat or setup_cpu.bat to install.
    echo.
)

rem -- launch ------------------------------------------------------------
echo [INFO] Starting ORCP...
echo.

"%PYTHON_EXE%" ocr_gui.py
if errorlevel 1 (
    echo.
    echo =========================================
    echo   Startup failed.
    echo   Try: setup_gpu.bat or setup_cpu.bat
    echo =========================================
    echo.
    pause
    exit /b 1
)

exit /b 0
