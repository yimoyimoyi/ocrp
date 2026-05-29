@echo off
setlocal enabledelayedexpansion
title ORCP Setup (CPU)

set "DIR=%~dp0"
cd /d "%DIR%"
set "LOG=%DIR%install.log"
set "USE_GPU=0"

cls
echo.>>"%LOG%"
call :log "=== CPU Setup started ==="
echo.
echo =========================================
echo   ORCP Setup (CPU Mode)
echo   Log: install.log
echo =========================================
echo.

rem -- [1/6] uv ---------------------------------------------------------
call :step "1/6" "Check uv"
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

rem -- [2/6] Python -----------------------------------------------------
call :step "2/6" "Check Python 3.12+"
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

rem -- [3/6] FFmpeg -----------------------------------------------------
call :step "3/6" "Check FFmpeg"
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

rem -- [4/6] Sync dependencies (CPU, incremental) -----------------------
call :step "4/6" "Sync dependencies (CPU)"
echo     Syncing dependencies...
uv sync --index-strategy unsafe-best-match
if !errorlevel! neq 0 (
    echo     [WARN] Sync failed, retrying with clean lock...
    del uv.lock 2>nul
    uv sync --index-strategy unsafe-best-match
    if !errorlevel! neq 0 (
        echo [ERROR] uv sync failed. See install.log
        pause
        exit /b 1
    )
)

rem Ensure CPU paddle is installed (not GPU version)
uv run python -c "import paddle; assert not paddle.device.is_compiled_with_cuda()" 2>nul
if !errorlevel! equ 0 (
    echo     CPU paddle confirmed
) else (
    echo     Installing CPU paddlepaddle...
    uv pip install paddlepaddle --reinstall
)
call :log "uv sync done"

rem -- [5/6] Verify PaddleOCR -------------------------------------------
call :step "5/6" "Verify PaddleOCR"
uv run python -c "from paddleocr import PaddleOCR; print('    PaddleOCR OK')" 2>&1
if !errorlevel! neq 0 (
    echo     [WARN] PaddleOCR import failed - use API engines
    call :log "PaddleOCR FAILED"
) else (
    call :log "PaddleOCR OK"
)

rem -- [6/6] Done -------------------------------------------------------
:done
echo.
echo =========================================
echo   Setup complete! (CPU Mode)
echo.
echo   Launch:   orcp_gui.bat
echo   Reinstall: del .venv ^&^& setup_cpu.bat
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
