@echo off
setlocal EnableDelayedExpansion
REM v3.51.1: derive version from the package so the banner can never
REM go stale. Falls back to "unknown" only if the file is missing.
set "BD_VER=unknown"
if exist "%~dp0bulk_downloader\__init__.py" (
  for /f "tokens=2 delims== " %%V in ('findstr /b /c:"__version__ " "%~dp0bulk_downloader\__init__.py"') do set "BD_VER=%%~V"
)
title Bulk Downloader v!BD_VER! Windows Installer

REM ============================================================================
REM  install_windows.bat - Bulk Downloader v!BD_VER! Windows Installer
REM
REM  v3.43.16: stripped ALL non-ASCII characters (em-dashes, box-drawing,
REM  curly quotes). Earlier versions had em-dashes inside `set` values and
REM  `echo` statements which cmd.exe could mis-parse on some Server 2025
REM  systems depending on console code page, causing the window to close
REM  mid-step. This version is pure 7-bit ASCII for maximum compatibility.
REM
REM  Every error path routes through :end so the user always sees the result.
REM ============================================================================

echo.
echo  ================================================================
echo   Bulk Downloader v!BD_VER!  -  Windows Installer
echo  ================================================================
echo.

REM -- CONFIGURATION --------------------------------------------------
set "INSTALL_DIR=%USERPROFILE%\BulkDownloader"
set "APP_PY=downloader_ui.py"
set "SPEC_FILE=downloader.spec"
set "REQ_FILE=requirements.txt"
set "EXE_NAME=BulkDownloader"
set "VENV_DIR=%INSTALL_DIR%\venv"
set "DIST_DIR=%INSTALL_DIR%\dist"
set "BUILD_DIR=%INSTALL_DIR%\build_tmp"
set "INSTALL_OK=0"
set "EXE_PATH="
set "FATAL_ERROR="

set "SCRIPT_DIR=%~dp0"
set "SRC_PY=%SCRIPT_DIR%%APP_PY%"
set "SRC_SPEC=%SCRIPT_DIR%%SPEC_FILE%"
set "SRC_REQ=%SCRIPT_DIR%%REQ_FILE%"

REM -- STEP 1: Verify source files exist ------------------------------
echo [1/8] Checking source files...
if not exist "%SRC_PY%" (
    set "FATAL_ERROR=%APP_PY% not found next to this script. Expected: %SRC_PY%"
    goto :end
)
echo       Found: %SRC_PY%
if exist "%SRC_SPEC%" (
    echo       Found: %SRC_SPEC%
) else (
    echo       NOTE: %SPEC_FILE% not found, will use auto flags if .exe is built
)
if exist "%SRC_REQ%" (
    echo       Found: %SRC_REQ%
) else (
    echo       NOTE: %REQ_FILE% not found, will pin packages inline instead
)

REM -- STEP 2: Check for Python 3.9+ ----------------------------------
echo.
echo [2/8] Checking for Python 3.9+...
REM D1 (post-v3.63.8): prefer py -3.12 specifically (the documented
REM target), then py -3 (latest installed Python launcher pick), then
REM python3 / python on PATH. The v3.63.7 Windows verification ran on
REM 3.14 cleanly (4414/4414); we accept any 3.9+ but try 3.12 first
REM so a host that has both gets the documented version. Log the
REM picked interpreter+version so a future support pass can tell at a
REM glance whether the install was on the documented target or a
REM newer fallback.
set "PYTHON_CMD="
set "PYTHON_VER="
REM Try the Windows py launcher with explicit 3.12 first.
py -3.12 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq tokens=*" %%V in (`py -3.12 -c "import sys;print('.'.join(map(str,sys.version_info[:3])))" 2^>nul`) do (
        set "PYTHON_CMD=py -3.12"
        set "PYTHON_VER=%%V"
        echo       Found: py -3.12  ^(version %%V, documented target^)
    )
)
REM Fallback: any 3.9+ on PATH or via the launcher's default.
if not defined PYTHON_CMD (
    for %%C in (python3 python py) do (
        if not defined PYTHON_CMD (
            %%C -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
            if not errorlevel 1 (
                for /f "usebackq tokens=*" %%V in (`%%C -c "import sys;print('.'.join(map(str,sys.version_info[:3])))" 2^>nul`) do (
                    set "PYTHON_CMD=%%C"
                    set "PYTHON_VER=%%V"
                    echo       Found: %%C  ^(version %%V^)
                    REM Note if we fell past the documented 3.12 target.
                    %%C -c "import sys; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>&1
                    if errorlevel 1 (
                        echo       NOTE: 3.12 was not available; %%V is supported but not the documented target.
                    )
                )
            )
        )
    )
)
if not defined PYTHON_CMD (
    echo.
    echo  Python 3.9 or newer was not found on PATH.
    set /p "INSTALL_PY=  Try to install Python 3.12 via winget? [Y/N]: "
    if /i "!INSTALL_PY!"=="Y" (
        winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        if errorlevel 1 (
            set "FATAL_ERROR=winget install failed. Install Python manually from python.org and re-run."
            goto :end
        )
        echo.
        echo  Python installed. Please CLOSE this window, open a NEW cmd/PowerShell
        echo  so PATH refreshes, then re-run install_windows.bat.
        goto :end
    ) else (
        set "FATAL_ERROR=Python is required. Install Python 3.9+ from python.org and re-run."
        goto :end
    )
)

