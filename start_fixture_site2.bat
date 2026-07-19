@echo off
setlocal EnableDelayedExpansion
title BulkDownloader Test Fixture Site 2
REM ====================================================================
REM  start_fixture_site2.bat - launch the SECOND test-fixture site.
REM
REM  fixture_site2.py serves synthetic pages for the awkward site
REM  shapes the first fixture doesn't cover: infinite scroll, JSON /
REM  GraphQL APIs, challenge interstitials, paywalls, SPA shells. It
REM  has no fault injector and no management UI - it is purely about
REM  site structure. It touches no real website.
REM
REM  Usage:  start_fixture_site2.bat [port]   (default port 8898)
REM ====================================================================
cd /d "%~dp0"

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

if not exist "%~dp0tools\fixture_site2.py" (
    echo  ERROR: tools\fixture_site2.py not found next to this script.
    pause
    exit /b 1
)

set "PORT=8898"
if not "%~1"=="" set "PORT=%~1"

echo.
echo  ================================================================
echo   BulkDownloader Test Fixture Site 2 (awkward site shapes)
echo  ================================================================
echo   Site: http://127.0.0.1:%PORT%
echo.
echo   Shapes: /infinite  /api  /challenge  /paywall  /spa
echo   Point a dev BulkDownloader instance at the site URL to test
echo   adapters against these shapes. Press Ctrl+C to stop.
echo  ================================================================
echo.

"%PYEXE%" "%~dp0tools\fixture_site2.py" --port %PORT%

echo.
echo  Fixture site 2 stopped.
pause
exit /b 0
