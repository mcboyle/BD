@echo off
setlocal EnableDelayedExpansion
title BulkDownloader - local AI (Ollama) install [Windows]
REM ============================================================================
REM  install_ai_ollama.bat - set up local AI (Ollama) for BulkDownloader on
REM  Windows. Mirrors install_ai_ollama.sh; same models, same endpoint, same
REM  contract. Separate from install_windows.bat on purpose: the core app
REM  runs fine without AI, this is opt-in.
REM
REM  Installs the Ollama Windows runtime (runs as a Windows service called
REM  "Ollama" auto-started by the installer), waits for the API at
REM  http://localhost:11434, then pulls the two models BulkDownloader's
REM  Ollama provider expects:
REM
REM      vision : qwen2.5vl:7b     (image-based template extraction)
REM      text   : qwen2.5:7b       (text reasoning / login assist)
REM
REM  These match ai_provider.py::OllamaProvider.default_models. If you change
REM  the models in the app's AI settings, pull those instead.
REM
REM  Assumes NVIDIA drivers are already installed (this script does NOT
REM  install them). Ollama auto-detects the GPU via its bundled CUDA libs;
REM  if no GPU is found it falls back to CPU (much slower).
REM
REM  Pure 7-bit ASCII / CRLF / no unquoted redirection inside for-loops, per
REM  the .bat lint contract.
REM ============================================================================

set "VISION_MODEL=qwen2.5vl:7b"
set "TEXT_MODEL=qwen2.5:7b"
set "OLLAMA_HOST=http://localhost:11434"
set "INSTALLER_URL=https://ollama.com/download/OllamaSetup.exe"
set "INSTALLER_LOCAL=%TEMP%\OllamaSetup.exe"

echo.
echo  ================================================================
echo   BulkDownloader - local AI (Ollama) install  [Windows]
echo  ================================================================
echo.

REM -- [1/5] GPU check (informational, does NOT install drivers) -----------
echo [1/5] GPU check
where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo       WARNING: nvidia-smi not found. Either the NVIDIA drivers
    echo       are not installed, or this box has no NVIDIA GPU. Ollama
    echo       will still install but run on CPU ^(slow^).
    set "CONTINUE_CPU=N"
    set /p "CONTINUE_CPU=       Continue anyway? [Y/N, default N]: "
    if /i not "!CONTINUE_CPU!"=="Y" (
        echo       Aborted.
        goto :end_fail
    )
) else (
    call :detect_gpu
    echo       GPU detected : !GPU_NAME!
    echo       VRAM         : !GPU_MEM!
    echo       Ollama will use the GPU automatically.
    echo       Note: each 7B q4 model needs roughly 5-6 GB VRAM. If your
    echo       card has less, Ollama offloads layers to CPU.
)
echo.

REM -- [2/5] Ollama runtime ------------------------------------------------
echo [2/5] Ollama runtime
where ollama >nul 2>nul
if not errorlevel 1 (
    for /f "tokens=*" %%V in ('ollama --version 2^>nul') do echo       Already installed: %%V
    goto :ollama_ready
)

echo       Ollama not on PATH. About to download and run the official
echo       Windows installer: %INSTALLER_URL%
set "DO_INSTALL=N"
set /p "DO_INSTALL=       Download and install Ollama now? [Y/N, default N]: "
if /i not "!DO_INSTALL!"=="Y" (
    echo       Aborted. To install manually, download from
    echo       https://ollama.com/download and re-run this script.
    goto :end_fail
)

echo       Downloading installer to %INSTALLER_LOCAL% ...
REM curl ships with Windows 10 1803+ / Server 2019+, fall back to PowerShell.
where curl >nul 2>nul
if not errorlevel 1 (
    curl -fL --retry 3 -o "%INSTALLER_LOCAL%" "%INSTALLER_URL%"
    set "DL_RC=!ERRORLEVEL!"
) else (
    call :ps_download
    set "DL_RC=!ERRORLEVEL!"
)
if not "!DL_RC!"=="0" (
    echo       ERROR: download failed ^(exit !DL_RC!^). Check network and
    echo       https://ollama.com/download.
    goto :end_fail
)
if not exist "%INSTALLER_LOCAL%" (
    echo       ERROR: installer file missing after download.
    goto :end_fail
)

echo       Running installer silently. A UAC prompt may appear.
REM /SILENT shows progress but no questions; /VERYSILENT hides progress
REM too. The Ollama installer is built with Inno Setup, which honours
REM both. Use /SILENT so the operator sees what is happening.
"%INSTALLER_LOCAL%" /SILENT /NORESTART
set "INST_RC=!ERRORLEVEL!"
if not "!INST_RC!"=="0" (
    echo       ERROR: installer exited with code !INST_RC!. Try running
    echo       %INSTALLER_LOCAL% by hand and re-run this script.
    goto :end_fail
)

