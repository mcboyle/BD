@echo off
REM ====================================================================
REM  install_dev.bat  -  Bulk Downloader DEV / FULL install
REM ====================================================================
REM  DEV-ONLY installer. Installs EVERY dependency across
REM  requirements.txt, requirements-optional.txt, and
REM  requirements-dev.txt. Also installs Playwright Chromium and
REM  builds the standalone .exe.
REM
REM  NOT FOR PUBLIC RELEASE. This script intentionally:
REM    - installs every library-extractor (some flag adult-content
REM      site APIs)
REM    - installs PyInstaller + pytest (dev tooling)
REM    - does NOT prompt for skip-paths the way install_windows.bat does
REM    - assumes you know what you're doing
REM
REM  Use install_windows.bat for the user-facing path. Keep this file
REM  out of any public-facing distribution.
REM
REM  Usage:
REM    1. Drop the repo in a folder
REM    2. Double-click this file (or run from cmd)
REM    3. Wait. End-to-end takes ~5-10 minutes on a fresh machine.
REM ====================================================================

setlocal EnableDelayedExpansion
REM v3.51.1: derive version from the package so the banner can never
REM go stale. Falls back to "unknown" only if the file is missing.
set "BD_VER=unknown"
if exist "%~dp0bulk_downloader\__init__.py" (
  for /f "tokens=2 delims== " %%V in ('findstr /b /c:"__version__ " "%~dp0bulk_downloader\__init__.py"') do set "BD_VER=%%~V"
)
title Bulk Downloader v!BD_VER! DEV Installer

cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
set "INSTALL_DIR=%USERPROFILE%\BulkDownloader-dev"
set "VENV_DIR=%INSTALL_DIR%\venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
REM v3.63.5: removed `set "PIP=...\pip.exe"`. All pip calls now go
REM through `"%PYTHON_EXE%" -m pip` per the project NEVER rule (and
REM matching the Linux installer + install_dev.bat's own v3.47.6
REM comment that already explained why for pip-upgrade).

echo.
echo  ================================================================
echo   Bulk Downloader v!BD_VER! DEV / FULL Installer
echo  ================================================================
echo   This installs EVERY dependency (base + optional + dev).
echo   Install dir: %INSTALL_DIR%
echo  ================================================================
echo.

REM -- [1/8] Source-file sanity -----------------------------------------
echo [1/8] Checking source files...
set "MISSING="
for %%F in (downloader_ui.py requirements.txt requirements-optional.txt requirements-dev.txt downloader.spec) do (
    if not exist "%SCRIPT_DIR%%%F" (
        echo       MISSING: %SCRIPT_DIR%%%F
        set "MISSING=1"
    ) else (
        echo       Found: %%F
    )
)
if defined MISSING (
    echo.
    echo  ERROR: required files not found alongside install_dev.bat.
    echo  Drop this script in the repo root and try again.
    pause
    exit /b 1
)
echo.

REM -- [2/8] Python detection -------------------------------------------
echo [2/8] Checking for Python 3.9+...
where python >nul 2>nul
if errorlevel 1 (
    echo       ERROR: 'python' not on PATH.
    echo       Install Python 3.9+ from https://www.python.org/downloads/
    echo       or via 'winget install Python.Python.3.12' and retry.
    pause
    exit /b 1
)
for /f "tokens=*" %%V in ('python --version') do echo       Found: %%V
echo.

REM -- [3/8] Install dir + venv -----------------------------------------
echo [3/8] Setting up install directory and virtual environment...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

REM v3.47.6: %SCRIPT_DIR% from %~dp0 always ends with \, which breaks
REM robocopy on some paths (the trailing \" gets interpreted as an
REM escaped quote, leading to "invalid path" with exit code 16).
REM Strip the trailing backslash before passing.
set "SRC=%SCRIPT_DIR%"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
set "DST=%INSTALL_DIR%"
if "%DST:~-1%"=="\" set "DST=%DST:~0,-1%"