REM -- STEP 3: Set up install directory + virtual environment ---------
echo.
echo [3/8] Setting up install directory and virtual environment...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

REM v3.63.5: switched the partial xcopy + per-file copy block to a
REM single robocopy /E of the whole tree. The old block copied only
REM the bulk_downloader\ package, downloader_ui.py, downloader.spec,
REM requirements*.txt, and uninstall_windows.bat -- leaving tests\,
REM live_tests\, extension\, tools\, fixtures\, run_tests.py, the
REM .bat helpers, and the docs missing from %INSTALL_DIR%. As a
REM result, the Windows install couldn't run the test suite from
REM its install target (run_tests.py wasn't there) and the
REM extension/icon files added in v3.63.5 wouldn't reach the
REM install. Robocopy /E with the install_dev.bat exclude list
REM brings everything needed. Exit codes 0-7 are success in
REM robocopy parlance; 8+ are real failures.
set "SRC=%SCRIPT_DIR%"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
set "DST=%INSTALL_DIR%"
if "%DST:~-1%"=="\" set "DST=%DST:~0,-1%"
robocopy "%SRC%" "%DST%" /E /NFL /NDL /NJH /NJS /NP ^
    /XD __pycache__ .git .venv venv build build_tmp dist node_modules ^
    /XF *.pyc *.pyo .DS_Store Thumbs.db sites_config.json >nul
set "RC=!ERRORLEVEL!"
if !RC! GEQ 8 (
    set "FATAL_ERROR=robocopy failed with code !RC! copying %SRC% to %DST%. Common causes: source path with special characters (drop install_windows.bat into the repo root and run from there); access denied on target (try cmd as Administrator)."
    goto :end
)
echo       Copied repo to %INSTALL_DIR% ^(robocopy exit !RC!^)

if exist "%VENV_DIR%\Scripts\python.exe" (
    echo       Virtual environment already exists, reusing.
) else (
    echo       Creating virtual environment at %VENV_DIR%...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        set "FATAL_ERROR=Failed to create virtual environment at %VENV_DIR%"
        goto :end
    )
)
set "VPYTHON=%VENV_DIR%\Scripts\python.exe"
REM v3.63.5: removed `set "PIP=...\pip.exe"`. All pip calls now go
REM through `"%VPYTHON%" -m pip` per the NEVER rule (and matching
REM install_linux.sh's `"$VPYTHON" -m pip` pattern).

REM -- STEP 4: Install Python dependencies ----------------------------
echo.
echo [4/8] Installing Python dependencies...
echo.
"%VPYTHON%" -m pip install --upgrade pip
set "PIP_RC=0"
if exist "%INSTALL_DIR%\%REQ_FILE%" (
    echo       Using requirements file: %REQ_FILE%
    "%VPYTHON%" -m pip install -r "%INSTALL_DIR%\%REQ_FILE%"
    set "PIP_RC=!ERRORLEVEL!"
) else (
    echo       No requirements file, installing pinned packages directly
    "%VPYTHON%" -m pip install "flask>=3.0" "playwright>=1.61,<2.0" "httpx>=0.25,<1.0"
    set "PIP_RC=!ERRORLEVEL!"
)
if not "!PIP_RC!"=="0" (
    set "FATAL_ERROR=pip install exited with code !PIP_RC!. See error output above. Common causes: firewall blocking pypi.org, missing MSVC Build Tools, or Python version too new for the wheels."
    goto :end
)
echo       Dependencies installed successfully.

REM -- STEP 5: Install Playwright's Chromium browser ------------------
echo.
echo [5/8] Installing Playwright Chromium browser...
echo       ^(downloads ~300 MB on first install, please wait^)
echo.
REM v3.63.5: use `"%VPYTHON%" -m playwright install chromium` per the
REM project NEVER rule (matches install_linux.sh's
REM `"$VPYTHON" -m playwright install chromium`). The previous primary
REM call was `"%VENV_DIR%\Scripts\playwright.exe" install chromium`
REM with the -m form as a fallback; the .exe wrapper isn't structurally
REM different, but the canonical form is the -m form everywhere else.
"%VPYTHON%" -m playwright install chromium
set "PW_RC=!ERRORLEVEL!"
if not "!PW_RC!"=="0" (
    echo.
    echo  WARNING: Playwright Chromium install may have failed ^(exit code !PW_RC!^).
    echo  The app needs Chromium to run. Try manually after install completes:
    echo    "%VPYTHON%" -m playwright install chromium
    echo  Continuing, this isn't fatal, you can fix it later.
    echo.
) else (
    echo       Chromium installed.
)

REM -- ffmpeg presence check (ROB-3 alignment: HLS/TS + startup health) ------
echo.
echo       Checking for ffmpeg ^(HLS/TS video downloads^)...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo  WARNING: ffmpeg was not found on PATH.
    echo  HLS/TS downloads and the app startup health check need ffmpeg.
    echo  Install it, for example: winget install Gyan.FFmpeg
    echo  Continuing, this isn't fatal, you can add ffmpeg later.
) else (
    echo       ffmpeg found on PATH.
)

