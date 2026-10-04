"""O1819 R16 (SECURITY T2): shell_input appended repr(text) of every input chunk
to shell_audit.log, so a password typed at a sudo/ssh prompt landed there in
plaintext. The audit must record metadata only (length, keyed digest, line-break
count) and never the typed text.

No pty is spawned: a fake session is registered and the PTY write is stubbed, so
only the audit path under test runs.
"""
import hashlib

import pytest

BD_GATE_SCOPE = "module"

_SECRET = "Hunter2-O1819!"
_SID = "o1819r16"


@pytest.fixture
def audit_log(monkeypatch, tmp_path):
    from tools import cockpit_shell as sh
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path))
    monkeypatch.setattr(sh, "shell_enabled", lambda: True)
    monkeypatch.setattr(sh, "_write", lambda sess, payload: None)
    monkeypatch.setitem(sh._SESSIONS, _SID, {"alive": True, "last": 0.0})
    return sh, tmp_path / "shell_audit.log"


def _lines(log):
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if f"\t{_SID}\t" in ln]


def test_password_at_prompt_never_lands_in_audit_log(audit_log):
    sh, log = audit_log
    assert sh.shell_input(_SID, _SECRET + "\r") == {"ok": True}
    text = log.read_text(encoding="utf-8")
    lines = _lines(log)
    # positive control: the probe sees the record for this input
    assert len(lines) == 1, "O1819-AUDIT-NO-RECORD: " + repr(text)
    assert _SECRET not in text, "O1819-RAW-INPUT-IN-AUDIT: " + repr(text)
    assert f"len={len(_SECRET) + 1}" in lines[0] and "breaks=1" in lines[0], lines[0]


def test_digest_is_keyed_not_plain_sha256(audit_log):
    sh, log = audit_log
    sh.shell_input(_SID, _SECRET)
    plain = hashlib.sha256(_SECRET.encode()).hexdigest()
    assert plain[:12] not in log.read_text(encoding="utf-8"), "O1819-UNKEYED-DIGEST"


@pytest.mark.parametrize("chunk,breaks", [("ls\r\n", 1), ("a\nb\n", 2), ("x", 0)])
def test_command_boundaries_counted(audit_log, chunk, breaks):
    sh, log = audit_log
    sh.shell_input(_SID, chunk)
    assert f"breaks={breaks}" in _lines(log)[0], _lines(log)
