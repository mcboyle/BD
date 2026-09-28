"""Every .gitleaks-baseline.json entry carries its classification and its reason.

The scheduled CI secret scan walks full history; the baseline is what keeps it
green, so the baseline is where a real secret would be hidden. A4: a baseline
entry is allowed only with a per-row justification -- "class" (FIXTURE,
NONSECRET or ROTATED; LIVE is never baselined) and a non-empty "reason" -- and it never
carries a secret value (CI scans with --redact, so entries are redacted too).
tools/secret_scan_triage.py writes the entries; this gate keeps them honest.
"""
from __future__ import annotations

BD_GATE_SCOPE = "repo-wide"

import base64
import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".gitleaks-baseline.json"
TOOL = ROOT / "tools" / "secret_scan_triage.py"
ALLOWED = {"FIXTURE", "NONSECRET", "ROTATED"}

sys.path.insert(0, str(ROOT / "tools"))
import secret_scan_triage as triage


def baseline_problems(entries: list[dict]) -> list[str]:
    problems = []
    for i, e in enumerate(entries):
        where = f"entry {i} {e.get('Fingerprint', '<no fingerprint>')}"
        if e.get("class") not in ALLOWED:
            problems.append(f"{where}: class {e.get('class')!r} not in {sorted(ALLOWED)}")
        if not str(e.get("reason", "")).strip():
            problems.append(f"{where}: no reason")
        if e.get("Secret") != "REDACTED":
            problems.append(f"{where}: Secret is not redacted")
        if not e.get("Commit"):
            problems.append(f"{where}: no Commit (a commit-less entry never matches a git-mode scan)")
    return problems


def test_every_baseline_entry_has_class_and_reason():
    entries = json.loads(BASELINE.read_text())
    assert entries, "empty baseline: nothing measured"
    problems = baseline_problems(entries)
    assert not problems, "baseline entries without justification:\n" + "\n".join(problems)


def test_reasonless_entry_is_caught():
    entry = {"Fingerprint": "c:f:generic-api-key:1", "Secret": "REDACTED", "Commit": "c", "class": "FIXTURE"}
    assert baseline_problems([entry]) == ["entry 0 c:f:generic-api-key:1: no reason"]
    assert baseline_problems([dict(entry, reason="  ")]) == ["entry 0 c:f:generic-api-key:1: no reason"]
    assert baseline_problems([dict(entry, reason="r", **{"class": "LIVE"})])[0].endswith(
        "class 'LIVE' not in ['FIXTURE', 'NONSECRET', 'ROTATED']")
    assert baseline_problems([dict(entry, reason="r", Secret="x")]) == [
        "entry 0 c:f:generic-api-key:1: Secret is not redacted"]
    assert baseline_problems([dict(entry, reason="r")]) == []


def _jwt(sig: str) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc({'sub': 'u'})}.{sig}"


def test_classify_evidence():
    assert triage.classify(_jwt("abc"), {})[0] == "FIXTURE"
    assert "43" in triage.classify(_jwt("abc"), {})[1]
    assert triage.classify("sk-row553-synthetic-key", {})[0] == "FIXTURE"
    assert triage.classify(base64.b64encode(b"fakefakefakefake").decode(), {})[0] == "FIXTURE"
    assert triage.classify("api/jsonapi/path", {})[0] == "NONSECRET"


def test_filler_inside_a_real_key_is_not_evidence():
    # Security lens REFUTE (gen 1): 'aaaa'/'abcd'/'1234' matched as substrings, so a
    # real key that happens to contain them was classed FIXTURE and baselined.
    # Real-shaped keys are generated here, filler spliced in; all must stay LIVE.
    for _ in range(200):
        body = secrets.token_urlsafe(30)
        for value in (body[:20] + "AAAA" + body[20:36],        # 40-char key with AAAA
                      "6Le" + body[:25] + "AAAAA" + body[25:32],  # reCAPTCHA-format key
                      secrets.token_hex(6) + "abcd",            # short key + abcd
                      secrets.token_urlsafe(12) + "1234"):      # 16-char key + 1234
            cls, why = triage.classify(value, {})
            assert cls == "LIVE", f"real-shaped key with filler classed {cls}: {why}"


def test_marker_must_dominate_the_value():
    assert triage.classify("row553-synthetic-key-a", {})[0] == "FIXTURE"
    assert triage.classify("sk-abcdefgh-12345678", {})[0] == "FIXTURE"
    # the same marker words wrapped around a random body are not evidence
    assert triage.classify("row553-" + secrets.token_urlsafe(18) + "-synthetic", {})[0] == "LIVE"


def test_unexplained_values_are_live_never_fixture():
    # Values generated here, never literals: a real-shaped token with no marker
    # and a well-formed HS256 JWT (43-char MAC) must both come out LIVE.
    for value in (secrets.token_urlsafe(24), _jwt(secrets.token_urlsafe(32))):
        cls, _ = triage.classify(value, {})
        assert cls == "LIVE", f"unexplained value classified {cls}"


def test_rotation_record_makes_rotated():
    value = secrets.token_urlsafe(24)
    digest = hashlib.sha256(value.encode()).hexdigest()
    assert triage.classify(value, {digest: "vault rotation 2026-09-28 ticket X"}) == (
        "ROTATED", "rotation record: vault rotation 2026-09-28 ticket X")


def _finding(value: str, n: int) -> dict:
    return {"Fingerprint": f"c{n}:f.py:generic-api-key:{n}", "Commit": f"c{n}", "File": "f.py",
            "RuleID": "generic-api-key", "Secret": value, "Match": f'key = "{value}"'}


def _run(tmp_path, findings):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(findings))
    tsv, base = tmp_path / "t.tsv", tmp_path / "b.json"
    res = subprocess.run([sys.executable, str(TOOL), str(report), "--tsv", str(tsv), "--baseline", str(base)],
                         capture_output=True, text=True, timeout=60, check=False)
    return res, tsv, base


def test_cli_live_rows_are_not_baselined_and_exit_1(tmp_path):
    live = secrets.token_urlsafe(24)
    res, tsv, base = _run(tmp_path, [_finding(live, 1), _finding("row553-synthetic-key-a", 2)])
    assert res.returncode == 1, res.stdout + res.stderr
    assert "FIXTURE=1 NONSECRET=0 ROTATED=0 LIVE=1 total=2" in res.stdout
    entries = json.loads(base.read_text())
    assert [e["Fingerprint"] for e in entries] == ["c2:f.py:generic-api-key:2"]
    assert entries[0]["Secret"] == "REDACTED" and entries[0]["Match"] == 'key = "REDACTED"'
    assert not baseline_problems(entries)
    written = tsv.read_text() + base.read_text()
    assert live not in written and "row553-synthetic-key-a" not in written


def test_cli_redacted_report_is_could_not_look(tmp_path):
    f = _finding("x", 1)
    f["Secret"] = "REDACTED"
    res, _, base = _run(tmp_path, [f])
    assert res.returncode == 2 and "COULD-NOT-LOOK" in res.stderr
    assert not base.exists()