REM -- STEP 6: Build .exe with PyInstaller ^(OPTIONAL^) ---------------
echo.
echo [6/8] Build standalone .exe with PyInstaller?
echo       Bundles app + Python runtime into BulkDownloader.exe so the app
echo       can run on machines without Python installed. Takes 1-3 minutes.
echo       The app runs fine WITHOUT the .exe via launch.bat or
echo       "python downloader_ui.py". Skip this unless you specifically
echo       need a standalone executable.
echo.
set "BUILD_EXE=N"
set /p "BUILD_EXE=       Build .exe now? [Y/N, default N]: "
if /i not "!BUILD_EXE!"=="Y" (
    echo       Skipping .exe build.
    goto :create_launcher
)

echo.
echo       Building .exe...
cd /d "%INSTALL_DIR%"
REM v3.47.1: install pyinstaller into the venv if it's not already there.
REM Previously the script assumed it was pre-installed and failed with
REM exit 9009 ("not recognized") on fresh installs because pyinstaller
REM lives in requirements-dev.txt, not requirements.txt.
if not exist "%VENV_DIR%\Scripts\pyinstaller.exe" (
    echo       Installing PyInstaller into venv...
    "%VPYTHON%" -m pip install pyinstaller
    if not "!ERRORLEVEL!"=="0" (
        echo       WARNING: pip install pyinstaller failed.
        echo       Skipping .exe build. The app still runs via launch.bat.
        goto :create_launcher
    )
)
if exist "%INSTALL_DIR%\%SPEC_FILE%" (
    echo       Using spec file: %SPEC_FILE%
    "%VENV_DIR%\Scripts\pyinstaller.exe" "%SPEC_FILE%" --distpath "%DIST_DIR%" --workpath "%BUILD_DIR%" --noconfirm
) else (
    echo       No spec file found, using auto flags
    "%VENV_DIR%\Scripts\pyinstaller.exe" "%APP_PY%" ^
        --name "%EXE_NAME%" ^
        --distpath "%DIST_DIR%" ^
        --workpath "%BUILD_DIR%" ^
        --collect-all playwright ^
        --hidden-import flask ^
        --hidden-import flask.templating ^
        --hidden-import werkzeug ^
        --hidden-import werkzeug.serving ^
        --hidden-import jinja2 ^
        --hidden-import markupsafe ^
        --hidden-import click ^
        --hidden-import sqlite3 ^
        --hidden-import _sqlite3 ^
        --hidden-import concurrent.futures ^
        --hidden-import concurrent.futures.thread ^
        --hidden-import asyncio ^
        --hidden-import playwright._impl._driver ^
        --exclude-module tkinter ^
        --exclude-module matplotlib ^
        --exclude-module numpy ^
        --exclude-module pandas ^
        --noconfirm ^
        --console
)
set "PI_RC=!ERRORLEVEL!"
if not "!PI_RC!"=="0" (
    echo.
    echo  WARNING: PyInstaller build failed ^(exit code !PI_RC!^).
    echo  This isn't fatal, the app still runs via launch.bat.
    echo.
) else (
    echo       Build complete.
    set "EXE_PATH=%DIST_DIR%\%EXE_NAME%\%EXE_NAME%.exe"
    if not exist "!EXE_PATH!" set "EXE_PATH=%DIST_DIR%\%EXE_NAME%.exe"
)

