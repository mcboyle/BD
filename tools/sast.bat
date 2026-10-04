@echo off
REM ============================================================================
REM sast.bat -- Bulk Downloader static analysis pipeline
REM ============================================================================
REM Runs every available SAST tool against the codebase and writes
REM per-tool reports + a combined summary under tools\sast_results\.
REM
REM Requires the project venv to be activated OR the install_dev.bat venv
REM at <repo>\.venv. Tools auto-install on first run via pip.
REM
REM Exit code:
REM   0  -- clean (no Bandit HIGH, no Semgrep ERROR, no pip-audit findings)
REM   1  -- findings present (see tools\sast_results\SUMMARY.txt)
REM   2  -- setup error (no Python, no venv, no internet on first run, or a
REM         scanner missing/crashed so its report was never written)
REM ============================================================================
setlocal enabledelayedexpansion

REM -- Locate repo root (this script lives in tools\) ---------------------
set "REPO_DIR=%~dp0.."
pushd "%REPO_DIR%" >nul
set "REPO_DIR=%CD%"

REM -- Locate python (prefer venv) ----------------------------------------
set "PYTHON_EXE="
if exist "%REPO_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%REPO_DIR%\.venv\Scripts\python.exe"
if "!PYTHON_EXE!"=="" (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if "!PYTHON_EXE!"=="" set "PYTHON_EXE=%%P"
    )
)
if "!PYTHON_EXE!"=="" (
    echo [sast] ERROR: no python found. Activate venv or install Python 3.10+.
    popd >nul & exit /b 2
)
echo [sast] Python: !PYTHON_EXE!

REM -- Output dir ----------------------------------------------------------
set "OUT=%REPO_DIR%\tools\sast_results"
if not exist "%OUT%" mkdir "%OUT%"
del /q "%OUT%\*.txt" 2>nul
del /q "%OUT%\*.json" 2>nul
del /q "%OUT%\*.sarif" 2>nul

REM -- Tool installer (idempotent; uses python -m pip to avoid the .exe lock) -
echo.
echo [sast] Ensuring SAST tools are installed...
"!PYTHON_EXE!" -m pip install --quiet --disable-pip-version-check ^
    "bandit>=1.7" "semgrep>=1.50" "pip-audit>=2.6" "ruff>=0.4" ^
    "detect-secrets>=1.4" 2>&1 ^
    | findstr /v "DEPRECATION already" >nul
if errorlevel 1 (
    echo [sast] WARN: pip install reported errors. Some tools may not run.
)

REM -- Counters ------------------------------------------------------------
set /a FINDINGS=0
set /a SCANNER_FAIL=0

REM ============================================================================
echo.
echo [sast] === 1/7 Bandit (Python security linter) ===
REM ============================================================================
"!PYTHON_EXE!" -m bandit -r bulk_downloader -ll -f json -o "%OUT%\bandit.json" 2>nul
set "BANDIT_JS_RC=!errorlevel!"
"!PYTHON_EXE!" -m bandit -r bulk_downloader -ll > "%OUT%\bandit.txt" 2>&1
set "BANDIT_TX_RC=!errorlevel!"
call :require_report bandit "%OUT%\bandit.json" !BANDIT_JS_RC! 1 "%OUT%\bandit.txt" !BANDIT_TX_RC! 1 "Issue:" "Code scanned:"
if not errorlevel 1 (
    findstr /c:"Issue:" "%OUT%\bandit.txt" >nul && (
        for /f %%C in ('findstr /c:"Issue:" "%OUT%\bandit.txt" ^| find /c "Issue:"') do (
            echo   bandit findings: %%C
            set /a FINDINGS+=%%C
        )
    ) || echo   bandit: clean
)

