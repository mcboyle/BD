"""Row 785 -- the login evidence phase must not leak into the FILENAME.

Row 708 made the page a login verdict was read from into evidence, and named
that file after the tag the verdict carried.  The tag is built at the seam as
``f"login-{phase}"`` (bulk_downloader/login_impl/submit.py::_no_nav_verdict) and
every phase the tree passes is an ENGLISH SENTENCE FRAGMENT WITH SPACES, so the
file written by ``write_login_evidence`` is, at v3.66.1540, literally

    login-page closed mid-submit-20260914T183334Z-996923.html

The write SUCCEEDS.  This row is not about a crash: it is about the naming
burden that a space-bearing name puts on every shell command, archive entry and
log line that later has to name the file, and about the fact that the phase --
the one thing an operator wants to read -- is encoded in the place that is
hardest to quote rather than in the file itself.

THE ACCEPTANCE, verbatim from the register (row 785):
  "explicit phase slug asserted restricted character class; human phase INSIDE
   file; enumerate ALL current phases, not frozen three.  Negative
   punctuation-only phase yields usable unique nonempty name."

so this gate asserts four separable things:

  1. RESTRICTED CHARACTER CLASS.  Every evidence filename matches an explicitly
     declared safe class -- ASCII letters, digits, dot, dash, underscore -- and
     nothing else.  Not "no spaces": a restricted class refuses the quoting
     characters a later shell would also have to survive.
  2. THE HUMAN PHASE IS NOT LOST.  It moves INSIDE the file.  A slug that
     discarded the operator-readable phase would satisfy (1) and lose the row.
  3. ALL CURRENT PHASES, NOT THE FROZEN THREE.  The corpus is DERIVED FROM THE
     TREE by walking every ``_no_nav_verdict(...)`` call site in submit.py, so a
     fourth seam added later is covered by this gate on the day it is added
     rather than on the day somebody remembers to edit a list here.  A call site
     whose phase this gate cannot read statically is a COULD NOT LOOK and is
     raised, never folded into a pass.
  4. THE PUNCTUATION-ONLY NEGATIVE.  A phase made only of characters the slug
     strips must still produce a usable, nonempty name, and two DIFFERENT such
     phases must not produce the SAME name -- otherwise the fix trades a
     quoting burden for silently overwritten evidence.

No claim is made here about WHEN a phase is reached: that is row 708's gate
(tests/test_row708_no_nav_login_is_not_success.py), which drives the real
do_login through all three seams.  This gate judges the naming only.
"""
import ast
import pathlib
import re

import pytest

from bulk_downloader.login_impl import replay

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
SUBMIT = REPO / "bulk_downloader" / "login_impl" / "submit.py"

# The class, declared here and asserted below rather than implied by a
# "no spaces" check: ASCII letters, digits, dot, dash, underscore.  A name must
# also START with a letter or digit, so no name can be read as an option by a
# command that later takes it as an argument.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# The seam builds the tag from the phase.  Declared as a format so that the
# corpus this gate builds is the corpus the product actually writes.
TAG_FOR_PHASE = "login-{}".format

# Phases made only of characters a slug strips.  Not derived from the tree:
# these are the row's NEGATIVE, the shapes a future phase could take.
PUNCTUATION_ONLY_PHASES = ("!!!", "???", "-- --", "   ")


class _Page:
    """The browser boundary, and nothing else."""

    def __init__(self, html="<html>fixture login page</html>"):
        self._html = html

    def content(self):
        return self._html


def _phases_from_the_tree():
    """Every phase literal passed to _no_nav_verdict, read from submit.py.

    Returns (phases, unreadable): `unreadable` names any call site whose phase
    is not a literal -- a COULD NOT LOOK the caller must raise rather than skip.
    """
    tree = ast.parse(SUBMIT.read_text(encoding="utf-8"))
    phases, unreadable = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "_no_nav_verdict":
            continue
        arg = None
        if len(node.args) > 4:
            arg = node.args[4]
        else:
            for kw in node.keywords:
                if kw.arg == "phase":
                    arg = kw.value
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            phases.append(arg.value)
        else:
            unreadable.append(f"{SUBMIT.name}:{node.lineno}")
    return phases, unreadable


def _write(tmp_path, tag, final_url="https://login.example.invalid/login?error=1"):
    path = replay.write_login_evidence(
        _Page(), {"login_evidence_dir": str(tmp_path)}, final_url, tag)
    assert path, f"no evidence written for tag {tag!r}"
    return pathlib.Path(path)