REM -- STEP 7: Create shortcuts ---------------------------------------
:create_launcher
echo.
echo [7/8] Creating shortcuts...

(
echo @echo off
echo title Bulk Downloader v!BD_VER!
echo cd /d "%INSTALL_DIR%"
echo echo Starting Bulk Downloader...
echo echo Open http://localhost:5555 in your browser.
echo echo.
echo "%VPYTHON%" "%INSTALL_DIR%\%APP_PY%"
echo echo.
echo echo App exited. Press any key to close.
echo pause
) > "%INSTALL_DIR%\launch.bat"
echo       Launcher created: %INSTALL_DIR%\launch.bat

set "DESKTOP=%USERPROFILE%\Desktop"
set "STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\BulkDownloader"
if not exist "%STARTMENU%" mkdir "%STARTMENU%" >nul 2>&1

powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%DESKTOP%\Bulk Downloader.lnk'); " ^
    "$s.TargetPath = '%INSTALL_DIR%\launch.bat'; " ^
    "$s.WorkingDirectory = '%INSTALL_DIR%'; " ^
    "$s.Description = 'Bulk Downloader v!BD_VER!'; " ^
    "$s.Save()" >nul 2>&1
if not errorlevel 1 echo       Desktop shortcut created.

powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%STARTMENU%\Bulk Downloader.lnk'); " ^
    "$s.TargetPath = '%INSTALL_DIR%\launch.bat'; " ^
    "$s.WorkingDirectory = '%INSTALL_DIR%'; " ^
    "$s.Description = 'Bulk Downloader v!BD_VER!'; " ^
    "$s.Save()" >nul 2>&1
if not errorlevel 1 echo       Start Menu shortcut created.

