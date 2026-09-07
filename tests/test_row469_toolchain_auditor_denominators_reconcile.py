"""Row 469: the four toolchain auditors must state ONE denominator and reconcile to it.

bd-selfcheck, bd-tools, bd-tool-lint and bd-tool-smoke each judge toolchain/bin.
Each used to derive its own population with a private glob, and the four counts
disagreed (measured 258 entries; 257/257/255/250 examined). None stated an
exclusion, so a tool missing from an auditor's view was unaudited while the
auditor printed CLEAN.

Every auditor must now print, over any --bin it is pointed at:
    POPULATION bin=<dir> expected=N examined=M excluded=K unknown=0
with expected derived from the TREE, examined + excluded == expected, every
exclusion carrying a reason from a closed vocabulary, and unknown != 0 forcing
a nonzero UNKNOWN exit instead of a verdict.
"""
import os
import re
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "repo-wide"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "toolchain", "bin")
AUDITORS = ("bd-selfcheck", "bd-tools", "bd-tool-lint", "bd-tool-smoke")
LINE = re.compile(
    r"POPULATION bin=(?P<bin>\S+) expected=(?P<expected>\d+) "
    r"examined=(?P<examined>\d+) excluded=(?P<excluded>\d+) unknown=(?P<unknown>\d+) "
    r"unmeasured=(?P<unmeasured>\d+)")
MEMBERS = re.compile(r"POPULATION-EXAMINED ([^\n]*)")


def _fixture_bin(tmp_path):
    """A bin dir holding one of every shape the classifier admits.

    THE HAZARD IS THE DIVERGENCE: a shell tool and two non-invocable modules,
    which is exactly what the four auditors used to disagree about. A corpus of
    python tools only cannot go red for this row.
    """
    d = tmp_path / "bin"
    d.mkdir()
    files = {
        "bd-alpha": "#!/usr/bin/env python3\nimport sys\ndef main():\n    return 0\n"
                    "if __name__ == '__main__':\n    sys.exit(main())\n",
        "bd-beta": "#!/usr/bin/env python3\nimport sys\ndef main():\n    return 0\n"
                   "if __name__ == '__main__':\n    sys.exit(main())\n",
        "bd-shellish": "#!/bin/bash\nexit 0\n",
        "bd_libmod.py": '"""A library beside the tools: no shebang, no entrypoint."""\n',
        "_bd_helper.py": "#!/usr/bin/env python3\n\"\"\"Helper.\"\"\"\n",
    }
    for name, body in files.items():
        p = d / name
        p.write_text(body)
        p.chmod(0o755)
    return str(d), files


def _run(tool, bindir, extra=()):
    return subprocess.run([sys.executable, os.path.join(BIN, tool), "--bin", bindir]
                          + list(extra),
                          capture_output=True, text=True, timeout=180,
                          cwd=ROOT, env=dict(os.environ, BD_DISABLE_KEEPALIVE="1"))


def _members(tool, bindir, extra=()):
    """The examined SET this auditor reports, member by member."""
    r = _run(tool, bindir, extra)
    m = MEMBERS.search(r.stderr) or MEMBERS.search(r.stdout)
    assert m, ("%s printed no POPULATION-EXAMINED line: it reports a count but "
               "never says WHICH tools it examined, so no two auditors can be "
               "compared.\nstderr=%r" % (tool, r.stderr[-400:]))
    names = m.group(1).split()
    assert len(names) == len(set(names)), "%s listed a tool twice" % tool
    return names, r


def _census(tool, bindir, extra=()):
    r = _run(tool, bindir, extra)
    m = LINE.search(r.stderr) or LINE.search(r.stdout)
    assert m, (
        "%s printed no POPULATION line over a %d-entry fixture bin: it reports a "
        "verdict over a population it never states.\nstdout=%r\nstderr=%r"
        % (tool, len(os.listdir(bindir)), r.stdout[-400:], r.stderr[-400:]))
    return {k: int(v) for k, v in m.groupdict().items() if k != "bin"}, r


def test_every_auditor_states_a_denominator_that_reconciles(tmp_path):
    bindir, files = _fixture_bin(tmp_path)
    seen = {}
    for tool in AUDITORS:
        c, _ = _census(tool, bindir)
        assert c["expected"] == len(files), (
            "%s expected=%d but the tree holds %d files" % (tool, c["expected"], len(files)))
        assert c["examined"] + c["excluded"] == c["expected"], (
            "%s does not reconcile: %d examined + %d excluded != %d expected"
            % (tool, c["examined"], c["excluded"], c["expected"]))
        assert c["unknown"] == 0, "%s reported %d unknown" % (tool, c["unknown"])
        assert c["examined"] > 0, "%s examined nothing" % tool
        seen[tool] = c
    assert len(seen) == len(AUDITORS), "fired for %d auditors, expected %d" % (
        len(seen), len(AUDITORS))
    assert len({c["expected"] for c in seen.values()}) == 1, (
        "the four auditors disagree on the denominator: "
        + ", ".join("%s=%d" % (t, c["expected"]) for t, c in seen.items()))


