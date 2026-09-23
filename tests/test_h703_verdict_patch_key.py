"""H703-verdict-patch-key: the verdict key is the PLAIN-diff PATCH-SHA256 (refute N5-A E1/E2).

POLICY-0010 s2 + PM decision (PLAN-2040/ORDERS-0041): PATCH-SHA256 = sha256 of the plain
``git diff --cached <declared base>`` (no --full-index). DONE.md, verdict files, bd-assign-lens.sh
(has_verdict) and bd-boardgate.sh compare THAT field; a --full-index digest may be recorded as
PATCH-SHA256-FULL for humans but is never compared.

E1  has_verdict chained ``A && a || B && b || C && c``. Bash && and || have equal precedence and
    associate left, so a failing later arm vetoed an earlier match: a verdict whose PATCH-SHA256
    equals DONE's (or the cut's current digest) with a stale TREE read as "no verdict" unless the
    review copy's digest matched too. Each arm is braced now.
E2  the H703 producers used --full-index: has_verdict (cut and review digests), bd-boardgate.sh
    ACT4, the write hook's instruction to lenses, and bd-pm-census.py. They use the plain diff now.

Harness cut (O1045/O1066): the tools live outside this repository. The directory under test is
selected ONLY by BD_H703_VERDICT_PATCH_KEY_CANDIDATE (holding bd-assign-lens.sh, bd-boardgate.sh,
bd-verdict-write-hook.sh and bd-pm-census.py); without it the module skips, and a supplied
directory that lacks a tool fails. Every fixture is a scratch git repository with hermetic config
(rule 41); nothing here reads or writes fleet state.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_H703_VERDICT_PATCH_KEY_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

TOOLS = ("bd-assign-lens.sh", "bd-boardgate.sh", "bd-verdict-write-hook.sh", "bd-pm-census.py")
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
STALE_TREE = "0123456789abcdef0123456789abcdef01234567"
CLAUDE_SEAT = "bd-review-correctness-N0-A"
VERDICT_NAME = f"VERDICT-correctness-{CLAUDE_SEAT}.md"
WRITTEN_AT = "2026-09-23T02:00:00Z"
DRY_RUN_SEAM = 'if [ "${1:-}" = --dry-run ]; then'
APP_LINES = "".join(f"L{i:02d}\n" for i in range(1, 21))


def _tool(name: str) -> Path:
    path = Path(CANDIDATE) / name
    assert path.is_file(), f"candidate tool missing: {path}"
    return path


def _env(tmp: Path, **extra: str) -> dict[str, str]:
    """Hermetic child environment: no inherited GIT_*/BD_* (a hook-run pytest exports GIT_DIR)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BD_"))}
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env.update(HOME=str(home), TMPDIR=str(tmp), LC_ALL="C",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    env.update(extra)
    return env


def _git(tmp: Path, repo: Path, *args: str) -> bytes:
    res = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-C", str(repo), *args],
        env=_env(tmp), capture_output=True, timeout=60, check=False,
    )
    assert res.returncode == 0, f"git {' '.join(args)}: {res.stderr.decode(errors='replace')}"
    return res.stdout


def _source(tmp: Path) -> dict:
    """B1; B2 = three lines inserted above the patched line (hunk offsets move); B3 = unrelated file."""
    src = tmp / "src"
    src.mkdir()
    _git(tmp, src, "init", "-q", "-b", "main")
    (src / "app.txt").write_text(APP_LINES)
    (src / "other.txt").write_text("o1\n")
    _git(tmp, src, "add", "app.txt", "other.txt")
    _git(tmp, src, "commit", "-q", "-m", "B1")
    b1 = _git(tmp, src, "rev-parse", "HEAD").decode().strip()
    (src / "app.txt").write_text("H1\nH2\nH3\n" + APP_LINES)
    _git(tmp, src, "commit", "-q", "-am", "B2")
    b2 = _git(tmp, src, "rev-parse", "HEAD").decode().strip()
    (src / "other.txt").write_text("o2\n")
    _git(tmp, src, "commit", "-q", "-am", "B3")
    b3 = _git(tmp, src, "rev-parse", "HEAD").decode().strip()
    return {"src": src, "b1": b1, "b2": b2, "b3": b3}


