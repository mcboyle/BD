"""O1807 R8: tools/sast.bat must not report "clean" for a scanner that never ran.

Each scanner's verdict came only from grepping its text output (bandit
``Issue:``, pip-audit ``Found``, ruff ``S[0-9]``), so a missing or crashed
module -- whose output is just "No module named ..." -- matched nothing and
printed "clean"; detect-secrets defaulted an unparseable scan to 0 secrets,
and ESLint "linted" an absent app.js clean. The script then exited 0 having
scanned nothing. Every scanner now has to leave its report behind
(``:require_report``) before its verdict is read; a missing report is counted
in SCANNER_FAIL and the run exits 2. Bandit, pip-audit, ruff and ESLint run
twice and the verdict greps the second (text) run, so that run is gated too:
its exit code must be in range and its output must hold the tool's own
completion marker, or a crash there (traceback, network error) reads as clean.

The JSON run's exit code was overwritten by the text run on the next line,
and a report only had to be non-empty, so a JSON run that exited 2 having
written ``{"error": ...}`` -- or a detect-secrets scan with no ``results`` map
-- still printed clean. Every run's exit code now has its own variable, the
report must parse and not be an error object, and when either run exits
nonzero the text output must hold the very string the verdict counts as a
finding: "clean" is only ever read after two exit-0 runs.

No cmd.exe exists on the test hosts, so these tests pin the control flow of
the batch file rather than executing it.
"""

import re
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

SAST_BAT = Path(__file__).resolve().parents[1] / "tools" / "sast.bat"

# scanner -> (the "nothing found" echo that must only follow a written report,
#             how the :require_report result gates it)
# "guard":  `if not errorlevel 1 (` wraps the whole verdict.
# "branch": `if errorlevel 1 (` holds only a REM; the verdict is in the `) else` arms.
GATED = {
    "bandit": ("echo   bandit: clean", "guard"),
    "semgrep": ("echo   semgrep: clean", "branch"),
    "pip-audit": ("echo   pip-audit: no known CVEs", "guard"),
    "ruff": ("echo   ruff: clean", "guard"),
    "eslint": ("echo   eslint: clean", "guard"),
    "detect-secrets": ("echo   detect-secrets: clean", "branch"),
}


def _lines():
    return SAST_BAT.read_bytes().decode("ascii").replace("\r\n", "\n").split("\n")


def _sections():
    """Scanner section body lines keyed by the scanner named in its header."""
    out, name = {}, None
    for line in _lines():
        m = re.match(r"echo \[sast\] === \d/7 (\S+)", line)
        if m:
            name = m.group(1).lower()
            out[name] = []
        elif line.startswith("echo [sast] === Summary"):
            name = None
        elif name:
            out[name].append(line)
    return out


def _assert_gated(scanner):
    clean, kind = GATED[scanner]
    raw = _sections()[scanner]
    body = [ln.strip() for ln in raw]
    call = [i for i, ln in enumerate(body)
            if ln.startswith(f"call :require_report {scanner} ")]
    assert call, (f"O1807-R8 {scanner}: no :require_report before its verdict -- "
                  f"a missing {scanner} module still prints clean")
    report = re.match(rf'call :require_report {re.escape(scanner)} "([^"]+)"', body[call[0]]).group(1)
    writes = "\n".join(body[:call[0]])
    assert re.search(rf'(?:-o|--output|>)\s*"{re.escape(report)}"', writes), (
        f"O1807-R8 {scanner}: required report {report} is not one the scanner writes")
    clean_at = [i for i, ln in enumerate(body) if ln.endswith(clean)]
    assert clean_at and clean_at[0] > call[0], f"O1807-R8 {scanner}: clean echo not after the gate"
    # The gate must read errorlevel straight from the call, with the exact polarity:
    # inverted, the verdict is skipped whenever the report exists (findings dropped).
    gate, clean_at = call[0] + 1, clean_at[0]
    if kind == "guard":
        assert body[gate] == "if not errorlevel 1 (", (
            f"O1807-R8 {scanner}: gate polarity {body[gate]!r}, want 'if not errorlevel 1 ('")
        indent = raw[gate][:len(raw[gate]) - len(body[gate])]
        close = next(i for i in range(gate + 1, len(raw)) if raw[i] == indent + ")")
        assert gate < clean_at < close, f"O1807-R8 {scanner}: clean echo outside the report guard"
    else:
        assert body[gate] == "if errorlevel 1 (", (
            f"O1807-R8 {scanner}: gate polarity {body[gate]!r}, want 'if errorlevel 1 ('")
        assert body[gate + 1].startswith("REM ") and body[gate + 2].startswith(") else "), (
            f"O1807-R8 {scanner}: the missing-report arm must hold only a REM")
        assert clean_at > gate + 2, f"O1807-R8 {scanner}: clean echo in the missing-report arm"