REM ============================================================================
echo.
echo [sast] === 2/7 Semgrep (security rule packs) ===
REM ============================================================================
REM Semgrep needs internet on first run to fetch rule packs. After that
REM rules are cached under %USERPROFILE%\.semgrep\.
"!PYTHON_EXE!" -m semgrep ^
    --config p/security-audit ^
    --config p/python ^
    --config p/javascript ^
    --config p/owasp-top-ten ^
    --severity ERROR --severity WARNING ^
    --json --output "%OUT%\semgrep.json" ^
    --error --quiet ^
    --exclude tests --exclude __pycache__ ^
    bulk_downloader 2>nul
set "SG_JS_RC=!errorlevel!"
"!PYTHON_EXE!" -m semgrep ^
    --config p/security-audit ^
    --config p/python ^
    --config p/javascript ^
    --severity ERROR --severity WARNING ^
    --quiet --exclude tests --exclude __pycache__ ^
    bulk_downloader > "%OUT%\semgrep.txt" 2>&1
set "SG_TX_RC=!errorlevel!"
call :require_report semgrep "%OUT%\semgrep.json" !SG_JS_RC! 1 "%OUT%\semgrep.txt" !SG_TX_RC! 0
if errorlevel 1 (
    REM counted in SCANNER_FAIL
) else if !SG_JS_RC! NEQ 0 (
    echo   semgrep: findings present ^(see semgrep.txt^)
    set /a FINDINGS+=1
) else (
    echo   semgrep: clean
)

REM ============================================================================
echo.
echo [sast] === 3/7 pip-audit (dependency CVE scan) ===
REM ============================================================================
"!PYTHON_EXE!" -m pip_audit -r requirements.txt --desc on ^
    --format json --output "%OUT%\pip-audit.json" 2>nul
set "PA_JS_RC=!errorlevel!"
"!PYTHON_EXE!" -m pip_audit -r requirements.txt --desc on > "%OUT%\pip-audit.txt" 2>&1
set "PA_TX_RC=!errorlevel!"
call :require_report pip-audit "%OUT%\pip-audit.json" !PA_JS_RC! 1 "%OUT%\pip-audit.txt" !PA_TX_RC! 1 "Found" "known vulnerabilit"
if not errorlevel 1 (
    findstr /c:"Found" "%OUT%\pip-audit.txt" >nul && (
        echo   pip-audit: vulnerabilities reported
        set /a FINDINGS+=1
    ) || echo   pip-audit: no known CVEs
)

REM ============================================================================
echo.
echo [sast] === 4/7 Ruff with security rule set (Bandit-equivalent + more) ===
REM ============================================================================
"!PYTHON_EXE!" -m ruff check --select S,B,E9,F63 ^
    --output-format json --exit-zero ^
    bulk_downloader > "%OUT%\ruff.json" 2>nul
set "RUFF_JS_RC=!errorlevel!"
"!PYTHON_EXE!" -m ruff check --select S,B,E9,F63 --exit-zero ^
    bulk_downloader > "%OUT%\ruff.txt" 2>&1
set "RUFF_TX_RC=!errorlevel!"
call :require_report ruff "%OUT%\ruff.json" !RUFF_JS_RC! 0 "%OUT%\ruff.txt" !RUFF_TX_RC! 0 "Found" "All checks passed"
if not errorlevel 1 (
    findstr /r "S[0-9]" "%OUT%\ruff.txt" >nul && (
        for /f %%C in ('findstr /r "S[0-9]" "%OUT%\ruff.txt" ^| find /c " S"') do (
            echo   ruff security findings: %%C
            set /a FINDINGS+=%%C
        )
    ) || echo   ruff: clean
)