def _staged(tmp: Path, src: Path, dest: Path, base: str, change: bool = True) -> Path:
    """A scratch clone detached at ``base`` with the cut's one-line change staged."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp, tmp, "clone", "-q", str(src), str(dest))
    _git(tmp, dest, "checkout", "-q", "--detach", base)
    if change:
        app = dest / "app.txt"
        app.write_text(app.read_text().replace("L15\n", "L15 changed by the cut\n"))
        _git(tmp, dest, "add", "app.txt")
    return dest


def _case(tmp: Path, name: str, src: dict, change: bool = True) -> tuple[Path, Path, Path]:
    """Cut clone at B1 and its review copy (<root>/<cut>-local) rebased onto B2, same change staged."""
    cut = _staged(tmp, src["src"], tmp / name / "cuts" / f"fx-{name}", src["b1"], change)
    review_root = tmp / name / "review"
    review = _staged(tmp, src["src"], review_root / f"fx-{name}-local", src["b2"], change)
    return cut, review_root, review


def _digest(tmp: Path, repo: Path, *diff_args: str) -> str:
    return hashlib.sha256(_git(tmp, repo, "diff", "--cached", *diff_args)).hexdigest()


def _done(cut: Path, *lines: str) -> Path:
    path = cut / "DONE.md"
    path.write_text("VERDICT: PATCH\n# fixture record\n" + "".join(f"{line}\n" for line in lines))
    return path


def _verdict(review: Path, *lines: str, verdict: str = "BOARD", seat: str = CLAUDE_SEAT) -> Path:
    rdir = review / ".review"
    rdir.mkdir(parents=True, exist_ok=True)
    path = rdir / f"VERDICT-correctness-{seat}.md"
    body = [f"VERDICT: {verdict}", *lines, f"SEAT: {seat}", f"WRITTEN-AT: {WRITTEN_AT}"]
    path.write_text("\n".join(body) + "\n")
    return path


def _clear_verdicts(review: Path) -> None:
    for old in (review / ".review").glob("VERDICT-*.md"):
        old.unlink()


# ---------------------------------------------------------------- bd-assign-lens.sh has_verdict
def _has_verdict(tmp: Path, cut: Path, review_root: Path) -> int:
    """Run the candidate's own has_verdict (every definition before the --dry-run seam) on the fixture."""
    text = _tool("bd-assign-lens.sh").read_text(encoding="utf-8")
    assert text.count(DRY_RUN_SEAM) == 1, "bd-assign-lens.sh: --dry-run seam not found exactly once"
    helpers = text.split(DRY_RUN_SEAM)[0]
    assert "\nhas_verdict(){" in helpers, "has_verdict() is not defined before the --dry-run seam"
    harness = tmp / "has_verdict_harness.sh"
    harness.write_text(helpers + '\nhas_verdict "$1" "$2"\necho "HAS_VERDICT_RC=$?"\n', encoding="utf-8")
    res = subprocess.run(
        ["bash", str(harness), cut.name, str(cut / "DONE.md")],
        env=_env(tmp, BD_ASSIGN_LENS_REVIEW_ROOT=str(review_root),
                 BD_ASSIGN_LENS_REPO=str(tmp / "no-repo")),
        capture_output=True, text=True, timeout=120, check=False,
    )
    rcs = re.findall(r"^HAS_VERDICT_RC=(\d+)$", res.stdout, re.MULTILINE)
    assert len(rcs) == 1, f"harness never reached has_verdict: rc={res.returncode}\n{res.stdout}\n{res.stderr}"
    return int(rcs[0])


