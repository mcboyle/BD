@echo off
setlocal EnableDelayedExpansion
title BulkDownloader - Single Test File
REM ====================================================================
REM  run_test.bat - run ONE test file (or a subset) from the suite.
REM
REM  Safe per-file test running with no web server / no command surface.
REM  The argument is a test file name; the .py extension and tests\
REM  prefix are optional - all of these work:
REM      run_test.bat test_contracts
REM      run_test.bat test_contracts.py
REM      run_test.bat tests\test_contracts.py
REM
REM  Add --summary or --json to also write SUMMARY.txt / test_results.json:
REM      run_test.bat test_contracts --summary
REM
REM  With no argument it lists the available test files.
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

if "%~1"=="" (
    echo  Usage: run_test.bat ^<test_file^> [--summary] [--json]
    echo.
    echo  Available test files:
    for %%F in ("%~dp0tests\test_*.py") do echo    %%~nF
    echo.
    pause
    exit /b 0
)

REM Normalize the file argument: strip a tests\ prefix and .py suffix,
REM then rebuild as tests\NAME.py
set "ARG=%~1"
set "NAME=%~n1"
set "TARGET=%~dp0tests\%NAME%.py"
if not exist "%TARGET%" (
    echo  ERROR: test file not found: %TARGET%
    echo  Run with no argument to list available test files.
    pause
    exit /b 1
)

REM Pass through any flags after the first argument (--summary, --json).
set "FLAGS="
shift
:collect
if not "%~1"=="" (
    set "FLAGS=!FLAGS! %~1"
    shift
    goto collect
)

echo.
echo  ================================================================
echo   Running: tests\%NAME%.py
echo  ================================================================
echo.

set "BD_DISABLE_KEEPALIVE=1"
set "BD_INSTALL_DIR="
"%PYEXE%" "%~dp0run_tests.py" "tests\%NAME%.py"!FLAGS!
set "RC=!ERRORLEVEL!"

echo.
if "!RC!"=="0" (
    echo   Result: PASS ^(exit 0^)
) else (
    echo   Result: FAIL ^(exit !RC!^)
)
echo.
pause
exit /b !RC!