REM Copy the entire repo (excluding common junk) into INSTALL_DIR.
REM Using robocopy because xcopy can't exclude folders cleanly.
REM Robocopy exit codes: 0-7 are SUCCESS (different levels of "how
REM much copied"), 8+ are real errors. Use 'if not errorlevel 8' so
REM only true failures fail the install.
echo       Source: %SRC%
echo       Target: %DST%
robocopy "%SRC%" "%DST%" /E /NFL /NDL /NJH /NJS /NP ^
    /XD __pycache__ .git .venv venv build dist node_modules ^
    /XF *.pyc *.pyo .DS_Store Thumbs.db sites_config.json >nul
set "RC=!ERRORLEVEL!"
if !RC! GEQ 8 (
    echo       ERROR: robocopy failed with code !RC!.
    echo.
    echo       Common causes:
    echo         * 16 = source path invalid. Drop install_dev.bat into
    echo                the repo root and run from there, not a path with
    echo                special characters.
    echo         * 0x10000000 = access denied on the target. Try running
    echo                cmd as Administrator.
    echo.
    echo       Retrying with verbose output to surface the real error...
    robocopy "%SRC%" "%DST%" /E /XD __pycache__ .git .venv venv build dist node_modules /XF *.pyc *.pyo
    pause
    exit /b 1
)
echo       Copied repo to %INSTALL_DIR% ^(robocopy exit !RC!^)

if not exist "%VENV_DIR%" (
    echo       Creating virtual environment at %VENV_DIR%...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo       ERROR: venv creation failed.
        pause
        exit /b 1
    )
) else (
    echo       Reusing existing venv at %VENV_DIR%
)
echo.

REM -- [4/8] Upgrade pip ------------------------------------------------
echo [4/8] Upgrading pip...
REM v3.47.6: invoke pip via `python -m pip` instead of `pip.exe` directly.
REM Windows can't replace a running .exe, so `pip install --upgrade pip`
REM fails with "ERROR: To modify pip, please run..." when called as
REM pip.exe. Running it through the python interpreter avoids that
REM (the interpreter is the running process, not pip).
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 (
    echo       WARNING: pip upgrade failed; continuing with existing pip.
)
echo.

REM -- [5/8] Install requirements-dev.txt -------------------------------
REM This file has '-r requirements.txt' at the top, so it brings in
REM the base deps automatically. Then we layer in optional separately.
echo [5/8] Installing base + dev dependencies...
echo       (requirements-dev.txt includes requirements.txt via -r)
"%PYTHON_EXE%" -m pip install -r "%INSTALL_DIR%\requirements-dev.txt"
if errorlevel 1 (
    echo       ERROR: base+dev install failed. Output above shows why.
    pause
    exit /b 1
)
echo       Base + dev dependencies installed.
echo.

REM -- [6/8] Install optional deps (one at a time, best-effort) ---------
REM Some library-extractor packages get pulled from PyPI without
REM warning. Installing one at a time means a single dead package
REM doesn't drop every later line on the floor.
echo [6/8] Installing optional dependencies (one-at-a-time, best-effort)...
echo       Some lines may fail; each failure is logged but doesn't stop
echo       the rest. Final summary at the end.
echo.
REM v3.47.6: dropped the OPT_FAIL_LIST + summary for-loop because pip
REM version pins contain >= and <, which cmd.exe interprets as
REM redirection operators when the variable is re-parsed inside the
REM final 'for %%P in (!OPT_FAIL_LIST!)' loop. The literal text
REM 'beeg_api>=1.3' becomes 'beeg_api' redirected to a file named
REM '=1.3', triggering ": was unexpected at this time" and exiting
REM the script silently. Failed package names are now echoed inline
REM during install, which is good enough - the summary just shows
REM counts.
set "OPT_OK=0"
set "OPT_FAIL=0"
for /f "usebackq tokens=* eol=#" %%L in ("%INSTALL_DIR%\requirements-optional.txt") do (
    set "LINE=%%L"
    REM Trim surrounding whitespace
    for /f "tokens=* delims= " %%T in ("!LINE!") do set "LINE=%%T"
    if not "!LINE!"=="" (
        if not "!LINE:~0,1!"=="#" (
            echo       Installing: !LINE!
            "%PYTHON_EXE%" -m pip install "!LINE!" >nul 2>&1
            if errorlevel 1 (
                echo         ^^^! failed: !LINE!
                set /a OPT_FAIL+=1
            ) else (
                set /a OPT_OK+=1
            )
        )
    )
)
echo.
echo       Optional summary: !OPT_OK! installed, !OPT_FAIL! failed.
echo       Any failures are listed above with '! failed:' prefix.
echo       The app runs fine without optional packages; sites that
echo       depended on them fall back to the generic teach path.
echo.