# ── 3. the corpus proves its own shape, before any verdict ──────────────────

def test_the_phase_corpus_is_derived_from_the_tree_and_is_not_empty():
    """Positive control for the scan: it finds phases, and it finds them all."""
    phases, unreadable = _phases_from_the_tree()
    assert not unreadable, (
        f"_no_nav_verdict call sites whose phase this gate cannot read "
        f"statically: {unreadable} -- COULD NOT LOOK, not a pass. Either pass a "
        f"literal phase or teach this scan to read the new shape."
    )
    assert phases, (
        "the scan found NO phase at any _no_nav_verdict call site; the probe "
        "cannot say yes, so a green result below would mean nothing"
    )
    assert len(phases) == len(set(phases)), f"duplicate phases: {phases}"
    # The shape that makes this row a row: today every phase is prose.
    prose = [p for p in phases if " " in p]
    assert prose, (
        f"no phase contains a space any more ({phases}); the fixture is no "
        f"longer building the shape row 785 is about"
    )


# ── 1. the restricted character class ───────────────────────────────────────

def test_every_current_phase_produces_a_restricted_class_filename(tmp_path):
    """ALL phases in the tree, not a frozen three."""
    phases, unreadable = _phases_from_the_tree()
    assert not unreadable and phases, (phases, unreadable)
    offenders = {}
    checked = 0
    for phase in phases:
        name = _write(tmp_path, TAG_FOR_PHASE(phase)).name
        checked += 1
        if not SAFE_NAME.fullmatch(name):
            offenders[phase] = name
    assert not offenders, (
        f"evidence filenames outside the declared safe class "
        f"[A-Za-z0-9][A-Za-z0-9._-]*: {offenders} -- the phase text is being "
        f"used as a filename, so every shell command, archive entry and log "
        f"line that later names this file has to quote it"
    )
    assert checked == len(phases), (checked, phases)


def test_the_default_tag_is_also_in_the_class(tmp_path):
    """member_state_check's own default tag goes through the same namer."""
    name = _write(tmp_path, "login").name
    assert SAFE_NAME.fullmatch(name), name


# ── 2. the human phase is kept, inside the file ─────────────────────────────

def test_the_human_phase_is_inside_the_file(tmp_path):
    """A slug that lost the phase would satisfy the class and lose the row."""
    phases, _ = _phases_from_the_tree()
    assert phases
    missing = {}
    for phase in phases:
        body = _write(tmp_path, TAG_FOR_PHASE(phase)).read_text(encoding="utf-8")
        if phase not in body:
            missing[phase] = body[:160]
    assert not missing, (
        f"the operator-readable phase is in neither the name nor the file: "
        f"{missing} -- moving it out of the filename may not delete it"
    )


def test_the_final_url_is_still_kept(tmp_path):
    """Negative control for the file body: row 708's evidence is not displaced."""
    url = "https://login.example.invalid/login?error=1"
    body = _write(tmp_path, TAG_FOR_PHASE("no nav signal"), final_url=url).read_text(
        encoding="utf-8")
    assert url in body, body[:200]
    assert "fixture login page" in body, body[:200]


# ── 4. the punctuation-only negative ────────────────────────────────────────

def test_a_punctuation_only_phase_yields_a_usable_unique_nonempty_name(tmp_path):
    """The row's negative: strip everything and you must still have a name."""
    names = {}
    for phase in PUNCTUATION_ONLY_PHASES:
        path = _write(tmp_path, TAG_FOR_PHASE(phase))
        assert path.name, phase
        assert SAFE_NAME.fullmatch(path.name), (phase, path.name)
        assert path.exists() and path.stat().st_size > 0, (phase, path)
        names[phase] = path.name
    assert len(set(names.values())) == len(PUNCTUATION_ONLY_PHASES), (
        f"two different punctuation-only phases produced the SAME evidence "
        f"filename: {names} -- a naming fix that silently overwrites one "
        f"verdict's evidence with another's is worse than the quoting burden "
        f"it replaces"
    )


def test_a_readable_phase_is_still_readable_in_the_name(tmp_path):
    """Negative control for the slug: hashing every tag would pass the class
    test and the uniqueness test and destroy the reason the tag is in the name
    at all."""
    name = _write(tmp_path, TAG_FOR_PHASE("no nav signal")).name
    assert name.startswith("login-no-nav-signal-"), (
        f"{name!r} no longer carries the phase in readable form; a slug is a "
        f"transliteration, not a digest"
    )