def test_a_new_tool_moves_every_denominator_together(tmp_path):
    bindir, files = _fixture_bin(tmp_path)
    before = {t: _census(t, bindir)[0] for t in AUDITORS}
    p = os.path.join(bindir, "bd-newly-added")
    with open(p, "w") as fh:
        fh.write("#!/usr/bin/env python3\nimport sys\ndef main():\n    return 0\n"
                 "if __name__ == '__main__':\n    sys.exit(main())\n")
    os.chmod(p, 0o755)
    after = {t: _census(t, bindir)[0] for t in AUDITORS}
    moved = 0
    for t in AUDITORS:
        assert after[t]["expected"] == before[t]["expected"] + 1, (
            "%s did not see the new tool: expected %d -> %d"
            % (t, before[t]["expected"], after[t]["expected"]))
        assert after[t]["examined"] == before[t]["examined"] + 1, (
            "%s saw the new tool in its denominator but did not examine it"
            % t)
        moved += 1
    assert moved == 4, "only %d denominators moved" % moved


def test_a_tool_removed_from_one_auditors_view_is_UNKNOWN_not_dropped(tmp_path):
    """NEGATIVE CONTROL / the gate's own RED: hide a file from an auditor's
    examined set and the census must refuse rather than shrink silently."""
    sys.path.insert(0, BIN)
    import bdtools_population as pop
    bindir, files = _fixture_bin(tmp_path)
    full = pop.scoped_census(bindir)
    assert full.ok and len(full.expected) == len(files)
    hidden = pop.Census(bindir, full.expected,
                        [n for n in full.examined if n != "bd-alpha"], full.excluded)
    assert not hidden.ok, "a tool removed from the examined set still reconciled"
    assert hidden.unknown == ["bd-alpha"], hidden.unknown
    assert "UNKNOWN" in hidden.diagnosis()
    unreasoned = pop.Census(bindir, full.expected, full.examined,
                            dict(full.excluded, **{"bd-alpha": "because"}))
    assert not unreasoned.ok and unreasoned.unreasoned == ["bd-alpha"]


def test_an_absent_or_empty_bin_is_UNKNOWN_not_an_empty_population(tmp_path):
    sys.path.insert(0, BIN)
    import bdtools_population as pop
    with pytest.raises(pop.PopulationUnavailable):
        pop.expected(str(tmp_path / "nope"))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(pop.PopulationUnavailable):
        pop.expected(str(empty))


def test_the_real_toolchain_bin_reconciles_for_all_four():
    """POSITIVE CONTROL over the real tree: nonzero denominator, no unknowns."""
    for tool in AUDITORS:
        c, _ = _census(tool, BIN)
        assert c["expected"] >= 200, "%s expected=%d over the real bin" % (tool, c["expected"])
        assert c["examined"] + c["excluded"] == c["expected"]
        assert c["unknown"] == 0


def test_report_refuses_to_certify_a_census_that_does_not_reconcile(tmp_path):
    """report() is the seam every auditor consults before printing a verdict."""
    import io
    sys.path.insert(0, BIN)
    import bdtools_population as pop
    bindir, files = _fixture_bin(tmp_path)
    good = pop.scoped_census(bindir)
    buf = io.StringIO()
    assert pop.report(good, buf) is True
    assert "unknown=0" in buf.getvalue()
    bad = pop.Census(bindir, good.expected,
                     [n for n in good.examined if n != "bd-alpha"], good.excluded)
    buf2 = io.StringIO()
    assert pop.report(bad, buf2) is False, (
        "report() certified a census with a dropped tool -- the auditor would "
        "print a verdict over a population it cannot state")
    assert "UNKNOWN" in buf2.getvalue() and "bd-alpha" in buf2.getvalue()


