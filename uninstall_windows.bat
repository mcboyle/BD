@echo off
setlocal EnableDelayedExpansion
title Bulk Downloader - Uninstaller

REM ============================================================================
REM  uninstall_windows.bat - Bulk Downloader Uninstaller
REM
REM  Pure 7-bit ASCII / CRLF / no unquoted redirection inside for-loops, per
REM  the .bat lint contract.
REM
REM  Covers the full surface of all three install_*.bat installers PLUS
REM  the runtime AppData dirs the app itself creates on first use:
REM    install_windows.bat   -> %USERPROFILE%\BulkDownloader
REM    install_dev.bat       -> %USERPROFILE%\BulkDownloader-dev
REM    install_ai_ollama.bat -> Ollama Windows service + binary + models
REM    app at runtime        -> %APPDATA%\BulkDownloader (vpn/, widgets.json)
REM                          -> %APPDATA%\bdctl (CLI config)
REM
REM  Always removes (after confirmation):
REM    1. %USERPROFILE%\BulkDownloader            (if present)
REM    2. %USERPROFILE%\BulkDownloader-dev        (if present)
REM    3. Desktop shortcut "Bulk Downloader.lnk"  (if present)
REM    4. Start Menu folder "BulkDownloader"      (if present)
REM    5. App-data dirs:                          (if present)
REM         %APPDATA%\BulkDownloader (vpn config + widget layouts)
REM         %APPDATA%\bdctl          (bdctl CLI config)
REM
REM  Prompted, separately:
REM    6. Playwright Chromium cache    %LOCALAPPDATA%\ms-playwright (~300 MB)
REM    7. pip download cache           %LOCALAPPDATA%\pip\Cache
REM    8. Ollama Windows runtime       service + %LOCALAPPDATA%\Programs\Ollama
REM    9. Ollama model cache           %USERPROFILE%\.ollama (large, GBs)
REM
REM  Does NOT remove:
REM    - Python itself (Python 3.12 / 3.14 installs stay)
REM    - NVIDIA drivers
REM    - Files in your configured download_dir (e.g. E:\videos)
REM
REM  Flags:
REM    --yes          accept every prompt that has a sensible default,
REM                   EXCEPT Playwright cache, pip cache, and Ollama
REM                   (those still ask, since they may be shared with
REM                   other tools on the box).
REM    --all          like --yes but also opts in to Playwright cache, pip
REM                   cache, and Ollama (binary + service). Does NOT
REM                   delete the Ollama model cache without a second pass.
REM    --purge        with --yes or --all, also delete the Ollama model
REM                   cache at %USERPROFILE%\.ollama. Big and irreversible.
REM    --deep-clean   legacy alias for "also remove Playwright cache".
REM                   Kept for back-compat with the previous script.
REM ============================================================================

set "INSTALL_DIR=%USERPROFILE%\BulkDownloader"
set "DEV_INSTALL_DIR=%USERPROFILE%\BulkDownloader-dev"
set "DESKTOP=%USERPROFILE%\Desktop"
set "STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\BulkDownloader"
set "APPDATA_BD=%APPDATA%\BulkDownloader"
set "APPDATA_BDCTL=%APPDATA%\bdctl"
set "PW_CACHE=%LOCALAPPDATA%\ms-playwright"
set "PIP_CACHE=%LOCALAPPDATA%\pip\Cache"
set "OLLAMA_EXE_DIR=%LOCALAPPDATA%\Programs\Ollama"
set "OLLAMA_DATA=%USERPROFILE%\.ollama"

set "ASSUME_YES=0"
set "ALL_FLAG=0"
set "PURGE_OLLAMA=0"
set "DEEP_CLEAN=0"

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--yes"        ( set "ASSUME_YES=1" & shift & goto parse_args )
if /i "%~1"=="/yes"         ( set "ASSUME_YES=1" & shift & goto parse_args )
if /i "%~1"=="-y"           ( set "ASSUME_YES=1" & shift & goto parse_args )
if /i "%~1"=="--all"        ( set "ASSUME_YES=1" & set "ALL_FLAG=1" & shift & goto parse_args )
if /i "%~1"=="/all"         ( set "ASSUME_YES=1" & set "ALL_FLAG=1" & shift & goto parse_args )
if /i "%~1"=="--purge"      ( set "PURGE_OLLAMA=1" & shift & goto parse_args )
if /i "%~1"=="/purge"       ( set "PURGE_OLLAMA=1" & shift & goto parse_args )
if /i "%~1"=="--deep-clean" ( set "DEEP_CLEAN=1" & shift & goto parse_args )
if /i "%~1"=="/deep-clean"  ( set "DEEP_CLEAN=1" & shift & goto parse_args )
if /i "%~1"=="--help"       ( goto show_help )
if /i "%~1"=="/help"        ( goto show_help )
if /i "%~1"=="-h"           ( goto show_help )
if /i "%~1"=="/?"           ( goto show_help )
echo  ERROR: unknown argument: %~1
echo  Run uninstall_windows.bat --help for the full list.
exit /b 1
:args_done