def test_candidate_dir_supplies_every_tool():
    root = Path(CANDIDATE)
    assert root.is_dir(), f"BD_H703_VERDICT_PATCH_KEY_CANDIDATE is not a directory: {root}"
    for name in TOOLS:
        path = root / name
        assert path.is_file(), f"candidate tool missing: {path}"
        if name.endswith(".sh"):
            assert os.access(path, os.X_OK), f"candidate tool not executable: {path}"


def test_e1_done_sha_arm_alone_makes_the_verdict_current(tmp_path):
    """E1: DONE names no 40-hex BASE (so no cut digest is measured) and the verdict carries DONE's
    PATCH-SHA256 under a stale TREE; the review copy was rebased, so its digest differs. The DONE
    arm alone makes the verdict CURRENT (H651/H703 design); the unbraced chain returned 1."""
    src = _source(tmp_path)
    cut, review_root, review = _case(tmp_path, "done-alone", src)
    s1, s2 = _digest(tmp_path, cut, src["b1"]), _digest(tmp_path, review)
    assert EMPTY_SHA not in (s1, s2) and s1 != s2, "fixture: the rebase must move the hunk offsets"
    _done(cut, f"BASE: origin/main {src['b1'][:8]}", f"PATCH-SHA256: {s1}")
    _verdict(review, f"TREE: {STALE_TREE}", f"PATCH-SHA256: {s1}")
    assert _has_verdict(tmp_path, cut, review_root) == 0


def test_e1_verdict_copying_done_sha_survives_a_stale_tree(tmp_path):
    """E1 (N5-A caseB): DONE carries BASE + its plain PATCH-SHA256; the verdict copies that sha, its
    TREE is stale and the rebased review copy hashes differently -> CURRENT (rc 0), not rc 1."""
    src = _source(tmp_path)
    cut, review_root, review = _case(tmp_path, "done-sha", src)
    s1, s2 = _digest(tmp_path, cut, src["b1"]), _digest(tmp_path, review)
    assert EMPTY_SHA not in (s1, s2) and s1 != s2
    cuttree = _git(tmp_path, cut, "write-tree").decode().strip()
    _done(cut, f"BASE: {src['b1']}", f"INDEX TREE: {cuttree}", f"PATCH-SHA256: {s1}")
    _verdict(review, f"TREE: {STALE_TREE}", f"PATCH-SHA256: {s1}")
    assert _has_verdict(tmp_path, cut, review_root) == 0


def test_e2_cut_plain_digest_makes_the_verdict_current(tmp_path):
    """E2 (with E1): DONE records no digest; the verdict's PATCH-SHA256 is the plain
    `git diff --cached <declared base>` of the cut. has_verdict must measure that same producer."""
    src = _source(tmp_path)
    cut, review_root, review = _case(tmp_path, "cut-plain", src)
    s1, s2 = _digest(tmp_path, cut, src["b1"]), _digest(tmp_path, review)
    full1 = _digest(tmp_path, cut, "--full-index", src["b1"])
    assert EMPTY_SHA not in (s1, s2) and len({s1, s2, full1}) == 3
    _done(cut, f"BASE: {src['b1']}")
    _verdict(review, f"TREE: {STALE_TREE}", f"PATCH-SHA256: {s1}")
    assert _has_verdict(tmp_path, cut, review_root) == 0


def test_e2_review_plain_digest_survives_a_reprep_onto_a_new_base(tmp_path):
    """E2: the lens recorded the review copy's plain digest and write-tree; the copy was then
    re-prepped onto B3 with the identical patch (new tree, same plain digest) -> still CURRENT."""
    src = _source(tmp_path)
    cut, review_root, review = _case(tmp_path, "reprep", src)
    s1 = _digest(tmp_path, cut, src["b1"])
    s2, t2 = _digest(tmp_path, review), _git(tmp_path, review, "write-tree").decode().strip()
    _git(tmp_path, review, "checkout", "-q", "--detach", src["b3"])
    s3, t3 = _digest(tmp_path, review), _git(tmp_path, review, "write-tree").decode().strip()
    assert s3 == s2 and t3 != t2 and s2 not in (s1, EMPTY_SHA), "fixture: identical patch, new tree"
    _done(cut, f"BASE: {src['b1']}", f"PATCH-SHA256: {s1}")
    _verdict(review, f"TREE: {t2}", f"PATCH-SHA256: {s2}")
    assert _has_verdict(tmp_path, cut, review_root) == 0


