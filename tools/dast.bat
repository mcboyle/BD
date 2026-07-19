@echo off
REM ============================================================================
REM dast.bat -- Bulk Downloader dynamic analysis pipeline (Windows)
REM ============================================================================
REM Runs the in-process probe + optional external scanners against a
REM locally-launched dev server. Optional scanners are SKIPPED if their
REM binaries aren't on PATH -- install them via:
REM
REM   ZAP:    https://www.zaproxy.org/download/  (or use Docker)
REM   Nikto:  perl/Strawberry + git clone the nikto repo
REM   sqlmap: pip install sqlmap, or download from sqlmap.org
REM
REM Exit code:
REM   0 -- clean
REM   1 -- findings present (see tools\dast_results\SUMMARY.txt)
REM   2 -- setup error
REM ============================================================================
setlocal enabledelayedexpansion

set "REPO_DIR=%~dp0.."
pushd "%REPO_DIR%" >nul
set "REPO_DIR=%CD%"

REM -- Flags ---------------------------------------------------------------
set "PROBE_ONLY=0"
for %%A in (%*) do (
    if /i "%%A"=="--probe-only" set "PROBE_ONLY=1"
    if /i "%%A"=="-h"           goto :usage
    if /i "%%A"=="--help"       goto :usage
)
goto :no_usage
:usage
echo Usage: dast.bat [--probe-only]
echo   --probe-only   Skip external scanners ^(ZAP, Nikto, Wapiti, sqlmap^).
popd >nul
exit /b 0
:no_usage

REM -- Locate python -------------------------------------------------------
set "PYTHON_EXE="
if exist "%REPO_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%REPO_DIR%\.venv\Scripts\python.exe"
if "!PYTHON_EXE!"=="" (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if "!PYTHON_EXE!"=="" set "PYTHON_EXE=%%P"
    )
)
if "!PYTHON_EXE!"=="" (
    echo [dast] ERROR: no python found. Activate venv or install Python 3.10+.
    popd >nul & exit /b 2
)
echo [dast] Python: !PYTHON_EXE!

REM -- Output dir ----------------------------------------------------------
set "OUT=%REPO_DIR%\tools\dast_results"
if not exist "%OUT%" mkdir "%OUT%"
del /q "%OUT%\*.txt" "%OUT%\*.json" "%OUT%\*.html" 2>nul

set /a FINDINGS=0

REM ============================================================================
echo.
echo [dast] === 1/6 In-process probe (dast_probe.py) ===
REM ============================================================================
set "BD_DISABLE_KEEPALIVE=1"
"!PYTHON_EXE!" "%REPO_DIR%\tools\dast_probe.py" > "%OUT%\probe.txt" 2>&1
set "PROBE_EXIT=!errorlevel!"
if !PROBE_EXIT! EQU 0 (
    echo   probe: clean ^(0 bugs^)
) else (
    echo   probe: findings present ^(see probe.txt^)
    set /a FINDINGS+=1
)
findstr /b "DAST:" "%OUT%\probe.txt" 2>nul

REM -- --probe-only short-circuit ------------------------------------------
if "!PROBE_ONLY!"=="1" (
    echo.
    echo [dast] --probe-only: skipping external scanners ^(ZAP/Nikto/sqlmap/Wapiti^)
    echo Reports written to: %OUT%
    if !FINDINGS! GTR 0 (
        echo [dast] !FINDINGS! finding categories. Review tools\dast_results\.
        popd >nul & exit /b 1
    ) else (
        echo [dast] Clean ^(probe-only^).
        popd >nul & exit /b 0
    )
)

REM ============================================================================
echo.
echo [dast] === 2/6 Launch app for external scanners ===
REM ============================================================================
REM Boot the app on 127.0.0.1:9876 in the background.
set "APP_PORT=9876"
set "APP_HOST=127.0.0.1"
set "APP_URL=http://%APP_HOST%:%APP_PORT%"

REM tmp BD_HOME so we don't pollute user data
for /f "delims=" %%T in ('powershell -nop -c "[IO.Path]::GetTempPath() + 'bd_dast_app_' + [Guid]::NewGuid().ToString('N')"') do set "APP_HOME=%%T"
mkdir "!APP_HOME!" 2>nul

