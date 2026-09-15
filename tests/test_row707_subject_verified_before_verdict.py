"""Row 707 -- MUTATION-TREE-SUBSTITUTION-GOES-UNDETECTED.

bd-mutate writes a mutant to the subject, runs the band, then grades the band's
result.  The grade is evidence about *whatever content the band imported*.  On a
shared work tree a second run -- or any other tooling -- can substitute the
subject *during* the band, in place, on the same inode.  When the substitute is
the original source the band goes green, and before this gate bd-mutate attributed
that green to the mutant it had written: a CAUGHT mutant was silently graded
ESCAPED, and nothing said so.

``_PinnedSubject.require_attached`` already catches a *detached* inode (a rename
or unlink-and-replace drops ``st_nlink`` to 0).  It does NOT catch an in-place
rewrite of the same inode -- the receipt still matches.  So the fix is two
checks before each verdict: ``require_attached`` for the inode, and the content
sha against the exact mutant that was written.  A mismatch is UNKNOWN, because a
verdict about substituted content is a verdict about a mutant that never ran.

The substitution here is injected deterministically: the band opens a FIFO
window only while the mutant is on disk, and this test does the in-place rewrite
inside that window.  That inlines the race a concurrent process would create on
the shared work tree without depending on timing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"
_BAND = "tests/test_m.py"
_CATCHER = f"{_BAND}::test_behavior"
_ORIGINAL = "VALUE = 1\n"

# The band imports the subject by executing its current bytes, so an in-place
# substitution reaches the verdict.  It opens the FIFO window ONLY when the
# mutant is on disk, so the clean baseline run never blocks.
_BAND_SRC = (
    "from pathlib import Path\n"
    "ROOT = Path(__file__).resolve().parent.parent\n"
    "READY = ROOT / 'run-ready'\n"
    "RELEASE = ROOT / 'run-release'\n"
    "SUBJECT = ROOT / 'm.py'\n"
    "def test_behavior():\n"
    "    text = SUBJECT.read_text(encoding='utf-8')\n"
    "    if 'VALUE = 2' in text and READY.exists():\n"
    "        with READY.open('w', encoding='utf-8') as signal:\n"
    "            signal.write('in-band\\n')\n"
    "        with RELEASE.open('r', encoding='utf-8') as release:\n"
    "            release.read()\n"
    "        text = SUBJECT.read_text(encoding='utf-8')\n"
    "    namespace = {}\n"
    "    exec(text, namespace)\n"
    "    assert namespace['VALUE'] == 1\n"
)


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "detached-work"
    (work / "tests").mkdir(parents=True)
    (work / "m.py").write_text(_ORIGINAL, encoding="utf-8")
    (work / _BAND).write_text(_BAND_SRC, encoding="utf-8")
    spec = work / "spec.json"
    spec.write_text(json.dumps({
        "schema": "bd-mutate-spec/1",
        "subject": "row 707 subject verified before verdict",
        "band": [_BAND],
        "mutants": [{
            "label": "row 707 value mutation",
            "file": "m.py",
            "old": "VALUE = 1",
            "new": "VALUE = 2",
            "direction": "regression",
            "catcher": _CATCHER,
        }],
    }), encoding="utf-8")
    return work, spec


def _argv(work: Path, spec: Path) -> list[str]:
    return [sys.executable, str(_TOOL), "--spec", str(spec),
            "--work", str(work), "--json"]


def _payload(stdout: str) -> dict:
    return json.loads(stdout[stdout.index("{"):])


def test_substitution_under_the_band_before_the_verdict_is_unknown(tmp_path):
    work, spec = _tree(tmp_path)
    ready = work / "run-ready"
    release = work / "run-release"
    os.mkfifo(ready)
    os.mkfifo(release)

    proc = subprocess.Popen(
        _argv(work, spec), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # A dead first run must not hang the test: read the ready signal on a
    # thread and fail loud if it never comes (positive control on the window).
    signal: dict[str, str] = {}

    def _await_band() -> None:
        with ready.open("r", encoding="utf-8") as handle:
            signal["value"] = handle.read()

    # daemon: if the mutant kills the band before it opens the FIFO, the read
    # here blocks forever; a daemon thread lets the interpreter exit anyway so
    # this test fails loud (mutant CAUGHT) instead of hanging to a timeout.
    waiter = threading.Thread(target=_await_band, daemon=True)
    waiter.start()
    waiter.join(timeout=60)
    if waiter.is_alive():
        proc.kill()
        out, err = proc.communicate(timeout=30)
        raise AssertionError(
            "the band never reached its mutant window -- the substitution was "
            "never injected, so this run proves nothing:\n" + out + err)
    assert signal["value"] == "in-band\n", (
        "shape control: the band signalled from inside the mutant window")

    same_inode_before = os.stat(work / "m.py").st_ino
    with open(work / "m.py", "r+", encoding="utf-8") as handle:
        handle.seek(0)
        handle.truncate()
        handle.write(_ORIGINAL)
    same_inode_after = os.stat(work / "m.py").st_ino
    assert same_inode_before == same_inode_after, (
        "shape control: the substitution is in place on the same inode, which "
        "is exactly the case require_attached alone cannot see")

    with release.open("w", encoding="utf-8") as handle:
        handle.write("release\n")
    out, err = proc.communicate(timeout=60)

    assert proc.returncode == 2, out + err
    payload = _payload(out)
    assert len(payload["rows"]) == 1, payload
    row = payload["rows"][0]
    assert row["verdict"] == "UNKNOWN", (out + err)
    assert "before grading" in row["why"], row["why"]
    # The subject is left as it was found.
    assert (work / "m.py").read_text(encoding="utf-8") == _ORIGINAL


def test_unsubstituted_mutant_is_caught(tmp_path):
    """Negative control: with no substitution the verdict is the true CAUGHT,
    proving the guard does not fire on an undisturbed subject."""
    work, spec = _tree(tmp_path)
    # No FIFOs: the band's window never opens, nothing substitutes the subject.
    result = subprocess.run(
        _argv(work, spec), capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = _payload(result.stdout)
    assert len(payload["rows"]) == 1, payload
    assert payload["rows"][0]["verdict"] == "CAUGHT", result.stdout
    assert (work / "m.py").read_text(encoding="utf-8") == _ORIGINAL
