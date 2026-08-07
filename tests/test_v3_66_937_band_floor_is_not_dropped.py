"""v3.66.937 -- bd-band-derive derived its contract FLOOR in two places.

WHAT WAS ACTUALLY WRONG, stated precisely because the first reading of this
was wrong and shipping it would have put a false claim in the CHANGELOG.

`derive()` unions FLOOR into every band, probed against the tree under
derivation. That is correct and always worked. `emit_band()` then unioned the
SAME FLOOR in again, probed against a hardcoded sandbox home -- a directory
that exists on no machine this repository runs on. So the second union
matched nothing, contributed nothing, and was dead in every environment.

THE FLOOR DID REACH EVERY BAND. Measured on the pristine tool, in place:

    bd-band-derive --file bulk_downloader/aiassist.py --emit
        -> 23 suites, tests/test_contracts.py PRESENT

    the same tool with derive()'s union alone neutralised
        -> 22 suites, tests/test_contracts.py ABSENT

That pair is what identifies derive() as the supplier and emit_band's copy as
the spare. An earlier measurement appeared to show the floor missing from
pristine output; it was taken against a COPY of the tool outside
toolchain/bin, where the `bdtools_sec` import fails, and `2>/dev/null` hid the
traceback -- so an empty stdout read as "a band without the floor". A probe
that cannot run reports whatever the reader expects. CLAUDE.md section 1.

SO THE DEFECT IS THE DUPLICATE DENOMINATOR, not the dead literal inside it.
Two places computing band membership can disagree, and the one nobody reads is
the one that rots: had derive()'s union ever been removed, emit_band's would
NOT have covered it. @897 made exactly this argument when it put the is_suite
filter at derive()'s single return rather than in emit_band -- the JSON
payload publishes `band` raw while only band_cmd goes through emit_band, so
two derivations mean two answers to one question.

THE SECOND HALF, and it is the part with live consequence. derive()'s
existence check drops an absent floor entry in SILENCE. The check itself is
right -- naming a suite that is not on disk would emit a command that cannot
run -- but a band silently narrower than the caller was promised is CLAUDE.md
section 0, and section 4 tells every agent to treat this tool's output as
their floor. The omission is now announced on stderr; stdout stays the single
line `--emit` exists to produce.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import tokenize
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-band-derive"


def _load():
    """Import the extensionless tool as a module.

    A fresh module object per call: these tests capture stderr, and a cached
    import would let one test's state reach the next.
    """
    loader = importlib.machinery.SourceFileLoader("_bd_band_derive", str(_TOOL))
    spec = importlib.util.spec_from_loader("_bd_band_derive", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _code_only(src: str) -> str:
    """`src` with comments and docstrings removed.

    CLAUDE.md section 0: a comment is inside the denominator of every gate
    that reads source text. This file's own prose describes the dead path and
    emit_band's docstring explains the removal, so an assertion over raw text
    would fail on the explanation of the fix. Strip first, assert second.
    """
    out = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:  # pragma: no cover - the tool parses
        return src
    prev = None
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev in (
                tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, None):
            continue
        out.append(tok.string)
        prev = tok.type
    return " ".join(out)


def _func_source(name: str) -> str:
    """The comment-stripped source of one top-level function."""
    text = _TOOL.read_text("utf-8")
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return _code_only(ast.get_source_segment(text, node))
    raise AssertionError(
        f"{name}() is gone from bd-band-derive -- this file's subject no "
        f"longer exists, which is a failure, not a pass")


def _tree(tmp_path: Path, *rels: str) -> str:
    root = tmp_path / "work"
    root.mkdir(parents=True, exist_ok=True)
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# present\n")
    return str(root)


# ── the duplicate denominator ────────────────────────────────────────────────

def test_the_floor_is_unioned_in_exactly_one_place():
    """RED on pristine: FLOOR was unioned in both derive() and emit_band().

    AST over the whole tool, so the count cannot be fooled by a mention in a
    comment or a docstring -- both of which now legitimately name FLOOR.
    """
    text = _TOOL.read_text("utf-8")
    tree = ast.parse(text)

    def _derives_floor(fn):
        """A comprehension that iterates FLOOR and filters on a disk probe.

        THE PREDICATE, not merely the denominator, is what had to be got right
        here -- CLAUDE.md section 1. A first version asked whether a function's
        text contained both 'FLOOR' and 'isfile' and flagged `selftest`, which
        legitimately does `ref -= set(FLOOR)` and stats the disk for unrelated
        reasons. Mentioning the floor is not deriving it; the structure that
        derives it is the filtered comprehension.
        """
        for node in ast.walk(fn):
            if not isinstance(node, (ast.ListComp, ast.SetComp,
                                     ast.GeneratorExp)):
                continue
            for gen in node.generators:
                if not (isinstance(gen.iter, ast.Name)
                        and gen.iter.id == "FLOOR"):
                    continue
                probes = {n.attr for cond in gen.ifs for n in ast.walk(cond)
                          if isinstance(n, ast.Attribute)}
                if probes & {"isfile", "exists", "is_file"}:
                    return True
        return False

    unioners = [n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and _derives_floor(n)]
    assert unioners, (
        "no function derives the contract FLOOR against the tree -- the floor "
        "is not a floor at all. An empty denominator is a failure.")
    assert unioners == ["derive"], (
        f"the contract floor is derived in {len(unioners)} places: {unioners}. "
        f"Two derivations of one set can disagree, and the copy nobody reads "
        f"is the one that rots. derive() owns it; emit_band formats.")


def test_emit_band_is_a_formatter_and_touches_no_filesystem():
    """A formatter that stats the disk is a second derivation wearing a
    formatter's name."""
    src = _func_source("emit_band")
    for probe in ("isfile", "exists(", "os.path.join", "FLOOR"):
        assert probe not in src, (
            f"emit_band's code still references {probe!r}; it must format the "
            f"band it is handed and derive nothing.")


