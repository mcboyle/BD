"""Row 1033: the failure advisor must not be confidently wrong, and the CLI
must be able to answer a machine.

MEASURED ON THE BASE (bc1544b75, bulk_downloader/doctor.py:302 and
bdctl.py:cmd_doctor), two defects in the same surface:

1. diagnose_failure() matched its signatures as bare substrings, so any text
   containing "auth" inside a longer word was classified "Authentication /
   session expiry" with confidence "high". Measured on the base:
     'GET /author/feed returned HTTP 500'       -> high, Authentication
     'Upload failed: authored content rejected' -> high, Authentication
   The module's own docstring promises a generic answer "never a confident
   wrong answer". A confident wrong cause sends the operator to re-login
   instead of reading the HTTP 500 in front of them.

2. `bdctl doctor --diagnose <text>` printed three prose lines and exited. It
   never called _maybe_json, so --json was silently ignored on exactly the
   sub-path an automation calls, and the advice carried no rule identity and
   no command to run.

WHAT IS DELIBERATELY NOT DONE: no signature is deleted and no confidence is
lowered to buy a green -- test_the_known_signatures_still_classify re-runs the
corpus the base already gets right, so a fix that merely stopped matching
fails here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_BDCTL = _REPO / "bdctl.py"

# Text an operator really sees, none of which is an auth or content failure.
_NOT_AUTH = (
    "GET /author/feed returned HTTP 500",
    "Upload failed: authored content rejected",
    "author.js:41 TypeError: undefined is not a function",
    "author profile page",
    "authored by admin",
    "authorship confirmed",
    "Certificate Authority invalid",
    "undergone maintenance",
    "The file was grossly corrupted",
    "running headlessly and seamlessly",
)

# The corpus the base already classifies, plus live product shapes and stems,
# kept as the positive control: the fix must keep every one of these matching,
# with the exact rule id and cause (catching mutant M2).
_KNOWN = (
    # The 9 positive controls the base gets right
    ("HTTP 429 Too Many Requests", "rate-limit", "Rate limiting"),
    ("rate limit exceeded, slow down", "rate-limit", "Rate limiting"),
    ("Turnstile captcha challenge detected", "captcha", "Captcha challenge"),
    ("session expired, please sign in", "auth", "Authentication / session expiry"),
    ("HTTP 403 Forbidden", "auth", "Authentication / session expiry"),
    ("waiting for selector .btn timed out", "selector-drift", "Selector drift"),
    ("ENOSPC: no space left on device", "disk", "Disk pressure"),
    ("connection reset by peer", "network", "Network / connectivity"),
    ("HTTP 404 Not Found", "content-removed", "Content removed"),
    # Live product text and runner shapes (test2 / runner.py)
    ("Auto-teach: take over to teach download selectors. Click 'Take over'...", "selector-drift", "Selector drift"),
    ("solver error: TimeoutError: Timeout 30000ms exceeded.", "network", "Network / connectivity"),
    ("browser_unavailable:TimeoutError", "network", "Network / connectivity"),
    ("search_raised:TimeoutError", "network", "Network / connectivity"),
    ("capability_probe_failed:TimeoutError", "network", "Network / connectivity"),
    ("Verify failed to run: TimeoutError", "network", "Network / connectivity"),
    # Product stem, plural, and CamelCase exception shapes
    ("Authentication failed", "auth", "Authentication / session expiry"),
    ("authentication required", "auth", "Authentication / session expiry"),
    ("Session authentication expired", "auth", "Authentication / session expiry"),
    ("could not authenticate", "auth", "Authentication / session expiry"),
    ("authorization header missing", "auth", "Authentication / session expiry"),
    ("AuthenticationError: invalid credentials", "auth", "Authentication / session expiry"),
    ("OAuth token rejected", "auth", "Authentication / session expiry"),
    ("OAuthError: token revoked", "auth", "Authentication / session expiry"),
    ("LoginError: bad credentials", "auth", "Authentication / session expiry"),
    ("LoginRequired: please log in", "auth", "Authentication / session expiry"),
    ("TimeoutError", "network", "Network / connectivity"),
    ("asyncio.TimeoutError", "network", "Network / connectivity"),
    ("httpx.ConnectTimeout", "network", "Network / connectivity"),
    ("Read timeout", "network", "Network / connectivity"),
    ("NetworkError when attempting to fetch resource", "network", "Network / connectivity"),
    ("SSLError: certificate verify failed", "network", "Network / connectivity"),
    ("requests.exceptions.SSLError(MaxRetryError)", "network", "Network / connectivity"),
    ("OpenSSL error: wrong version number", "network", "Network / connectivity"),
    ("DNSError", "network", "Network / connectivity"),
    ("diskfull", "disk", "Disk pressure"),
    ("captchas everywhere", "captcha", "Captcha challenge"),
)


def _doctor():
    from bulk_downloader import doctor
    return doctor


def test_a_signature_inside_a_longer_word_is_not_a_confident_diagnosis():
    """The defect: 'author' is not 'auth', 'undergone' is not 'gone'."""
    doctor = _doctor()
    wrong = []
    for text in _NOT_AUTH:
        d = doctor.diagnose_failure(text)
        if d["matched"] or d["rule"] is not None:
            wrong.append(f"{text!r} -> matched={d['matched']} rule={d['rule']} cause={d['cause']}")
    assert not wrong, (
        "text containing a signature inside a longer unrelated word was matched: "
        + "; ".join(wrong))


def test_the_known_signatures_still_classify():
    """POSITIVE CONTROL. A matcher that stopped matching would pass the test
    above for the wrong reason; this is the corpus the base gets right,
    including product shapes, stems, and CamelCase exceptions."""
    doctor = _doctor()
    for error, expected_rule, expected_cause in _KNOWN:
        d = doctor.diagnose_failure(error)
        assert d["matched"] is True, f"{error!r} stopped matching: {d}"
        assert d["rule"] == expected_rule, (
            f"{error!r} rule mismatch: got {d['rule']!r}, expected {expected_rule!r}")
        assert d["cause"] == expected_cause, (
            f"{error!r} cause mismatch: got {d['cause']!r}, expected {expected_cause!r}")


def test_every_diagnosis_names_the_rule_that_fired():
    """Structured half: prose is not an identity. A stable rule id is what an
    automation (or a later regression) can key on."""
    doctor = _doctor()
    matched = doctor.diagnose_failure("HTTP 429 Too Many Requests")
    assert matched["rule"] == "rate-limit", matched
    unmatched = doctor.diagnose_failure("some totally novel error blah blah")
    assert unmatched["matched"] is False
    assert unmatched["rule"] is None, (
        "an unmatched diagnosis named a rule; the id must be absent, not a "
        "default that reads as a match")
    ids = {rule for _sigs, rule, *_rest in doctor._FAILURE_SIGNATURES}
    assert len(ids) == len(doctor._FAILURE_SIGNATURES), "rule ids collide"


def test_the_advisor_offers_a_machine_readable_remedy():
    """Self-healing half: a suggestion an operator must read and retype is not
    an advisor. NEGATIVE CONTROL in the same test: no match, no remedy."""
    doctor = _doctor()
    d = doctor.diagnose_failure("session expired, please sign in")
    remedy = d["remedy"]
    assert remedy and remedy["action"] and remedy["command"], d
    assert remedy["command"].startswith("bdctl "), remedy
    assert doctor.diagnose_failure("the quick brown fox")["remedy"] is None


def _run_bdctl(argv, *, diagnosis):
    """Run the real CLI against a scripted /api/doctor/diagnose."""
    env = dict(os.environ)
    env["BD_ROW1033_FAKE_DIAGNOSIS"] = json.dumps(diagnosis)
    env.pop("BD_TOKEN", None)
    stub = (
        "import json, os, sys\n"
        "sys.argv = ['bdctl'] + %r\n"
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('bdctl', %r)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "mod._request = lambda *a, **k: {'ok': True, 'diagnosis': "
        "json.loads(os.environ['BD_ROW1033_FAKE_DIAGNOSIS'])}\n"
        "sys.exit(mod.main())\n" % (list(argv), str(_BDCTL))
    )
    proc = subprocess.run([sys.executable, "-c", stub], capture_output=True,
                          text=True, timeout=60, cwd=str(_REPO), env=env)
    return proc.returncode, proc.stdout, proc.stderr


_DIAGNOSIS = {
    "matched": True,
    "cause": "Authentication / session expiry",
    "suggestion": "Re-login for this site.",
    "confidence": "high",
    "rule": "auth",
    "remedy": {"action": "relogin", "command": "bdctl login <site>"},
}


def test_bdctl_doctor_diagnose_answers_json_when_asked():
    rc, out, err = _run_bdctl(["doctor", "--diagnose", "HTTP 403", "--json"],
                              diagnosis=_DIAGNOSIS)
    assert out.strip(), f"--diagnose --json printed nothing (rc={rc}) {err}"
    payload = json.loads(out)
    assert payload["rule"] == "auth"
    assert payload["remedy"]["command"] == "bdctl login <site>"
    assert payload["confidence"] == "high"
    assert rc == 0


def test_bdctl_doctor_diagnose_prose_path_is_unchanged_and_names_the_remedy():
    """REGRESSION CONTROL for the human path: the three lines the base prints
    stay, and the remedy command joins them rather than replacing them."""
    rc, out, _err = _run_bdctl(["doctor", "--diagnose", "HTTP 403"],
                               diagnosis=_DIAGNOSIS)
    assert "cause:" in out and "confidence:" in out and "suggestion:" in out
    assert "bdctl login <site>" in out
    assert rc == 0