echo.
echo  ================================================================
echo   Bulk Downloader  -  Uninstaller
echo  ================================================================
echo.
echo  Plan:
echo.
echo    [1] Regular install dir : %INSTALL_DIR%
if exist "%INSTALL_DIR%" (
    echo                            ^(exists, will be removed^)
) else (
    echo                            ^(not found, skipping^)
)
echo    [2] Dev install dir     : %DEV_INSTALL_DIR%
if exist "%DEV_INSTALL_DIR%" (
    echo                            ^(exists, will be removed^)
) else (
    echo                            ^(not found, skipping^)
)
echo    [3] Desktop shortcut    : %DESKTOP%\Bulk Downloader.lnk
if exist "%DESKTOP%\Bulk Downloader.lnk" (
    echo                            ^(exists, will be removed^)
) else (
    echo                            ^(not found, skipping^)
)
echo    [4] Start Menu folder   : %STARTMENU%
if exist "%STARTMENU%" (
    echo                            ^(exists, will be removed^)
) else (
    echo                            ^(not found, skipping^)
)
echo    [5] App-data dirs       : %APPDATA_BD%
echo                              %APPDATA_BDCTL%
if exist "%APPDATA_BD%" (
    echo                            ^(BulkDownloader exists, will be removed^)
) else (
    echo                            ^(BulkDownloader not found, skipping^)
)
if exist "%APPDATA_BDCTL%" (
    echo                            ^(bdctl exists, will be removed^)
) else (
    echo                            ^(bdctl not found, skipping^)
)
echo.
echo  Optional, prompted separately:
echo    [6] Playwright cache    : %PW_CACHE%
echo    [7] pip download cache  : %PIP_CACHE%
echo    [8] Ollama runtime      : %OLLAMA_EXE_DIR%  + Windows service
echo    [9] Ollama models       : %OLLAMA_DATA%  ^(typically several GB^)
echo.
echo  Heads up: removing [1] / [2] deletes queue.db ^(URL queue, history,
echo  stats^) and sites_config.json ^(site definitions, login cookies^).
echo  Removing [5] deletes your VPN tunnel definitions and widget layouts.
echo  Back those up FIRST if you want to keep them.
echo.

if "%ASSUME_YES%"=="1" goto skip_top_confirm

set "CONFIRM1=N"
set /p "CONFIRM1=  Proceed with uninstall? [Y/N, default N]: "
if /i not "!CONFIRM1!"=="Y" (
    echo  Uninstall cancelled, nothing was deleted.
    echo.
    pause
    exit /b 0
)

echo.
echo  Last chance. Type DELETE in caps to confirm, anything else cancels.
set "CONFIRM2="
set /p "CONFIRM2=  Type DELETE: "
if not "!CONFIRM2!"=="DELETE" (
    echo  Uninstall cancelled, nothing was deleted.
    echo.
    pause
    exit /b 0
)
:skip_top_confirm

echo.
echo  Stopping any running BulkDownloader server on port 5555...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr :5555 ^| findstr LISTENING 2^>nul') do (
    echo    Killing PID %%P
    taskkill /F /PID %%P >nul 2>&1
)

REM -- [1] Regular install dir ----------------------------------------------
echo.
echo  [1] Regular install dir
if exist "%INSTALL_DIR%" (
    echo      Removing %INSTALL_DIR% ...
    rmdir /S /Q "%INSTALL_DIR%"
    if exist "%INSTALL_DIR%" (
        echo      WARNING: some files could not be deleted. Close File
        echo      Explorer windows pointing into the dir, kill any
        echo      python.exe / BulkDownloader.exe in Task Manager,
        echo      then re-run.
    ) else (
        echo      OK
    )
) else (
    echo      Not found, skipping.
)

REM -- [2] Dev install dir --------------------------------------------------
echo.
echo  [2] Dev install dir
if exist "%DEV_INSTALL_DIR%" (
    echo      Removing %DEV_INSTALL_DIR% ...
    rmdir /S /Q "%DEV_INSTALL_DIR%"
    if exist "%DEV_INSTALL_DIR%" (
        echo      WARNING: some files could not be deleted. Close File
        echo      Explorer windows / kill stray python.exe and retry.
    ) else (
        echo      OK
    )
) else (
    echo      Not found, skipping.
)

REM -- [3] Desktop shortcut -------------------------------------------------
echo.
echo  [3] Desktop shortcut
if exist "%DESKTOP%\Bulk Downloader.lnk" (
    del /Q "%DESKTOP%\Bulk Downloader.lnk" >nul 2>&1
    echo      Removed.
) else (
    echo      Not found, skipping.
)