echo   starting app on %APP_URL% ^(BD_HOME=!APP_HOME!^)...
start "BD_DAST_APP" /B cmd /c "set BD_HOME=!APP_HOME!&& set BD_HOST=%APP_HOST%&& set BD_PORT=%APP_PORT%&& set BD_DISABLE_KEEPALIVE=1&& "!PYTHON_EXE!" downloader_ui.py > "%OUT%\server.log" 2>&1"

REM Wait for the server (up to 5 seconds)
set "APP_UP=0"
for /l %%i in (1,1,10) do (
    powershell -nop -c "try { iwr -UseBasicParsing -TimeoutSec 1 %APP_URL%/ | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
    if !errorlevel! EQU 0 (
        set "APP_UP=1"
        echo   app responsive after %%i*0.5s
        goto :app_up
    )
    powershell -nop -c "Start-Sleep -Milliseconds 500" >nul
)
:app_up
if "!APP_UP!"=="0" (
    echo   WARN: app did not respond within 5s -- external scanners SKIPPED
)

REM ============================================================================
echo.
echo [dast] === 3/6 OWASP ZAP baseline scan ===
REM ============================================================================
where docker >nul 2>&1
if errorlevel 1 (
    echo   zap: SKIPPED ^(Docker not installed^) > "%OUT%\zap.txt"
    echo   zap: SKIPPED ^(Docker not installed^)
) else if "!APP_UP!"=="0" (
    echo   zap: SKIPPED ^(app not running^) > "%OUT%\zap.txt"
    echo   zap: SKIPPED ^(app not running^)
) else (
    docker pull -q ghcr.io/zaproxy/zaproxy:stable >nul 2>&1
    REM On Windows, --network host doesn't work -- use host.docker.internal
    set "ZAP_URL=http://host.docker.internal:%APP_PORT%"
    docker run --rm ^
        -v "%OUT%:/zap/wrk:rw" ^
        ghcr.io/zaproxy/zaproxy:stable ^
        zap-baseline.py -t "!ZAP_URL!" ^
        -r zap-report.html -J zap-report.json ^
        > "%OUT%\zap.txt" 2>&1
    if exist "%OUT%\zap-report.json" (
        echo   zap: report saved to zap-report.html
        findstr /c:"\"riskcode\":\"3\"" "%OUT%\zap-report.json" >nul
        if not errorlevel 1 (
            echo   zap: HIGH-severity alerts present
            set /a FINDINGS+=1
        ) else (
            echo   zap: no HIGH alerts
        )
    ) else (
        echo   zap: scan did not produce report ^(see zap.txt^)
    )
)

REM ============================================================================
echo.
echo [dast] === 4/6 Nikto web server scan ===
REM ============================================================================
where nikto >nul 2>&1
if errorlevel 1 (
    echo   nikto: SKIPPED ^(not installed^) > "%OUT%\nikto.txt"
    echo   nikto: SKIPPED ^(install Strawberry Perl + clone the Nikto repo^)
) else if "!APP_UP!"=="0" (
    echo   nikto: SKIPPED ^(app not running^) > "%OUT%\nikto.txt"
    echo   nikto: SKIPPED ^(app not running^)
) else (
    nikto -h "%APP_URL%" -ask no -output "%OUT%\nikto.txt" >nul 2>&1
    findstr /i "vulnerab" "%OUT%\nikto.txt" >nul
    if not errorlevel 1 (
        echo   nikto: findings present
        set /a FINDINGS+=1
    ) else (
        echo   nikto: clean
    )
)

