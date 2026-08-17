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

import argparse
import importlib.machinery
import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

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


def _synthetic_contract(text: str):
    """Return a populated temporary contract root and its loaded tool."""
    td = tempfile.TemporaryDirectory()
    root = pathlib.Path(td.name)
    (root / "project-knowledge").mkdir()
    contract = root / "CLAUDE.md"
    baseline = root / "project-knowledge" / "CONTRACT_RULES.baseline"
    contract.write_text(text, encoding="utf-8")
    mod = _load()
    ns = argparse.Namespace(root=str(root), baseline=str(baseline), show=20)
    assert mod.cmd_extract(ns) == 0, "fixture precondition: fresh extraction failed"
    active, _ = mod.load_baseline(baseline)
    assert active, "fixture precondition: extraction produced no denominator"
    return td, root, contract, baseline, mod


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
    current = mod.paragraphs(CONTRACT.read_text(encoding="utf-8"))
    current_fps = {mod.fingerprint(paragraph) for paragraph in current}
    assert len(active) == len(current) == len(current_fps) == 306, (
        "the intended paragraph denominator drifted: "
        f"active={len(active)}, paragraphs={len(current)}, unique={len(current_fps)}. "
        "Every current paragraph must be frozen explicitly; additions are not "
        "allowed to fall outside the conservation gate.")
    assert set(active) == current_fps, (
        "the baseline and current contract have different paragraph identities; "
        "a matching count does not prove matching subjects")
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
        active, _ = mod.load_baseline(root / "project-knowledge" / BASELINE.name)
        frozen = [paragraph for paragraph in paras if mod.fingerprint(paragraph) in active]
        assert len(frozen) == len(active) == 306, (
            "fixture precondition: the copied corpus is not the frozen denominator")
        # Drop a LONG paragraph so the failure identifies a substantial active
        # subject rather than a heading or another incidental short unit.
        victim = max(frozen, key=len)
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


def test_preserving_an_excerpt_prefix_does_not_authorize_suffix_loss():
    """A human excerpt is diagnostic text, never proof of paragraph identity."""
    original = (
        "The release gate must preserve this deliberately long and unique prefix "
        "through the complete frozen excerpt boundary before the decisive rule: "
        "NEVER authorize publication without the exact terminal evidence."
    )
    td, root, contract, baseline, mod = _synthetic_contract(
        "# Contract\n\n" + original + "\n")
    with td:
        active, _ = mod.load_baseline(baseline)
        victim_fp = mod.fingerprint(original)
        assert victim_fp in active, "fixture precondition: victim is frozen"
        excerpt = active[victim_fp]
        assert len(excerpt) == mod.EXCERPT
        assert original.startswith(excerpt)

        changed = excerpt + " The decisive rule was deleted after the excerpt."
        assert mod.normalise(changed) != mod.normalise(original)
        contract.write_text("# Contract\n\n" + changed + "\n", encoding="utf-8")
        fps, _, _ = mod.corpus_state(root)
        assert victim_fp not in fps, "fixture precondition: exact identity still survived"

        r = _check(root, baseline)
        assert r.returncode == 1, (
            "a matching excerpt prefix authorized a different paragraph; human "
            f"display text became the success oracle:\n{r.stdout}")
        assert f"LOST {victim_fp}" in r.stdout


def test_case_only_semantic_mutation_is_not_the_same_paragraph():
    original = "You MUST preserve UNKNOWN as a distinct state before authorization."
    changed = original.replace("MUST", "must").replace("UNKNOWN", "unknown")
    assert changed != original
    assert changed.lower() == original.lower()
    td, root, contract, baseline, mod = _synthetic_contract(
        "# Contract\n\n" + original + "\n")
    with td:
        active, _ = mod.load_baseline(baseline)
        victim_fp = next(fp for fp, excerpt in active.items() if "UNKNOWN" in excerpt)
        contract.write_text("# Contract\n\n" + changed + "\n", encoding="utf-8")

        r = _check(root, baseline)
        assert r.returncode == 1, (
            "lowercasing fingerprints hid a case-only semantic mutation:\n" + r.stdout)
        assert f"LOST {victim_fp}" in r.stdout


