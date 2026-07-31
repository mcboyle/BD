"""The retired csrf-meta-tag premise must be gone from tools/ as well.

tests/test_cut35_csrf_meta_contract_retired.py established this contract and
proved it BICONDITIONALLY against measured reachability -- but its denominator
was `capture.sh` and nothing else. Three siblings under tools/ kept probing the
same deleted contract, invisibly to it. That is the exact denominator gap the
sibling gate's own docstring describes for the cookie leak, repeated one
directory over: a gate that cannot see the thing it is asked about reports OK.

WHAT WAS THERE, measured at retirement time:

  tools/diag_csrf_bootstrap.py   its whole stated purpose (docstring
                                 hypotheses A/B/C) was templates/index.html and
                                 _load_index_html, both deleted at v3.66.334.
                                 No sys.exit, no assert: print-only, exit 0
                                 always. It printed `NO -- code differs!` for a
                                 regex anchor that can never match again, and
                                 its "substitution failed, show me what
                                 happened" branch had become the only branch.
                                 RETIRED -- the file's reason to exist is gone.

  tools/diag_d2_fresh_bd_home.py recorded template_exists / template_has_marker
                                 / has_csrf_meta_tag / has_unsubst_marker and
                                 csrf_token_value into a JSON result. Constants
                                 now -- and csrf_token_value would have written
                                 a live CSRF token into a shipped diagnostic if
                                 the tag ever came back.

  tools/functional_probe.py      graded `bug` unless GET / carried csrf-token.
                                 Since the tag is gone that arm ALWAYS fired:
                                 the sole bug in an otherwise healthy 24-ok
                                 run, captioned "GET / failed: HTTP 200" --
                                 a message contradicted by its own status code.

The first two cannot fail; the third cannot pass. Both are section 0 failures,
and the crying-wolf one is the worse of the pair: CLAUDE.md calls
over-sensitivity a soundness bug rather than a safe default, because a gate
that fires on healthy code gets switched off.

WHY THESE ARE NOT STRING BANS. The source test below compares each probe
against MEASURED reachability -- the set of bodies GET / can actually return,
driven through the real app on both branches -- so it would permit these probes
again the day a reachable body could satisfy one. The functional_probe test is
behavioural: it RUNS the probe against a healthy app rather than asserting
anything about its spelling, which is also why it catches a defect no literal
ban would (that arm tests `csrf-token`, a fragment that is legitimate in the
X-CSRF-Token header name and so cannot be banned by spelling at all).

UNKNOWN IS A THIRD STATE: reachability is only established when the standin for
GET /'s 200 branch is proven faithful; when it cannot be, these tests FAIL
rather than certify an unmeasured tree.

run_tests.py conventions: repo root from __file__; no pytest builtins.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"

# The three literals that can only be satisfied by the deleted Jinja shell.
# Deliberately NOT the bare fragment "csrf-token": that is legitimate inside the
# X-CSRF-Token header name, and a gate that fired on it would be making a false
# statement about its own input. The probe that uses the bare fragment is caught
# behaviourally instead, by test_functional_probe_does_not_cry_wolf_on_a_healthy_root.
RETIRED_PROBES = (
    '<meta name="csrf-token"',
    "{{ csrf_token }}",
    "<!--CSRF_META-->",
)


def _root_bodies() -> dict[str, bytes]:
    """Every body GET / can return, MEASURED by driving the real app.

    Re-measured here rather than imported from the sibling module: this gate's
    whole subject is a denominator that excluded its subject, so it derives its
    own reachability rather than inheriting one.
    """
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    import bulk_downloader.app as A
    saved = A._M2_DIST_ROOT
    out: dict[str, bytes] = {}
    try:
        A._M2_DIST_ROOT = Path(tempfile.mkdtemp()) / "no-such-dist"
        with A.app.test_client() as c:
            out["dist-absent-503"] = c.get("/").data
        built = REPO / "frontend" / "dist" / "index.html"
        if built.is_file():
            A._M2_DIST_ROOT, label = built.parent, "built-dist"
        else:
            d = Path(tempfile.mkdtemp())
            shutil.copy(REPO / "frontend" / "index.html", d / "index.html")
            A._M2_DIST_ROOT, label = d, "vite-source-index-standin"
        with A.app.test_client() as c:
            out[label] = c.get("/").data
    finally:
        A._M2_DIST_ROOT = saved
    return out


def _standin_is_faithful() -> tuple[bool, str]:
    if (REPO / "frontend" / "dist" / "index.html").is_file():
        return True, "measured the real built dist/index.html"
    cfg = REPO / "frontend" / "vite.config.ts"
    if not cfg.is_file():
        return False, "frontend/vite.config.ts is missing"
    if "transformIndexHtml" in cfg.read_text(encoding="utf-8"):
        return False, "vite.config.ts declares a transformIndexHtml hook"
    return True, "no built dist; vite declares no HTML-transform hook"


def _tool_sources() -> list[Path]:
    return [p for p in sorted(TOOLS.rglob("*.py"))
            if "__pycache__" not in p.parts]


def test_no_tool_probes_a_root_contract_that_cannot_be_served():
    """A tools/ probe for the meta contract is allowed IFF a body can carry it."""
    ok, why = _standin_is_faithful()
    assert ok, f"cannot establish what GET / can serve, so UNKNOWN and FAIL: {why}"

    sources = _tool_sources()
    assert sources, (
        "no sources found under tools/ -- the denominator is empty, so this "
        "test would certify nothing (UNKNOWN fails)")

    bodies = _root_bodies()
    assert bodies, "GET / returned no measurable bodies; reachability is UNKNOWN"

    offenders = []
    for probe in RETIRED_PROBES:
        reachable = [k for k, b in bodies.items() if probe.encode() in b]
        if reachable:
            continue  # a real contract again; probing it is legitimate
        for p in sources:
            text = p.read_text(encoding="utf-8", errors="replace")
            if probe in text:
                offenders.append(f"{p.relative_to(REPO)} probes {probe!r}")

    assert not offenders, (
        "these tools/ sources probe a contract no reachable GET / body can "
        f"serve ({why}), so the probe is a constant rather than a check:\n  "
        + "\n  ".join(sorted(offenders)))


def test_functional_probe_does_not_cry_wolf_on_a_healthy_root():
    """Behavioural: the probe is RUN, and must not grade a healthy root a bug.

    Asserting about functional_probe's source would be the presence-not-
    behaviour class this contract already rejects, and could not see the defect
    anyway -- the offending arm tests the fragment `csrf-token`, which is
    legitimate spelling inside the X-CSRF-Token header name.

    The subject is F.1 alone. Findings from other sections are deliberately NOT
    asserted on: they have their own subjects, and folding them in here would
    make this gate fire for reasons it cannot describe.
    """
    r = subprocess.run(
        [sys.executable, str(TOOLS / "functional_probe.py")],
        cwd=str(REPO), capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr

    header = "F.1 - CSRF bootstrap"
    start = out.find("F.1")
    assert start != -1, (
        "functional_probe printed no F.1 section, so its CSRF arm did not run "
        f"and this test would certify nothing (UNKNOWN fails). output:\n{out[:2000]}")
    nxt = out.find("=== F.2", start)
    section = out[start:nxt if nxt != -1 else len(out)]

    graded_bug = [ln for ln in section.splitlines()
                  if re.search(r"\[(bug|crit)\s*\]", ln)]
    assert not graded_bug, (
        f"functional_probe grades a healthy root a defect ({header}). GET / "
        f"returns 200 and serves the SPA shell; the CSRF token ships via "
        f"GET /api/csrf, not an HTML meta tag. Offending findings:\n  "
        + "\n  ".join(graded_bug))