def test_the_denominator_is_every_file_in_the_tree(tmp_path):
    """scoped_census may not shrink the population it was handed."""
    sys.path.insert(0, BIN)
    import bdtools_population as pop
    bindir, files = _fixture_bin(tmp_path)
    for python_analyzer in (False, True):
        c = pop.scoped_census(bindir, python_analyzer=python_analyzer)
        assert sorted(c.expected) == sorted(files), (
            "python_analyzer=%s: denominator %r != tree %r"
            % (python_analyzer, c.expected, sorted(files)))
        assert len(c.examined) + len(c.excluded) == len(files)
        # MEMBERSHIP MUST NOT DEPEND ON THE ANALYZER: a shell tool (or a tool
        # with a wrong shebang) is examined by every auditor either way; only
        # its MEASURABILITY differs, and that is reported by name.
        assert c.examined == sorted(pop.scoped_census(bindir).examined), (
            "the analyzer changed the examined SET: %r" % c.examined)
        assert set(c.unmeasured) <= set(c.examined)


def test_all_four_auditors_examine_the_same_set_member_by_member(tmp_path):
    """THE ROW'S GATE. Four identical COUNTS over four different SETS is the
    same blindness with better arithmetic, so this compares members, derived at
    check time from the tree, and fails on any pairwise disagreement."""
    bindir, files = _fixture_bin(tmp_path)
    seen = {}
    for tool in AUDITORS:
        names, _ = _members(tool, bindir)
        seen[tool] = sorted(names)
    sys.path.insert(0, BIN)
    import bdtools_population as pop
    tree_truth = pop.scoped_census(bindir)
    assert tree_truth.examined, "fixture produced no examined tools"
    assert len(seen) == 4, "compared %d auditors, expected 4" % len(seen)
    for a in AUDITORS:
        for b in AUDITORS:
            if a >= b:
                continue
            assert seen[a] == seen[b], (
                "%s and %s disagree on the EXAMINED SET: only %s examines %r; "
                "only %s examines %r"
                % (a, b, a, sorted(set(seen[a]) - set(seen[b])),
                   b, sorted(set(seen[b]) - set(seen[a]))))
    assert seen[AUDITORS[0]] == sorted(tree_truth.examined), (
        "the agreed set is not the tree's: auditors=%r tree=%r"
        % (seen[AUDITORS[0]], sorted(tree_truth.examined)))
    # The hazard the fixture carries: a shell tool and a wrong-shebang tool are
    # examined by ALL FOUR. Delete this divergence and the test cannot fire.
    assert "bd-shellish" in seen[AUDITORS[0]]


def test_one_auditor_examining_a_smaller_set_is_caught(tmp_path):
    """NEGATIVE CONTROL through the REAL consumers, not a constructed Census:
    give ONE auditor a bin with a tool hidden and the member-by-member
    comparison must fail."""
    bindir, files = _fixture_bin(tmp_path)
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    for name in files:
        if name == "bd-beta":
            continue                      # the tool removed from ONE view
        src = os.path.join(bindir, name)
        dst = hidden / name
        dst.write_bytes(open(src, "rb").read())
        dst.chmod(0o755)
    full, _ = _members("bd-selfcheck", bindir)
    short, _ = _members("bd-tool-lint", str(hidden))
    assert "bd-beta" in full and "bd-beta" not in short
    disagreement = sorted(set(full) ^ set(short))
    assert disagreement == ["bd-beta"], disagreement
    with pytest.raises(AssertionError) as ei:
        assert sorted(full) == sorted(short), (
            "bd-selfcheck and bd-tool-lint disagree on the EXAMINED SET: %r"
            % disagreement)
    assert "EXAMINED SET" in str(ei.value)


def test_an_auditor_that_cannot_measure_reports_UNKNOWN_not_a_number(tmp_path):
    """Through the four consumers: an unavailable bin is UNKNOWN and a nonzero
    exit, never a count."""
    absent = str(tmp_path / "not-there")
    empty = tmp_path / "empty"
    empty.mkdir()
    for tool in AUDITORS:
        for target in (absent, str(empty)):
            r = _run(tool, target)
            out = r.stdout + r.stderr
            assert r.returncode != 0, (
                "%s exited 0 over %s -- a verdict over a population it could "
                "not derive" % (tool, target))
            assert "UNKNOWN" in out or "CANNOT-EVALUATE" in out, (
                "%s over %s printed no UNKNOWN: %r" % (tool, target, out[-300:]))
            assert not LINE.search(out), (
                "%s printed a POPULATION count over an unmeasurable bin" % tool)


def test_an_unmeasurable_tool_stays_in_the_examined_set_and_is_named(tmp_path):
    """A tool an analyzer cannot read is UNMEASURED, not dropped: it stays in
    the examined set (so the four still agree) and is reported by name."""
    bindir, files = _fixture_bin(tmp_path)
    names, r = _members("bd-tool-smoke", bindir)
    assert "bd-shellish" in names, (
        "bd-tool-smoke dropped a tool its analyzer cannot read; a wrong shebang "
        "would then remove a real python tool from its view entirely")
    out = r.stdout + r.stderr
    assert "POPULATION-UNMEASURED" in out and "bd-shellish=not-python-source" in out
    c, _ = _census("bd-tool-smoke", bindir)
    assert c["unmeasured"] == 1, c


