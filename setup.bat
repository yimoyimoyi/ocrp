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
if defined FORCE_GPU goto :gpu_done
if defined FORCE_CPU goto :gpu_done

echo [1/8] Detect GPU...
call :log "Step 1 GPU detect"

nvidia-smi >nul 2>&1
if !errorlevel! neq 0 goto :gpu_cpu
set "USE_GPU=1"
echo        GPU detected:
for /f "tokens=*" %%i in ('nvidia-smi --query-gpu^=name --format^=csv,noheader 2^>nul') do echo          %%i
goto :gpu_done

:gpu_cpu
echo        No GPU found. CPU mode.
:gpu_done
call :log "GPU=!USE_GPU!"

rem -- [2] uv (installer) ------------------------------------------------
echo.
echo [2/8] Check uv...

set "UV_EXE="
where uv >nul 2>&1 && set "UV_EXE=1"

if not defined UV_EXE goto :install_uv
rem uv 已安装 —— 用 for 捕获版本（输出含括号，存变量后 echo）
for /f "delims=" %%v in ('uv --version 2^>^&1') do set "UV_VER=%%v"
echo        !UV_VER!
goto :uv_ok

:install_uv
echo        Installing uv (standalone)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>>"%LOG%"
set "PATH=!USERPROFILE!\.local\bin;!PATH!"
where uv >nul 2>&1
if !errorlevel! neq 0 (
    echo [ERROR] uv install failed. Install manually: https://docs.astral.sh/uv/
    call :log "uv install failed"
    pause
    exit /b 1
)
for /f "delims=" %%v in ('uv --version 2^>^&1') do echo        %%v

:uv_ok
call :log "uv: installed"

rem -- [3] Python (uv-managed) -----------------------------------------
echo.
echo [3/8] Install Python 3.12 (uv-managed)...

rem 先查 uv 是否已有托管 Python
set "UV_PY_OK=0"
uv python find 2>nul | findstr /R "3\.1[2-3]" >nul && set "UV_PY_OK=1"
if !UV_PY_OK! equ 1 (
    for /f "delims=" %%v in ('uv python find 2^>^&1') do echo        Found: %%v
    goto :pyok
)

rem 尝试下载（3.12 优先，3.13 回退）
echo 3.12 > .python-version
echo        Downloading Python 3.12...
uv python install 3.12 2>>"%LOG%"
if !errorlevel! equ 0 goto :pyok

echo        Python 3.12 failed, trying 3.13...
echo 3.13 > .python-version
uv python install 3.13 2>>"%LOG%"
if !errorlevel! equ 0 goto :pyok

rem 兜底 —— 系统 Python 3.11-3.13
echo        Download failed. Searching system Python...
for %%c in (python3.13 python3.12 python3.11 python3 python) do (
    where %%c >nul 2>&1
    if not !errorlevel! equ 0 (
        rem not found, continue
    ) else (
        for /f "delims=" %%v in ('%%c --version 2^>^&1') do (
            echo %%v | findstr /R "3\.1[1-3]" >nul
            if not !errorlevel! equ 0 (
                rem wrong version
            ) else (
                echo        Using system: %%v
                for /f "tokens=2" %%w in ("%%v") do (
                    for /f "tokens=1-3 delims=." %%a in ("%%w") do echo %%a.%%b.%%c > .python-version
                )
                goto :pyok
            )
        )
    )
)

echo [ERROR] Cannot get Python 3.12/3.13.
echo         uv download may be blocked by firewall.
echo         Try manual:  uv python install 3.13
echo         Or install Python 3.12 from https://www.python.org/downloads/
call :log "Python install failed"
pause
exit /b 1

:pyok
for /f "delims=" %%v in ('uv python find 2^>^&1') do echo        %%v
call :log "Python ready"

rem -- [4] FFmpeg -------------------------------------------------------
echo.
echo [4/8] Check FFmpeg...

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
    echo [5/8] Clean old venv...
    if exist ".venv" rmdir /s /q ".venv" >nul 2>&1
    del uv.lock 2>nul
    del .python-version 2>nul
    call :log "Cleaned old venv"
)

rem -- [6] uv sync ------------------------------------------------------
echo.
echo [6/8] Sync dependencies (uv sync)...
call :log "uv sync starting [GPU=!USE_GPU!]"

rem 清除旧锁文件，确保干净解析
del uv.lock 2>nul

rem 选择 PaddlePaddle 索引
set "UV_SYNC_OPTS=--index-strategy unsafe-best-match"
if !USE_GPU! equ 1 goto :sync_gpu
set "UV_EXTRA_INDEX_URL=https://www.paddlepaddle.org.cn/packages/stable/cpu/"
echo        CPU mode - PaddlePaddle CPU index
goto :sync_run

:sync_gpu
set "UV_EXTRA_INDEX_URL=https://www.paddlepaddle.org.cn/packages/stable/cu126/"
set "UV_SYNC_OPTS=--index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cu124"
echo        GPU mode - PaddlePaddle CUDA 12.6 + PyTorch CUDA 12.4 index
echo        NOTE: torch (cu124)自带全套 CUDA DLL，nvidia pip 包已移除

:sync_run
uv sync %UV_SYNC_OPTS% 2>>"%LOG%"
if !errorlevel! equ 0 goto :sync_ok

rem GPU 失败 → 回退 CPU
if !USE_GPU! neq 1 goto :sync_fail
echo        GPU version failed, retry CPU...
call :log "GPU failed, retry CPU"
set "USE_GPU=0"
del uv.lock 2>nul
set "UV_EXTRA_INDEX_URL=https://www.paddlepaddle.org.cn/packages/stable/cpu/"
uv sync %UV_SYNC_OPTS% 2>>"%LOG%"
if !errorlevel! equ 0 goto :sync_ok

:sync_fail
echo [ERROR] uv sync failed. See install.log
call :log "uv sync failed"
pause
exit /b 1

:sync_ok
call :log "uv sync done"

rem -- [7] cuDNN 8 check ------------------------------------------------
echo.
echo [7/8] Check cuDNN 8 (GPU ASR)...
call :log "cuDNN check [GPU=!USE_GPU!]"

if !USE_GPU! neq 1 goto :done
mkdir models\asr\lib 2>nul
uv run python -c "import ctypes; ctypes.CDLL('cudnn_cnn_infer64_8.dll')" 2>nul
if !errorlevel! equ 0 (
    echo        cuDNN 8 ready - GPU ASR enabled
    call :log "cuDNN 8 OK"
    goto :done
)
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
