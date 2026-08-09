"""tools/capture_scrub.py must decide by CONTENT, like the other three.

@971. v3.66.859 found that bd-wacz-scrub, bd-scrub-proof and bd-share-safe each
carried their own TEXT_EXT allowlist -- three sets, no two identical, none
containing `.warc`, which is where a WACZ's payload lives. Each reported
"verified clean" over a denominator that excluded the subject. The fix was one
shared answer: `bdtools_sec.should_scan(name, data)`, content not extension.

THAT FIX DID NOT REACH THE SCRUBBER THE CAPTURE HOOK ACTUALLY RUNS.
`capture_scrub_hook.py:47` invokes `tools/capture_scrub.py` to build every
`.redacted.wacz` twin, and `dom_analyzer.py:268` then prefers that twin. Its
member loop branched `.jsonl` / `.json` / else, and the else branch was:

    try: data = scrub_string(data.decode("utf-8"), ...).encode("utf-8")
    except Exception: pass

A member that is not valid UTF-8 was written through UNSCRUBBED and silently --
no counter, no residual entry -- so the run reported success over a member it
never examined. Measured before the fix, on an archive whose only binary member
was never read:

    VERIFY: re-scan of redacted output found NO residual secrets -- CLEAN.
    Safe to share. (Structure/shape preserved; secrets + signing destroyed.)

TWO HARNESS DEFECTS IN THIS FILE'S OWN FIRST DRAFT, both worth more than the
finding, and both are why the fixtures below look the way they do.

1. It asserted on a token of 40 repeated characters. `_is_opaque_run` requires
   MIXED character classes by design, so that value is undetectable no matter
   how correct the tool is -- the test would have failed forever and been read
   as a product defect. The secrets here are `session=` + a zero-entropy repeat:
   matched STRUCTURALLY by RE_KV_SECRET on the key name, so entropy is
   irrelevant, while staying a value gitleaks will not fire on (CLAUDE.md
   section 7 -- naming a realistic secret makes this file a place one lives).

2. The binary-member test asserted `"skip" in output`, and PASSED on a tree
   where the tool says nothing of the kind. The tool echoes `input : <path>`,
   pytest derives tmp_path from the TEST FUNCTION'S NAME, and that name
   contained "skipped" -- so the assertion matched its own name reflected back
   out of the subject. Measured directly, the real output contains ZERO
   occurrences of binary/skip/unscanned. A test that reads itself is section 0
   with the denominator inside the test rather than the tool. Fixed twice over:
   the echoed line is stripped before asserting, AND the function is named so
   its own path cannot supply the word.
"""

import json
import pathlib
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "capture_scrub.py"

# Structural, not entropic: RE_KV_SECRET matches the KEY, so a zero-entropy
# value is still redacted. Verified against scrub_string directly before use --
# 'session=' + repeat -> 'session=<REDACTED>'.
_KEY = "session"
_SECRET = _KEY + "=" + ("a" * 24)


