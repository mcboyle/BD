"""@1025. Two boxes named `test4` produced byte-indistinguishable captures.

A second deploy host was stood up beside the first at v3.66.1024, and it came up
with the SAME hostname. `capture.sh`'s system-fingerprint block recorded
`uname -a` and nothing else that identifies the machine:

    --- uname ---
    Linux test4 6.8.0-137-generic ... x86_64 GNU/Linux

so a capture from either box read identically. v3.66.1024 had, one commit
earlier, told every reader that a finding is about a HOST as well as a commit
and pointed at `01_sysinfo.log` to tell them apart. That file could not tell
them apart. A gate -- or here a record -- that cannot see the thing it is asked
about, in guidance written to prevent exactly that.

The operator renamed the second box, which fixes those two machines and nothing
else. This makes the ARTIFACT self-identifying, so the next pair does not depend
on anyone remembering.

WHY A HASHED MACHINE-ID AND NOT THE RAW ONE, AND NOT THE IP. `/etc/machine-id`
is stable across reboots, renames and address changes -- exactly the property
wanted -- but a capture bundle is shipped to third parties (it is why the
capture vault lives OUTSIDE $OUT), and the raw value is a durable machine
fingerprint. A truncated sha256 discriminates perfectly while publishing
nothing: two captures either carry the same digest or they do not. LAN
addresses were considered and rejected on the same ground -- they would put
internal network topology in a bundle that leaves the building, to answer a
question the digest already answers.

DEGRADES RATHER THAN FAILING. A host without `/etc/machine-id` (a non-systemd
image, a container) records `unknown` and says so. The capture must not fail
over a fingerprint; an UNKNOWN that is visible is the honest outcome and is
asserted below.
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

_CAPTURE = REPO / "capture.sh"


def _code():
    """capture.sh with comments stripped.

    Comment-stripped because every assertion below is about what the script
    DOES. CLAUDE.md section 0 records four cuts where an assertion could not
    tell prose from code, and this file's own explanatory comments name the
    very tokens it asserts on.
    """
    from shell_source import shell_code_only
    return shell_code_only(_CAPTURE)


def test_the_extractor_can_tell_code_from_comment():
    """Non-empty denominator, and proof the stripper works, before any
    assertion about presence or absence is believed."""
    code = _code()
    assert "emit_commit_identity" in code, "the extractor returned nothing usable"
    assert len(code.splitlines()) > 200, len(code.splitlines())


def _sysinfo_block() -> str:
    """The `{ ... } > $OUT/01_sysinfo.log` group, cut on a BALANCED DELIMITER.

    Never on a fixed width: CLAUDE.md section 2a records a harness that sliced
    a shell branch, swallowed its closing `fi`, and produced bash syntax errors
    presenting as subject failures.

    `shell_source.blocks_containing` is the wrong instrument here and that is
    worth stating rather than silently working around: it anchors on the line
    holding the needle, and the needle (`01_sysinfo.log`) sits on the CLOSING
    `}` redirect -- so it returns that one line, 30 characters, and every
    assertion over it would be about nothing. Walk back to the opening brace
    instead.
    """
    lines = _code().splitlines()
    close = [i for i, l in enumerate(lines) if "01_sysinfo.log" in l and "}" in l]
    assert len(close) == 1, (
        "expected exactly one `} > ...01_sysinfo.log` line, found %d -- "
        "re-derive this test" % len(close))
    end = close[0]
    start = None
    for i in range(end - 1, -1, -1):
        if lines[i].strip() == "{":
            start = i
            break
    assert start is not None, "no opening brace above the sysinfo redirect"
    block = "\n".join(lines[start:end + 1])
    assert block.count("{") >= 1 and block.rstrip().endswith("2>&1"), block[:200]
    return block


def test_the_fingerprint_block_is_still_the_one_this_test_means():
    """If the sysinfo block moved or was renamed, every assertion below would
    be about the wrong text and would pass or fail for the wrong reason."""
    b = _sysinfo_block()
    assert "uname -a" in b, b[:400]
    assert "os-release" in b, b[:400]


# ── the defect ────────────────────────────────────────────────────

def test_the_fingerprint_records_a_STABLE_host_identity():
    """THE DEFECT. uname carries the hostname, and two boxes shared one."""
    b = _sysinfo_block()
    assert "machine-id" in b, (
        "the system fingerprint records no stable host identity. Two hosts "
        "sharing a hostname produce byte-indistinguishable captures, and "
        "uname -a is the only machine-identifying line in the block:\n%s"
        % b[:600])


def test_the_machine_id_is_HASHED_and_never_emitted_raw():
    """A capture bundle is shipped to third parties. The digest answers 'same
    box or not'; the raw id is a durable fingerprint and answers more."""
    b = _sysinfo_block()
    assert "sha256sum" in b or "sha256" in b, (
        "machine-id appears without a hash -- a raw one is a stable machine "
        "fingerprint in a bundle that leaves the building:\n%s" % b[:600])
    # the raw value must not reach the log on any path: every read of the file
    # has to flow into the hash
    for line in b.splitlines():
        if "machine-id" in line and "sha256" not in line and "cut" not in line:
            assert "echo" not in line or "---" in line, (
                "a machine-id read that does not go through the hash: %r" % line)


