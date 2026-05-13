@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title ORCP Diagnose

cd /d "%~dp0"

cls
echo.
echo =========================================
echo   ORCP - Diagnostics
echo =========================================
echo.

rem -- [1] System ------------------------------------------------------
echo --- 1. System ---
for /f "tokens=*" %%a in ('wmic os get caption ^| findstr /v Caption 2^>nul') do echo   OS:   %%a
echo   User: %USERNAME%

rem -- [2] Python ------------------------------------------------------
echo.
echo --- 2. Python ---
set "PY_OK=0"
for %%c in (python3.13 python3.12 python3.11 python3 python) do (
    where %%c >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%v in ('%%c --version 2^>^&1') do (
            echo   OK: %%v
            set "PY_OK=1"
        )
        goto :py_done
    )
)
echo   MISSING: Python 3.11+
:py_done

rem -- [3] Package managers -------------------------------------------
echo.
echo --- 3. Package managers ---
where uv >nul 2>&1 && (for /f "tokens=*" %%v in ('uv --version') do echo   OK: uv %%v) || echo   MISSING: uv
where pip >nul 2>&1 && echo   OK: pip available || echo   INFO: pip not found (may be in venv)

rem -- [4] Key dependencies -------------------------------------------
echo.
echo --- 4. FFmpeg ---
set "FF_OK=0"
where ffmpeg >nul 2>&1 && set "FF_OK=1"
if exist "core\ffmpeg.exe" set "FF_OK=1"
if !FF_OK! equ 1 (echo   OK) else (echo   MISSING)

rem -- [5] GPU / CUDA -------------------------------------------------
echo.
echo --- 5. GPU ---
nvidia-smi >nul 2>&1
if !errorlevel! equ 0 (
    echo   OK: NVIDIA GPU
    for /f "tokens=*" %%i in ('nvidia-smi --query-gpu^=name --format^=csv,noheader 2^>nul') do echo    %%i
) else (
    echo   INFO: No NVIDIA GPU (CPU mode)
)

rem -- [6] cuDNN 8 -----------------------------------------------------
echo.
echo --- 6. cuDNN 8 (GPU ASR) ---
if !PY_OK! equ 1 (
    python -c "import ctypes; ctypes.CDLL('cudnn_cnn_infer64_8.dll')" >nul 2>&1
    if !errorlevel! equ 0 (
        echo   OK: cuDNN 8 ready
    ) else (
        echo   MISSING: GPU ASR disabled (CPU ASR works)
    )
) else (
    echo   SKIP: Python not found
)

rem -- [7] Python packages --------------------------------------------
echo.
echo --- 7. Python packages ---
if exist ".venv" (echo   OK: .venv exists) else (echo   INFO: No .venv yet)
if exist "pyproject.toml" (echo   OK: pyproject.toml) else (echo   MISSING: pyproject.toml)

rem -- [8] Project structure ------------------------------------------
echo.
echo --- 8. Project files ---
if exist core\        (echo   OK: core\)     else (echo   MISSING: core\)
if exist ui\          (echo   OK: ui\)       else (echo   MISSING: ui\)
if exist models\      (echo   OK: models\)   else (echo   MISSING: models\)
if exist config\      (echo   OK: config\)   else (echo   MISSING: config\)
if exist ocr_gui.py   (echo   OK: ocr_gui.py) else (echo   MISSING: ocr_gui.py)

rem -- [9] Logs --------------------------------------------------------
echo.
echo --- 9. Install log ---
if exist install.log (
    echo   OK: install.log (last 3 lines)
    powershell -Command "Get-Content install.log -Tail 3" 2>nul | findstr /v "^$"
) else (
    echo   INFO: No install.log yet
)

rem -- [10] Recommendations --------------------------------------------
echo.
echo --- 10. Summary ---
set "OK_COUNT=0"

where uv >nul 2>&1
if !errorlevel! neq 0 (
    echo   ACTION: Install uv -- pip install uv
) else (
    set /a OK_COUNT+=1
)

if !FF_OK! equ 0 (
    echo   ACTION: Install FFmpeg -- Run setup.bat
) else (
    set /a OK_COUNT+=1
)

if !PY_OK! equ 0 (
    echo   ACTION: Install Python 3.12+ from python.org
) else (
    set /a OK_COUNT+=1
)

if !OK_COUNT! geq 3 (
    echo.
    echo   Ready! Launch with: orcp_gui.bat
) else (
    echo.
    echo   Run setup.bat to fix missing dependencies.
)

echo.
echo =========================================
echo   Diagnostics complete.
echo =========================================
echo.
pause
exit /b 0