REM -- [7/8] Playwright Chromium ----------------------------------------
echo [7/8] Installing Playwright Chromium...
echo       ^(downloads ~300 MB on first install, please wait^)
"%PYTHON_EXE%" -m playwright install chromium
if errorlevel 1 (
    echo       WARNING: Playwright install failed. Run manually later:
    echo         "%PYTHON_EXE%" -m playwright install chromium
) else (
    echo       Chromium installed.
)
echo.

REM -- [8/8] Build standalone .exe --------------------------------------
echo [8/8] Building standalone .exe with PyInstaller...
echo       ^(no prompt; dev install always builds. Skip with Ctrl+C if not wanted.^)
echo.

set "SPEC_FILE=downloader.spec"
set "DIST_DIR=%INSTALL_DIR%\dist"
set "BUILD_DIR=%INSTALL_DIR%\build"

cd /d "%INSTALL_DIR%"
if exist "%INSTALL_DIR%\%SPEC_FILE%" (
    echo       Using spec file: %SPEC_FILE%
    "%VENV_DIR%\Scripts\pyinstaller.exe" "%SPEC_FILE%" --distpath "%DIST_DIR%" --workpath "%BUILD_DIR%" --noconfirm
) else (
    echo       WARNING: %SPEC_FILE% missing. Skipping .exe build.
)
if errorlevel 1 (
    echo       WARNING: PyInstaller build failed.
    echo       App still runs via launch.bat or "python downloader_ui.py".
) else (
    echo       Built: %DIST_DIR%\BulkDownloader\
)
echo.

REM -- Launcher + run-tests shortcut ------------------------------------
echo Creating launcher + test runner...

REM launch.bat - runs the app. As of v3.47.7 the Dev tab + log viewer
REM + test runner are part of the normal install (no env var needed).
REM To re-gate the dev endpoints, set BD_DEV_MODE_DISABLE=1.
(
    echo @echo off
    echo title Bulk Downloader v!BD_VER! ^(dev^)
    echo cd /d "%INSTALL_DIR%"
    echo "%VENV_DIR%\Scripts\python.exe" downloader_ui.py
    echo pause
) > "%INSTALL_DIR%\launch.bat"
echo       Launcher: %INSTALL_DIR%\launch.bat

REM run_tests.bat - runs the unit test suite (dev convenience)
(
    echo @echo off
    echo title Bulk Downloader Tests
    echo cd /d "%INSTALL_DIR%"
    echo set BD_DISABLE_KEEPALIVE=1
    echo "%VENV_DIR%\Scripts\python.exe" run_tests.py %%*
    echo pause
) > "%INSTALL_DIR%\run_tests.bat"
echo       Test runner: %INSTALL_DIR%\run_tests.bat

REM preflight.bat - runs the preflight script (dev convenience)
(
    echo @echo off
    echo title Bulk Downloader Preflight
    echo cd /d "%INSTALL_DIR%"
    echo "%VENV_DIR%\Scripts\python.exe" preflight.py
    echo pause
) > "%INSTALL_DIR%\preflight.bat"
echo       Preflight runner: %INSTALL_DIR%\preflight.bat
echo.

REM -- Done -------------------------------------------------------------
echo  ================================================================
echo   DEV install complete
echo  ================================================================
echo.
echo   Install dir : %INSTALL_DIR%
echo   Launcher    : %INSTALL_DIR%\launch.bat
echo   Tests       : %INSTALL_DIR%\run_tests.bat
echo   Preflight   : %INSTALL_DIR%\preflight.bat
echo.
echo   To run the app:
echo     - Double-click launch.bat
echo     - OR: "%PYTHON_EXE%" "%INSTALL_DIR%\downloader_ui.py"
echo.
echo   Then open http://localhost:5555 in your browser.
echo.
echo   Environment variables you may want to set:
echo     BD_HOST=0.0.0.0       (LAN-accessible; default localhost only)
echo     BD_PORT=5555          (default)
echo     BD_AUTH_TOKEN=^<...^>   (require bearer token on every API call)
echo     BD_DISABLE_KEEPALIVE=1 (for testing - disables persistent browsers)
echo.

set "RUN_NOW=N"
set /p "RUN_NOW=Launch the app now? [Y/N, default N]: "
if /i "!RUN_NOW!"=="Y" (
    echo.
    echo  Starting Bulk Downloader...
    start "" "%INSTALL_DIR%\launch.bat"
    echo  Launched. Open http://localhost:5555.
    echo.
)

echo  Press any key to close this window...
pause >nul
endlocal
