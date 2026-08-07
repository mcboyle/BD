"""v3.66.938 -- an atomic write leaves a sidecar, and .gitignore covered only
the destination.

THE IDIOM, everywhere in this tree:

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(...)
    tmp.replace(path)                    # atomic

`path` is gitignored. `tmp` is a DIFFERENT path and was not. The window is
narrow -- a crash, an OSError, a kill between the write and the replace -- but
what sits in it is the file the ignore rule exists to keep out of the index,
under a name git does not know about. Untracked-and-unignored is precisely the
state where one `git add -A` commits it.

MEASURED at v3.66.937, all four bases ignored and all four sidecars not:

    .integrity_last_run   -> .integrity_last_run.tmp     db.py
    vapid_keys.json       -> vapid_keys.json.tmp         push.py:127
    secrets.json          -> secrets.json.tmp            secrets_store.py:509
    secrets_meta.json     -> secrets_meta.json.tmp       secrets_store.py:175, :342

The register carried this as "gitignore misses .integrity_last_run.tmp" --
trivial, one line. It is four, and two of them are the credential files.
`vapid_keys.json` holds the web-push PRIVATE key;
tests/test_gitignore_rules_actually_match.py already names that file as the
one whose exposure matters most, and it was gated on the destination alone.
Both resolve through a BARE RELATIVE default, so they land in whatever
directory the service was started from -- on the deploy host, the checkout.

WHY THE EXISTING GATE COULD NOT SEE THIS. Its subject is the RULES:
"does every line in .gitignore match something?" A path with no rule at all is
outside that denominator by construction, so it answers truthfully and
uselessly -- CLAUDE.md section 0. This file's subject is the other side: the
paths the CODE WRITES. The two together are the pair; neither alone is.

THE DISCOVERY SCAN IS DELIBERATELY OVER-BROAD, AND THAT IS WHY IT HAS AN
EXCEPTIONS TABLE. Associating a suffix with a constant at MODULE level rather
than by dataflow found the four real cases and one false one -- push.py
declares `_DB_REL = "downloader_history.db"` and, 100 lines away, writes
`vapid_path.with_suffix(".json.tmp")`, so the scan paired them and proposed
`downloader_history.json.tmp`, which nothing writes. A precise dataflow
predicate would need two hops through `_vapid_key_path()` and `_resolve()`.
Rather than ship false precision, the imprecision is declared: every candidate
must be ignored OR listed below with a reason. A new base/suffix pair in
source therefore fails this file until someone looks at it, which is the
behaviour worth having -- and unlike a pinned count, it cannot go quietly
blind, because an empty candidate set is itself a failure.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# Hand-verified at v3.66.938 by reading each write site. base -> (sidecar, site)
_SIDECARS = {
    ".integrity_last_run": (".integrity_last_run.tmp",
                            "bulk_downloader/db.py _record_integrity_check_ts"),
    "vapid_keys.json": ("vapid_keys.json.tmp", "bulk_downloader/push.py:127"),
    "secrets.json": ("secrets.json.tmp", "bulk_downloader/secrets_store.py:509"),
    "secrets_meta.json": ("secrets_meta.json.tmp",
                          "bulk_downloader/secrets_store.py:175,:342"),
}

# Candidates the over-broad scan proposes that no write site actually produces.
# A reason, not a mute button: each entry names why the pairing is spurious.
_NOT_WRITTEN = {
    "downloader_history.json.tmp":
        "push.py declares _DB_REL at module scope and writes "
        "vapid_path.with_suffix('.json.tmp') far below it; the scan pairs them "
        "at module level. The db is never written with a .json.tmp sidecar -- "
        "verified by reading every .tmp site in push.py (there is one, :127).",
}

_PLUS_SUFFIX = re.compile(r"""\+\s*["'](\.[A-Za-z0-9_.]{1,12})["']""")
_WITH_SUFFIX = re.compile(r"""with_suffix\(\s*["'](\.[A-Za-z0-9_.]{1,20})["']\s*\)""")


def _ignored(rel: str) -> bool:
    """Ask git, not the .gitignore text.

    The rule that achieves the ignore is not this file's business -- a literal
    line, a glob, or a directory rule are all fine. Asserting on the TEXT would
    fail a correct fix for its form, which CLAUDE.md section 0 counts as a
    soundness bug in its own right.
    """
    return subprocess.run(["git", "check-ignore", "-q", rel],
                          cwd=str(_REPO)).returncode == 0


def _tracked_py() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", "*.py"],
                         cwd=str(_REPO), capture_output=True, text=True).stdout
    return out.split()