def test_no_hardcoded_home_decides_the_floor():
    """The mechanism, named rather than spelled.

    The two path segments are joined at runtime so this assertion cannot
    re-introduce into its own file the literal it forbids -- CLAUDE.md section
    4: writing the bad form as an example is itself the bug.
    """
    dead_root = "/home/" + "claude"
    for fn in ("derive", "emit_band"):
        assert dead_root not in _func_source(fn), (
            f"{fn}() decides band membership against a hardcoded home "
            f"directory. A fixed absolute path is a denominator that "
            f"structurally excludes the subject on every machine but one.")


# ── the silent drop ──────────────────────────────────────────────────────────

def test_an_absent_floor_is_announced(tmp_path, capsys):
    """RED on pristine: the floor was dropped without a word.

    Section 4 tells every agent to treat this tool's output as their floor. A
    band quietly narrower than promised is the section 0 shape.
    """
    mod = _load()
    floor = mod.FLOOR[0]
    work = _tree(tmp_path, "tests/test_other.py")
    band = mod.derive(work, ["bulk_downloader/aiassist.py"], [])
    err = capsys.readouterr().err
    assert floor not in band, (
        f"derive() named {floor!r} for a tree that does not contain it; the "
        f"emitted command would not run. Band: {band}")
    assert floor in err, (
        f"derive() dropped the contract floor and said nothing. stderr was "
        f"{err!r}")


def test_no_notice_when_the_floor_is_present(tmp_path, capsys):
    """Over-sensitivity is a soundness bug too (CLAUDE.md section 0): a gate
    that cries wolf on every ordinary run gets tuned out."""
    mod = _load()
    work = _tree(tmp_path, mod.FLOOR[0])
    mod.derive(work, ["bulk_downloader/aiassist.py"], [])
    err = capsys.readouterr().err
    assert "floor" not in err.lower(), (
        f"derive() warned about the floor on a tree that has it: {err!r}")


def test_the_floor_still_rides_every_derived_band(tmp_path, capsys):
    """The regression guard. This behaviour was already correct before the
    cut and the cut must not have cost it."""
    mod = _load()
    floor = mod.FLOOR[0]
    work = _tree(tmp_path, floor)
    band = mod.derive(work, ["bulk_downloader/aiassist.py"], [])
    capsys.readouterr()
    assert floor in band, (
        f"the contract floor no longer rides a derived band: {band}")


def test_the_floor_is_not_invented_when_it_is_absent(tmp_path, capsys):
    """The over-correction guard.

    'Stop checking existence' passes the announcement test and ships a band
    naming a file that is not there -- a fix reproducing the shape of the
    defect it repairs.
    """
    mod = _load()
    floor = mod.FLOOR[0]
    work = _tree(tmp_path, "tests/test_only.py")
    band = mod.derive(work, [], [])
    capsys.readouterr()
    assert floor not in band, (
        f"derive() named {floor!r} for a tree without it: {band}")
    assert floor not in mod.emit_band(band), (
        f"emit_band re-introduced {floor!r} into a band that omitted it")


# ── the emitted line ─────────────────────────────────────────────────────────

def test_the_emitted_line_is_a_single_clean_command(tmp_path, capsys):
    """`--emit` is consumed by a paste or a shell. The notice belongs on
    stderr; on stdout it corrupts the command the flag exists to produce."""
    mod = _load()
    work = _tree(tmp_path, "tests/test_other.py")
    band = mod.derive(work, ["bulk_downloader/aiassist.py"], [])
    captured = capsys.readouterr()
    assert captured.out == "", (
        f"derive() wrote to stdout while dropping the floor: {captured.out!r}")
    line = mod.emit_band(band)
    assert capsys.readouterr().out == "", "emit_band must not print"
    assert line.startswith("bd-band"), line
    assert "\n" not in line.strip(), f"the band must be one line: {line!r}"


def test_the_cli_emit_carries_the_floor_and_every_suite_exists():
    """End-to-end on the real tree.

    Green before the cut as well as after -- derive() always supplied the
    floor. It is here so a refactor of emit_band's signature cannot break the
    CLI unnoticed, and it says so rather than implying it proves the fix.
    """
    mod = _load()
    r = subprocess.run(
        [sys.executable, str(_TOOL), "--file", "bulk_downloader/aiassist.py",
         "--emit", "--work", str(_REPO)],
        capture_output=True, text=True, cwd=str(_REPO), timeout=300)
    assert r.returncode == 0, f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
    out = r.stdout.strip()
    assert out.startswith("bd-band "), f"--emit did not emit a band: {out!r}"
    named = [w for w in out.split()[1:] if w.startswith("tests/")]
    assert named, f"--emit produced no suites: {out!r}"
    assert mod.FLOOR[0] in named, (
        f"the contract floor is missing from a real emitted band: {out!r}")
    missing = [s for s in named if not (_REPO / s).is_file()]
    assert not missing, (
        f"--emit named suites that do not exist and so cannot run: {missing}")