def test_every_scanner_verdict_requires_its_report():
    sections = _sections()
    assert set(GATED) <= set(sections), sorted(sections)
    for scanner in ("bandit", "semgrep", "pip-audit", "ruff", "detect-secrets"):
        _assert_gated(scanner)


def test_eslint_crash_is_not_clean():
    # npx present but eslint crashed / failed to install: no eslint.json, so the
    # "problem"-less error text in eslint.txt must not read as clean.
    _assert_gated("eslint")


# scanner -> (JSON report, its run's rc variable, max rc,
#             second report the verdict reads, its run's rc variable, max rc,
#             [hit marker -- the verdict's finding string, done marker])
RUNS = {
    "bandit": ("bandit.json", "BANDIT_JS_RC", "1", "bandit.txt", "BANDIT_TX_RC", "1",
               ['"Issue:"', '"Code scanned:"']),
    "semgrep": ("semgrep.json", "SG_JS_RC", "1", "semgrep.txt", "SG_TX_RC", "0", []),
    "pip-audit": ("pip-audit.json", "PA_JS_RC", "1", "pip-audit.txt", "PA_TX_RC", "1",
                  ['"Found"', '"known vulnerabilit"']),
    "ruff": ("ruff.json", "RUFF_JS_RC", "0", "ruff.txt", "RUFF_TX_RC", "0",
             ['"Found"', '"All checks passed"']),
    "eslint": ("eslint.json", "ESL_JS_RC", "1", "eslint.txt", "ESL_TX_RC", "1", ['"problem"']),
    "detect-secrets": ("detect-secrets.json", "DS_JS_RC", "0",
                       "detect-secrets-count.txt", "DS_CT_RC", "0", []),
}
# scanners whose verdict greps the second report rather than reading the JSON run's rc
TEXT_VERDICT = ("bandit", "pip-audit", "ruff", "eslint")


def _rc_captured_after_write(body, report, var, scanner, call):
    """The line right after the (possibly ^-continued) scanner run writing <report> sets <var>."""
    write = [i for i, ln in enumerate(body[:call])
             if re.search(rf'(?:-o|--output|>)\s*"%OUT%\\{re.escape(report)}"', ln)
             and not ln.startswith("echo ")]
    assert write, f"O1807-R8 {scanner}: no run writes {report} before the gate"
    end = write[-1]
    while body[end].endswith("^"):
        end += 1
    assert body[end + 1] == f'set "{var}=!errorlevel!"', (
        f"O1807-R8 {scanner}: {var} is not the exit code of the run that writes {report} "
        f"-- got {body[end + 1]!r}")
    return end + 1


@pytest.mark.parametrize("scanner", sorted(RUNS))
def test_each_run_exit_code_is_kept_and_gated(scanner):
    # cx3 r3 F1: the JSON run's exit code was overwritten by the text run, so a JSON run
    # that exited 2 with {"error": ...} still read clean. Each run keeps its own rc and
    # both reach the gate.
    js, js_var, js_max, txt, tx_var, tx_max, markers = RUNS[scanner]
    body = [ln.strip() for ln in _sections()[scanner]]
    call = next(i for i, ln in enumerate(body) if ln.startswith(f"call :require_report {scanner} "))
    args = re.findall(r'"[^"]*"|\S+', body[call])[3:]
    want = [f'"%OUT%\\{js}"', f"!{js_var}!", js_max, f'"%OUT%\\{txt}"', f"!{tx_var}!", tx_max, *markers]
    assert args == want, f"O1807-R8 {scanner}: gate args {args}, want {want}"
    js_set = _rc_captured_after_write(body, js, js_var, scanner, call)
    tx_set = _rc_captured_after_write(body, txt, tx_var, scanner, call)
    assert js_set < tx_set < call, f"O1807-R8 {scanner}: runs/captures out of order"
    # a variable shared across runs or scanners could carry a stale exit code into the gate
    sets = [ln for ln in _lines() if re.match(rf'\s*set "({js_var}|{tx_var})=', ln)]
    assert len(sets) == 2, f"O1807-R8 {scanner}: rc variables set {len(sets)} times, want 2"


@pytest.mark.parametrize("scanner", TEXT_VERDICT)
def test_clean_only_after_two_exit_zero_runs(scanner):
    # :require_report demands <hit marker> whenever either run exited nonzero; that only
    # keeps a failed run out of the "clean" arm if <hit marker> is exactly what the verdict
    # counts as a finding (or no nonzero exit is allowed at all).
    js, js_var, js_max, txt, tx_var, tx_max, markers = RUNS[scanner]
    body = [ln.strip() for ln in _sections()[scanner]]
    call = next(i for i, ln in enumerate(body) if ln.startswith(f"call :require_report {scanner} "))
    reads = [m for ln in body[call + 1:] for m in re.findall(r'findstr /\w:?\s*"[^"]*" "([^"]+)"', ln)]
    assert reads and set(reads) == {f"%OUT%\\{txt}"}, (
        f"O1807-R8 {scanner}: verdict reads {sorted(set(reads))}, want only the gated {txt}")
    verdict = {m for ln in body[call + 1:] for m in re.findall(r'findstr /\w:?\s*"([^"]*)" "', ln)}
    if (js_max, tx_max) != ("0", "0"):
        assert verdict == {markers[0].strip('"')}, (
            f"O1807-R8 {scanner}: hit marker {markers[0]} is not the verdict's finding string {verdict}"
            " -- a nonzero run without findings would still print clean")