def test_e2_full_index_digest_is_never_compared(tmp_path):
    """E2 (PM decision): a --full-index digest decides nothing, whether it sits in PATCH-SHA256
    (bd-review-prep's value) or in PATCH-SHA256-FULL; with a stale TREE the cut is undecided (1)."""
    src = _source(tmp_path)
    cut, review_root, review = _case(tmp_path, "full-index", src)
    s1 = _digest(tmp_path, cut, src["b1"])
    full2 = _digest(tmp_path, review, "--full-index")
    assert full2 not in (s1, _digest(tmp_path, review), EMPTY_SHA)
    _done(cut, f"BASE: {src['b1']}", f"PATCH-SHA256: {s1}")
    _verdict(review, f"TREE: {STALE_TREE}", f"PATCH-SHA256: {full2}")
    assert _has_verdict(tmp_path, cut, review_root) == 1
    _clear_verdicts(review)
    _verdict(review, f"TREE: {STALE_TREE}", f"PATCH-SHA256-FULL: {full2}")
    assert _has_verdict(tmp_path, cut, review_root) == 1


def test_e2_full_line_beside_the_plain_key_is_ignored(tmp_path):
    """A verdict may carry PATCH-SHA256-FULL for humans ahead of the key; the plain key still decides."""
    src = _source(tmp_path)
    cut, review_root, review = _case(tmp_path, "full-beside", src)
    s1, s2 = _digest(tmp_path, cut, src["b1"]), _digest(tmp_path, review)
    full2 = _digest(tmp_path, review, "--full-index")
    assert len({s1, s2, full2, EMPTY_SHA}) == 4
    _done(cut, f"BASE: {src['b1']}", f"PATCH-SHA256: {s1}")
    _verdict(review, f"TREE: {STALE_TREE}", f"PATCH-SHA256-FULL: {full2}", f"PATCH-SHA256: {s2}")
    assert _has_verdict(tmp_path, cut, review_root) == 0


def test_empty_diff_sha_has_no_identity_even_when_declared_in_done(tmp_path):
    """Self-lens of the E1 fix: with the DONE arm live again, DONE's empty-input sha must not match a
    verdict's empty-input sha (nothing staged names no object); TREE still decides."""
    src = _source(tmp_path)
    cut, review_root, review = _case(tmp_path, "empty", src, change=False)
    assert _digest(tmp_path, cut, src["b1"]) == EMPTY_SHA == _digest(tmp_path, review)
    _done(cut, f"BASE: {src['b1']}", f"PATCH-SHA256: {EMPTY_SHA}")
    _verdict(review, f"TREE: {STALE_TREE}", f"PATCH-SHA256: {EMPTY_SHA}")
    assert _has_verdict(tmp_path, cut, review_root) == 1
    _clear_verdicts(review)
    cuttree = _git(tmp_path, cut, "write-tree").decode().strip()
    _verdict(review, f"TREE: {cuttree}", f"PATCH-SHA256: {EMPTY_SHA}")
    assert _has_verdict(tmp_path, cut, review_root) == 0