REM ============================================================================
echo.
echo [dast] === 5/6 sqlmap probe ===
REM ============================================================================
where sqlmap >nul 2>&1
if errorlevel 1 (
    "!PYTHON_EXE!" -m sqlmap --version >nul 2>&1
    if errorlevel 1 (
        echo   sqlmap: SKIPPED ^(not installed^) > "%OUT%\sqlmap.txt"
        echo   sqlmap: SKIPPED ^(install: pip install sqlmap^)
        set "SQLMAP_OK=0"
    ) else (
        set "SQLMAP_CMD=!PYTHON_EXE! -m sqlmap"
        set "SQLMAP_OK=1"
    )
) else (
    set "SQLMAP_CMD=sqlmap"
    set "SQLMAP_OK=1"
)
if "!SQLMAP_OK!"=="1" (
    if "!APP_UP!"=="1" (
        !SQLMAP_CMD! -u "%APP_URL%/api/logs/tail?lines=10" ^
            --batch --level=2 --risk=2 ^
            --output-dir="%OUT%\sqlmap" ^
            > "%OUT%\sqlmap.txt" 2>&1
        findstr /i "vulnerable injectable" "%OUT%\sqlmap.txt" >nul
        if not errorlevel 1 (
            echo   sqlmap: SQL INJECTION FOUND
            set /a FINDINGS+=1
        ) else (
            echo   sqlmap: no injection found
        )
    ) else (
        echo   sqlmap: SKIPPED ^(app not running^) > "%OUT%\sqlmap.txt"
    )
)

REM ============================================================================
echo.
echo [dast] === 6/6 Wapiti active fuzzer ===
REM ============================================================================
REM Wapiti is pip-installable. Active web fuzzer covering XSS, SQLi,
REM command-injection, file-disclosure, redirect. Cap scan time at 4 min.
if "!APP_UP!"=="1" (
    "!PYTHON_EXE!" -m wapiti --version >nul 2>&1
    if errorlevel 1 (
        "!PYTHON_EXE!" -m pip install --quiet --disable-pip-version-check ^
            "wapiti3>=3.1" 2>>"%OUT%\wapiti-install.log"
    )
    "!PYTHON_EXE!" -m wapiti --version >nul 2>&1
    if errorlevel 1 (
        echo   wapiti: SKIPPED ^(install failed; see wapiti-install.log^) > "%OUT%\wapiti.txt"
        echo   wapiti: SKIPPED ^(install failed^)
    ) else (
        "!PYTHON_EXE!" -m wapiti -u "%APP_URL%" ^
            -m xss,sql,exec,file,redirect,permanentxss,htaccess,xxe ^
            -f txt -o "%OUT%\wapiti.txt" ^
            --flush-session --max-scan-time 240 ^
            > "%OUT%\wapiti.log" 2>&1
        findstr /r "High Critical" "%OUT%\wapiti.txt" >nul
        if not errorlevel 1 (
            echo   wapiti: High/Critical findings present
            set /a FINDINGS+=1
        ) else (
            echo   wapiti: no High/Critical findings
        )
    )
) else (
    echo   wapiti: SKIPPED ^(app not running^) > "%OUT%\wapiti.txt"
    echo   wapiti: SKIPPED ^(app not running^)
)

REM Stop the app
echo.
echo [dast] Stopping the app...
taskkill /F /FI "WINDOWTITLE eq BD_DAST_APP*" >nul 2>&1
REM Belt + suspenders: kill by port
for /f "tokens=5" %%P in ('netstat -ano ^| findstr :%APP_PORT% ^| findstr LISTENING') do (
    taskkill /F /PID %%P >nul 2>&1
)
if exist "!APP_HOME!" rd /s /q "!APP_HOME!" 2>nul

REM -- Summary -------------------------------------------------------------
echo.
echo [dast] === Summary ===
(
    echo Bulk Downloader DAST run -- %DATE% %TIME%
    echo ==========================================
    echo.
    echo --- probe ---
    type "%OUT%\probe.txt" 2>nul | findstr /b "  "
    echo.
    for %%F in (zap nikto sqlmap wapiti) do (
        if exist "%OUT%\%%F.txt" (
            echo --- %%F ---
            type "%OUT%\%%F.txt"
            echo.
        )
    )
    echo Findings categories with hits: !FINDINGS!
) > "%OUT%\SUMMARY.txt"
echo Reports written to: %OUT%
echo.
if !FINDINGS! GTR 0 (
    echo [dast] %FINDINGS% finding categories. Review tools\dast_results\.
    popd >nul & exit /b 1
) else (
    echo [dast] Clean.
    popd >nul & exit /b 0
)