def test_semgrep_findings_come_from_the_json_run_rc():
    body = [ln.strip() for ln in _sections()["semgrep"]]
    assert ") else if !SG_JS_RC! NEQ 0 (" in body
    assert not any("SG_EXIT" in ln for ln in body)


REQUIRE_REPORT = [
    '"!PYTHON_EXE!" -c "import json,sys; d=json.load(open(sys.argv[1],encoding=\'utf-8-sig\','
    "errors='replace')); sys.exit(isinstance(d,dict) and 'error' in d)\" \"%~2\" >nul 2>&1"
    " || goto :report_missing",
    "if %~3 LSS 0 goto :run_failed",
    "if %~3 GTR %~4 goto :run_failed",
    'if "%~5"=="" exit /b 0',
    "if %~6 LSS 0 goto :run_failed",
    "if %~6 GTR %~7 goto :run_failed",
    'if not "%~8"=="" findstr /c:"%~8" "%~5" >nul 2>&1 && exit /b 0',
    'if not "%~3%~6"=="00" if not "%~8"=="" goto :run_failed',
    'if "%~9"=="" exit /b 0',
    'findstr /c:"%~9" "%~5" >nul 2>&1 && exit /b 0',
    ":run_failed",
    "echo   %~1: FAILED ^(scanner missing or crashed -- exit %~3 / %~6, %~nx2 / %~nx5 incomplete^)",
    "set /a SCANNER_FAIL+=1",
    "exit /b 1",
    ":report_missing",
    "echo   %~1: FAILED ^(scanner missing or crashed -- %~nx2 absent, empty, unparseable or an error^)",
    "set /a SCANNER_FAIL+=1",
    "exit /b 1",
]


def test_require_report_fails_closed():
    lines = _lines()
    label = lines.index(":require_report")
    assert label > max(i for i, ln in enumerate(lines) if ln.strip() == "popd >nul & exit /b 0"), (
        "O1807-R8: :require_report is reachable by fallthrough")
    body = [ln for ln in lines[label + 1:] if ln.strip()]
    # Success only when the JSON report parses and is not an {"error": ...} object, its run
    # exited in [0, max rc], and -- if a second report is named -- that run exited in
    # [0, its max rc] and printed the hit marker (required if either run exited nonzero)
    # or the done marker; anything else counts a failure.
    assert body == REQUIRE_REPORT, "\n".join(body)


def test_scanner_failure_exits_nonzero_before_clean_exit():
    lines = _lines()
    assert "set /a SCANNER_FAIL=0" in lines
    gate = lines.index("if !SCANNER_FAIL! GTR 0 (")
    assert lines[gate + 2].strip() == "popd >nul & exit /b 2", lines[gate:gate + 3]
    clean_exit = max(i for i, ln in enumerate(lines) if ln.strip() == "popd >nul & exit /b 0")
    assert gate < clean_exit, "O1807-R8: scanner-failure exit comes after the clean exit"


def test_detect_secrets_unparseable_scan_is_not_zero():
    body = _sections()["detect-secrets"]
    assert 'if not defined SECRET_COUNT set "SECRET_COUNT=0"' not in body, (
        "O1807-R8 detect-secrets: an unparseable scan still defaults to 0 secrets")
    setp = next(i for i, ln in enumerate(body) if ln.startswith("set /p SECRET_COUNT="))
    assert body[setp - 1] == 'set "SECRET_COUNT="', "inherited SECRET_COUNT would mask a failed scan"
    count = [ln for ln in body if "detect-secrets-count.txt" in ln and "json.load" in ln]
    assert len(count) == 1 and "d['results'].values()" in count[0], (
        "O1807-R8 detect-secrets: a scan with no 'results' map must fail the count, not read as {}")
    assert ".get('results'" not in "\n".join(body)


def test_eslint_absent_target_is_skipped_not_clean():
    body = _sections()["eslint"]
    guard = next((i for i, ln in enumerate(body)
                  if re.match(r'\) else if not exist "%REPO_DIR%\\bulk_downloader\\static\\app\.js" \($', ln)),
                 None)
    assert guard is not None, "O1807-R8 eslint: absent app.js is linted and reported clean"
    assert "SKIPPED" in body[guard + 1]
    assert guard < next(i for i, ln in enumerate(body) if "npx eslint" in ln)


def test_crlf_preserved():
    raw = SAST_BAT.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "cmd.exe labels need CRLF line endings"
