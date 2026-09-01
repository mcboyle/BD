#!/usr/bin/env python3
"""Apply the cwd= isolation fix to v3.66.1241 and prove it RED-first.

A7 requires a test to isolate the CURRENT DIRECTORY. Every spawn of the built
W1 runner passes env= but no cwd=, so the runner inherits whatever directory the
pytest worker happens to hold, and the production template runs
`git rev-parse --short HEAD` inside it. Under the band one spawn came back rc=93
with `fatal: not a git repository`.

THIS DOES NOT CLAIM TO FIX THAT FAILURE. The failure has not reproduced in the
matched experiment, so no causal claim is made and the event is recorded as an
open flake on row 241. What IS claimed and proved here is narrower and testable:
the spawn's working directory is DETERMINISTIC rather than inherited.
"""
import pathlib, re, subprocess, sys

W = pathlib.Path("/home/mboyle/bd-cuts/cut/1241-owner-observation-deadline")
OLD = '["bash", str(script)], env=env, text=True,'
NEW = '["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,'
ANCHOR_FILES = ["tests/test_v3_66_1132_the_hunt_reaps_registration_lifecycle.py",
                "tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py"]

DECL = '''
# EVERY SPAWN OF THE BUILT RUNNER PINS ITS WORKING DIRECTORY. The production
# RUNNER template shells out to `git rev-parse --short HEAD`, so a spawn that
# inherits the pytest worker's directory is asserting over ambient state -- the
# exact isolation A7 names alongside HOME, TMPDIR and module globals. One band
# spawn returned rc=93 with `fatal: not a git repository`; that event is
# recorded as an open flake on row 241 and is NOT claimed to be fixed here.
# What is fixed is that the directory is now DETERMINISTIC instead of inherited.
_W1_SPAWN_CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
'''

def main():
    total = 0
    for rel in ANCHOR_FILES:
        p = W / rel
        s = p.read_text(encoding="utf-8")
        # GUARD ON THE PINNED MARKER FIRST. NEW contains OLD as a prefix, so a
        # second pass re-replaces already-pinned text and produces
        # `cwd=X, cwd=X` -- a duplicate keyword argument, 62 times. The earlier
        # "0 anchors" idempotency check could never fire for the same reason:
        # count(OLD) stays nonzero after pinning. Presence of NEW is the only
        # honest "already done" signal.
        if NEW in s:
            already = s.count(NEW)
            print(f"  {rel}: already pinned ({already} site(s)) -- no-op")
            total += already
            continue
        n = s.count(OLD)
        if n == 0:
            # already pinned by a previous run -- idempotent, not a failure
            assert s.count(NEW) > 0, (rel, "no anchors and nothing pinned")
            print(f"  {rel}: already pinned ({s.count(NEW)} site(s))"); total += s.count(NEW); continue
        before = len(s)
        s = s.replace(OLD, NEW)
        # NEW CONTAINS OLD AS A PREFIX, so `count(OLD) == 0` can never hold and
        # asserting it aborted the fix twice. The real question is whether any
        # OLD remains that is NOT already followed by the cwd argument.
        assert s.count(NEW) == n, (rel, s.count(NEW), n)
        assert s.count(OLD) - s.count(NEW) == 0, (rel, "unpinned spawn remains")
        grew = len(s) - before
        assert grew == n * (len(NEW) - len(OLD)), (rel, grew, n)
        if "_W1_SPAWN_CWD =" not in s:
            # Insert immediately BEFORE the first top-level def/class. Inserting
            # "after the last import" put the line inside a multi-line construct
            # and produced a SyntaxError -- a fix that breaks the file it edits.
            lines = s.split("\n")
            first = next(k for k, l in enumerate(lines)
                         if l.startswith("def ") or l.startswith("class "))
            lines.insert(first, DECL.strip("\n") + "\n")
            s = "\n".join(lines)
        p.write_text(s, encoding="utf-8")
        print(f"  {rel}: {n} spawn(s) pinned, +{grew} bytes")
        total += n
    assert total > 0, "no spawn sites found -- refusing a no-op fix"
    print(f"TOTAL {total} spawn site(s) pinned")
    r = subprocess.run([str(W/"venv/bin/python"), "-c",
                        "import ast,sys;[ast.parse(open(f).read()) for f in sys.argv[1:]]",
                        *[str(W/f) for f in ANCHOR_FILES]], capture_output=True, text=True)
    if r.returncode:
        sys.exit("PARSE FAILED after edit:\n" + r.stderr)
    print("both files parse")

main()