def _candidates() -> dict[str, str]:
    """{sidecar_path: "module: CONST"} for every base/suffix pair in source.

    Over-broad by construction -- see the module docstring.
    """
    found: dict[str, str] = {}
    for rel in _tracked_py():
        p = _REPO / rel
        try:
            src = p.read_text("utf-8")
        except OSError:
            continue
        if ".tmp" not in src and ".part" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        consts = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                v = node.value.value
                if v and "/" not in v and "\n" not in v and "." in v and len(v) < 60:
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            consts[t.id] = v
        if not consts:
            continue
        suffixes = set(_PLUS_SUFFIX.findall(src)) | set(_WITH_SUFFIX.findall(src))
        if not suffixes:
            continue
        for name, val in consts.items():
            if not _ignored(val):
                # A base git does not ignore is a different subject entirely.
                continue
            for sfx in suffixes:
                sidecar = (Path(val).with_suffix(sfx) if sfx.count(".") > 1
                           else Path(val).with_suffix(Path(val).suffix + sfx))
                found[str(sidecar)] = f"{rel}: {name}={val!r}"
    return found


# ── the four measured instances ──────────────────────────────────────────────

@pytest.mark.parametrize("base,sidecar,site",
                         [(b, s, w) for b, (s, w) in sorted(_SIDECARS.items())])
def test_the_sidecar_is_ignored_wherever_its_destination_is(base, sidecar, site):
    """RED on pristine for all four."""
    assert _ignored(base), (
        f"{base} is not gitignored, so this row's premise is gone. Either the "
        f"rule was removed or the filename changed; fix the table rather than "
        f"letting the row pass vacuously.")
    assert _ignored(sidecar), (
        f"{site} writes {sidecar!r} and replaces it onto {base!r}. The "
        f"destination is ignored and the sidecar is not, so a crash between "
        f"the write and the replace leaves the file untracked-and-unignored -- "
        f"one `git add -A` from being committed.")


def test_the_write_site_named_for_each_row_still_exists():
    """A table of paths is worth nothing if the code stopped writing them.

    Guards the vacuous direction: were these writes deleted, the rows above
    would keep passing and the table would slowly become fiction.
    """
    for base, (sidecar, site) in sorted(_SIDECARS.items()):
        rel = site.split()[0].split(":")[0]
        src = (_REPO / rel).read_text("utf-8")
        assert Path(base).name in src or Path(base).stem in src, (
            f"{rel} no longer mentions {base!r}; the row for {sidecar!r} is "
            f"describing code that is gone.")
        assert ".tmp" in src, (
            f"{rel} no longer writes a .tmp sidecar; retire the row rather "
            f"than carrying an assertion about a vanished idiom.")


# ── the discovery scan ───────────────────────────────────────────────────────

def _unhandled(cands: dict[str, str]) -> dict[str, str]:
    """The candidates that are neither ignored nor declared not-written.

    EXTRACTED SO IT CAN BE TESTED DIRECTLY. A mutation battery showed the
    verdict below could be severed from its own measurement -- replacing this
    filter's condition with a constant made the gate pass unconditionally and
    NO test noticed, because the only assertion about it lived inside the test
    being mutated. A detector with no detector.
    """
    return {c: why for c, why in sorted(cands.items())
            if not _ignored(c) and c not in _NOT_WRITTEN}


