@echo off
setlocal EnableDelayedExpansion
title BulkDownloader Test Fixture Site
REM ====================================================================
REM  start_fixture_site.bat - launch the self-hosted test-fixture site.
REM
REM  The fixture site (tools/fixture_site.py) serves synthetic pages for
REM  testing adapters, the download path, and fault handling. It does
REM  NOT touch any real website. Open the management UI in a browser to
REM  arm faults.
REM
REM  Usage:  start_fixture_site.bat [port]
REM          (default port 8899)
REM ====================================================================
cd /d "%~dp0"

REM Pick an interpreter: prefer a local venv, else python on PATH.
set "PYEXE="
if exist "%~dp0venv\Scripts\python.exe" set "PYEXE=%~dp0venv\Scripts\python.exe"
if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo  ERROR: no Python found. Install Python 3.9+ or run the
    echo  installer first so a venv exists.
    pause
    exit /b 1
)

if not exist "%~dp0tools\fixture_site.py" (
    echo  ERROR: tools\fixture_site.py not found next to this script.
    pause
    exit /b 1
)

set "PORT=8899"
if not "%~1"=="" set "PORT=%~1"

echo.
echo  ================================================================
echo   BulkDownloader Test Fixture Site
echo  ================================================================
echo   Site:          http://127.0.0.1:%PORT%
echo   Management UI: http://127.0.0.1:%PORT%/_control
echo.
echo   Point a dev BulkDownloader instance at the site URL to test
echo   adapters and downloads. Use the management UI to arm faults.
echo   Press Ctrl+C to stop.
echo  ================================================================
echo.

"%PYEXE%" "%~dp0tools\fixture_site.py" --port %PORT%

echo.
echo  Fixture site stopped.
pause
exit /b 0