def test_held_decision_rules_by_tree(tmp_path):
    """HELD by N5-A (O1264b), keyed by TREE so it is independent of E1/E2: no verdict -> 1;
    Claude BOARD -> 0; agy BOARD alone -> 1; Claude REFUTE -> 0; one other-seat BOARD -> 1;
    two distinct other-seat BOARDs -> 0."""
    src = _source(tmp_path)
    cut, review_root, review = _case(tmp_path, "held", src)
    cuttree = _git(tmp_path, cut, "write-tree").decode().strip()
    _done(cut, f"BASE: {src['b1']}", f"INDEX TREE: {cuttree}")
    (review / ".review").mkdir()
    assert _has_verdict(tmp_path, cut, review_root) == 1
    _verdict(review, f"TREE: {cuttree}")
    assert _has_verdict(tmp_path, cut, review_root) == 0
    _clear_verdicts(review)
    _verdict(review, f"TREE: {cuttree}", seat="bd-agy-review-correctness1")
    assert _has_verdict(tmp_path, cut, review_root) == 1
    _clear_verdicts(review)
    _verdict(review, f"TREE: {cuttree}", verdict="REFUTE")
    assert _has_verdict(tmp_path, cut, review_root) == 0
    _clear_verdicts(review)
    _verdict(review, f"TREE: {cuttree}", seat="bd-cx-review-1")
    assert _has_verdict(tmp_path, cut, review_root) == 1
    _verdict(review, f"TREE: {cuttree}", seat="bd-cx-review-2")
    assert _has_verdict(tmp_path, cut, review_root) == 0


# ---------------------------------------------------------------- bd-boardgate.sh PATCH-SHA256 block
def _boardgate_harness(tmp: Path) -> Path:
    """The candidate's own refuse/unknown, verdict_written_at and PATCH-SHA256 block, in a one-id loop."""
    lines = _tool("bd-boardgate.sh").read_text(encoding="utf-8").splitlines()
    defs = [line for line in lines if re.match(r"^(unknown|refuse)\(\)\s*\{.*\}\s*$", line)]
    assert len(defs) == 2, f"refuse()/unknown() one-line definitions not found: {defs}"
    vwa0 = [i for i, line in enumerate(lines) if line.startswith("verdict_written_at() {")]
    assert len(vwa0) == 1, "verdict_written_at() not found exactly once"
    vwa1 = next(i for i in range(vwa0[0], len(lines)) if lines[i] == "}")
    start = [i for i, line in enumerate(lines) if line.startswith("  SHA_FROM=${BD_BOARDGATE_SHA_FROM:-")]
    end = [i for i, line in enumerate(lines)
           if line.startswith('  [ -n "$SHAMISS" ] && echo "NOTE $ID -- sha-checked')]
    assert len(start) == 1 and len(end) == 1 and start[0] < end[0], "PATCH-SHA256 block anchors not unique"
    hdir = tmp / "boardgate"
    hdir.mkdir()
    iddir = hdir / "bd-iddir.sh"
    iddir.write_text('#!/bin/bash\n[ "${1:-}" = --collected ] && [ -n "${BD_H703_FX_COLLECTED:-}" ] || exit 1\n'
                     'printf "%s\\n" "$BD_H703_FX_COLLECTED"\n')
    iddir.chmod(0o755)
    harness = hdir / "boardgate_sha_block.sh"
    harness.write_text("\n".join([
        "#!/bin/bash", "set -uo pipefail", "RC=0", *defs, *lines[vwa0[0]:vwa1 + 1],
        'for ID in "$@"; do', '  WT=$BD_H703_FX_WT', *lines[start[0]:end[0] + 1],
        '  echo "SHA-CHECK-PASSED $ID"', "done", 'echo "RC=$RC"', 'exit "$RC"',
    ]) + "\n")
    return harness