def test_the_real_toolchain_bin_examined_sets_are_identical():
    """POSITIVE CONTROL over the real tree, member by member."""
    sets = {}
    for tool in AUDITORS:
        names, _ = _members(tool, BIN)
        sets[tool] = sorted(names)
        assert len(names) >= 200, "%s examined %d" % (tool, len(names))
    assert len({tuple(v) for v in sets.values()}) == 1, {
        t: sorted(set(v) ^ set(sets[AUDITORS[0]])) for t, v in sets.items()}


def test_a_wrong_shebang_does_not_remove_a_tool_from_any_auditors_view(tmp_path):
    """THE LENS'S DEMONSTRATION, as a test (row469 verdict, clause 4).

    Flipping a real Python tool's shebang to #!/bin/bash used to move it out of
    bd-tool-lint's and bd-tool-smoke's examined sets while every auditor still
    printed a clean POPULATION line -- "a tool absent from an auditor's
    denominator is unaudited while the auditor prints CLEAN", reproduced on the
    patched tree. Classification must not gate examination.
    """
    bindir, files = _fixture_bin(tmp_path)
    victim = os.path.join(bindir, "bd-alpha")
    body = open(victim, "rb").read()
    assert body.startswith(b"#!/usr/bin/env python3"), "fixture precondition"
    before = {t: sorted(_members(t, bindir)[0]) for t in AUDITORS}
    assert all("bd-alpha" in v for v in before.values())
    with open(victim, "wb") as fh:
        fh.write(body.replace(b"#!/usr/bin/env python3", b"#!/bin/bash", 1))
    after = {t: sorted(_members(t, bindir)[0]) for t in AUDITORS}
    for tool in AUDITORS:
        assert "bd-alpha" in after[tool], (
            "%s dropped bd-alpha after a shebang flip: the file is still a real "
            "tool and is now unaudited while %s prints a clean POPULATION line"
            % (tool, tool))
        assert after[tool] == before[tool], (
            "%s's examined set moved on a shebang flip: %r"
            % (tool, sorted(set(before[tool]) ^ set(after[tool]))))
    assert len({tuple(v) for v in after.values()}) == 1, after
    # ...and the loss of measurability is REPORTED, not silent.
    _, r = _members("bd-tool-smoke", bindir)
    assert "bd-alpha=not-python-source" in (r.stdout + r.stderr)


def test_an_unreadable_member_is_unmeasured_by_name_not_a_population_wide_unknown(tmp_path):
    """Row 469 x row 745. Two different events, two different diagnostics.

    A denominator that cannot be DERIVED is UNKNOWN over the whole population.
    A single member that cannot be READ is a failed MEASUREMENT of a member that
    is still in the denominator: it stays examined, it is named, and it carries
    its own declared reason. Collapsing the second into the first loses which
    file could not be read, which is the CLAUDE.md A7 defect row 745 exists to
    prevent -- and it is what the v3.66.1507 landing measured against this cut.
    """
    if os.geteuid() == 0:
        pytest.skip("root can read mode-000 fixtures")
    sys.path.insert(0, BIN)
    import bdtools_population as pop
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("bd-one", "bd-two", "bd-three"):
        p = bindir / name
        p.write_text("#!/usr/bin/env python3\nif __name__ == '__main__': pass\n")
        p.chmod(0o755)
    (bindir / "bd-two").chmod(0)
    with pytest.raises(PermissionError):
        (bindir / "bd-two").open("rb").read()

    cen = pop.scoped_census(str(bindir))
    assert cen.expected == ["bd-one", "bd-three", "bd-two"], cen.expected
    assert cen.examined == ["bd-one", "bd-three", "bd-two"], cen.examined
    assert "bd-two" not in cen.excluded, cen.excluded
    assert cen.unmeasured == {"bd-two": "unreadable"}, cen.unmeasured
    assert "unreadable" in pop.UNMEASURED_REASONS
    assert cen.ok, cen.diagnosis()
    assert "bd-two=unreadable" in cen.unmeasured_line()

    # The POSITIVE control for the other vocabulary: a denominator that truly
    # cannot be derived still raises, so the two outcomes remain distinguishable.
    with pytest.raises(pop.PopulationUnavailable):
        pop.expected(str(tmp_path / "absent"))