def test_extract_refuses_to_overwrite_an_established_baseline():
    td, root, contract, baseline, mod = _synthetic_contract(
        "# Contract\n\nNever replace an established intent baseline silently.\n")
    with td:
        before = baseline.read_bytes()
        contract.write_text(
            "# Contract\n\nA different contract must not become truth by extraction.\n",
            encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(TOOL), "--extract", "--root", str(root),
             "--baseline", str(baseline)],
            capture_output=True, text=True, timeout=180)
        assert r.returncode == 2
        assert "REFUSED" in r.stderr and "already exists" in r.stderr
        assert baseline.read_bytes() == before, (
            "a refused extraction changed the established baseline")


def test_whitespace_only_reflow_preserves_the_exact_fingerprint():
    original = "Every required gate must preserve its exact measured denominator."
    td, root, contract, baseline, mod = _synthetic_contract(
        "# Contract\n\n" + original + "\n")
    with td:
        changed = "Every required\n gate\tmust preserve its exact measured denominator."
        assert changed != original
        assert mod.normalise(changed) == mod.normalise(original)
        assert mod.fingerprint(changed) == mod.fingerprint(original)
        contract.write_text("# Contract\n\n" + changed + "\n", encoding="utf-8")
        assert _check(root, baseline).returncode == 0


def test_verbatim_move_is_the_only_thing_that_restores_a_missing_paragraph():
    victim = "A frozen rule may move verbatim, but it may not silently disappear."
    td, root, contract, baseline, mod = _synthetic_contract(
        "# Contract\n\n" + victim + "\n")
    with td:
        victim_fp = mod.fingerprint(victim)
        contract.write_text("# Contract\n\nA different retained paragraph remains.\n",
                            encoding="utf-8")
        missing = _check(root, baseline)
        assert missing.returncode == 1 and f"LOST {victim_fp}" in missing.stdout

        casebook = root / "project-knowledge" / "CONTRACT_CASEBOOK.md"
        casebook.write_text("# Casebook\n\n" + victim + "\n", encoding="utf-8")
        contract_fps = {mod.fingerprint(p) for p in mod.paragraphs(contract.read_text())}
        corpus_fps, _, _ = mod.corpus_state(root)
        assert victim_fp not in contract_fps
        assert victim_fp in corpus_fps
        assert _check(root, baseline).returncode == 0


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

        mod = _load()
        body = CONTRACT.read_text(encoding="utf-8")
        active, _ = mod.load_baseline(root / "project-knowledge" / BASELINE.name)
        candidates = [
            paragraph for paragraph in mod.paragraphs(body)
            if mod.fingerprint(paragraph) in active
            and len(paragraph) > 300
            and not any(line.lstrip().startswith(("#", "|", "-", "*", "```"))
                        for line in paragraph.splitlines())
        ]
        assert candidates, "fixture precondition: no active plain-prose paragraph"
        victim = candidates[0]
        assert body.count(victim) == 1
        reflowed = "\n".join(textwrap.wrap(
            mod.normalise(victim), width=55,
            break_long_words=False, break_on_hyphens=False))
        assert reflowed != victim, (
            "fixture precondition: the re-wrap must actually change the text, "
            "or this test proves nothing (CLAUDE.md section 6)")
        assert mod.normalise(reflowed) == mod.normalise(victim)
        assert mod.fingerprint(reflowed) == mod.fingerprint(victim)
        changed = body.replace(victim, reflowed, 1)
        assert len(mod.paragraphs(changed)) == len(mod.paragraphs(body)) == 306
        (root / "CLAUDE.md").write_text(changed, encoding="utf-8")

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
        active, _ = mod.load_baseline(root / "project-knowledge" / BASELINE.name)
        frozen = [paragraph for paragraph in paras if mod.fingerprint(paragraph) in active]
        assert len(frozen) == len(active) == 306
        victim = max(frozen, key=len)
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