def _boardgate_fixture(tmp: Path) -> dict:
    """Review copy at B2; the collected patch.diff is the cut's plain diff at B1 (bytes differ).
    The change carries a binary file: git abbreviates `index` lines under --binary unless a side is
    binary, so on a text-only patch the plain digest equals the H368 --binary one (ACT2)."""
    src = _source(tmp)
    cut, _root, review = _case(tmp, "bg", src)
    for repo in (cut, review):
        (repo / "blob.bin").write_bytes(b"\x00\x01binary fixture\x00")
        _git(tmp, repo, "add", "blob.bin")
    collected = tmp / "collected"
    collected.mkdir()
    (collected / "patch.diff").write_bytes(_git(tmp, cut, "diff", "--cached", src["b1"]))
    fx = {
        "review": review, "collected": collected, "harness": _boardgate_harness(tmp),
        "act": hashlib.sha256((collected / "patch.diff").read_bytes()).hexdigest(),
        "plain": _digest(tmp, review),
        "bin": _digest(tmp, review, "--binary"),
        "binfull": _digest(tmp, review, "--binary", "--full-index"),
        "full": _digest(tmp, review, "--full-index"),
    }
    assert EMPTY_SHA not in fx.values(), "fixture: every digest must hash a non-empty diff"
    assert len({fx[k] for k in ("act", "plain", "bin", "binfull", "full")}) == 5, "fixture: producers must differ"
    return fx


def _boardgate(tmp: Path, fx: dict) -> tuple[int, list[str]]:
    res = subprocess.run(
        ["bash", str(fx["harness"]), "fx-bg"],
        env=_env(tmp, BD_H703_FX_WT=str(fx["review"]), BD_H703_FX_COLLECTED=str(fx["collected"])),
        capture_output=True, text=True, timeout=120, check=False,
    )
    return res.returncode, res.stdout.splitlines()


def test_e2_boardgate_accepts_the_canonical_plain_digest(tmp_path):
    """E2: a verdict keyed by the plain review-index digest (and matching no legacy producer) boards."""
    fx = _boardgate_fixture(tmp_path)
    assert fx["plain"] not in (fx["act"], fx["bin"], fx["binfull"], fx["full"])
    _verdict(fx["review"], f"TREE: {STALE_TREE}", f"PATCH-SHA256: {fx['plain']}")
    assert _boardgate(tmp_path, fx) == (0, ["SHA-CHECK-PASSED fx-bg", "RC=0"])
    _clear_verdicts(fx["review"])
    _verdict(fx["review"], f"TREE: {STALE_TREE}", f"PATCH-SHA256-FULL: {fx['full']}",
             f"PATCH-SHA256: {fx['plain']}")
    assert _boardgate(tmp_path, fx) == (0, ["SHA-CHECK-PASSED fx-bg", "RC=0"])


def test_boardgate_refuses_a_verdict_about_another_object(tmp_path):
    """Negative control: a digest of a different object refuses with rc 3 and the STALE-VERDICT line."""
    fx = _boardgate_fixture(tmp_path)
    other = hashlib.sha256(b"a different patch").hexdigest()
    _verdict(fx["review"], f"TREE: {STALE_TREE}", f"PATCH-SHA256: {other}")
    refusal = (f"REFUSED fx-bg -- STALE-VERDICT: {VERDICT_NAME} records PATCH-SHA256 {other[:12]} but the "
               f"collected patch hashes {fx['act'][:12]} -- that verdict describes a different object")
    assert _boardgate(tmp_path, fx) == (3, [refusal, "RC=3"])


def test_boardgate_never_reads_patch_sha256_full(tmp_path):
    """E2 (PM decision): PATCH-SHA256-FULL alone is no key -- the verdict records no digest and refuses."""
    fx = _boardgate_fixture(tmp_path)
    _verdict(fx["review"], f"TREE: {STALE_TREE}", f"PATCH-SHA256-FULL: {fx['full']}")
    rc, out = _boardgate(tmp_path, fx)
    assert rc == 3 and out[-1] == "RC=3" and len(out) == 2, out
    assert out[0].startswith(f"REFUSED fx-bg -- {VERDICT_NAME} declares WRITTEN-AT {WRITTEN_AT}, after the "
                             "PATCH-SHA256 rule, and records no digest"), out