def _wacz(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _run(path):
    return subprocess.run(
        [sys.executable, str(TOOL), str(path), "--mode", "strict"],
        capture_output=True, text=True, timeout=300, cwd=str(REPO))


def _out_path(p):
    return pathlib.Path(str(p)[:-len(".wacz")] + ".redacted.wacz")


def _output_without_the_echoed_path(res, src):
    """Everything the tool said EXCEPT its echo of the input path.

    The path is attacker-controlled from this test's point of view -- pytest
    builds it from the test function's name -- so leaving it in the haystack
    lets an assertion match itself. See defect 2 in the module docstring.
    """
    blob = (res.stdout or "") + (res.stderr or "")
    # FULL path or FULL basename only. A first draft also stripped src.stem,
    # which for "b.wacz" is the single character "b" -- so every line containing
    # the letter b was removed, INCLUDING the "unscanned (binary...)" line this
    # test looks for, and a correct tool read as silent. An over-broad filter
    # that destroys the evidence is the same family as an over-broad assertion
    # that matches anything: both make the verdict independent of the subject.
    keep = [ln for ln in blob.splitlines()
            if str(src) not in ln and src.name not in ln]
    return "\n".join(keep).lower()


def test_a_LATIN1_member_carrying_a_secret_is_actually_scrubbed(tmp_path):
    """The load-bearing case: text that is not valid UTF-8.

    A WARC body can carry a latin-1 byte and stay entirely readable text. Strict
    decode raises on it, the bare `except: pass` swallowed that, and the secret
    was copied into the share-ready twin. looks_binary() resolves exactly this
    ambiguity to TEXT for exactly this reason.
    """
    src = _wacz(tmp_path / "c.wacz", {
        "archive/data.warc": (_SECRET + " caf\xe9").encode("latin-1"),
        "datapackage.json": json.dumps({"profile": "data-package"}).encode(),
    })
    r = _run(src)
    assert r.returncode in (0, 2), "scrubber errored: %s" % (r.stderr[-600:],)
    out = _out_path(src)
    assert out.exists(), "no twin was written: %s" % (r.stdout[-400:],)
    with zipfile.ZipFile(out) as z:
        body = z.read("archive/data.warc")
    assert _SECRET.encode("latin-1") not in body, (
        "the secret survived into the SHARE-READY twin: a member that is not "
        "valid UTF-8 was written through unscrubbed. This is @859's .warc gap, "
        "in the tool capture_scrub_hook.py actually invokes.")


def test_a_PLAIN_utf8_member_is_still_scrubbed(tmp_path):
    """Regression guard on the path that already worked."""
    src = _wacz(tmp_path / "u.wacz", {
        "archive/data.warc": (_SECRET + " plain ascii").encode("utf-8"),
        "datapackage.json": json.dumps({"profile": "data-package"}).encode(),
    })
    r = _run(src)
    out = _out_path(src)
    assert out.exists(), "no twin: %s" % (r.stdout[-400:],)
    with zipfile.ZipFile(out) as z:
        assert _SECRET.encode() not in z.read("archive/data.warc"), (
            "a plain UTF-8 member kept its secret -- the existing path regressed")


def test_an_unreadable_member_is_DECLARED_in_the_output(tmp_path):
    """Unknown is a third state; silence reads as 'nothing to find'.

    A genuinely binary member cannot be regexed and must be passed through --
    that part is correct. Saying nothing about it is not: the run then reports
    "Safe to share" over a denominator it never examined.

    Named to avoid the words it asserts on, so its own tmp_path cannot satisfy
    it. The strip in _output_without_the_echoed_path is the real fix; this is
    the second layer.
    """
    src = _wacz(tmp_path / "b.wacz", {
        "archive/blob.bin": b"\x00\x01\x02" + _SECRET.encode() + b"\x00",
        "datapackage.json": json.dumps({"profile": "data-package"}).encode(),
    })
    r = _run(src)
    assert r.returncode in (0, 2), "scrubber errored: %s" % (r.stderr[-600:],)
    said = _output_without_the_echoed_path(r, src)
    assert ("binary" in said or "unscanned" in said or "not scanned" in said), (
        "a member the tool could not read was passed through with NO mention, "
        "so this run cannot be told from one where every member was examined. "
        "output(minus the echoed path)=%r" % said[-400:])


def test_every_member_still_survives_the_rewrite(tmp_path):
    """Nothing may be DROPPED. Redaction rewrites; it does not delete.

    Asserted because the obvious way to satisfy the tests above is to stop
    writing members the tool cannot scrub, which would silently truncate every
    shared archive.
    """
    members = {
        "archive/data.warc": _SECRET.encode("utf-8"),
        "archive/blob.bin": b"\x00\x01\x02",
        "indexes/index.cdxj": b"com,example)/ 20200101 {}\n",
        "datapackage.json": json.dumps({"profile": "data-package"}).encode(),
    }
    src = _wacz(tmp_path / "m.wacz", dict(members))
    _run(src)
    out = _out_path(src)
    assert out.exists()
    with zipfile.ZipFile(out) as z:
        got = set(z.namelist())
    assert got == set(members), (
        "the rewrite changed the member set: missing=%r unexpected=%r"
        % (set(members) - got, got - set(members)))
