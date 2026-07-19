@echo off
setlocal EnableDelayedExpansion
title BulkDownloader - Full Test Suite
REM ====================================================================
REM  run_all_tests.bat - run the entire BulkDownloader test suite.
REM
REM  Sets BD_DISABLE_KEEPALIVE=1 (required - the suite must not spawn
REM  persistent browser sessions) and runs run_tests.py. Any extra
REM  arguments are passed straight through, so you can run a subset:
REM      run_all_tests.bat tests\test_contracts.py
REM
REM  Expected at v3.62.7: ~4105 pass / 3 skip / 0 fail.
REM  (Verify the number - do not treat it as a contract.)
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

if not exist "%~dp0run_tests.py" (
    echo  ERROR: run_tests.py not found next to this script.
    pause
    exit /b 1
)

echo.
echo  ================================================================
echo   BulkDownloader - Full Test Suite
echo  ================================================================
echo   Clearing stale __pycache__ directories...

REM Stale .pyc files have caused false failures - clear them first.
for /d /r "%~dp0" %%D in (__pycache__) do (
    if exist "%%D" rmdir /s /q "%%D" >nul 2>&1
)

echo   Running run_tests.py ...
echo  ================================================================
echo.

set "BD_DISABLE_KEEPALIVE=1"
"%PYEXE%" "%~dp0run_tests.py" %*
set "RC=!ERRORLEVEL!"

echo.
echo  ================================================================
if "!RC!"=="0" (
    echo   Test run finished - exit code 0.
) else (
    echo   Test run finished - exit code !RC! ^(failures or error^).
)
echo  ================================================================
echo.
pause
exit /b !RC!