def test_e2_boardgate_does_not_compare_a_full_index_digest(tmp_path):
    """E2 (PM decision): a --full-index digest is never compared, even under the PATCH-SHA256 name."""
    fx = _boardgate_fixture(tmp_path)
    _verdict(fx["review"], f"TREE: {STALE_TREE}", f"PATCH-SHA256: {fx['full']}")
    refusal = (f"REFUSED fx-bg -- STALE-VERDICT: {VERDICT_NAME} records PATCH-SHA256 {fx['full'][:12]} but the "
               f"collected patch hashes {fx['act'][:12]} -- that verdict describes a different object")
    assert _boardgate(tmp_path, fx) == (3, [refusal, "RC=3"])


def test_boardgate_keeps_the_legacy_h606_producer(tmp_path):
    """Regression guard: pre-H703 accepted objects (H606 --binary --full-index) still board."""
    fx = _boardgate_fixture(tmp_path)
    _verdict(fx["review"], f"TREE: {STALE_TREE}", f"PATCH-SHA256: {fx['binfull']}")
    assert _boardgate(tmp_path, fx) == (0, ["SHA-CHECK-PASSED fx-bg", "RC=0"])


# ---------------------------------------------------------------- bd-verdict-write-hook.sh
def _hook(tmp: Path, *lines: str) -> tuple[int, str, list[str]]:
    verdict = _verdict(tmp / "hookcut", *lines)
    log = tmp / "verdictlint-warn.log"
    res = subprocess.run(
        [str(_tool("bd-verdict-write-hook.sh"))],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(verdict)}}),
        env=_env(tmp, BD_VERDICT_LINT_LOG=str(log), BD_SCRATCH_REAPER_SH=str(tmp / "no-reaper")),
        capture_output=True, text=True, timeout=60, check=False,
    )
    return res.returncode, res.stdout, (log.read_text().splitlines() if log.exists() else [])


def test_e2_hook_tells_lenses_the_plain_producer(tmp_path):
    """E2: the WARN is the lens's instruction; it must name the canonical producer, fired once."""
    rc, out, log = _hook(tmp_path, f"INDEX TREE: {STALE_TREE}")
    assert rc == 0 and out.count("NO PATCH-SHA256 --") == 1 and len(log) == 1, (out, log)
    producer = re.findall(r"record 'PATCH-SHA256: <sha256 of (git diff[^']*?)>'", out)
    assert producer == ["git diff --cached <declared base>"], out


def test_hook_does_not_take_patch_sha256_full_for_the_key(tmp_path):
    rc, out, log = _hook(tmp_path, f"INDEX TREE: {STALE_TREE}", f"PATCH-SHA256-FULL: {'a' * 64}")
    assert rc == 0 and out.count("NO PATCH-SHA256 --") == 1 and len(log) == 1, (out, log)


def test_hook_is_silent_when_the_key_is_recorded(tmp_path):
    rc, out, log = _hook(tmp_path, f"INDEX TREE: {STALE_TREE}", f"PATCH-SHA256: {'a' * 64}")
    verdict = tmp_path / "hookcut" / ".review" / VERDICT_NAME
    assert f"PATCH-SHA256: {'a' * 64}" in verdict.read_text()  # the fixture carries the key
    assert (rc, out, log) == (0, "", [])


# ---------------------------------------------------------------- bd-pm-census.py current_sha
_CENSUS_RUNNER = r'''
import ast, sys
src, cut, done = sys.argv[1:4]
tree = ast.parse(open(src, encoding="utf-8").read(), src)
keep = [n for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))
        or (isinstance(n, ast.Assign) and [getattr(t, "id", "") for t in n.targets] == ["EMPTY_SHA"])]
ns = {"__name__": "bd_pm_census_under_test"}
exec(compile(ast.Module(body=keep, type_ignores=[]), src, "exec"), ns)
print("CURRENT_SHA=" + ns["current_sha"](cut, open(done, encoding="utf-8").read()))
'''