def test_the_unhandled_filter_actually_filters():
    """The positive control for the gate below.

    Two synthetic candidates whose status is not in doubt: a tracked source
    file (never ignored) must come back unhandled, and a path under venv/
    (ignored by a long-standing rule) must not. If this filter ever stops
    depending on its input, the gate underneath it is decoration.
    """
    definitely_not_ignored = "bulk_downloader/app.py"
    definitely_ignored = "venv/lib/python3.12/site-packages/anything.tmp"
    assert not _ignored(definitely_not_ignored), (
        f"{definitely_not_ignored} is gitignored, so this control proves "
        f"nothing -- pick another tracked file.")
    assert _ignored(definitely_ignored), (
        f"{definitely_ignored} is not gitignored, so this control proves "
        f"nothing -- the venv rule must have changed.")

    got = _unhandled({definitely_not_ignored: "synthetic",
                      definitely_ignored: "synthetic"})
    assert definitely_not_ignored in got, (
        "the unhandled filter dropped a candidate that is plainly not "
        "ignored; its verdict no longer depends on its input.")
    assert definitely_ignored not in got, (
        "the unhandled filter reported an already-ignored path; it would fire "
        "on every clean tree and be switched off.")


def test_the_exception_table_is_honoured_but_only_for_its_own_entries():
    """The other half of the control: _NOT_WRITTEN must excuse exactly what it
    names and nothing else."""
    excused = next(iter(_NOT_WRITTEN))
    got = _unhandled({excused: "synthetic", "bulk_downloader/app.py": "synthetic"})
    assert excused not in got, (
        f"{excused!r} is in _NOT_WRITTEN and was still reported unhandled")
    assert "bulk_downloader/app.py" in got, (
        "the exception table excused a path it does not name")


def test_every_derived_sidecar_is_ignored_or_declared_not_written():
    """The gate whose denominator is the paths the CODE WRITES.

    The existing .gitignore gate asks whether every RULE matches something; a
    path with no rule is outside its denominator by construction. This asks the
    other question, and it is the one that was never asked.
    """
    cands = _candidates()
    assert cands, (
        "the sidecar scan found no candidates at all. Every assertion here "
        "would pass over nothing, which is a failure and not a clean run -- "
        "the scan itself has broken.")
    unhandled = _unhandled(cands)
    assert not unhandled, (
        "atomic-write sidecar path(s) whose destination is gitignored but "
        "which are not ignored themselves:\n"
        + "\n".join(f"    {c}   <- {why}" for c, why in unhandled.items())
        + "\n\nAdd a .gitignore rule, or -- if no write site actually produces "
          "this path -- add it to _NOT_WRITTEN with the reading that shows so.")


def test_the_scan_still_reaches_the_four_known_instances():
    """The discovery scan's own denominator check.

    A scan that silently stopped matching would report 'no unhandled
    candidates' forever. CLAUDE.md section 0: make the denominator contain the
    subject, and prove it does.
    """
    cands = _candidates()
    for base, (sidecar, site) in sorted(_SIDECARS.items()):
        assert sidecar in cands, (
            f"the scan no longer finds {sidecar!r} (from {site}). It is a "
            f"known instance, so its absence means the scan went blind rather "
            f"than the tree got clean.")


def test_the_exceptions_table_carries_no_dead_entries():
    """An exception for a candidate the scan no longer proposes is a licence
    nobody is watching. Over-sensitivity in the other direction."""
    cands = _candidates()
    dead = [c for c in _NOT_WRITTEN if c not in cands]
    assert not dead, (
        f"_NOT_WRITTEN excuses candidate(s) the scan no longer proposes: "
        f"{dead}. Remove them; a standing exception for a case that cannot "
        f"arise will silently excuse a future real one of the same name.")


def test_no_exception_is_granted_to_a_path_that_is_already_ignored():
    """If a path is ignored, it needs no excuse. Keeping one blurs which
    mechanism is doing the work."""
    redundant = [c for c in _NOT_WRITTEN if _ignored(c)]
    assert not redundant, (
        f"_NOT_WRITTEN names path(s) that ARE gitignored: {redundant}. The "
        f"ignore rule already settles them; the exception only obscures that.")
