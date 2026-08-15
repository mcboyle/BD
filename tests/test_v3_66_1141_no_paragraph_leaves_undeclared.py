"""CLAUDE.md may be reduced, but nothing may leave it silently.

WHY, MEASURED 2026-08-15 at v3.66.1140. The contract is ~48k tokens -- 4.8% of
every window, in every session and every subagent -- and it grows monotonically,
because every cut appends a lesson and nothing is ever removed. Composition:
2,349 lines, 447 rule-bearing, 1,502 narrative. Three sections carry half the
file.

Reducing it is worth doing and dangerous to do. The danger is specific: in a
diff, a deleted RULE looks exactly like a deleted RETELLING, and no gate in this
repo reads the contract for meaning. bd-freshcheck resolves cited PATHS and says
so itself on every run ("this covers the DERIVABLE half of staleness only");
bd-doc-truth resolves file-path claims. A claim about BEHAVIOUR passes both
untouched -- which is exactly how section 5 asserted for weeks that
check_requirements.py discarded its version result while the tool had already
grown specifier comparison.

THIS GATE IS NOT A RULE DETECTOR, AND THE HISTORY OF WHY IS THE POINT. Three
predicates for "which lines are rules" were built and measured before this
design:

  * normative markers, line-scoped -> 229 units, mostly narrative FRAGMENTS,
    because hard-wrapped prose puts "never" in the middle of a story;
  * the same predicate spelled MUST|NEVER|...|must|never|... -> blind to
    "Never", the capitalised sentence opening this contract uses constantly;
  * bold spans -> a non-greedy regex paired one span's CLOSING `**` with the
    next span's OPENING `**`, yielding 321 "spans" starting with `(` and `,`.

A rule is a rule by virtue of what it ASSERTS, which its syntax does not record.
v3.66.1072 reached the identical finding when it tried to replace BD_GATE_SCOPE
with a derived predicate and caught 3 of 8. So this freezes PARAGRAPHS -- an
objective unit, and exactly what an extraction moves -- and asks only whether
any of them left the corpus without someone saying so.

A move to the casebook passes. A deletion or a rewrite must be DECLARED, with
the survivor named, in a file this test reads. CLAUDE.md section 1: a deferral
that lives only in prose has not been deferred, it has been dropped.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# Its subject is the contract corpus and one tool -- not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-contract-rules"
BASELINE = REPO / "project-knowledge" / "CONTRACT_RULES.baseline"
CONTRACT = REPO / "CLAUDE.md"
CASEBOOK = REPO / "project-knowledge" / "CONTRACT_CASEBOOK.md"


def _load():
    loader = importlib.machinery.SourceFileLoader("bd_contract_rules", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _check(root, baseline) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root), "--baseline", str(baseline)],
        capture_output=True, text=True, timeout=180)


# ---------------------------------------------------------------- preconditions

def test_the_tool_and_the_baseline_exist():
    """PRECONDITION -- without both, every assertion below is vacuous."""
    assert TOOL.is_file(), (
        "bd-contract-rules is missing. CLAUDE.md cannot be reduced safely "
        "without something that notices a paragraph leaving it.")
    assert BASELINE.is_file(), (
        f"no {BASELINE.relative_to(REPO)} -- the gate has no denominator, and a "
        "gate with no denominator reports clean (CLAUDE.md section 0).")


def test_the_baseline_is_not_empty_and_covers_the_contract():
    """A baseline of nothing certifies everything.

    This is the non-empty-denominator assertion CLAUDE.md section 0 says to
    write BEFORE the verdict -- it is the only thing that reliably catches this
    class, because the author has just convinced themselves the logic is right.
    """
    mod = _load()
    active, removed = mod.load_baseline(BASELINE)
    assert len(active) >= 100, (
        f"the baseline holds only {len(active)} active paragraph(s); the "
        "contract had 300 when it was frozen. A shrunken baseline protects "
        "nothing and must be explained by DECLARED REMOVALS, not by silence.")
    # Every declared removal must carry a reason -- a bare fingerprint under
    # that heading is a deletion with the paperwork skipped.
    for fp, reason in removed.items():
        assert len(reason.strip()) >= 10, (
            f"declared removal {fp} has no reason. A declaration whose content "
            "is empty is indistinguishable from a silent deletion.")


def test_the_live_contract_passes_its_own_gate():
    """The tree as shipped must be clean, or the gate is already bypassed."""
    r = _check(REPO, BASELINE)
    assert r.returncode == 0, (
        "the contract corpus has lost a frozen paragraph:\n"
        + r.stdout[-2500:] + r.stderr[-800:])


# --------------------------------------------------------------- both directions

def test_a_deleted_paragraph_fails_the_gate():
    """THE RED DIRECTION, driven against a copy of the REAL corpus.

    Proving only that the tree is green is the default mistake and it is
    invisible, because everything is green either way -- a test that passes on
    both is not a test (CLAUDE.md section 6).
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "project-knowledge").mkdir()
        shutil.copy(CONTRACT, root / "CLAUDE.md")
        if CASEBOOK.is_file():
            shutil.copy(CASEBOOK, root / "project-knowledge" / CASEBOOK.name)
        shutil.copy(BASELINE, root / "project-knowledge" / BASELINE.name)

        paras = mod.paragraphs((root / "CLAUDE.md").read_text(encoding="utf-8"))
        assert len(paras) > 20, "fixture precondition: the copy has paragraphs"
        # Drop a LONG paragraph: a short one risks its excerpt surviving as a
        # substring of some other paragraph, which would make this test pass for
        # the wrong reason.
        victim = max(paras, key=len)
        body = (root / "CLAUDE.md").read_text(encoding="utf-8")
        assert body.count(victim) == 1, "the victim paragraph is not unique"
        (root / "CLAUDE.md").write_text(body.replace(victim, "", 1),
                                        encoding="utf-8")

        r = _check(root, root / "project-knowledge" / BASELINE.name)
        assert r.returncode == 1, (
            f"deleting a whole paragraph did NOT fail the gate (exit "
            f"{r.returncode}). It cannot protect the contract.\n{r.stdout[-1200:]}")
        assert "FAIL" in r.stdout and "LOST" in r.stdout, (
            "the gate failed without naming what it lost")