REM -- [4] Start Menu folder ------------------------------------------------
echo.
echo  [4] Start Menu folder
if exist "%STARTMENU%" (
    rmdir /S /Q "%STARTMENU%" >nul 2>&1
    echo      Removed.
) else (
    echo      Not found, skipping.
)

REM -- [5] App-data dirs (Roaming) ------------------------------------------
REM These are created by the app itself at runtime, not by the installer:
REM   %APPDATA%\BulkDownloader\  -> widgets.json (widget_config.py),
REM                                 vpn\tunnels.json (vpn_config.py)
REM   %APPDATA%\bdctl\           -> CLI config (bdctl.py)
echo.
echo  [5] App-data dirs in %%APPDATA%%
if exist "%APPDATA_BD%" (
    echo      Removing %APPDATA_BD% ...
    rmdir /S /Q "%APPDATA_BD%"
    if exist "%APPDATA_BD%" (
        echo      WARNING: could not fully remove %APPDATA_BD%.
        echo      A file may be locked by a still-running BulkDownloader.
    ) else (
        echo      OK
    )
) else (
    echo      %APPDATA_BD% not found, skipping.
)
if exist "%APPDATA_BDCTL%" (
    echo      Removing %APPDATA_BDCTL% ...
    rmdir /S /Q "%APPDATA_BDCTL%"
    if exist "%APPDATA_BDCTL%" (
        echo      WARNING: could not fully remove %APPDATA_BDCTL%.
    ) else (
        echo      OK
    )
) else (
    echo      %APPDATA_BDCTL% not found, skipping.
)

REM -- [6] Playwright cache -------------------------------------------------
echo.
echo  [6] Playwright browser cache
if not exist "%PW_CACHE%" (
    echo      Not found at %PW_CACHE%, skipping.
    goto step_pip
)
set "REMOVE_PW=N"
if "%ALL_FLAG%"=="1"   set "REMOVE_PW=Y"
if "%DEEP_CLEAN%"=="1" set "REMOVE_PW=Y"
if "%ASSUME_YES%"=="0" (
    set /p "REMOVE_PW=     Remove %PW_CACHE% ^(~300 MB^)? [y/N]: "
)
if /i "!REMOVE_PW!"=="Y" (
    rmdir /S /Q "%PW_CACHE%" >nul 2>&1
    if exist "%PW_CACHE%" (
        echo      WARNING: could not fully remove %PW_CACHE%.
        echo      Close any open Chromium / Edge / Playwright tools and retry.
    ) else (
        echo      Removed.
    )
) else (
    echo      Preserved.
)

:step_pip
REM -- [7] pip download cache -----------------------------------------------
echo.
echo  [7] pip download cache
if not exist "%PIP_CACHE%" (
    echo      Not found at %PIP_CACHE%, skipping.
    goto step_ollama
)
set "REMOVE_PIP=N"
if "%ALL_FLAG%"=="1" set "REMOVE_PIP=Y"
if "%ASSUME_YES%"=="0" (
    set /p "REMOVE_PIP=     Remove %PIP_CACHE%? [y/N]: "
)
if /i "!REMOVE_PIP!"=="Y" (
    rmdir /S /Q "%PIP_CACHE%" >nul 2>&1
    if exist "%PIP_CACHE%" (
        echo      WARNING: could not fully remove %PIP_CACHE%.
    ) else (
        echo      Removed.
    )
) else (
    echo      Preserved.
)

:step_ollama
REM -- [8] Ollama runtime ---------------------------------------------------
echo.
echo  [8] Ollama runtime
set "OLLAMA_PRESENT=0"
if exist "%OLLAMA_EXE_DIR%\ollama.exe" set "OLLAMA_PRESENT=1"
sc query Ollama >nul 2>&1
if not errorlevel 1 set "OLLAMA_PRESENT=1"
where ollama >nul 2>&1
if not errorlevel 1 set "OLLAMA_PRESENT=1"

if "%OLLAMA_PRESENT%"=="0" (
    echo      Ollama is not installed on this box, skipping.
    goto step_models
)

set "REMOVE_OLLAMA=N"
if "%ALL_FLAG%"=="1" set "REMOVE_OLLAMA=Y"
if "%ASSUME_YES%"=="0" (
    echo      Ollama may be used by other tools on this box.
    set /p "REMOVE_OLLAMA=     Stop the Windows service and uninstall the Ollama runtime? [y/N]: "
)
if /i not "!REMOVE_OLLAMA!"=="Y" (
    echo      Preserved.
    goto step_models
)

