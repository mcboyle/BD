"""row lessons-3 (O908 LESSONS #3): cut-root drift census -- portable half.

Cut root paths (/home/mboyle/bd-cuts, /home/mboyle/bd-review-wt) are
hardcoded string literals across dozens of harness scripts, so a path change
or a new root does not propagate. The census logic lives here (importable,
fully parameterised); the LIVE census over the host's script set runs from
harness/bd-cut-roots-drift.sh on hub-mesh01 (O1104: a product-repo pytest may
not depend on host-only artifacts, and FLEET_RULE 46 forbids skip-on-missing).
Every test below runs against tests/fixtures/lessons3_cut_roots/ or a tmp tree
and passes on any box.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

LITERALS = ("/home/mboyle/bd-cuts", "/home/mboyle/bd-review-wt")
UNREADABLE = "UNREADABLE"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lessons3_cut_roots"
#: what the live census on hub-mesh01 passes in (kept here so the harness
#: script and this module agree on the denominator's shape, not its location)
LIVE_SEARCH_DIRS = (
    "/home/mboyle/bd-persist/harness",
    "/home/mboyle/BulkDownloader/toolchain/bin",
    "/home/mboyle/BulkDownloader/scripts",
    "/home/mboyle/BulkDownloader/tools",
)
LIVE_CUT_ROOTS_SH = "/home/mboyle/bd-persist/harness/CUT-ROOTS.sh"
LIVE_ALLOWLIST = "/home/mboyle/bd-persist/harness/CUT-ROOTS-ALLOWLIST.txt"
LIVE_HOME = "/home/mboyle"

#: editing-hook backup snapshots, not live scripts: x.sh.pre-<ts>, x.sh.post-<ts>, x.sh.ORIGINAL --
#: anchored at the END of the name (E5, lens 02:25Z: a bare substring ".pre" swallowed deploy.prepare.sh)
SNAPSHOT_RE = re.compile(r"\.(pre|post|ORIGINAL)(-[^/]*)?$")


class MissingRoot(AssertionError):
    """A required search root is absent: the census cannot look (E2, lens
    2026-09-21T01:00Z) -- fail closed instead of measuring a population of 0."""


def _is_snapshot(p: Path) -> bool:
    return p.suffix in (".pyc", ".pyo") or bool(SNAPSHOT_RE.search(p.name))


def _walk(root, unreadable):
    """os.walk with errors PROPAGATED: a directory that cannot be listed is
    recorded in `unreadable` instead of being skipped (E3, lens 2026-09-21T01:48Z:
    Path.rglob silently omits unreadable directories, so an offender inside a
    mode-000 directory vanished from the census)."""
    def onerror(exc):
        unreadable.append((Path(exc.filename), exc.__class__.__name__))
    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for name in filenames:
            yield Path(dirpath) / name


def script_population(search_dirs, home=None, unreadable=None):
    """Every live script under the required roots (+ ~/*.sh when home is given).
    Raises MissingRoot if any root is absent: an empty denominator is never a
    pass. Directories that cannot be traversed are appended to `unreadable`
    (list of (path, ExcName)); when the caller passes none, they RAISE."""
    dirs = [Path(d) for d in search_dirs]
    missing = [str(d) for d in dirs if not d.is_dir()]
    if missing:
        raise MissingRoot("required search root(s) absent, census COULD NOT LOOK: %s" % missing)
    own = unreadable is None
    unreadable = [] if own else unreadable
    seen = set()
    for d in dirs:
        for p in _walk(d, unreadable):
            if not p.is_file() or _is_snapshot(p):
                continue
            seen.add(p.resolve())
    if own and unreadable:
        raise MissingRoot("unreadable director(y/ies) inside a required root, census COULD NOT LOOK: %s"
                          % ["%s (%s)" % (str(q), e) for q, e in unreadable])
    if home is not None:
        # E4 (lens 02:25Z): a supplied home that is missing or unreadable is COULD NOT LOOK, never a silent 0
        hp = Path(home)
        if not hp.is_dir():
            raise MissingRoot("supplied home is not a directory, census COULD NOT LOOK: %s" % hp)
        try:
            entries = list(os.scandir(hp))
        except OSError as exc:
            unreadable.append((hp, exc.__class__.__name__))
            entries = []
        for e in entries:
            if e.name.endswith(".sh") and e.is_file() and not SNAPSHOT_RE.search(e.name):
                seen.add(Path(e.path).resolve())
    return seen


def load_allowlist(allowlist_path):
    """Seeded exemptions, one path per line (absolute, or relative to the
    allowlist's own directory), '#' comments; a missing file is an empty
    allowlist (the census then reports every literal, never fewer)."""
    path = Path(allowlist_path)
    if not path.is_file():
        return set()
    out = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entry = Path(line)
            out.add((entry if entry.is_absolute() else path.parent / entry).resolve())
    return out


def find_drift(search_dirs, cut_roots_sh, allowlist_path, extra_paths=(), home=None):
    """Sorted (path, literal) hits outside cut_roots_sh and the allowlist across
    the script population plus extra_paths. A candidate that cannot be read is
    a hit tagged 'UNREADABLE: <ExcName>' (E1: an offender made unreadable used
    to vanish from the census and the gate went green); a directory the walk
    cannot enter is a hit tagged 'UNREADABLE: <ExcName> (directory)' (E3) --
    unknown is never permission. Raises MissingRoot (E2) when a required root
    is absent."""
    allowlist = load_allowlist(allowlist_path)
    roots_file = Path(cut_roots_sh)
    cut_roots_resolved = roots_file.resolve() if roots_file.is_file() else None
    hits = []
    unreadable_dirs = []
    candidates = script_population(search_dirs, home, unreadable_dirs) | {Path(p).resolve() for p in extra_paths}
    for q, exc_name in unreadable_dirs:      # E3: a directory the census could not enter is a hit, never a skip
        hits.append((str(q), "%s: %s (directory)" % (UNREADABLE, exc_name)))
    for path in candidates:
        if path == cut_roots_resolved or path in allowlist:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError as exc:
            hits.append((str(path), "%s: %s" % (UNREADABLE, exc.__class__.__name__)))
            continue
        for literal in LITERALS:
            if literal in text:
                hits.append((str(path), literal))
    return sorted(hits)


# ---------------------------------------------------------------- portable tests

def _fx(name):
    p = FIXTURES / name
    assert p.exists(), "fixture missing: %s" % p
    return p


def test_fixture_roots_are_committed():
    """The fixtures ARE the test's host: a clean root, a drifted root, a
    CUT-ROOTS.sh and an allowlist -- all under tests/fixtures, nothing on the box."""
    for name in ("root_clean/clean.sh", "root_drift/offender.sh", "root_drift/seeded.sh",
                 "root_drift/old.sh.pre-20260101T000000Z", "CUT-ROOTS.sh", "ALLOWLIST.txt"):
        _fx(name)


def test_allowlist_parsing_ignores_comments_and_blank_lines():
    allow = load_allowlist(_fx("ALLOWLIST.txt"))
    assert allow == {_fx("root_drift/seeded.sh").resolve()}, allow
    assert load_allowlist(FIXTURES / "does-not-exist.txt") == set()


def test_census_counts_live_scripts_and_excludes_snapshots():
    pop = script_population([_fx("root_clean"), _fx("root_drift")])
    names = sorted(p.name for p in pop)
    assert names == ["clean.sh", "offender.sh", "seeded.sh"], names   # the .pre snapshot is not a live script


def test_drift_on_fixture_roots_reports_exactly_the_unseeded_offender():
    """Seeded (allowlisted) and CUT-ROOTS.sh itself are exempt; the clean root
    contributes nothing; the offender is reported with its literal."""
    hits = find_drift([_fx("root_clean"), _fx("root_drift")], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"))
    assert hits == [(str(_fx("root_drift/offender.sh").resolve()), LITERALS[0])], hits


def test_drift_check_is_clean_when_only_the_clean_root_is_searched():
    assert find_drift([_fx("root_clean")], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt")) == []


def test_drift_check_fails_when_a_new_offender_appears_outside_allowlist(tmp_path):
    """Positive control (FLEET_RULE 7): prove the probe can say yes on a
    brand-new file that no fixture or allowlist has seen."""
    offender = tmp_path / "new_offending_script.sh"
    offender.write_text('WT="/home/mboyle/bd-cuts/cut/new-thing"\n')
    hits = find_drift([_fx("root_clean")], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"), extra_paths=[offender])
    assert hits == [(str(offender.resolve()), LITERALS[0])], hits


def test_a_missing_required_root_fails_closed(tmp_path):
    """E2 paired control: an absent root raises (COULD NOT LOOK); the same root
    present is counted."""
    present = tmp_path / "root"
    present.mkdir()
    (present / "a.sh").write_text("echo ok\n")
    assert script_population([present]) == {(present / "a.sh").resolve()}
    absent = tmp_path / "gone"
    with pytest.raises(MissingRoot):
        script_population([absent])
    with pytest.raises(MissingRoot):
        find_drift([present, absent], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"))


def test_an_unreadable_candidate_is_a_hit_not_a_skip(tmp_path):
    """E1 paired control: the same offender is a literal hit while readable and
    an UNREADABLE hit after a controlled PermissionError -- never silence."""
    if os.geteuid() == 0:
        pytest.skip("root reads mode-000 files; the control cannot be armed")
    root = tmp_path / "root"
    root.mkdir()
    offender = root / "offender.sh"
    offender.write_text('WT="/home/mboyle/bd-cuts/cut/x"\n')
    readable = find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"))
    assert readable == [(str(offender.resolve()), LITERALS[0])], readable
    offender.chmod(0)
    try:
        blind = find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"))
    finally:
        offender.chmod(0o644)
    assert blind, "the unreadable offender vanished from the census (E1)"
    assert blind[0][0] == str(offender.resolve()) and blind[0][1].startswith(UNREADABLE), blind


def test_an_unreadable_directory_is_a_hit_not_an_omission(tmp_path):
    """E3 paired control (lens 2026-09-21T01:48Z): 101 clean scripts plus one
    offender inside a nested directory; with the directory readable the
    offender is the single hit; with the directory at mode 000 the census
    must report the DIRECTORY as unreadable -- never a clean 101/0."""
    if os.geteuid() == 0:
        pytest.skip("root traverses mode-000 directories; the control cannot be armed")
    root = tmp_path / "root"
    root.mkdir()
    for i in range(101):
        (root / ("clean%d.sh" % i)).write_text("echo %d\n" % i)
    hidden = root / "nested"
    hidden.mkdir()
    offender = hidden / "offender.sh"
    offender.write_text('WT="/home/mboyle/bd-cuts/cut/x"\n')
    readable = find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"))
    assert readable == [(str(offender.resolve()), LITERALS[0])], readable
    assert len(script_population([root])) == 102
    hidden.chmod(0)
    try:
        blind = find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"))
        with pytest.raises(MissingRoot):
            script_population([root])          # without a collector the omission RAISES
    finally:
        hidden.chmod(0o755)
    assert blind, "the unreadable directory vanished from the census (E3)"
    assert blind[0][0] == str(hidden) and blind[0][1].startswith(UNREADABLE) and "(directory)" in blind[0][1], blind
    after = find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"))
    assert after == readable


def test_a_supplied_home_that_is_missing_or_unreadable_fails_closed(tmp_path):
    """E4 paired control: home present -> its *.sh counted; home missing ->
    MissingRoot; home unreadable -> UNREADABLE (directory) hit, never 101/0."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.sh").write_text("echo a\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / "mine.sh").write_text('WT="/home/mboyle/bd-cuts/cut/x"\n')
    (home / "notes.txt").write_text("/home/mboyle/bd-cuts is not a script\n")
    hits = find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"), home=home)
    assert hits == [(str((home / "mine.sh").resolve()), LITERALS[0])], hits
    with pytest.raises(MissingRoot):
        find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"), home=tmp_path / "gone")
    if os.geteuid() == 0:
        pytest.skip("root reads mode-000 directories; the unreadable half cannot be armed")
    home.chmod(0)
    try:
        blind = find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"), home=home)
    finally:
        home.chmod(0o755)
    assert blind == [(str(home), "%s: PermissionError (directory)" % UNREADABLE)], blind


def test_snapshot_exclusion_is_anchored_to_the_name_end(tmp_path):
    """E5 paired control: deploy.prepare.sh is a LIVE script (".pre" mid-name)
    and its literal is a hit; x.sh.pre-<ts> / x.sh.post-<ts> / x.sh.ORIGINAL are snapshots."""
    root = tmp_path / "root"
    root.mkdir()
    live = root / "deploy.prepare.sh"
    live.write_text('WT="/home/mboyle/bd-cuts/cut/x"\n')
    for snap in ("x.sh.pre-20260101T000000Z", "x.sh.post-20260101T000000Z", "x.sh.ORIGINAL"):
        (root / snap).write_text('WT="/home/mboyle/bd-cuts/cut/old"\n')
    assert {p.name for p in script_population([root])} == {"deploy.prepare.sh"}
    hits = find_drift([root], _fx("CUT-ROOTS.sh"), _fx("ALLOWLIST.txt"))
    assert hits == [(str(live.resolve()), LITERALS[0])], hits