REM ============================================================================
echo.
echo [sast] === 5/7 ESLint on app.js (security plugin) ===
REM ============================================================================
where npx >nul 2>&1
if errorlevel 1 (
    echo   eslint: SKIPPED ^(npx not on PATH; install Node.js to enable^) > "%OUT%\eslint.txt"
    echo   eslint: SKIPPED ^(Node.js not installed^)
) else if not exist "%REPO_DIR%\bulk_downloader\static\app.js" (
    echo   eslint: SKIPPED ^(bulk_downloader\static\app.js absent^) > "%OUT%\eslint.txt"
    echo   eslint: SKIPPED ^(lint target absent^)
) else (
    REM Install eslint locally on first run
    if not exist "%REPO_DIR%\node_modules\eslint" (
        echo   installing eslint + eslint-plugin-security...
        cd /d "%REPO_DIR%"
        call npm init -y >nul 2>&1
        call npm install --save-dev --silent ^
             eslint@^^8 eslint-plugin-security@^^2 2>"%OUT%\eslint-install.log"
    )
    REM Minimal config -- just security rules
    if not exist "%REPO_DIR%\.eslintrc.sast.json" (
        echo {                                            > "%REPO_DIR%\.eslintrc.sast.json"
        echo   "plugins": ["security"],                  >> "%REPO_DIR%\.eslintrc.sast.json"
        echo   "extends": ["plugin:security/recommended"],>> "%REPO_DIR%\.eslintrc.sast.json"
        echo   "parserOptions": {"ecmaVersion": 2022}    >> "%REPO_DIR%\.eslintrc.sast.json"
        echo }                                           >> "%REPO_DIR%\.eslintrc.sast.json"
    )
    call npx eslint --no-eslintrc -c .eslintrc.sast.json ^
         --format json -o "%OUT%\eslint.json" ^
         bulk_downloader\static\app.js 2>nul
    set "ESL_JS_RC=!errorlevel!"
    call npx eslint --no-eslintrc -c .eslintrc.sast.json ^
         bulk_downloader\static\app.js > "%OUT%\eslint.txt" 2>&1
    set "ESL_TX_RC=!errorlevel!"
    REM exit 0 prints nothing; lint errors exit 1 and end in the "N problems" summary
    call :require_report eslint "%OUT%\eslint.json" !ESL_JS_RC! 1 "%OUT%\eslint.txt" !ESL_TX_RC! 1 "problem"
    if not errorlevel 1 (
        findstr /r "problem" "%OUT%\eslint.txt" >nul && (
            echo   eslint: findings reported
            set /a FINDINGS+=1
        ) || echo   eslint: clean
    )
)

REM ============================================================================
echo.
echo [sast] === 6/7 Project preflight (cross-cutting integration) ===
REM ============================================================================
set "BD_DISABLE_KEEPALIVE=1"
"!PYTHON_EXE!" preflight.py > "%OUT%\preflight.txt" 2>&1
findstr /c:"NO CRITICAL FINDINGS" "%OUT%\preflight.txt" >nul && (
    echo   preflight: clean
) || (
    echo   preflight: findings present
    set /a FINDINGS+=1
)

REM ============================================================================
echo.
echo [sast] === 7/7 detect-secrets (committed-credential scanner) ===
REM ============================================================================
REM Sweep working tree for high-entropy strings / API-key shapes. First
REM run produces a baseline JSON; review with `python -m detect_secrets
REM audit tools\sast_results\detect-secrets.json` to mark known-safe
REM placeholders.
"!PYTHON_EXE!" -m detect_secrets scan ^
    --exclude-files "\.venv/|/__pycache__/|tools/sast_results/|node_modules/|\.git/|tests/fixtures/.*\.(png|jpg|mp4|webp|zip|gz)$" ^
    --exclude-secrets "EXAMPLE|placeholder|sample_|test_token_" ^
    > "%OUT%\detect-secrets.json" 2>&1