REM Stop the service first so the .exe is unlocked.
sc query Ollama >nul 2>&1
if not errorlevel 1 (
    echo      Stopping Ollama service ...
    sc stop Ollama >nul 2>&1
    REM Give it a beat to release file locks.
    >nul 2>&1 ping 127.0.0.1 -n 3
)

REM Run the official uninstaller if it's there - that's the cleanest path
REM since the installer wrote registry keys + a Programs and Features entry
REM that a manual rmdir won't clean up.
set "OLLAMA_UNINSTALLER="
if exist "%OLLAMA_EXE_DIR%\unins000.exe" set "OLLAMA_UNINSTALLER=%OLLAMA_EXE_DIR%\unins000.exe"
if exist "%OLLAMA_EXE_DIR%\Uninstall.exe" set "OLLAMA_UNINSTALLER=%OLLAMA_EXE_DIR%\Uninstall.exe"

if defined OLLAMA_UNINSTALLER (
    echo      Running Ollama's own uninstaller: !OLLAMA_UNINSTALLER!
    echo      ^(silent flag; a GUI may briefly appear^)
    "!OLLAMA_UNINSTALLER!" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
    if errorlevel 1 (
        echo      WARNING: the Ollama uninstaller exited non-zero. Falling
        echo      back to manual removal of %OLLAMA_EXE_DIR%.
    )
)

REM Manual cleanup in case the uninstaller wasn't there or didn't finish.
if exist "%OLLAMA_EXE_DIR%" (
    rmdir /S /Q "%OLLAMA_EXE_DIR%" >nul 2>&1
    if exist "%OLLAMA_EXE_DIR%" (
        echo      WARNING: could not remove %OLLAMA_EXE_DIR%. A locked
        echo      ollama.exe is the usual cause; reboot and re-run.
    ) else (
        echo      Removed %OLLAMA_EXE_DIR%.
    )
)

REM Service may linger if the uninstaller wasn't used.
sc query Ollama >nul 2>&1
if not errorlevel 1 (
    echo      Deleting leftover Ollama service entry ...
    sc delete Ollama >nul 2>&1
)
echo      Ollama runtime removed.

:step_models
REM -- [9] Ollama model cache -----------------------------------------------
echo.
echo  [9] Ollama model cache
if not exist "%OLLAMA_DATA%" (
    echo      Not found at %OLLAMA_DATA%, skipping.
    goto done
)
set "REMOVE_MODELS=N"
if "%PURGE_OLLAMA%"=="1" set "REMOVE_MODELS=Y"
if "%ASSUME_YES%"=="0" (
    echo      Model cache may be several GB. Keep it to skip a re-pull next install.
    set /p "REMOVE_MODELS=     Delete %OLLAMA_DATA%? [y/N]: "
)
if /i "!REMOVE_MODELS!"=="Y" (
    rmdir /S /Q "%OLLAMA_DATA%" >nul 2>&1
    if exist "%OLLAMA_DATA%" (
        echo      WARNING: could not fully remove %OLLAMA_DATA%.
    ) else (
        echo      Removed.
    )
) else (
    echo      Preserved.
)

:done
echo.
echo  ================================================================
echo   Uninstall complete.
echo  ================================================================
echo.
echo  To reinstall, re-run install_windows.bat ^(or install_dev.bat^)
echo  from the unzipped release directory.
echo.
if "%ASSUME_YES%"=="0" pause
exit /b 0

:show_help
echo.
echo  uninstall_windows.bat - removes Bulk Downloader and optionally
echo                          its Playwright/pip caches and the Ollama runtime.
echo.
echo  Usage:
echo    uninstall_windows.bat                interactive ^(asks before each step^)
echo    uninstall_windows.bat --yes          accept every prompt except Ollama
echo                                         and the caches ^(those still ask^)
echo    uninstall_windows.bat --all          --yes plus also remove the
echo                                         Playwright cache, pip cache,
echo                                         and the Ollama runtime ^(service
echo                                         + binary^). Does NOT delete the
echo                                         Ollama model cache.
echo    uninstall_windows.bat --all --purge  --all plus delete the Ollama
echo                                         model cache at %%USERPROFILE%%\.ollama.
echo    uninstall_windows.bat --deep-clean   legacy: same as --all minus
echo                                         the Ollama parts. Kept for
echo                                         back-compat with the prior script.
echo.
echo  Always removed ^(after one confirm + typing DELETE^):
echo    %%USERPROFILE%%\BulkDownloader            the regular install
echo    %%USERPROFILE%%\BulkDownloader-dev        the dev install
echo    %%APPDATA%%\BulkDownloader                vpn config, widget layouts
echo    %%APPDATA%%\bdctl                         bdctl CLI config
echo    Desktop shortcut and Start Menu folder
echo.
echo  Never removed:
echo    Python itself, NVIDIA drivers, your download_dir.
echo.
exit /b 0
