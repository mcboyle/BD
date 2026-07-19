#!/usr/bin/env python3
"""test_bd_kb_sync -- standalone self-test for the static-KB sync engine.

Stdlib-only; builds a throwaway static set in a tempdir and exercises every mode
(seed / check / verify / stage / diff) plus the drift path. Run directly:
    python3 test_bd_kb_sync.py
Exit 0 = all pass; nonzero + a FAIL line otherwise. Ships beside bd-kb-sync so the
tool can't silently regress.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SYNC = os.path.join(HERE, "bd-kb-sync")
PASS, FAIL = 0, 0


def run(*args):
    return subprocess.run([sys.executable, SYNC, *args], capture_output=True, text=True)


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS {name}")
    else:
        FAIL += 1; print(f"  FAIL {name}")


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p) or root, exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def main():
    if not os.path.exists(SYNC):
        print(f"FAIL: bd-kb-sync not found at {SYNC}"); return 1

    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out:
        # HERMETIC: every check/verify passes an explicit, test-controlled --state so the
        # verdict never leans on an ambient version pack _default_state() might reach
        # (KBSYNC-STAGE @756: check/stage are now pin-aware). STATE lives OUTSIDE root.
        state = os.path.join(out, "STATE.json")
        with open(state, "w") as f:
            f.write("{}")  # exists but UNPINNED -> a genuine 'unknown' pin state

        def pin():
            return run("pin", root, "--state", state, "--version", "v9.9.9")

        write(root, "A.md", "alpha\n")
        write(root, "B.md", "beta\n")
        write(root, "sub/C.md", "gamma\n")

        r = run("seed", root, "--version", "v9.9.9")
        check("seed exits 0", r.returncode == 0)
        man = os.path.join(root, "STATIC_KB_MANIFEST.json")
        check("manifest written", os.path.exists(man))
        doc = json.load(open(man))
        check("manifest tracks 3 files (excludes itself)", doc.get("file_count") == 3)
        check("manifest does not track itself", "STATIC_KB_MANIFEST.json" not in doc["files"])
        check("manifest tracks nested file", "sub/C.md" in doc["files"] or os.path.join("sub", "C.md") in doc["files"])

        # UNKNOWN pin (state exists but is unpinned): freshness is UNVERIFIABLE, a caveat,
        # NOT a re-paste. check must stay a drift gate -> LOCAL-MATCH, exit 0.
        ru = run("check", root, "--state", state)
        check("check unknown-pin -> exit 0 (not a false re-paste)", ru.returncode == 0)
        check("check unknown-pin says LOCAL-MATCH/UNVERIFIED", "LOCAL-MATCH" in ru.stdout or "UNVERIFIED" in ru.stdout)

        check("pin exits 0", pin().returncode == 0)

        rc0 = run("check", root, "--state", state)
        check("check clean (pinned) -> exit 0", rc0.returncode == 0)
        check("check clean says IN-SYNC", "IN-SYNC" in rc0.stdout)
        check("verify clean (pinned) -> exit 0", run("verify", root, "--state", state).returncode == 0)

        # drift: edit a file -> drift trumps the pin
        write(root, "A.md", "alpha EDITED\n")
        rc = run("check", root, "--state", state)
        check("check after edit -> exit 1 (drift)", rc.returncode == 1)
        check("check names the changed file", "A.md" in rc.stdout)
        check("verify after edit -> exit 1", run("verify", root, "--state", state).returncode == 1)

        # stage: builds zip + flag + RESEEDS the manifest to the edited state; the pin still
        # points at the PRE-edit manifest -> the paste is now behind.
        rs = run("stage", root, "--out", out, "--state", state, "--version", "v9.9.9")
        check("stage exits 0", rs.returncode == 0)
        zips = [f for f in os.listdir(out) if f.endswith(".zip")]
        check("stage produced a zip", len(zips) == 1)
        flag = os.path.join(out, "PROJECT_KNOWLEDGE_UPDATE.md")
        check("stage produced the update flag", os.path.exists(flag))
        check("flag says re-paste", "Re-paste" in open(flag).read())
        check("flag lists the changed file", "A.md" in open(flag).read())

        # THE FIX (was the false-green): after the reseed root==manifest, but the manifest
        # has moved PAST the pinned/pasted canon -> RE-PASTE OWED, not "in sync". The old
        # test asserted exit 0 here; that assertion encoded the bug.
        rps = run("check", root, "--state", state)
        check("check after stage reseed -> exit 1 (re-paste owed, NOT false-green)", rps.returncode == 1)
        check("check after reseed says RE-PASTE OWED", "RE-PASTE OWED" in rps.stdout)

        # operator re-pastes + re-pins -> canon moves to the new manifest -> clean again
        check("re-pin exits 0", pin().returncode == 0)
        check("check after re-pin -> exit 0", run("check", root, "--state", state).returncode == 0)

        # new file -> added drift (drift trumps pin)
        write(root, "D.md", "delta\n")
        rc2 = run("check", root, "--state", state)
        check("check after new file -> exit 1", rc2.returncode == 1)
        check("check reports ADDED", "ADDED" in rc2.stdout and "D.md" in rc2.stdout)

        # diff freshness: stale dir vs a newer manifest (pin-independent)
        run("seed", root, "--version", "v9.9.9")
        newer = os.path.join(out, "newer_manifest.json")
        with open(newer, "w") as f:
            json.dump(json.load(open(os.path.join(root, "STATIC_KB_MANIFEST.json"))), f)
        os.remove(os.path.join(root, "D.md"))
        rd = run("diff", root, newer)
        check("diff stale dir vs newer manifest -> exit 1", rd.returncode == 1)
        check("diff reports the missing/older file", "D.md" in rd.stdout)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