set "DS_JS_RC=!errorlevel!"
REM a scan without a "results" map is a failed scan, not zero secrets
"!PYTHON_EXE!" -c "import json,sys; d=json.load(open(r'%OUT%\detect-secrets.json')); print(sum(len(v) for v in d['results'].values()))" > "%OUT%\detect-secrets-count.txt" 2>nul
set "DS_CT_RC=!errorlevel!"
set "SECRET_COUNT="
set /p SECRET_COUNT=<"%OUT%\detect-secrets-count.txt"
call :require_report detect-secrets "%OUT%\detect-secrets.json" !DS_JS_RC! 0 "%OUT%\detect-secrets-count.txt" !DS_CT_RC! 0
if errorlevel 1 (
    REM counted in SCANNER_FAIL
) else if "!SECRET_COUNT!"=="0" (
    echo   detect-secrets: clean
) else (
    echo   detect-secrets: !SECRET_COUNT! candidate^(s^) -- audit with: python -m detect_secrets audit %OUT%\detect-secrets.json
    set /a FINDINGS+=!SECRET_COUNT!
)
del /q "%OUT%\detect-secrets-count.txt" 2>nul

REM ============================================================================
echo.
echo [sast] === Summary ===
REM ============================================================================
(
    echo Bulk Downloader SAST run -- %DATE% %TIME%
    echo ==========================================
    echo.
    for %%F in (bandit semgrep pip-audit ruff eslint preflight detect-secrets) do (
        echo --- %%F ---
        if exist "%OUT%\%%F.txt" (
            type "%OUT%\%%F.txt" | findstr /n "." | findstr /b "[1-9]:" | findstr /b "[1-9]:" | findstr /b "[1-9][0-9]\?:"
            echo.
        ) else (
            echo   ^(no output^)
        )
    )
    echo.
    echo Total findings categories with hits: !FINDINGS!
    echo Scanners missing or crashed: !SCANNER_FAIL!
) > "%OUT%\SUMMARY.txt"
echo Reports written to: %OUT%
echo.
type "%OUT%\SUMMARY.txt" | findstr /b "---"
echo.
if !SCANNER_FAIL! GTR 0 (
    echo [sast] !SCANNER_FAIL! scanner^(s^) missing or crashed -- results incomplete, NOT clean.
    popd >nul & exit /b 2
)
if !FINDINGS! GTR 0 (
    echo [sast] %FINDINGS% finding categories. Review tools\sast_results\.
    popd >nul & exit /b 1
) else (
    echo [sast] Clean -- all SAST tools reported no findings.
    popd >nul & exit /b 0
)

REM -- require_report <scanner> <report> <rc> <max rc> [<text> <text rc> <text max rc> [<hit marker> [<done marker>]]]
REM A scanner that is missing or crashed is a failure, never "clean":
REM errorlevel 1 + SCANNER_FAIL+=1 unless its JSON <report> parses and is not
REM an {"error": ...} object, and the run that wrote it exited in [0, <max rc>].
REM Where a second, text run feeds the verdict, that run must exit in
REM [0, <text max rc>] and its output must hold <hit marker> or <done marker>;
REM if EITHER run exited nonzero it must hold <hit marker> -- the string the
REM verdict counts as a finding -- so "clean" is only read after two exit-0 runs.
:require_report
"!PYTHON_EXE!" -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8-sig',errors='replace')); sys.exit(isinstance(d,dict) and 'error' in d)" "%~2" >nul 2>&1 || goto :report_missing
if %~3 LSS 0 goto :run_failed
if %~3 GTR %~4 goto :run_failed
if "%~5"=="" exit /b 0
if %~6 LSS 0 goto :run_failed
if %~6 GTR %~7 goto :run_failed
if not "%~8"=="" findstr /c:"%~8" "%~5" >nul 2>&1 && exit /b 0
if not "%~3%~6"=="00" if not "%~8"=="" goto :run_failed
if "%~9"=="" exit /b 0
findstr /c:"%~9" "%~5" >nul 2>&1 && exit /b 0
:run_failed
echo   %~1: FAILED ^(scanner missing or crashed -- exit %~3 / %~6, %~nx2 / %~nx5 incomplete^)
set /a SCANNER_FAIL+=1
exit /b 1
:report_missing
echo   %~1: FAILED ^(scanner missing or crashed -- %~nx2 absent, empty, unparseable or an error^)
set /a SCANNER_FAIL+=1
exit /b 1