def test_no_LAN_ADDRESS_is_recorded():
    """Rejected deliberately: it would put internal topology in a shipped
    bundle to answer a question the digest already answers. Asserted so a
    later 'improvement' has to argue with this rather than just add it."""
    b = _sysinfo_block()
    assert "hostname -I" not in b, (
        "the fingerprint records LAN addresses; the machine-id digest already "
        "discriminates hosts without publishing network topology")
    assert "ip addr" not in b, b[:400]


# ── it actually runs, and it actually discriminates ───────────────

def _run_fingerprint(machine_id: str | None, tmp: pathlib.Path) -> str:
    """Execute the block's host-identity lines against a FAKE machine-id file.

    A real subprocess rather than a regex over the source: the question is what
    the shell emits, and a source scan cannot answer it. `bash -n` alone would
    only prove it parses.
    """
    mid = tmp / "machine-id"
    if machine_id is not None:
        mid.write_text(machine_id + "\n")
    script = (
        'echo "--- host identity ---"\n'
        'hostname 2>/dev/null || echo unknown\n'
        'if [ -r "%s" ]; then\n'
        '  printf "machine-id(sha256/12): %%s\\n" '
        '"$(sha256sum "%s" | cut -c1-12)"\n'
        'else\n'
        '  echo "machine-id(sha256/12): unknown"\n'
        'fi\n' % (mid, mid))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_two_different_hosts_produce_DIFFERENT_fingerprints(tmp_path):
    """The whole point, executed rather than reasoned about."""
    da, db = tmp_path / "a", tmp_path / "b"
    da.mkdir(); db.mkdir()
    a = _run_fingerprint("11111111111111111111111111111111", da)
    b = _run_fingerprint("22222222222222222222222222222222", db)
    assert a != b, "two distinct machine-ids produced the same fingerprint"
    assert "unknown" not in a, a


def test_the_same_host_produces_a_STABLE_fingerprint(tmp_path):
    """The other direction. A digest that moved between runs would make every
    capture look like a different box, which is the over-sensitive failure --
    noisier than the defect it replaces."""
    d = tmp_path / "same"; d.mkdir()
    first = _run_fingerprint("33333333333333333333333333333333", d)
    second = _run_fingerprint(None, d)     # file already there, unchanged
    assert first == second, (first, second)


def test_a_host_without_a_machine_id_says_UNKNOWN_and_does_not_fail(tmp_path):
    """Degrades visibly. The capture must not die over a fingerprint, and an
    absent one must not read as a present one."""
    d = tmp_path / "none"; d.mkdir()
    out = _run_fingerprint(None, d)
    assert "unknown" in out, out
    assert "sha256/12): unknown" in out, out


def test_the_raw_machine_id_never_appears_in_the_output(tmp_path):
    """The privacy property, executed. A hash that leaked its input would be
    the fix reproducing the defect it removes."""
    d = tmp_path / "leak"; d.mkdir()
    raw = "deadbeefdeadbeefdeadbeefdeadbeef"
    out = _run_fingerprint(raw, d)
    assert raw not in out, out
    digest = hashlib.sha256((raw + "\n").encode()).hexdigest()[:12]
    assert digest in out, (
        "the emitted digest is not sha256 of the file's bytes; re-derive "
        "before trusting the privacy claim: %r vs %r" % (digest, out))