def test_reflowing_the_contract_does_not_fire():
    """THE OVER-SENSITIVE DIRECTION, and the one that decides whether this
    gate survives contact with the work it exists to permit.

    An extraction pass RE-WRAPS prose by construction. A gate that fails
    correct work gets switched off, and CLAUDE.md section 0 counts that as a
    soundness bug equal to a false clean -- a manifest pin that hashed a
    wall-clock field had two sessions reconciling a diff that did not exist.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "project-knowledge").mkdir()
        if CASEBOOK.is_file():
            shutil.copy(CASEBOOK, root / "project-knowledge" / CASEBOOK.name)
        shutil.copy(BASELINE, root / "project-knowledge" / BASELINE.name)

        # Re-wrap every prose line to a different width, leaving fences alone.
        out, fence = [], False
        for line in CONTRACT.read_text(encoding="utf-8").split("\n"):
            if line.lstrip().startswith("```"):
                fence = not fence
                out.append(line)
                continue
            if fence or not line.strip() or line.lstrip().startswith(("#", "|", "-", "*")):
                out.append(line)
                continue
            words = line.split()
            cur = ""
            for w in words:
                if len(cur) + len(w) + 1 > 55:
                    out.append(cur)
                    cur = w
                else:
                    cur = f"{cur} {w}".strip()
            out.append(cur)
        rewrapped = "\n".join(out)
        assert rewrapped != CONTRACT.read_text(encoding="utf-8"), (
            "fixture precondition: the re-wrap must actually change the text, "
            "or this test proves nothing (CLAUDE.md section 6)")
        (root / "CLAUDE.md").write_text(rewrapped, encoding="utf-8")

        r = _check(root, root / "project-knowledge" / BASELINE.name)
        assert r.returncode == 0, (
            "re-wrapping the contract fired the gate. Extraction reflows by "
            "construction, so this gate would fail every correct reduction and "
            f"be switched off.\n{r.stdout[-2000:]}")


def test_a_paragraph_moved_to_the_casebook_passes():
    """The entire purpose: extraction is a MOVE, and a move is not a loss."""
    mod = _load()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "project-knowledge").mkdir()
        shutil.copy(BASELINE, root / "project-knowledge" / BASELINE.name)
        body = CONTRACT.read_text(encoding="utf-8")
        paras = mod.paragraphs(body)
        victim = max(paras, key=len)
        (root / "CLAUDE.md").write_text(body.replace(victim, "", 1),
                                        encoding="utf-8")
        existing = CASEBOOK.read_text(encoding="utf-8") if CASEBOOK.is_file() else "# Casebook\n"
        (root / "project-knowledge" / CASEBOOK.name).write_text(
            existing + "\n\n" + victim + "\n", encoding="utf-8")

        r = _check(root, root / "project-knowledge" / BASELINE.name)
        assert r.returncode == 0, (
            "a paragraph moved verbatim into the casebook was reported LOST. "
            "The gate cannot tell a move from a deletion, which makes the whole "
            f"reduction impossible.\n{r.stdout[-1500:]}")


def test_the_gate_refuses_rather_than_certifying_when_blind():
    """UNKNOWN is a third state and it FAILS (CLAUDE.md section 0)."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        empty = root / "empty.baseline"
        empty.write_text("# no active rows\n")
        r = _check(REPO, empty)
        assert r.returncode == 2, (
            f"an empty baseline returned {r.returncode}, not 2. A gate that "
            "cannot see its subject must not report clean.")
        assert "REFUSED" in r.stderr, "the refusal does not name itself"

        r2 = _check(root / "nowhere", BASELINE)
        assert r2.returncode == 2, (
            "an unreadable corpus must REFUSE, not report every paragraph lost")


def test_the_tool_states_its_blind_spots():
    """An instrument's wrong answer arrives wearing the authority of a
    measurement, so its limits belong in the OUTPUT, not in a README."""
    r = _check(REPO, BASELINE)
    assert "CANNOT SEE" in r.stdout, "the gate does not state its limits"
    assert "MEANING" in r.stdout, (
        "the output does not disclose that it asserts conservation of text "
        "rather than of meaning -- the one thing a reader will assume it did")


def test_the_selftest_is_clean():
    r = subprocess.run([sys.executable, str(TOOL), "--selftest"],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    assert "SELFTEST PASS" in r.stdout


def test_regenerating_the_baseline_is_not_how_a_failure_is_cleared():
    """The baseline file must SAY so, because the reflex is to regenerate.

    bd-mutate hits this exact shape: it reads the 'original' it will restore
    from the working tree, so a battery started on a dirty tree adopts the
    mutant as pristine and reports a clean sha256-verified restore. A baseline
    regenerated to clear a failure adopts the loss the same way.
    """
    head = BASELINE.read_text(encoding="utf-8")[:2000]
    assert re.search(r"DO NOT regenerate", head), (
        "the baseline header does not warn against regenerating it to clear a "
        "failure, which is the one mistake that silently destroys its value")