REM -- STEP 8: D3 frontend SPA build (non-fatal) -----------------------
REM Builds the React SPA served by Flask at /m2/*. If Node.js is not
REM installed the build is SKIPPED with a warning and the installer
REM completes. /m2 will return 503 with a clear "Node missing" message
REM until Node is installed and 'npm run build' runs under frontend/.
REM The existing UIs at / and /m are unaffected regardless.
REM
REM D5 (v3.64.4): if frontend\dist\index.html already exists, skip
REM the build entirely. Pairs with `tools\build_release.py
REM --prebuild-spa` (D5a, v3.64.3) which produces dist-included zips
REM on a Node-equipped machine for fresh-PC installs that don't have
REM Node. To force a rebuild from a dist-included install, delete
REM frontend\dist\ before re-running this script.
echo.
echo [8/8] Building D3 frontend (optional, needs Node.js 18+)...
if not exist "%INSTALL_DIR%\frontend\package.json" (
    echo       No frontend/ directory in this release. Skipping.
    goto :after_frontend
)
if exist "%INSTALL_DIR%\frontend\dist\index.html" (
    echo       Using existing frontend\dist\ ^(built ahead of install^).
    echo       To force a rebuild: delete %INSTALL_DIR%\frontend\dist\
    echo       and re-run this script with Node.js 18+ on PATH.
    goto :after_frontend
)
where node >nul 2>&1
if errorlevel 1 (
    echo       Node.js not found on PATH. SKIPPING frontend build.
    echo       The new D3 UI at /m2 will return 503 until Node 18+
    echo       is installed ^(https://nodejs.org/^) and this installer
    echo       is re-run. Existing UIs at / and /m are unaffected.
    goto :after_frontend
)
where npm >nul 2>&1
if errorlevel 1 (
    echo       npm not found on PATH. SKIPPING frontend build.
    goto :after_frontend
)
for /f "delims=" %%V in ('node --version 2^>nul') do set "NODE_VER=%%V"
echo       Node: !NODE_VER!
pushd "%INSTALL_DIR%\frontend" >nul
if exist package-lock.json (
    echo       Running: npm ci
    call npm ci
) else (
    echo       Running: npm install
    call npm install
)
set "NPM_INSTALL_RC=!errorlevel!"
if !NPM_INSTALL_RC! NEQ 0 (
    echo       WARNING: npm install/ci failed ^(exit !NPM_INSTALL_RC!^).
    echo       /m2 will 503 until you fix it. Existing UIs unaffected.
    popd >nul
    goto :after_frontend
)
echo       Running: npm run build
call npm run build
set "NPM_BUILD_RC=!errorlevel!"
popd >nul
if !NPM_BUILD_RC! NEQ 0 (
    echo       WARNING: npm run build failed ^(exit !NPM_BUILD_RC!^).
    echo       /m2 will 503 until 'cd frontend ^&^& npm run build'
    echo       succeeds. Existing UIs at / and /m are unaffected.
) else (
    echo       Frontend built: %INSTALL_DIR%\frontend\dist\
)
:after_frontend

set "INSTALL_OK=1"
goto :end

REM ===================================================================
REM  :end - unified exit handler. Every error path AND the success
REM         path reaches here so the user ALWAYS sees the result and
REM         gets the "Launch now?" prompt before the window closes.
REM ===================================================================
:end
echo.
echo  ================================================================
if defined FATAL_ERROR (
    echo   INSTALL FAILED
    echo  ================================================================
    echo.
    echo   !FATAL_ERROR!
    echo.
    echo  See above for the underlying error. Save the output of this
    echo  window if you need help diagnosing it.
    echo.
    echo  Press any key to close this window...
    pause >nul
    exit /b 1
)
if "%INSTALL_OK%"=="1" (
    echo   Installation complete!
) else (
    echo   Installation finished with warnings, check output above
)
echo  ================================================================
echo.
echo   Install dir : %INSTALL_DIR%
echo   Launcher    : %INSTALL_DIR%\launch.bat
if defined EXE_PATH (
    if exist "!EXE_PATH!" echo   .exe        : !EXE_PATH!
)
echo   Uninstall   : %INSTALL_DIR%\uninstall_windows.bat
echo.
echo   To run the app:
echo     - Double-click the Bulk Downloader shortcut on your Desktop
echo     - OR run: %INSTALL_DIR%\launch.bat
echo     - OR run from cmd: cd "%INSTALL_DIR%" ^&^& python downloader_ui.py
echo.
echo   Then open http://localhost:5555 in your browser.
echo.
echo   To uninstall later, run: %INSTALL_DIR%\uninstall_windows.bat
echo.
set "LAUNCH=N"
set /p "LAUNCH=  Launch the app now? [Y/N, default N]: "
if /i "!LAUNCH!"=="Y" (
    echo.
    echo  Starting Bulk Downloader...
    start "" "%INSTALL_DIR%\launch.bat"
    echo  Launch initiated. Open http://localhost:5555 in your browser.
    echo.
)
echo  Press any key to close this window...
pause >nul
exit /b 0