REM Refresh PATH-derived lookups in this session. The installer drops the
REM ollama binary at %LOCALAPPDATA%\Programs\Ollama\ollama.exe; we can
REM call it directly even if PATH has not propagated to this shell yet.
set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not exist "%OLLAMA_EXE%" (
    echo       WARNING: expected binary at %OLLAMA_EXE% not found.
    echo       The installer may have used a different path. Check and
    echo       re-run, or open a new cmd window so PATH refreshes.
    goto :end_fail
)
echo       Installed: %OLLAMA_EXE%

:ollama_ready
REM Resolve the ollama binary either way (was on PATH already, or just
REM installed and we know the path). Prefer PATH if available.
set "OLLAMA_BIN=ollama"
where ollama >nul 2>nul || set "OLLAMA_BIN=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
echo.

REM -- [3/5] Wait for Ollama API ------------------------------------------
echo [3/5] Ollama API
echo       The Windows installer launches Ollama as a background service
echo       automatically. Waiting for %OLLAMA_HOST% to respond (30s max)...
set "UP="
for /l %%I in (1,1,30) do (
    if not defined UP (
        call :probe_api
        if not errorlevel 1 set "UP=yes"
        if not defined UP call :sleep_1s
    )
)
if not defined UP (
    echo       ERROR: Ollama API did not come up within 30s. Open the
    echo       Ollama tray app, or run "%OLLAMA_BIN%" serve manually,
    echo       then re-run this script to pull models.
    goto :end_fail
)
echo       Ollama API is responding.
echo.

REM -- [4/5] Pull models --------------------------------------------------
echo [4/5] Pulling models (this downloads several GB - be patient)
for %%M in ("%VISION_MODEL%" "%TEXT_MODEL%") do (
    echo       pulling %%~M ...
    "%OLLAMA_BIN%" pull %%~M
    if errorlevel 1 (
        echo       WARNING: 'ollama pull %%~M' failed. The model name may
        echo       have changed in the Ollama library. Check
        echo       https://ollama.com/library and pull a current
        echo       equivalent, then set it in the app's AI settings.
    ) else (
        echo       ok: %%~M
    )
)
echo.

REM -- [5/5] Verify -------------------------------------------------------
echo [5/5] Installed models
"%OLLAMA_BIN%" list 2>nul || echo       (could not list models)
echo.

echo  ================================================================
echo   Local AI install complete.
echo.
echo   In BulkDownloader: Settings -^> AI -^> provider 'Ollama',
echo   endpoint %OLLAMA_HOST% (the default - no key needed).
echo   Vision model : %VISION_MODEL%
echo   Text model   : %TEXT_MODEL%
echo.
echo   Ollama is LAN-only by design in BulkDownloader; the privacy
echo   guard requires a loopback / RFC1918 endpoint. That is the
echo   default here, so nothing to change.
echo  ================================================================
echo.
pause
exit /b 0

REM ----- subroutines: callouts from inside (...) blocks -----
REM The PowerShell-bearing call sites used to be inline inside
REM parenthesized blocks. They are now subroutines so the embedded
REM `()` and `.` in PowerShell arguments can't confuse cmd's paren
REM tracker. (The actual `. was unexpected at this time` failure
REM that surfaced this whole investigation turned out to be a
REM literal `(slow)` inside an echo on what is now line 47 - cmd
REM counts unescaped parens in echo arguments inside (...) blocks.
REM The fix for that is `^(slow^)`. The earlier label-inside-block
REM diagnosis was wrong; subroutining was the right structural move
REM for the PowerShell calls regardless.)

:detect_gpu
set "GPU_NAME="
set "GPU_MEM="
for /f "tokens=*" %%G in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul') do (
    if not defined GPU_NAME set "GPU_NAME=%%G"
)
for /f "tokens=*" %%G in ('nvidia-smi --query-gpu^=memory.total --format^=csv^,noheader 2^>nul') do (
    if not defined GPU_MEM set "GPU_MEM=%%G"
)
if not defined GPU_NAME set "GPU_NAME=unknown"
if not defined GPU_MEM set "GPU_MEM=unknown"
exit /b 0

:probe_api
REM Returns errorlevel 0 if %OLLAMA_HOST%/api/tags responds, else 1.
REM Tries curl first (always present on Windows 10 1803+ / Server 2019+),
REM falls back to PowerShell. Runs outside any caller paren context so
REM the PowerShell command's literal `()` and `.` don't confuse cmd's
REM paren tracker.
curl -fsS "%OLLAMA_HOST%/api/tags" >nul 2>nul
if not errorlevel 1 exit /b 0
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri '%OLLAMA_HOST%/api/tags' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>nul
exit /b %errorlevel%

:sleep_1s
powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>nul
exit /b 0

:ps_download
REM PowerShell installer download - fallback when curl is missing.
REM Runs outside any caller paren context so the line-continued
REM PowerShell command can't confuse cmd's paren tracker.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%INSTALLER_URL%' -OutFile '%INSTALLER_LOCAL%' -UseBasicParsing"
exit /b %errorlevel%

:end_fail
echo.
echo  ================================================================
echo   Local AI install did NOT complete.
echo  ================================================================
echo.
pause
exit /b 1