def _census_current_sha(tmp: Path, cut: Path, **env: str) -> str:
    """The candidate's own current_sha (imports, functions and EMPTY_SHA only; no fleet scan)."""
    res = subprocess.run(
        [sys.executable, "-c", _CENSUS_RUNNER, str(_tool("bd-pm-census.py")), str(cut), str(cut / "DONE.md")],
        env=_env(tmp, **env), capture_output=True, text=True, timeout=120, check=False,
    )
    got = re.findall(r"^CURRENT_SHA=([0-9a-f]*)$", res.stdout, re.MULTILINE)
    assert len(got) == 1, f"census current_sha did not run: rc={res.returncode}\n{res.stdout}\n{res.stderr}"
    return got[0]


def test_e2_census_current_sha_is_the_plain_digest(tmp_path):
    src = _source(tmp_path)
    cut = _staged(tmp_path, src["src"], tmp_path / "census", src["b1"])
    plain = _digest(tmp_path, cut, src["b1"])
    assert plain not in (EMPTY_SHA, _digest(tmp_path, cut, "--full-index", src["b1"]))
    _done(cut, f"BASE: {src['b1']}")
    assert _census_current_sha(tmp_path, cut) == plain


def test_census_current_sha_hashes_the_bytes_git_wrote(tmp_path):
    """CR/CRLF (valid UTF-8) and then non-UTF-8 bytes in the diff are part of the plain digest."""
    src = _source(tmp_path)
    cut = _staged(tmp_path, src["src"], tmp_path / "census", src["b1"])
    _done(cut, f"BASE: {src['b1']}")
    (cut / "crlf.txt").write_bytes(b"crlf line\r\nlone\rcr\n")
    _git(tmp_path, cut, "add", "crlf.txt")
    raw = _git(tmp_path, cut, "diff", "--cached", src["b1"])
    assert b"crlf line\r\n" in raw and b"lone\rcr" in raw, "fixture: the diff must carry CR bytes"
    assert _census_current_sha(tmp_path, cut) == hashlib.sha256(raw).hexdigest()
    (cut / "latin.txt").write_bytes(b"latin \xe9\xff\n")
    _git(tmp_path, cut, "add", "latin.txt")
    raw = _git(tmp_path, cut, "diff", "--cached", src["b1"])
    assert b"\xe9\xff" in raw, "fixture: the diff must carry non-UTF-8 bytes"
    assert _census_current_sha(tmp_path, cut) == hashlib.sha256(raw).hexdigest()


def test_census_current_sha_is_empty_without_identity(tmp_path):
    """Nothing staged (the empty-input sha) or no 40-hex BASE in DONE.md -> "" (no identity)."""
    src = _source(tmp_path)
    empty = _staged(tmp_path, src["src"], tmp_path / "census-empty", src["b1"], change=False)
    assert _digest(tmp_path, empty, src["b1"]) == EMPTY_SHA
    _done(empty, f"BASE: {src['b1']}")
    assert _census_current_sha(tmp_path, empty) == ""
    unbased = _staged(tmp_path, src["src"], tmp_path / "census-unbased", src["b1"])
    assert _digest(tmp_path, unbased, src["b1"]) != EMPTY_SHA  # data present; only the BASE is missing
    _done(unbased, f"BASE: origin/main {src['b1'][:8]}")
    assert _census_current_sha(tmp_path, unbased) == ""


def test_census_current_sha_is_empty_when_git_cannot_run(tmp_path):
    """The except arm: no git on PATH is COULD NOT LOOK -> "" (no identity), never a crash."""
    src = _source(tmp_path)
    cut = _staged(tmp_path, src["src"], tmp_path / "census", src["b1"])
    _done(cut, f"BASE: {src['b1']}")
    assert _census_current_sha(tmp_path, cut) != ""  # the same fixture has an identity when git runs
    nogit = tmp_path / "empty-path"
    nogit.mkdir()
    assert _census_current_sha(tmp_path, cut, PATH=str(nogit)) == ""
